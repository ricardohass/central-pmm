# -*- coding: utf-8 -*-
"""No-show de verdade x call remarcada — desempate pelo e-mail do lead.

O `status` da cobertura separa "closer entrou e ficou sozinho" (`no_show`) de
"ninguém entrou na sala" (`nao_aconteceu`). O segundo virou a maioria do no-show
em agosto/2026 (103 de 132) e não é lead furando: é evento que ficou de pé na
agenda depois de o lead avisar que não dava e remarcar. Contar isso como no-show
inflava o número de todo mundo e escondia o problema real.

    python3 reagendamentos.py 2026-08-01 2026-08-24

REGRA (acordada com o Ricardo em 24/08/2026):
  · no-show   = SÓ `no_show` — o closer entrou na sala e ninguém apareceu.
  · remarcada = `nao_aconteceu` com OUTRA call do mesmo lead começando depois.
                Lead avisou antes e a call foi remarcada: não é falta.
  · vazia     = `nao_aconteceu` sem nenhuma call posterior do mesmo lead. Some
                da conta de no-show do mesmo jeito, mas fica visível — é aqui
                que mora a agenda suja (evento cancelado que ninguém apagou).

O lead é identificado pelo E-MAIL do convidado externo. O título entra só como
segunda chave, e apertado: primeiro nome igual MAIS outro token igual. Sozinho o
título erra feio ("Priscila Barreto dos Santos" casaria com "Cristiano dos
Santos"), mas sem ele escapam as remarcações em que o lead volta com outro
e-mail ou o convite não tem convidado externo — foram 3 em agosto/2026.

Procura a remarcação até 30 dias DEPOIS do fim da janela: call de 20/08 remarcada
pro dia 05/09 continua sendo remarcação.

Não escreve nada: imprime o relatório. Precisa das mesmas credenciais do
cobertura_calls.py (GOOGLE_SA_JSON, e o Meetrox só pra saber quem é da casa).
"""
import re
import sys
from collections import defaultdict
from datetime import datetime, time, timedelta

import cobertura_calls as cc

JANELA_REMARCACAO = timedelta(days=30)

AGENDADAS = {'ok', 'sem_gravacao', 'no_show', 'nao_aconteceu'}
REALIZADAS = {'ok', 'sem_gravacao', 'fora_da_agenda'}


def emails_externos(ev):
    """E-mails de fora do domínio no convite — quem é o lead do evento."""
    internos = cc.equipe_interna()
    return sorted({
        a['email'].lower() for a in (ev.get('attendees') or [])
        if a.get('email') and not a['email'].lower().endswith(cc.DOMINIO)
        and a['email'].lower() not in internos and not a.get('resource')})


# Ruído do título: prefixo de origem, nome dos closers e a palavra do cargo.
# Sem tirar isso, "[CIS] Fulano & Lígia" casa com qualquer call da Lígia.
RUIDO = {'call', 'analise', 'aplicacao', 'cis', 'pce', 'ss', 'estrategista',
         'amanda', 'duarte', 'caroline', 'neiva', 'gabriel', 'rocha',
         'janaina', 'xavier', 'ligia', 'oliveira'}
_PREFIXO = re.compile(r'^\s*(call\s*[—-]\s*)?(\[[^\]]+\]\s*)?'
                      r'(call\s*[—-]\s*)?(\[[^\]]+\]\s*)?')


def nome_do_titulo(titulo):
    """Tokens do nome do lead no título. Lista vazia quando não dá pra isolar."""
    t = _PREFIXO.sub('', cc.sem_acento(titulo or ''))
    candidatos = []
    for parte in re.split(r'\s*&\s*', t):
        toks = [w for w in re.findall(r'[a-z]+', parte)
                if len(w) > 2 and w not in RUIDO]
        if toks:
            candidatos.append(toks)
    return max(candidatos, key=len) if candidatos else []


def mesmo_lead_pelo_nome(a, b):
    """Primeiro nome igual E pelo menos mais um token em comum.

    O primeiro nome sozinho junta gente demais; dois tokens quaisquer casam por
    sobrenome comum. Exigir os dois derruba "Priscila Barreto dos Santos" x
    "Cristiano dos Santos" e mantém "Marcela Grape" x "Marcela Grape e Sarah".
    """
    if len(a) < 2 or len(b) < 2 or a[0] != b[0]:
        return False
    return len(set(a) & set(b)) >= 2


def inicio_do_evento(ev):
    s = (ev.get('start') or {}).get('dateTime') or (ev.get('start') or {}).get('date')
    if not s:
        return None
    if len(s) == 10:                       # evento de dia inteiro
        return datetime.combine(datetime.fromisoformat(s).date(), time(0), cc.SP)
    return datetime.fromisoformat(s).astimezone(cc.SP)


def remarcacao(call, ini, lead_do_evento, calls_do_lead, agenda_toda):
    """A próxima call do mesmo lead depois desta, se existir.

    Devolve (inicio, chave que casou) ou None. E-mail primeiro; o nome do título
    só entra quando o e-mail não resolve.
    """
    eid = call['evento_id']
    leads = lead_do_evento.get(eid) or []
    depois = [i for e in leads for i, outro in calls_do_lead[e]
              if i > ini and outro != eid]
    if depois:
        return min(depois), 'e-mail'
    nome = nome_do_titulo(call.get('titulo'))
    if not nome:
        return None
    porNome = [i for i, outro, toks in agenda_toda
               if i > ini and outro != eid and mesmo_lead_pelo_nome(nome, toks)]
    return (min(porNome), 'nome') if porNome else None


