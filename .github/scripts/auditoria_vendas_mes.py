# -*- coding: utf-8 -*-
"""Conferência de cadastro das vendas do mês — abre no Asana o que falta preencher.

Roda no último dia do mês, às 20h de São Paulo, pelo GitHub Actions: varre as
vendas com data_venda dentro do mês e lista, venda a venda, o que ficou em
branco. Uma tarefa só, com prazo pro dia seguinte.

    python3 auditoria_vendas_mes.py            # mês corrente
    python3 auditoria_vendas_mes.py 2026-08    # mês específico

O que confere, na ordem em que aparece na tarefa:

  · gateway        — parcela de pagamentos_venda sem gateway, ou a venda sem
                     gateway nenhum. Sem isso o estudo financeiro não sabe de
                     qual plataforma cobrar taxa, nem a conciliação do Asaas
                     acha a cobrança.
  · quem marcou    — venda sem SDR e sem social seller. Uma das duas costuma ter
                     originado a call; as duas em branco quase sempre é cadastro
                     incompleto, não venda inbound.
  · comprovante    — vendas.links_pagamento sem nenhum link (o campo é
                     obrigatório no formulário, mas o save não trava).
  · jurídico       — contrato_enviado = false ("Infos enviadas ao jurídico").
  · Central        — subido_central = false ("Subido na Central").

E mais três que impedem a venda de fechar conta, agrupados à parte:
cronograma de parcelas vazio, valor da negociação zerado e parcela marcada como
recebida sem data de recebimento.

NÃO confere contrato_assinado nem cpf_cnpj: as duas colunas existem no banco mas
não têm campo na tela, então vêm vazias em toda venda e só produziriam ruído.

Sem a variável ASANA_TOKEN no ambiente o script não toca no Asana: só imprime o
JSON do levantamento. É assim que ele roda na mão, pra conferir.
"""
import base64
import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

SUPA = 'https://ebcydqqhvdapruhnwbce.supabase.co/rest/v1/'
KEY = base64.b64decode(
    'ZXlKaGJHY2lPaUpJVXpJMU5pSXNJblI1Y0NJNklrcFhWQ0o5LmV5SnBjM01pT2lKemRYQmhZbUZ6WlNJc0lu'
    'SmxaaUk2SW1WaVkzbGtjWEZvZG1SaGNISjFhRzUzWW1ObElpd2ljbTlzWlNJNkltRnViMjRpTENKcFlYUWlP'
    'akUzTnprNU9ETXlOaklzSW1WNGNDSTZNakE1TlRVMU9USTJNbjAuME9SaTlGUlpWU3Q2V09iM1EzVnhWWXoy'
    'VWtYR1JDYlptcTJCVTUwWEpGMA==').decode()

ASANA = 'https://app.asana.com/api/1.0/'
PROJETO = '1215606464571136'  # projeto PMM
# .strip() porque token colado no secret costuma vir com quebra de linha junto
TOKEN = os.environ.get('ASANA_TOKEN', '').strip()

MES_EXT = {1: 'JANEIRO', 2: 'FEVEREIRO', 3: 'MARÇO', 4: 'ABRIL', 5: 'MAIO', 6: 'JUNHO',
           7: 'JULHO', 8: 'AGOSTO', 9: 'SETEMBRO', 10: 'OUTUBRO', 11: 'NOVEMBRO', 12: 'DEZEMBRO'}

# Venda morta não tem cadastro a cobrar de ninguém.
IGNORAR_STATUS = {'cancelada', 'arrependimento'}

rs = lambda x: 'R$ ' + f'{x:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
esc = lambda s: (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
br = lambda d: f'{d[8:10]}/{d[5:7]}' if d else '—'


def hoje_sp():
    """Data em São Paulo (UTC-3) — o runner do GitHub roda em UTC."""
    return (datetime.now(timezone.utc) - timedelta(hours=3)).date()


def fim_do_mes(d):
    """Último dia do mês de d."""
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)


