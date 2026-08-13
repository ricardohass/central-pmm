# -*- coding: utf-8 -*-
"""Cobertura de gravação das calls — agenda do Google x Meetrox.

Responde uma pergunta só: toda call que aconteceu foi gravada? E quando não foi,
foi falha do bot ou o lead não apareceu?

    python3 cobertura_calls.py                        # últimos 3 dias até ontem
    python3 cobertura_calls.py 2026-07-20 2026-08-13  # intervalo manual (backfill)

O bot do Meetrox espera 5 minutos na sala e sai. Se o lead atrasa, cabe ao closer
readmitir pela extensão do Chrome — e às vezes não readmite. É esse buraco que o
script encontra.

TRÊS FONTES, cruzadas pelo código do Meet (ex: 'nwz-fwmb-zgc'):
  1. Agenda de cada closer  → que calls estavam marcadas
  2. Meetrox               → quais foram gravadas
  3. Log de auditoria do Meet → quem de fato entrou na sala

A terceira é o que separa no-show de falha do bot. Sem ela o script ainda roda,
mas os casos sem gravação ficam como 'indeterminado' e alguém classifica na mão.

JANELA MÓVEL: reprocessa os últimos dias sempre, com upsert. O Meetrox às vezes
demora pra terminar de processar uma call, e a rodada seguinte corrige o veredito
sozinha.

Sem SUPABASE_SERVICE_ROLE no ambiente o script não grava nada: só imprime o
levantamento. É assim que se roda na mão pra conferir.
"""
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# google-auth é a única dependência fora da stdlib deste repositório. Não dá pra
# evitar: a conta de serviço exige um JWT assinado em RS256 e o Python padrão não
# tem RSA. O workflow instala antes de rodar.
try:
    from google.oauth2 import service_account
    import google.auth.transport.requests
    TEM_GOOGLE = True
except ImportError:
    TEM_GOOGLE = False

SP = ZoneInfo('America/Sao_Paulo')
DOMINIO = '@grupoprooficial.com'

SUPA = 'https://ebcydqqhvdapruhnwbce.supabase.co/rest/v1/'
ANON = base64.b64decode(
    'ZXlKaGJHY2lPaUpJVXpJMU5pSXNJblI1Y0NJNklrcFhWQ0o5LmV5SnBjM01pT2lKemRYQmhZbUZ6WlNJc0lu'
    'SmxaaUk2SW1WaVkzbGtjWEZvZG1SaGNISjFhRzUzWW1ObElpd2ljbTlzWlNJNkltRnViMjRpTENKcFlYUWlP'
    'akUzTnprNU9ETXlOaklzSW1WNGNDSTZNakE1TlRVMU9USTJNbjAuME9SaTlGUlpWU3Q2V09iM1EzVnhWWXoy'
    'VWtYR1JDYlptcTJCVTUwWEpGMA==').decode()

# .strip() em todo secret: valor colado no GitHub costuma vir com quebra de linha
# junto, e aí o header sai malformado.
SERVICE_ROLE = os.environ.get('SUPABASE_SERVICE_ROLE', '').strip()
MEETROX_KEY = os.environ.get('MEETROX_API_KEY', '').strip()
SA_JSON = os.environ.get('GOOGLE_SA_JSON', '').strip()
ADMIN_EMAIL = os.environ.get('GOOGLE_ADMIN_EMAIL', '').strip()

MEETROX = 'https://api.meetrox.ai/v1'
GCAL = 'https://www.googleapis.com/calendar/v3'
REPORTS = 'https://admin.googleapis.com/admin/reports/v1'
ESCOPO_CAL = ['https://www.googleapis.com/auth/calendar.readonly']
ESCOPO_LOG = ['https://www.googleapis.com/auth/admin.reports.audit.readonly']

DIAS_JANELA = int(os.environ.get('COBERTURA_DIAS', '3'))

