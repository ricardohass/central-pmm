# -*- coding: utf-8 -*-
"""Cobertura de gravação das calls — agenda do Google x Meetrox.

Responde uma pergunta só: toda call que aconteceu foi gravada? E quando não foi,
foi falha do bot ou o lead não apareceu?

    python3 cobertura_calls.py                        # últimos 3 dias, incluindo hoje
    python3 cobertura_calls.py 2026-07-20 2026-08-13  # intervalo manual (backfill)

O bot do Meetrox espera 5 minutos na sala e sai. Se o lead atrasa, cabe ao closer
readmitir pela extensão do Chrome — e às vezes não readmite. É esse buraco que o
script encontra.

TRÊS FONTES, cruzadas pelo código do Meet (ex: 'nwz-fwmb-zgc'):
  1. Agenda de cada closer  → que calls estavam marcadas
  2. Meetrox               → quais foram gravadas
  3. Meet API              → quem de fato entrou em cada sala

A terceira é o que separa no-show de falha do bot, e só é consultada para as
calls SEM gravação — nas gravadas o Meetrox já respondeu. Sem ela o script ainda
roda, mas aí "sem gravação" mistura falha de processo com lead que não apareceu.

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
MEET = 'https://meet.googleapis.com/v2'
ESCOPO_CAL = ['https://www.googleapis.com/auth/calendar.readonly']
# Escopo de usuário comum: alcança só as salas que o próprio closer criou.
# Não é o admin.reports do domínio — foi a versão que o TI aceitou autorizar.
ESCOPO_MEET = ['https://www.googleapis.com/auth/meetings.space.readonly']

# Como o bot do Meetrox se identifica na sala. Vê-lo entre os participantes é
# prova direta de que ele foi admitido, sem depender do registro do Meetrox.
BOT = 'meetrox'

DIAS_JANELA = int(os.environ.get('COBERTURA_DIAS', '3'))

# No Actions, faltar credencial é FALHA — sair verde esconde uma execução vazia.
# Rodando na mão, faltar credencial é modo de conferência e segue imprimindo.
NO_ACTIONS = os.environ.get('GITHUB_ACTIONS', '').lower() == 'true'

# Modo de leitura da agenda, descoberto sozinho na primeira tentativa:
#   'compartilhada' — o closer compartilhou a agenda com a conta de serviço
#   'delegacao'     — delegação em todo o domínio, a conta impersona o closer
# Descobrir em vez de configurar evita um secret a mais e sobrevive à troca de
# um modo pelo outro sem ninguém lembrar de mexer aqui.
MODO_COMPARTILHADO = None
_INTERNOS = None  # equipe, carregada uma vez do /users do Meetrox

# Encontro do time que por acaso tem gente de fora na sala (aluno, parceiro) e
# por isso passaria no teste de "tem convidado externo". Não é call de venda e
# não entra na auditoria. Comparação sem acento e em minúsculas.
TITULOS_FORA = ['treinamento', 'alinhamento', 'reuniao geral', 'daily',
                'time de vendas']


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


class _RespGoogle(object):
    """Resposta no formato que o google-auth espera do transporte."""
    def __init__(self, status, headers, data):
        self.status, self.headers, self.data = status, headers, data


class TransporteUrllib(object):
    """Transporte do google-auth feito com urllib da biblioteca padrão.

    O caminho normal seria `google.auth.transport.requests`, mas ele exige a
    biblioteca `requests`, que NÃO vem junto do google-auth — é um extra. Foi
    isso que derrubou a primeira execução real: o pip instalou, o import
    quebrou, e o script concluiu que faltava credencial.

    Corrigir pelo workflow (instalar `google-auth[requests]`) seria mais óbvio,
    mas o token do Mac não dá push em .github/workflows/ e a correção ficaria
    dependendo de alguém editar pela web. Sem dependência nenhuma, não quebra
    de novo.
    """
    def __call__(self, url, method='GET', body=None, headers=None, timeout=None, **kw):
        req = urllib.request.Request(url, data=body, method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout or 60) as r:
                return _RespGoogle(r.status, dict(r.headers), r.read())
        except urllib.error.HTTPError as e:
            # O google-auth lê o corpo do erro pra montar a mensagem — devolver
            # em vez de estourar é o que faz 'unauthorized_client' chegar legível.
            return _RespGoogle(e.code, dict(e.headers), e.read())


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
    cred.refresh(TransporteUrllib())
    return cred.token


def sem_acento(s):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', (s or '').lower())
                   if unicodedata.category(c) != 'Mn')


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


def com_traco(cod):
    """'okavtebywp' → 'oka-vteb-ywp'. A Meet API exige o formato com traço."""
    c = norm(cod)
    return '%s-%s-%s' % (c[:3], c[3:7], c[7:]) if len(c) == 10 else c


def janela_do_evento(ini, fim):
    """Intervalo em que a conferência daquele evento pode ter acontecido.

    Meia hora antes do horário marcado (closer que entra cedo) e duas horas
    depois do fim previsto (call que estica). Fora disso é outra call.
    """
    if not ini:
        return None, None
    t0 = datetime.fromisoformat(ini)
    t1 = datetime.fromisoformat(fim) if fim else t0 + timedelta(hours=1)
    return t0 - timedelta(minutes=30), t1 + timedelta(hours=2)


def _quando(iso):
    """Timestamp do Google → datetime com fuso. Sempre UTC quando vem com Z."""
    if not iso:
        return None
    t = iso.replace('Z', '+00:00')
    # A Meet API manda mais casas decimais do que o fromisoformat aceita.
    t = re.sub(r'\.(\d{6})\d+', r'.\1', t)
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# A Meet API guarda os registros de conferência por 30 dias. Passado isso, "não
# há registro" deixa de significar "ninguém entrou" e passa a significar "não dá
# mais pra saber" — e reprocessar um mês antigo transformaria calls que
# aconteceram em "ninguém entrou na sala".
RETENCAO_MEET_DIAS = 28


def quem_entrou(email_closer, cod, ini=None, fim=None):
    """Participantes da sala do Meet NAQUELE horário. None quando não dá pra apurar.

    Chamada só para as calls SEM gravação — que são as ambíguas. Para as
    gravadas o Meetrox já respondeu, e consultar seria gastar requisição à toa.

    O horário importa. Um mesmo código de Meet costuma servir a várias calls do
    mesmo lead — o closer reaproveita o link quando remarca. Sem filtrar pela
    janela do evento, a call de hoje herdava os participantes da conferência da
    semana passada, inclusive o bot do Meetrox: o veredito virava "o bot entrou
    e o Meetrox não gravou" quando o que houve foi o bot não ser admitido.
    """
    tok = token_google(email_closer, ESCOPO_MEET)
    h = {'Authorization': 'Bearer ' + tok}
    filtro = urllib.parse.quote('space.meeting_code="%s"' % com_traco(cod))
    r = http('%s/conferenceRecords?filter=%s' % (MEET, filtro), h, tolerar=(403, 404))
    if r is None:
        return None
    regs = r.get('conferenceRecords') or []

    j0, j1 = janela_do_evento(ini, fim)
    if j0:
        agora = datetime.now(timezone.utc)
        regs = [g for g in regs
                if (_quando(g.get('startTime')) or agora) < j1
                and (_quando(g.get('endTime')) or agora) > j0]
        velho = (agora - j1).days > RETENCAO_MEET_DIAS
    else:
        velho = False

    if not regs:
        # Sem registro dentro da janela: a sala não foi aberta naquele horário —
        # a menos que o Meet já tenha descartado o registro por idade.
        return None if velho else []

    # Dentro da janela ainda cabe mais de uma conferência: a sala abre, fecha e
    # reabre minutos depois, e uma delas vem vazia. Junta os participantes de
    # todas — a pergunta é quem esteve na sala, e entrar duas vezes não muda a
    # resposta.
    saida, vistos = [], set()
    for reg in regs[:4]:
        p = http('%s/%s/participants' % (MEET, reg['name']), h, tolerar=(403, 404))
        if p is None:
            continue
        for x in (p.get('participants') or []):
            nome = ((x.get('signedinUser') or {}).get('displayName')
                    or (x.get('anonymousUser') or {}).get('displayName')
                    or (x.get('phoneUser') or {}).get('displayName') or '')
            chave = sem_acento(nome)
            if chave in vistos:
                continue
            vistos.add(chave)
            saida.append({'nome': nome, 'entrou': x.get('earliestStartTime')})
    return saida


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

def equipe_interna():
    """E-mails de quem é da casa, pra não confundir reunião interna com call.

    Testar só o domínio não basta: parte do time usa domínio próprio ou Gmail
    pessoal (Gabriel Mor, SDRs), e aí Daily, Treinamento e Reunião Geral
    entravam na auditoria como se fossem call de cliente.

    A fonte é o /users do Meetrox: é a lista que já existe e já é mantida —
    quem entra no time aparece aqui sem ninguém precisar cadastrar de novo.
    """
    global _INTERNOS
    if _INTERNOS is None:
        _INTERNOS = set()
        try:
            for u in (meetrox('/users?first=100').get('data') or []):
                if u.get('email'):
                    _INTERNOS.add(u['email'].lower())
        except Exception as e:
            print('  não consegui listar a equipe no Meetrox (%s) — vou usar só '
                  'o domínio, e reunião interna pode entrar na conta.' % str(e)[:120])
    return _INTERNOS


def e_call_de_verdade(ev, email_closer):
    """Toda call com lead deve ter Meetrox, independente do prefixo do título.

    O que exclui: sem sala do Meet, sem ninguém de fora (reunião interna),
    cancelada, ou o closer recusou o convite.
    """
    if ev.get('status') == 'cancelled':
        return False, 'cancelada'
    if not codigo_da_url(ev.get('hangoutLink')):
        return False, 'sem sala do Meet'
    titulo = sem_acento(ev.get('summary') or '')
    if any(p in titulo for p in TITULOS_FORA):
        return False, 'encontro do time'
    internos = equipe_interna()
    externos = [a for a in (ev.get('attendees') or [])
                if a.get('email') and not a['email'].lower().endswith(DOMINIO)
                and a['email'].lower() not in internos
                and not a.get('resource')]
    if not externos:
        return False, 'reunião interna'
    # Lead que recusou o convite: a call não ia acontecer mesmo. Cobrar gravação
    # disso seria acusar o closer de algo que o próprio Google já registrou que
    # não ia ocorrer.
    if all(a.get('responseStatus') == 'declined' for a in externos):
        return False, 'lead recusou'
    # Compara por e-mail, não pelo campo `self`: `self` é relativo a quem
    # autenticou. Lendo uma agenda compartilhada, quem autentica é a conta de
    # serviço, o campo nunca vem, e a recusa do closer passaria despercebida.
    eu = [a for a in (ev.get('attendees') or [])
          if (a.get('email') or '').lower() == email_closer.lower() or a.get('self')]
    if eu and eu[0].get('responseStatus') == 'declined':
        return False, 'closer recusou'
    return True, len(externos)


def identificar_closer(humanos, nome_closer):
    """Quais participantes são o closer, e não o lead.

    O time inteiro assina o Meet com 'Estrategista' no nome — conferido nos
    cinco: 'Amanda Duarte - Estrategista', 'Janaina Xavier Estrategista',
    'Amanda Estrategista PMM'. É o sinal mais confiável que existe aqui.

    Primeiro nome sozinho não basta: um lead chamado Amanda numa call da Amanda
    Duarte seria contado como a closer, a lista de leads ficaria vazia e a call
    viraria no-show sem ter sido. Por isso, quando mais de um participante casa
    pelo primeiro nome, o desempate é o 'Estrategista'.
    """
    primeiro = sem_acento(nome_closer).split()[0] if nome_closer.strip() else ''
    if not primeiro:
        return []
    por_nome = [p for p in humanos if primeiro in sem_acento(p['nome'])]
    if len(por_nome) <= 1:
        return por_nome
    # Empate: quem carrega o cargo é o closer; os outros são leads homônimos.
    com_cargo = [p for p in por_nome if 'estrategista' in sem_acento(p['nome'])]
    return com_cargo or por_nome[:1]


# O Meetrox não publica a call no instante em que ela acaba: sobe o vídeo,
# transcreve e analisa. Enquanto isso a call existe no Meet e não existe em
# /calls — e cobrar gravação aí é alarme falso que se desfaz sozinho.
ESPERA_MEETROX_H = 3


def em_processamento(fim):
    """A call é recente demais pra cobrar gravação?"""
    if not fim:
        return False
    t = _quando(fim)
    if not t:
        return False
    return (datetime.now(timezone.utc) - t) < timedelta(hours=ESPERA_MEETROX_H)


def classificar(gravada, presencas, nome_closer, fim=None):
    """gravada + quem entrou na sala → veredito.

    A distinção que importa: sem gravação, a call aconteceu ou não? Só quem
    esteve na sala responde isso, e é o que separa falha de processo de lead
    que não apareceu.
    """
    if gravada:
        return 'ok', 'gravada'
    if presencas is None:
        return ('sem_gravacao', 'sem gravação · não consegui apurar quem entrou '
                                'na sala, pode ser no-show')

    bot = [p for p in presencas if BOT in sem_acento(p['nome'])]
    humanos = [p for p in presencas if p not in bot]
    closer = identificar_closer(humanos, nome_closer)
    leads = [p for p in humanos if p not in closer]

    if not presencas:
        return 'nao_aconteceu', 'ninguém entrou na sala'
    if closer and leads:
        if bot:
            # O bot entrou: não há o que cobrar do closer. Ou a gravação ainda
            # está subindo, ou o Meetrox perdeu a call — e a diferença entre as
            # duas é só o relógio.
            if em_processamento(fim):
                return ('processando',
                        'call recém-encerrada — a gravação ainda está subindo no Meetrox')
            return 'sem_gravacao', 'closer, lead e o bot entraram — o Meetrox não gerou a gravação'
        return 'sem_gravacao', 'closer e lead na sala, o bot do Meetrox não foi admitido'
    if closer and not leads:
        return 'no_show', 'só o closer entrou na sala'
    if leads and not closer:
        return 'sem_gravacao', 'o lead entrou e o closer não'
    return 'nao_aconteceu', 'ninguém identificável entrou na sala'


# ── execução ─────────────────────────────────────────────────────────────────

# O PostgREST exige que todo objeto do lote tenha exatamente as mesmas chaves
# ("All object keys must match"). As linhas nascidas da agenda carregam mais
# campos que as nascidas do Meetrox, então o lote precisa ser aparado antes.
# Os quatro booleanos são NOT NULL no banco: mandar null quebraria a inserção.
COLUNAS = {
    'chave': None, 'closer': None, 'closer_email': None, 'data': None,
    'inicio': None, 'fim': None, 'titulo': None, 'meet_code': None,
    'evento_id': None, 'convidados_ext': None,
    'evento_cancelado': False, 'closer_recusou': False,
    'meetrox_call_id': None, 'meetrox_url': None,
    'gravada': False, 'duracao_gravacao': None,
    'meet_entrou_closer': None, 'meet_entrou_ext': None, 'meet_dur_ext_seg': None,
    'meet_apurado': False,
    'status': None, 'motivo': None, 'atualizado_em': None,
}


def uniformizar(linha):
    saida = {}
    for coluna, padrao in COLUNAS.items():
        valor = linha.get(coluna)
        saida[coluna] = padrao if valor is None else valor
    return saida


# Palavras que aparecem no título sem identificar ninguém.
_RUIDO = {'call', 'calll', 'analise', 'aplicacao', 'cis', 'estrategista', 'reuniao',
          'com', 'para', 'dra', 'sra'}


def palavras_do_titulo(titulo, nome_closer):
    """Palavras que identificam o cliente, pra reconhecer o mesmo lead.

    Comparar o trecho inteiro não serve: o mesmo cliente aparece como
    'Frankilane melo santos' num evento e 'Frankilane melo santos bomfim' no
    outro, e 'Calll- Clarisse' com três L quebra qualquer regex de prefixo.
    Palavra a palavra sobrevive a sobrenome extra e a erro de digitação.
    """
    t = re.sub(r'^\s*\[[^\]]*\]\s*', ' ', titulo or '')
    palavras = {p for p in re.split(r'[^a-z0-9]+', sem_acento(t)) if len(p) > 3}
    return palavras - _RUIDO - set(re.split(r'\s+', sem_acento(nome_closer)))


def mesmo_cliente(a, b):
    """Dois títulos falam do mesmo lead?

    Duas palavras em comum bastam ('clarisse' + 'coutinho'). Uma só vale
    quando é longa — cliente de nome único, tipo 'Jemima'. O risco de juntar
    errado é baixo: teria que ser outro cliente, com o mesmo nome, do mesmo
    closer, no mesmo horário exato.
    """
    comum = a & b
    return len(comum) >= 2 or any(len(p) >= 6 for p in comum)


# Quando a call acontece, a sala que importa é a que teve gente. A ordem diz
# qual linha vence quando o mesmo encontro aparece em dois eventos.
_PESO = {'ok': 5, 'sem_gravacao': 4, 'processando': 3, 'no_show': 2,
         'nao_aconteceu': 1, 'fora_da_agenda': 0}


def remover_duplicatas(linhas, nomes_closers):
    """Mesmo encontro marcado duas vezes na agenda, cada um com sua sala.

    Acontece quando o closer cria um 'Call — Fulano' e a automação cria um
    '[Análise] Closer & Fulano' no mesmo horário. A call ocorre numa das salas;
    a outra fica vazia e viraria uma linha fantasma em "não aconteceu".

    Só junta quando o CLIENTE é o mesmo. Dois clientes diferentes no mesmo
    horário é agenda dupla de verdade — um aconteceu, o outro não — e as duas
    linhas têm que continuar existindo.
    """
    grupos = {}
    for l in linhas:
        if l.get('inicio') and l.get('evento_id'):
            grupos.setdefault((l['closer'], l['inicio']), []).append(l)

    descartadas = []
    for (closer, _), grupo in grupos.items():
        if len(grupo) < 2:
            continue
        nomes = [palavras_do_titulo(l['titulo'], closer) for l in grupo]
        if not all(mesmo_cliente(nomes[0], n) for n in nomes[1:]):
            continue                       # clientes diferentes: agenda dupla
        grupo.sort(key=lambda l: _PESO.get(l['status'], 0), reverse=True)
        for perdedora in grupo[1:]:
            descartadas.append(perdedora)

    if descartadas:
        chaves = {l['chave'] for l in descartadas}
        print('\nDuplicatas na agenda (mesmo cliente, mesmo horário): %d descartada(s)'
              % len(descartadas))
        for l in descartadas:
            print('  %s  %-16s %s' % (l['data'], l['closer'], (l['titulo'] or '')[:52]))
        linhas = [l for l in linhas if l['chave'] not in chaves]
        # Apaga o que rodadas antigas já gravaram, senão a fantasma fica pra
        # sempre: o upsert atualiza, nunca remove.
        if SERVICE_ROLE:
            for ch in chaves:
                try:
                    supa('cobertura_calls?chave=eq.' + urllib.parse.quote(ch),
                         'DELETE', prefer='return=minimal')
                except Exception:
                    pass
    return linhas



def preservar_apuracao_antiga(linhas):
    """Reprocessar mês antigo não pode apagar o que já se apurou.

    O Meet guarda os registros de conferência por 30 dias. Passado isso, quem
    entrou na sala vira pergunta sem resposta — e regravar a linha com "não
    consegui apurar" jogaria fora um veredito que estava certo. Quando a linha
    já existe no banco com presença apurada e a rodada de agora não conseguiu
    apurar, mantém o que estava lá.
    """
    pendentes = [l for l in linhas if l.get('meet_apurado') is False or l.get('meet_apurado') is None]
    if not pendentes:
        return linhas
    chaves = [l['chave'] for l in pendentes if l.get('chave')]
    antigas = {}
    for i in range(0, len(chaves), 100):
        lote = ','.join('"%s"' % c for c in chaves[i:i + 100])
        try:
            for a in (supa('cobertura_calls?select=chave,status,motivo,meet_entrou_closer,'
                           'meet_entrou_ext,meet_apurado&chave=in.(%s)' % lote) or []):
                antigas[a['chave']] = a
        except Exception as e:
            print('  não consegui reler o histórico (%s) — sigo com a apuração de agora.'
                  % str(e)[:120])
            return linhas
    mantidas = 0
    for l in pendentes:
        a = antigas.get(l.get('chave'))
        if not a or not a.get('meet_apurado') or l.get('gravada'):
            continue
        l['status'], l['motivo'] = a['status'], a['motivo']
        l['meet_entrou_closer'] = a['meet_entrou_closer']
        l['meet_entrou_ext'] = a['meet_entrou_ext']
        l['meet_apurado'] = True
        mantidas += 1
    if mantidas:
        print('\nPresença fora do alcance do Meet em %d linha(s) — mantive o veredito '
              'já apurado.' % mantidas)
    return linhas


def janela(argv):
    # Aspas vazias contam como argumento: na rodada agendada o workflow passa
    # "" "" e a janela tem que voltar a ser a padrão, não estourar.
    args = [a for a in argv[1:] if a.strip()]
    if len(args) >= 2:
        d0 = date.fromisoformat(args[0])
        d1 = date.fromisoformat(args[1])
    else:
        # Termina HOJE, não ontem: a rotina roda de 2 em 2 horas até 21:30, e a
        # graça é a aba Cobertura mostrar a call que acabou de acontecer. Como
        # cada rodada reapura a janela inteira, o dia vai se completando sozinho.
        # E a data é a de São Paulo — o runner do Actions roda em UTC, então
        # date.today() já vira amanhã às 21h daqui e pularia o dia corrente.
        d1 = datetime.now(SP).date()
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



def diagnostico(codigos):
    """Modo de conferência: tudo que as três fontes sabem sobre um código de Meet.

    Serve pra checar caso a caso quando o veredito da aba Cobertura não bate com
    o que o Meetrox mostra na tela. Roda pelo workflow, passando o código no
    campo "Data inicial": diag:mvt-mcyj-pnv

    Imprime cada conferência do Meet com horário e participantes, e as calls do
    Meetrox com o mesmo código — que é onde aparece o link reaproveitado em dias
    diferentes.
    """
    closers = supa('agendas_closers?ativo=eq.true&select=email,nome&order=email')
    for cod in [norm(c) for c in codigos if norm(c)]:
        print('\n=== codigo %s ===' % com_traco(cod))
        for c in closers:
            try:
                tok = token_google(c['email'], ESCOPO_MEET)
            except Exception as e:
                print('  %s: token falhou — %s' % (c['nome'], str(e)[:120]))
                continue
            h = {'Authorization': 'Bearer ' + tok}
            filtro = urllib.parse.quote('space.meeting_code="%s"' % com_traco(cod))
            r = http('%s/conferenceRecords?filter=%s' % (MEET, filtro), h, tolerar=(403, 404))
            regs = (r or {}).get('conferenceRecords') or []
            if not regs:
                continue
            print('  visto por %s — %d conferencia(s)' % (c['nome'], len(regs)))
            for reg in regs:
                print('    %s  inicio=%s  fim=%s' % (reg['name'], reg.get('startTime'), reg.get('endTime')))
                pp = http('%s/%s/participants' % (MEET, reg['name']), h, tolerar=(403, 404))
                for x in ((pp or {}).get('participants') or []):
                    nome = ((x.get('signedinUser') or {}).get('displayName')
                            or (x.get('anonymousUser') or {}).get('displayName')
                            or (x.get('phoneUser') or {}).get('displayName') or '(sem nome)')
                    print('       %-42s entrou=%s saiu=%s' % (nome[:42], x.get('earliestStartTime'), x.get('latestEndTime')))
        if MEETROX_KEY:
            achadas = [c for c in calls_do_meetrox(
                datetime(2026, 7, 1, tzinfo=timezone.utc), datetime.now(timezone.utc))
                if codigo_da_url((c.get('source') or {}).get('meeting_system_url')) == cod]
            print('  Meetrox: %d call(s) com esse codigo' % len(achadas))
            for a in achadas:
                print('    id=%s  %s  dur=%ss  %s' % (a['id'], a.get('timestamp'),
                                                     a.get('duration'), (a.get('title') or '')[:50]))



def diagnostico_api(caminhos):
    """Sonda endpoints do Meetrox: o que existe além de /calls e /users.

    A tela de Gravações do Meetrox mostra reunião NÃO gravada com o motivo
    ("tempo limite da sala de espera excedido"). Se isso existir na API, a
    cobertura para de inferir pelo Meet e passa a ler a resposta do próprio bot.

    Roda pelo workflow: diagapi:/recordings,/meetings
    """
    for c in caminhos:
        c = c.strip()
        if not c:
            continue
        if not c.startswith('/'):
            c = '/' + c
        try:
            r = meetrox(c + ('&' if '?' in c else '?') + 'first=2')
            amostra = json.dumps(r, ensure_ascii=False)[:900]
            print('  OK   %-28s %s' % (c, amostra))
        except Exception as e:
            print('  --   %-28s %s' % (c, str(e)[:160]))



def gravar(linhas):
    for i in range(0, len(linhas), 100):
        supa('cobertura_calls', 'POST', linhas[i:i + 100],
             prefer='resolution=merge-duplicates,return=minimal')


def main():
    arg1 = sys.argv[1] if len(sys.argv) > 1 else ''
    if arg1.startswith('diag:'):
        return diagnostico(arg1[5:].split(','))
    if arg1.startswith('diagapi:'):
        return diagnostico_api(arg1[8:].split(','))
    d0, d1, t0, t1 = janela(sys.argv)
    print('Janela: %s a %s (America/Sao_Paulo)' % (d0, d1))

    # Estado da configuração antes de qualquer coisa: quando isto falha, quem lê
    # o log quer saber primeiro o que chegou e o que não chegou.
    print('Credenciais: ' + ' · '.join(
        '%s=%s' % (nome, 'ok' if val else 'FALTA')
        for nome, val in [('MEETROX_API_KEY', MEETROX_KEY),
                          ('SUPABASE_SERVICE_ROLE', SERVICE_ROLE),
                          ('GOOGLE_SA_JSON', SA_JSON)]))
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
        if NO_ACTIONS:
            # Rodando sozinho, sem credencial, não há auditoria a fazer. Sair
            # verde aqui faria o GitHub pintar de sucesso uma execução vazia —
            # que foi exatamente o que aconteceu na primeira rodada real.
            sys.exit('\nFalta GOOGLE_SA_JSON: sem a agenda não existe cruzamento. '
                     'Confira o nome do secret em Settings > Secrets and variables > Actions.')
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

    # A Meet API responde "a call aconteceu?" para as sem gravação. Escopo de
    # usuário: cada closer só enxerga as salas que ele mesmo criou.
    usa_meet = True
    _avisou_meet = False

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
            ini = (ev.get('start') or {}).get('dateTime')
            fim = (ev.get('end') or {}).get('dateTime')
            # Código do Meet não basta pra casar: o mesmo link volta a ser usado
            # quando a call é remarcada, e a gravação da semana passada dava esta
            # call como gravada. A gravação tem que ter começado dentro da janela
            # do evento.
            j0, j1 = janela_do_evento(ini, fim)
            achadas = []
            for a in (por_codigo_mrx.get(cod) or []):
                q = _quando(a.get('timestamp'))
                if j0 is None or q is None or (j0 <= q <= j1):
                    achadas.append(a)
            mrx = achadas[0] if achadas else None
            # Todas, não só a primeira: quando o bot é readmitido o Meetrox gera
            # dois registros pro mesmo código. Marcar só uma faria a segunda
            # aparecer como "gravada fora da agenda", que é falso.
            for a in achadas:
                casados.add(a['id'])
            # Só as sem gravação vão à Meet API: nas gravadas o Meetrox já
            # respondeu, e consultar seria requisição jogada fora.
            pres = None
            if not mrx and usa_meet:
                try:
                    pres = quem_entrou(email, cod, ini, fim)
                except Exception as e:
                    if not _avisou_meet:
                        print('  Meet API indisponível (%s) — os casos sem gravação '
                              'ficam sem apuração de presença.' % str(e)[:140])
                        _avisou_meet = True
            status, porque = classificar(bool(mrx), pres, nome, fim)
            humanos = [p for p in (pres or []) if BOT not in sem_acento(p['nome'])]
            alvo = sem_acento(nome).split()[0] if nome.strip() else ''
            entrou_closer = [p for p in humanos if alvo and alvo in sem_acento(p['nome'])]
            leads_pres = [p for p in humanos if p not in entrou_closer]

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
                'meet_entrou_closer': bool(entrou_closer) if pres is not None else None,
                'meet_entrou_ext': bool(leads_pres) if pres is not None else None,
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
        cod_c = codigo_da_url((c.get('source') or {}).get('meeting_system_url'))
        # Mesmo link, dia diferente: não é call fora da agenda, é call remarcada
        # que aconteceu no link antigo. Dizer isso poupa a caçada manual.
        outro_dia = sorted({l['data'] for l in linhas
                            if l.get('meet_code') and l['meet_code'] == cod_c
                            and l.get('evento_id')})
        motivo_fora = ('gravada sem evento correspondente na agenda'
                       if not outro_dia else
                       'gravada no link de uma call marcada para %s — remarcação'
                       % ' e '.join('%s/%s' % (d[8:10], d[5:7]) for d in outro_dia[:2]))
        linhas.append({
            'chave': 'mr:%s' % c['id'],
            'closer': agente, 'closer_email': (c.get('agent') or {}).get('email'),
            'data': datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone(SP).date().isoformat(),
            'inicio': ts, 'titulo': c.get('title') or '(sem título)',
            'meet_code': cod_c,
            'meetrox_call_id': c['id'], 'meetrox_url': c.get('url'),
            'gravada': True,
            'duracao_gravacao': int(c['duration']) if c.get('duration') else None,
            'meet_apurado': False,
            'status': 'fora_da_agenda', 'motivo': motivo_fora,
            'atualizado_em': datetime.now(timezone.utc).isoformat(),
        })

    linhas = remover_duplicatas(linhas, {c['nome'] for c in closers})
    linhas = preservar_apuracao_antiga(linhas)

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
        if NO_ACTIONS:
            sys.exit('\nFalta SUPABASE_SERVICE_ROLE: apurei %d linha(s) e não pude '
                     'gravar nenhuma. Confira o nome do secret.' % len(linhas))
        print('\nSem SUPABASE_SERVICE_ROLE: não gravei nada.')
        return
    if not linhas:
        print('\nNada a gravar.')
        return
    try:
        linhas = [uniformizar(l) for l in linhas]
        try:
            gravar(linhas)
        except RuntimeError as e:
            # 23514 = o CHECK de status ainda não conhece 'processando'. Enquanto
            # a migração não roda, a call recém-encerrada volta a ser gravada como
            # sem_gravacao — o motivo já diz que é atraso do Meetrox, e o job não
            # pode parar de gravar por causa disso.
            if '23514' not in str(e):
                raise
            n = 0
            for l in linhas:
                if l.get('status') == 'processando':
                    l['status'] = 'sem_gravacao'
                    n += 1
            print('\nO banco ainda não aceita o status "processando" (%d linha[s]). '
                  'Rode sql/012_status_processando.sql; até lá elas entram como '
                  'sem gravação.' % n)
            gravar(linhas)
    except Exception as e:
        # 401 aqui quer dizer que a chave é a anon, não a service_role: a anon
        # tem policy só de select e de update em status_manual.
        sys.exit('\nNão consegui gravar: %s\nSe for 401, confira se o secret '
                 'SUPABASE_SERVICE_ROLE tem a chave service_role mesmo.' % str(e)[:300])
    print('\nGravado no Supabase: %d linha(s).' % len(linhas))


if __name__ == '__main__':
    main()