def q(path):
    out, off = [], 0
    while True:
        req = urllib.request.Request(f'{SUPA}{path}&limit=1000&offset={off}',
                                     headers={'apikey': KEY, 'Authorization': 'Bearer ' + KEY})
        d = json.load(urllib.request.urlopen(req))
        out += d
        if len(d) < 1000:
            return out
        off += 1000


def comprovantes(v):
    """links_pagamento vem como texto JSON ou como array, e às vezes com item vazio."""
    lp = v.get('links_pagamento') or []
    if isinstance(lp, str):
        try:
            lp = json.loads(lp or '[]')
        except ValueError:
            lp = [lp]
    if not isinstance(lp, list):
        lp = [lp]
    return [str(x).strip() for x in lp if str(x or '').strip()]


# ---------------------------------------------------------------- levantamento

def levantar(ini, fim):
    vendas = q(f'vendas?select=*&data_venda=gte.{ini}&data_venda=lte.{fim}&order=data_venda')
    vendas = [v for v in vendas if (v.get('status') or '') not in IGNORAR_STATUS]
    ids = [v['id'] for v in vendas]

    pags = []
    for i in range(0, len(ids), 40):
        lote = ','.join(str(x) for x in ids[i:i + 40])
        pags += q(f'pagamentos_venda?select=*&venda_id=in.({lote})&order=numero_parcela')
    por_venda = defaultdict(list)
    for p in pags:
        por_venda[p['venda_id']].append(p)

    itens = []
    for v in vendas:
        parcelas = por_venda[v['id']]
        faltas, extras = [], []

        # --- gateway ---
        sem_gw = [p for p in parcelas if not (p.get('gateway') or '').strip()]
        if sem_gw:
            quais = ', '.join(f"{p.get('numero_parcela') or '?'}ª ({rs(p.get('valor_bruto') or 0)}"
                              f", vence {br(p.get('data_prevista'))})" for p in sem_gw[:6])
            resto = f' e mais {len(sem_gw) - 6}' if len(sem_gw) > 6 else ''
            faltas.append(('gateway', f'{len(sem_gw)} de {len(parcelas)} parcelas sem gateway: {quais}{resto}'))
        elif not (v.get('gateway') or '').strip() and not parcelas:
            faltas.append(('gateway', 'venda sem gateway e sem parcelas cadastradas'))

        # --- quem marcou a call ---
        if not (v.get('sdr') or '').strip() and not (v.get('social_seller') or '').strip():
            faltas.append(('quem marcou', f"sem SDR e sem social seller (origem: {v.get('origem') or 'em branco'})"))

        # --- comprovante ---
        if not comprovantes(v):
            faltas.append(('comprovante', 'nenhum link de comprovante de pagamento'))

        # --- jurídico e Central ---
        if not v.get('contrato_enviado'):
            faltas.append(('jurídico', 'infos do cliente ainda não enviadas ao jurídico'))
        if not v.get('subido_central'):
            faltas.append(('Central', 'venda ainda não subida na Central'))

        # --- extras: o que impede a venda de fechar conta ---
        if not parcelas:
            extras.append('cronograma de parcelas vazio — a venda não aparece em Cobranças')
        if not (v.get('base_comissao') or 0):
            extras.append('valor da negociação zerado — a venda não entra na comissão')
        sem_data = [p for p in parcelas if (p.get('status') or '') == 'recebido'
                    and not (p.get('data_recebimento') or '').strip()]
        if sem_data:
            extras.append(f'{len(sem_data)} parcela(s) marcada(s) como recebida(s) sem data de recebimento')

        if faltas or extras:
            itens.append({
                'id': v['id'],
                'cliente': v.get('nome_cliente') or '(sem nome)',
                'closer': v.get('closer') or '(sem closer)',
                'data': v.get('data_venda') or '',
                'produto': (v.get('produto') or '').split(']')[0].lstrip('[') or '—',
                'valor': v.get('base_comissao') or 0,
                'reembolso': (v.get('status') or '') == 'reembolso',
                'faltas': faltas,
                'extras': extras,
            })
    itens.sort(key=lambda x: (x['closer'], x['data']))
    return itens, len(vendas)