# Modo de leitura da agenda, descoberto sozinho na primeira tentativa:
#   'compartilhada' — o closer compartilhou a agenda com a conta de serviço
#   'delegacao'     — delegação em todo o domínio, a conta impersona o closer
# Descobrir em vez de configurar evita um secret a mais e sobrevive à troca de
# um modo pelo outro sem ninguém lembrar de mexer aqui.
MODO_COMPARTILHADO = None


# ── infra ────────────────────────────────────────────────────────────────────

def http(url, headers=None, method='GET', body=None, tolerar=()):
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, data, timeout=90) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt.strip() else None
    except urllib.error.HTTPError as e:
        if e.code in tolerar:
            return None
        raise RuntimeError('%s %s → %s %s' % (method, url.split('?')[0], e.code,
                                              e.read().decode()[:400]))


def supa(path, method='GET', body=None, prefer=None):
    key = SERVICE_ROLE or ANON
    h = {'apikey': key, 'Authorization': 'Bearer ' + key}
    if prefer:
        h['Prefer'] = prefer
    return http(SUPA + path, h, method, body)


def meetrox(path):
    return http(MEETROX + path, {'x-api-key': MEETROX_KEY, 'accept': 'application/json'})


def token_google(subject, escopos):
    """Token de acesso. Com `subject`, agindo em nome daquele usuário.

    Dois modos, e o script funciona nos dois:

    - AGENDA COMPARTILHADA (subject=None): a conta de serviço usa a própria
      identidade e enxerga só as agendas que foram compartilhadas com o e-mail
      dela. Não precisa de delegação nenhuma.
    - DELEGAÇÃO (subject=e-mail): a conta pede pra ser aquele usuário, e o
      Workspace autoriza porque o Client ID dela está liberado no admin.

    O log de auditoria do Meet só existe no segundo modo, e só em nome de um
    admin — não há como um closer autorizar isso.
    """
    info = json.loads(SA_JSON)
    cred = service_account.Credentials.from_service_account_info(
        info, scopes=escopos, subject=subject or None)
    cred.refresh(google.auth.transport.requests.Request())
    return cred.token


def norm(codigo):
    """'nwz-fwmb-zgc', 'NWZ FWMB ZGC' e 'nwzfwmbzgc' viram a mesma coisa.

    O Meetrox e o log de auditoria nem sempre formatam igual, e o cruzamento
    inteiro depende deste código bater.
    """
    return re.sub(r'[^a-z0-9]', '', (codigo or '').lower())


def codigo_da_url(url):
    if not url:
        return ''
    m = re.search(r'meet\.google\.com/([a-z0-9\-]+)', url, re.I)
    return norm(m.group(1)) if m else ''


# ── fontes ───────────────────────────────────────────────────────────────────

def eventos_da_agenda(email, t0, t1):
    """Eventos do closer na janela. Cancelados já vêm de fora por padrão.

    Na primeira chamada tenta os dois modos e fixa o que funcionar, pra não
    repetir a descoberta a cada closer.
    """
    global MODO_COMPARTILHADO
    if MODO_COMPARTILHADO is None:
        for compartilhada in (True, False):
            try:
                _buscar_eventos(email, t0, t1, compartilhada)
                MODO_COMPARTILHADO = compartilhada
                print('  modo de leitura: %s'
                      % ('agenda compartilhada' if compartilhada else 'delegação no domínio'))
                break
            except Exception:
                continue
        else:
            # Nenhum funcionou: repete o compartilhado só pra propagar o erro
            # verdadeiro pra quem chamou, em vez de um genérico.
            MODO_COMPARTILHADO = True
            return _buscar_eventos(email, t0, t1, True)
    return _buscar_eventos(email, t0, t1, MODO_COMPARTILHADO)