def main():
    if len(sys.argv) < 3:
        sys.exit('uso: reagendamentos.py AAAA-MM-DD AAAA-MM-DD')
    d0, d1 = sys.argv[1], sys.argv[2]
    t0 = datetime.combine(datetime.fromisoformat(d0).date(), time(0), cc.SP)
    t1 = datetime.combine(datetime.fromisoformat(d1).date(), time(23, 59, 59), cc.SP)
    print('Janela: %s a %s · remarcação procurada até %s'
          % (d0, d1, (t1 + JANELA_REMARCACAO).date()))

    if not (cc.SA_JSON and cc.TEM_GOOGLE):
        sys.exit('Falta GOOGLE_SA_JSON — sem a agenda não dá pra saber quem é o lead.')

    closers = cc.supa('agendas_closers?ativo=eq.true&select=email,nome&order=email')
    print('Closers: %d' % len(closers))

    # ── agenda: evento_id → e-mails do lead, e a linha do tempo de cada lead ──
    lead_do_evento = {}
    calls_do_lead = defaultdict(list)     # e-mail do lead → [(inicio, evento_id)]
    agenda_toda = []                      # (inicio, evento_id, tokens do nome)
    for cl in closers:
        evs = cc.eventos_da_agenda(cl['email'], t0, t1 + JANELA_REMARCACAO)
        for ev in evs:
            if ev.get('status') == 'cancelled':
                continue
            if not cc.codigo_da_url(ev.get('hangoutLink')):
                continue
            leads = emails_externos(ev)
            ini = inicio_do_evento(ev)
            if not leads or not ini:
                continue
            lead_do_evento[ev['id']] = leads
            for e in leads:
                calls_do_lead[e].append((ini, ev['id']))
            agenda_toda.append((ini, ev['id'], nome_do_titulo(ev.get('summary'))))
        print('  %-18s %4d eventos lidos' % (cl['nome'], len(evs)))
    print('Eventos com lead identificado: %d · leads distintos: %d'
          % (len(lead_do_evento), len(calls_do_lead)))

    # ── cobertura apurada, a base que vai ser reclassificada ─────────────────
    cob = cc.supa('cobertura_calls?data=gte.%s&data=lte.%s'
                  '&select=evento_id,closer,data,inicio,titulo,status,status_manual'
                  '&order=inicio' % (d0, d1))
    print('Calls na cobertura: %d\n' % len(cob))

    por_closer = defaultdict(lambda: defaultdict(int))
    sem_lead = []
    remarcadas_det = []
    for c in cob:
        st = c.get('status_manual') or c.get('status')
        closer = c['closer']
        por_closer[closer][st] += 1
        if st != 'nao_aconteceu':
            # Curiosidade útil: no-show real que ainda assim foi remarcado depois.
            if st == 'no_show':
                ini = datetime.fromisoformat(c['inicio']).astimezone(cc.SP)
                if remarcacao(c, ini, lead_do_evento, calls_do_lead, agenda_toda):
                    por_closer[closer]['_noshow_remarcado'] += 1
            continue

        ini = datetime.fromisoformat(c['inicio']).astimezone(cc.SP)
        achado = remarcacao(c, ini, lead_do_evento, calls_do_lead, agenda_toda)
        if achado:
            prox, via = achado
            por_closer[closer]['_remarcada'] += 1
            leads = lead_do_evento.get(c['evento_id']) or ['—']
            remarcadas_det.append((closer, c['data'], prox.date().isoformat(),
                                   c['titulo'], leads[0], via))
        else:
            por_closer[closer]['_vazia'] += 1
            if not lead_do_evento.get(c['evento_id']):
                sem_lead.append(c)

    # ── relatório ────────────────────────────────────────────────────────────
    cab = ('%-18s %6s %6s %8s %7s %9s %8s' %
           ('closer', 'agend', 'reun', 'no-show', '%ns', 'remarcad', 'vazia'))
    print(cab)
    print('-' * len(cab))
    tot = defaultdict(int)
    for closer in sorted(por_closer):
        s = por_closer[closer]
        ag = sum(s[k] for k in AGENDADAS)
        re_ = sum(s[k] for k in REALIZADAS)
        ns = s['no_show']
        for k, v in [('ag', ag), ('re', re_), ('ns', ns),
                     ('rm', s['_remarcada']), ('vz', s['_vazia']),
                     ('nsr', s['_noshow_remarcado'])]:
            tot[k] += v
        print('%-18s %6d %6d %8d %6s%% %9d %8d'
              % (closer, ag, re_, ns, round(ns / ag * 100) if ag else 0,
                 s['_remarcada'], s['_vazia']))
    print('-' * len(cab))
    print('%-18s %6d %6d %8d %6s%% %9d %8d'
          % ('TOTAL', tot['ag'], tot['re'], tot['ns'],
             round(tot['ns'] / tot['ag'] * 100) if tot['ag'] else 0,
             tot['rm'], tot['vz']))
    print('\nno-show real que mesmo assim foi remarcado depois: %d '
          '(segue contando como no-show: o closer esperou)' % tot['nsr'])
    if sem_lead:
        print('\n%d calls "ninguém entrou" sem e-mail de lead na agenda e sem '
              'remarcação pelo nome (contadas como vazia):' % len(sem_lead))
        for c in sem_lead[:25]:
            print('   %s  %-18s %s' % (c['data'], c['closer'], (c['titulo'] or '')[:52]))
        if len(sem_lead) > 25:
            print('   ... e mais %d' % (len(sem_lead) - 25))

    print('\nRemarcações encontradas (%d) — call que não aconteceu → próxima do '
          'mesmo lead:' % len(remarcadas_det))
    for closer, de, para, titulo, email, via in sorted(remarcadas_det):
        print('   %-18s %s → %s  %-42s %-34s (%s)'
              % (closer, de, para, (titulo or '')[:42], email[:34], via))


if __name__ == '__main__':
    main()