# ---------------------------------------------------------------- tarefa

ROTULOS = {
    'gateway': 'Gateway da parcela',
    'quem marcou': 'SDR / social seller',
    'comprovante': 'Comprovante de pagamento',
    'jurídico': 'Infos pro jurídico',
    'Central': 'Subir na Central',
}


def montar(itens, total_vendas, ref, prazo, parcial):
    por_tipo = defaultdict(list)
    for i in itens:
        for tipo, _ in i['faltas']:
            por_tipo[tipo].append(i)

    h = [f'<body><strong>{len(itens)} das {total_vendas} vendas de {MES_EXT[ref.month]}/{ref.year} '
         f'estão com cadastro incompleto.</strong> Prazo: {prazo.strftime("%d/%m")}.',
         '<ul>']
    for tipo in ROTULOS:
        if por_tipo[tipo]:
            h.append(f'<li>{ROTULOS[tipo]}: <strong>{len(por_tipo[tipo])} venda(s)</strong></li>')
    com_extra = [i for i in itens if i['extras']]
    if com_extra:
        h.append(f'<li>Problemas que travam a conta do mês: <strong>{len(com_extra)} venda(s)</strong></li>')
    h.append('</ul>')

    por_closer = defaultdict(list)
    for i in itens:
        por_closer[i['closer']].append(i)

    for closer, lista in sorted(por_closer.items(), key=lambda kv: -len(kv[1])):
        pend = sum(len(x['faltas']) + len(x['extras']) for x in lista)
        h.append(f'<h2>{esc(closer)} — {len(lista)} venda(s), {pend} pendência(s)</h2>')
        for x in lista:
            marca = ' · <strong>em reembolso</strong>' if x['reembolso'] else ''
            h.append(f'<strong>{esc(x["cliente"])}</strong> · {br(x["data"])} · {esc(x["produto"])} · '
                     f'{rs(x["valor"])}{marca}<ul>')
            for tipo, det in x['faltas']:
                h.append(f'<li><strong>{ROTULOS[tipo]}</strong> — {esc(det)}</li>')
            for det in x['extras']:
                h.append(f'<li>⚠️ {esc(det)}</li>')
            h.append('</ul>')

    h.append('<hr/><strong>Como conferir:</strong> Central → aba Vendas → abrir a venda. '
             'Gateway fica no cronograma de pagamentos; SDR e social seller em Atribuições; '
             'comprovante na seção Comprovantes de pagamento; as duas caixas "Infos enviadas ao '
             'jurídico" e "Subido na Central" ficam no card da venda.'
             '<br/><strong>Origem:</strong> base da Central PMM, vendas com data_venda em '
             f'{MES_EXT[ref.month]}/{ref.year} (canceladas e arrependimentos fora). '
             + ('Rodada PARCIAL, com o mês ainda aberto — o fechamento sai no último dia.'
                if parcial else 'Gerado automaticamente no último dia do mês.') + '</body>')

    # A tarefa do fechamento tem nome fixo, pra não duplicar se a rotina rodar
    # duas vezes. A rodada parcial leva a data no nome justamente pelo contrário:
    # um teste no meio do mês não pode ocupar o nome e calar o fechamento.
    nome = f'Conferência de cadastro — vendas de {MES_EXT[ref.month]}/{ref.year}'
    if parcial:
        nome += f' (parcial, {parcial.strftime("%d/%m")})'
    resumo = {t: len(por_tipo[t]) for t in ROTULOS if por_tipo[t]}
    resumo['vendas com pendência'] = len(itens)
    resumo['vendas no mês'] = total_vendas
    return nome, ''.join(h), resumo


# ---------------------------------------------------------------- Asana