def _buscar_eventos(email, t0, t1, compartilhada):
    tok = token_google(None if compartilhada else email, ESCOPO_CAL)
    saida, page = [], None
    while True:
        q = {'timeMin': t0.isoformat(), 'timeMax': t1.isoformat(),
             'singleEvents': 'true', 'orderBy': 'startTime', 'maxResults': '250'}
        if page:
            q['pageToken'] = page
        url = '%s/calendars/%s/events?%s' % (
            GCAL, urllib.parse.quote(email), urllib.parse.urlencode(q))
        r = http(url, {'Authorization': 'Bearer ' + tok})
        saida.extend(r.get('items', []))
        page = r.get('nextPageToken')
        if not page:
            break
    return saida


def presencas_no_meet(t0, t1):
    """{codigo_do_meet: [participantes]} pelo log de auditoria do domínio.

    Só pode ser lido em nome de um super admin — não do closer. Por isso o
    GOOGLE_ADMIN_EMAIL é um secret à parte.
    """
    tok = token_google(ADMIN_EMAIL, ESCOPO_LOG)
    por_codigo, page = {}, None
    while True:
        q = {'startTime': t0.isoformat(), 'endTime': t1.isoformat(),
             'eventName': 'call_ended', 'maxResults': '1000'}
        if page:
            q['pageToken'] = page
        url = '%s/activity/users/all/applications/meet?%s' % (
            REPORTS, urllib.parse.urlencode(q))
        r = http(url, {'Authorization': 'Bearer ' + tok})
        for item in (r.get('items') or []):
            for ev in (item.get('events') or []):
                p = {}
                for par in (ev.get('parameters') or []):
                    nome = par.get('name')
                    if 'value' in par:
                        p[nome] = par['value']
                    elif 'intValue' in par:
                        p[nome] = int(par['intValue'])
                    elif 'boolValue' in par:
                        p[nome] = par['boolValue']
                cod = norm(p.get('meeting_code'))
                if not cod:
                    continue
                por_codigo.setdefault(cod, []).append({
                    'quem': (p.get('identifier') or '').lower(),
                    'segundos': int(p.get('duration_seconds') or 0),
                    'externo': bool(p.get('is_external')),
                })
        page = r.get('nextPageToken')
        if not page:
            break
    return por_codigo


def calls_do_meetrox(t0, t1):
    saida, cursor = [], None
    while True:
        q = ('/calls?first=100'
             '&filters[0][field]=timestamp&filters[0][op]=>=&filters[0][value]=' + t0.strftime('%Y-%m-%dT%H:%M:%SZ') +
             '&filters[1][field]=timestamp&filters[1][op]=<=&filters[1][value]=' + t1.strftime('%Y-%m-%dT%H:%M:%SZ'))
        if cursor:
            q += '&after=' + urllib.parse.quote(cursor)
        r = meetrox(q)
        saida.extend(r.get('data') or [])
        meta = r.get('meta') or {}
        if not meta.get('has_next_page'):
            break
        cursor = meta.get('end_cursor')
    return saida


# ── regra ────────────────────────────────────────────────────────────────────

def e_call_de_verdade(ev, email_closer):
    """Toda call com lead deve ter Meetrox, independente do prefixo do título.

    O que exclui: sem sala do Meet, sem ninguém de fora (reunião interna),
    cancelada, ou o closer recusou o convite.
    """
    if ev.get('status') == 'cancelled':
        return False, 'cancelada'
    if not codigo_da_url(ev.get('hangoutLink')):
        return False, 'sem sala do Meet'
    externos = [a for a in (ev.get('attendees') or [])
                if a.get('email') and not a['email'].lower().endswith(DOMINIO)
                and not a.get('resource')]
    if not externos:
        return False, 'sem convidado externo'
    # Compara por e-mail, não pelo campo `self`: `self` é relativo a quem
    # autenticou. Lendo uma agenda compartilhada, quem autentica é a conta de
    # serviço, o campo nunca vem, e a recusa do closer passaria despercebida.
    eu = [a for a in (ev.get('attendees') or [])
          if (a.get('email') or '').lower() == email_closer.lower() or a.get('self')]
    if eu and eu[0].get('responseStatus') == 'declined':
        return False, 'closer recusou'
    return True, len(externos)