def asana(path, method='GET', payload=None):
    req = urllib.request.Request(
        ASANA + path, method=method,
        data=json.dumps(payload).encode() if payload else None,
        headers={'Authorization': 'Bearer ' + TOKEN, 'Accept': 'application/json',
                 'Content-Type': 'application/json'})
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'Asana {method} {path} → HTTP {e.code}: {e.read().decode()[:400]}')


def nomes_no_projeto():
    """Todos os nomes de tarefa do projeto, inclusive as concluídas."""
    nomes, off = set(), None
    while True:
        p = f'tasks?project={PROJETO}&limit=100&opt_fields=name'
        if off:
            p += '&offset=' + off
        r = asana(p)
        nomes |= {t['name'] for t in r.get('data', [])}
        off = (r.get('next_page') or {}).get('offset')
        if not off:
            return nomes


def criar_tarefa(nome, notes, due):
    r = asana('tasks?opt_fields=permalink_url', 'POST', {'data': {
        'name': nome, 'html_notes': notes, 'projects': [PROJETO],
        'assignee': 'me', 'due_on': due}})
    return r['data']['permalink_url']


def avisar_falha(erro):
    """Falhou? Abre no Asana uma tarefa por dia contando o que quebrou."""
    if not TOKEN:
        return
    hoje = hoje_sp()
    nome = f"⚠️ Conferência de cadastro das vendas falhou — {hoje.strftime('%d/%m')}"
    try:
        if nome in nomes_no_projeto():
            return
        criar_tarefa(nome, '<body><strong>A conferência de cadastro do mês não conseguiu rodar.</strong>'
                           f'\n<strong>Erro:</strong><pre>{esc(erro[:1500])}</pre>'
                           '<hr/>Rodar de novo em GitHub → central-pmm → Actions → '
                           '"Conferência de cadastro das vendas" → Run workflow.</body>',
                     hoje.isoformat())
    except Exception:
        print('avisar_falha não conseguiu abrir a tarefa:\n' + traceback.format_exc(),
              file=sys.stderr)


def main():
    hoje = hoje_sp()
    if len(sys.argv) > 1:                       # mês manual YYYY-MM
        ano, mes = (int(x) for x in sys.argv[1].split('-'))
    else:
        # O cron bate todo dia às 23h UTC; só o último dia do mês interessa.
        # A conta é feita na data de São Paulo, então a rodada aguenta o
        # agendador do Actions atrasar até as 3h da manhã em UTC sem virar o mês.
        if hoje != fim_do_mes(hoje):
            print(f'{hoje} não é o último dia do mês — nada a fazer.')
            return
        ano, mes = hoje.year, hoje.month
    ref = date(ano, mes, 1)
    ini, fim = ref.isoformat(), fim_do_mes(ref).isoformat()
    # o levantamento fecha o mês, então o prazo é o primeiro dia do mês seguinte
    prazo = fim_do_mes(ref) + timedelta(days=1)
    # mês ainda em curso = rodada parcial: prazo vira o dia seguinte ao de hoje
    parcial = hoje if hoje < fim_do_mes(ref) else None
    if parcial:
        prazo = hoje + timedelta(days=1)

    itens, total = levantar(ini, fim)
    if not itens:
        print(json.dumps({'vazio': True, 'mes': f'{ano:04d}-{mes:02d}', 'vendas': total},
                         ensure_ascii=False))
        return
    nome, notes, resumo = montar(itens, total, ref, prazo, parcial)

    if not TOKEN:
        print(json.dumps({'nome': nome, 'html_notes': notes, 'resumo': resumo,
                          'due_on': prazo.isoformat(), 'itens': itens},
                         ensure_ascii=False, indent=1))
        return

    print(f'{ano:04d}-{mes:02d} · {len(itens)} de {total} vendas com pendência'
          + (' · rodada parcial' if parcial else ''))
    if nome in nomes_no_projeto():
        print(f'· já aberta: {nome}')
    else:
        print(f'+ criada: {nome} → {criar_tarefa(nome, notes, prazo.isoformat())}')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        erro = traceback.format_exc()
        print(erro, file=sys.stderr)
        avisar_falha(erro)
        sys.exit(1)