def classificar(gravada, presencas, email_closer):
    """gravada + quem entrou na sala → veredito."""
    if presencas is None:
        # Sem log de auditoria não dá pra afirmar nada sobre quem entrou.
        return ('ok', 'gravada') if gravada else \
               ('indeterminado', 'sem gravação e sem log de auditoria pra decidir')

    closer = any(p['quem'] == email_closer.lower() or not p['externo'] for p in presencas)
    ext = [p for p in presencas if p['externo'] and p['segundos'] > 0]

    if gravada:
        return 'ok', 'gravada'
    if closer and ext:
        return 'sem_gravacao', 'closer e lead entraram, o Meetrox não gravou'
    if closer and not ext:
        return 'no_show', 'só o closer entrou na sala'
    if not closer and not ext:
        return 'nao_aconteceu', 'ninguém entrou na sala'
    return 'sem_gravacao', 'lead entrou sem o closer'


# ── execução ─────────────────────────────────────────────────────────────────

def janela(argv):
    # Aspas vazias contam como argumento: na rodada agendada o workflow passa
    # "" "" e a janela tem que voltar a ser a padrão, não estourar.
    args = [a for a in argv[1:] if a.strip()]
    if len(args) >= 2:
        d0 = date.fromisoformat(args[0])
        d1 = date.fromisoformat(args[1])
    else:
        d1 = date.today() - timedelta(days=1)
        d0 = d1 - timedelta(days=DIAS_JANELA - 1)
    t0 = datetime.combine(d0, time.min, SP).astimezone(timezone.utc)
    t1 = datetime.combine(d1, time.max, SP).astimezone(timezone.utc)
    return d0, d1, t0, t1


def diagnosticar_google(e):
    """Traduz o erro do Google pra causa provável.

    Depurar isso pelo log do Actions é caro — a mensagem crua do Google não diz
    o que fazer, e quem lê o log não é quem configurou.
    """
    t = str(e)
    if 'unauthorized_client' in t:
        return ('a delegação em todo o domínio não está valendo. Confira no '
                'admin.google.com se o Client ID 102752329116181544361 está lá '
                'com os dois escopos. Recém-autorizada, pode levar alguns '
                'minutos pra propagar.')
    if 'access_denied' in t or 'forbidden' in t.lower():
        return ('a conta existe mas não pode agir por esse usuário. Verifique '
                'se o e-mail está no domínio autorizado.')
    if '404' in t and MODO_COMPARTILHADO:
        return ('agenda não compartilhada com a conta de serviço. O closer '
                'precisa liberar em "Ver todos os detalhes do evento" para '
                'central-pmm-cobertura@fifth-liberty-505418-u3.iam.gserviceaccount.com')
    if 'invalid_grant' in t:
        return ('usuário inexistente ou fora do domínio da delegação.')
    if 'not been used' in t or 'is disabled' in t:
        return ('a API não foi ativada no projeto do Google Cloud.')
    if 'Invalid JWT' in t or 'invalid_client' in t:
        return ('o GOOGLE_SA_JSON parece truncado ou não é o arquivo inteiro.')
    return None


def main():
    d0, d1, t0, t1 = janela(sys.argv)
    print('Janela: %s a %s (America/Sao_Paulo)' % (d0, d1))

    # Estado da configuração antes de qualquer coisa: quando isto falha, quem lê
    # o log quer saber primeiro o que chegou e o que não chegou.
    print('Credenciais: ' + ' · '.join(
        '%s=%s' % (nome, 'ok' if val else 'FALTA')
        for nome, val in [('MEETROX_API_KEY', MEETROX_KEY),
                          ('SUPABASE_SERVICE_ROLE', SERVICE_ROLE),
                          ('GOOGLE_SA_JSON', SA_JSON),
                          ('GOOGLE_ADMIN_EMAIL', ADMIN_EMAIL)]))
    if SA_JSON and not TEM_GOOGLE:
        print('  google-auth não importou — o passo de instalação do workflow falhou?')

    closers = supa('agendas_closers?ativo=eq.true&select=email,nome,inicio_em&order=email')
    print('Closers auditados: %d' % len(closers))

    if not MEETROX_KEY:
        sys.exit('Falta MEETROX_API_KEY — sem isso não há o que cruzar.')
    calls = calls_do_meetrox(t0, t1)
    por_codigo_mrx = {}
    for c in calls:
        cod = codigo_da_url((c.get('source') or {}).get('meeting_system_url'))
        if cod:
            por_codigo_mrx.setdefault(cod, []).append(c)
    print('Calls no Meetrox: %d (%d códigos de Meet distintos)'
          % (len(calls), len(por_codigo_mrx)))

    usa_google = bool(SA_JSON) and TEM_GOOGLE
    if not usa_google:
        print('\nSem GOOGLE_SA_JSON: só consigo ver o lado do Meetrox.')
        print('Calls gravadas por closer no período:')
        cont = {}
        for c in calls:
            cont[(c.get('agent') or {}).get('name') or '—'] = \
                cont.get((c.get('agent') or {}).get('name') or '—', 0) + 1
        for nome, n in sorted(cont.items(), key=lambda x: -x[1]):
            print('  %4d  %s' % (n, nome))
        print('\nO cruzamento com a agenda depende da credencial do Google.')
        return

    presencas = None
    if ADMIN_EMAIL:
        try:
            presencas = presencas_no_meet(t0, t1)
            print('Log de auditoria do Meet: %d salas com registro' % len(presencas))
        except Exception as e:
            print('Log de auditoria indisponível (%s) — casos sem gravação vão '
                  'ficar como indeterminado.' % str(e)[:160])
    else:
        print('Sem GOOGLE_ADMIN_EMAIL: não dá pra separar no-show de falha do bot.')

    linhas, casados = [], set()

    falhas = []
    for cl in closers:
        email, nome = cl['email'], cl['nome']
        try:
            eventos = eventos_da_agenda(email, t0, t1)
        except Exception as e:
            # Um closer com agenda inacessível não pode derrubar a auditoria dos
            # outros quatro — registra e segue.
            falhas.append(nome)
            print('  %s: não consegui ler a agenda. %s'
                  % (nome, diagnosticar_google(e) or str(e)[:200]))
            continue
        print('  %s: %d evento(s) na agenda' % (nome, len(eventos)))
        for ev in eventos:
            if cl.get('inicio_em') and (ev.get('start') or {}).get('dateTime', '')[:10] < cl['inicio_em']:
                continue
            vale, motivo = e_call_de_verdade(ev, email)
            if not vale:
                continue
            cod = codigo_da_url(ev.get('hangoutLink'))
            achadas = por_codigo_mrx.get(cod) or []
            mrx = achadas[0] if achadas else None
            # Todas, não só a primeira: quando o bot é readmitido o Meetrox gera
            # dois registros pro mesmo código. Marcar só uma faria a segunda
            # aparecer como "gravada fora da agenda", que é falso.
            for a in achadas:
                casados.add(a['id'])

            pres = presencas.get(cod, []) if presencas is not None else None
            status, porque = classificar(bool(mrx), pres, email)
            ini = (ev.get('start') or {}).get('dateTime')
            fim = (ev.get('end') or {}).get('dateTime')
            ext_pres = [p for p in (pres or []) if p['externo']]

            linhas.append({
                'chave': 'ev:' + ev['id'],
                'closer': nome, 'closer_email': email,
                'data': datetime.fromisoformat(ini).astimezone(SP).date().isoformat() if ini else str(d1),
                'inicio': ini, 'fim': fim,
                'titulo': ev.get('summary') or '(sem título)',
                'meet_code': cod,
                'evento_id': ev['id'],
                'convidados_ext': motivo if isinstance(motivo, int) else None,
                'evento_cancelado': False, 'closer_recusou': False,
                'meetrox_call_id': mrx['id'] if mrx else None,
                'meetrox_url': mrx.get('url') if mrx else None,
                'gravada': bool(mrx),
                'duracao_gravacao': int(mrx['duration']) if mrx and mrx.get('duration') else None,
                'meet_entrou_closer': (any(p['quem'] == email.lower() or not p['externo'] for p in pres)
                                       if pres is not None else None),
                'meet_entrou_ext': (bool(ext_pres) if pres is not None else None),
                'meet_dur_ext_seg': (max([p['segundos'] for p in ext_pres]) if ext_pres else 0)
                                    if pres is not None else None,
                'meet_apurado': pres is not None,
                'status': status, 'motivo': porque,
                'atualizado_em': datetime.now(timezone.utc).isoformat(),
            })

    if falhas and len(falhas) == len(closers):
        sys.exit('\nNenhuma agenda pôde ser lida — não há cruzamento a fazer. '
                 'Nada foi gravado.')

    # Gravou mas não havia evento na agenda: call marcada na hora.
    # Só entra quando a agenda foi lida: sem ela, "não estava marcada" seria
    # uma conclusão falsa — a agenda é que não foi consultada.
    nomes = {c['nome'] for c in closers if c['nome'] not in falhas}
    for c in calls:
        if c['id'] in casados:
            continue
        agente = (c.get('agent') or {}).get('name') or ''
        if agente not in nomes:
            continue
        ts = c.get('timestamp')
        linhas.append({
            'chave': 'mr:%s' % c['id'],
            'closer': agente, 'closer_email': (c.get('agent') or {}).get('email'),
            'data': datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone(SP).date().isoformat(),
            'inicio': ts, 'titulo': c.get('title') or '(sem título)',
            'meet_code': codigo_da_url((c.get('source') or {}).get('meeting_system_url')),
            'meetrox_call_id': c['id'], 'meetrox_url': c.get('url'),
            'gravada': True,
            'duracao_gravacao': int(c['duration']) if c.get('duration') else None,
            'meet_apurado': presencas is not None,
            'status': 'fora_da_agenda', 'motivo': 'gravada sem evento correspondente na agenda',
            'atualizado_em': datetime.now(timezone.utc).isoformat(),
        })

    resumo = {}
    for l in linhas:
        resumo[l['status']] = resumo.get(l['status'], 0) + 1
    print('\nResultado: %d linha(s)' % len(linhas))
    for st, n in sorted(resumo.items(), key=lambda x: -x[1]):
        print('  %4d  %s' % (n, st))

    faltando = [l for l in linhas if l['status'] == 'sem_gravacao']
    if faltando:
        print('\nCalls que aconteceram e NÃO foram gravadas:')
        for l in sorted(faltando, key=lambda x: (x['closer'], x['data'])):
            print('  %s  %-16s %s' % (l['data'], l['closer'], l['titulo'][:56]))

    if not SERVICE_ROLE:
        print('\nSem SUPABASE_SERVICE_ROLE: não gravei nada.')
        return
    if not linhas:
        print('\nNada a gravar.')
        return
    try:
        for i in range(0, len(linhas), 100):
            supa('cobertura_calls', 'POST', linhas[i:i + 100],
                 prefer='resolution=merge-duplicates,return=minimal')
    except Exception as e:
        # 401 aqui quer dizer que a chave é a anon, não a service_role: a anon
        # tem policy só de select e de update em status_manual.
        sys.exit('\nNão consegui gravar: %s\nSe for 401, confira se o secret '
                 'SUPABASE_SERVICE_ROLE tem a chave service_role mesmo.' % str(e)[:300])
    print('\nGravado no Supabase: %d linha(s).' % len(linhas))


if __name__ == '__main__':
    main()
