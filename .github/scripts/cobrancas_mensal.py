# -*- coding: utf-8 -*-
"""Fechamento mensal de cobranças — uma tarefa do Asana com todos os atrasados.

Roda todo dia 1º pelo GitHub Actions: puxa o Supabase da Central, corta no
primeiro dia do mês corrente e abre UMA tarefa com o resumo por closer.
Irmão da rotina diária (cobrancas_25dias.py), que abre uma tarefa por caso.

    python3 cobrancas_mensal.py            # mês corrente
    python3 cobrancas_mensal.py 2026-08    # corte manual (atrasos antes de 01/08)

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

APELIDO = {'Caroline Neiva': 'Carol', 'Gabriel Mor': 'Mor', 'Gabriel Rocha': 'Rocha',
           'Janaina Xavier': 'Jana', 'Lígia Oliveira': 'Lígia', 'Amanda Duarte': 'Amanda',
           'Bruno Martins': 'Bruno'}
MES_EXT = {1: 'JANEIRO', 2: 'FEVEREIRO', 3: 'MARÇO', 4: 'ABRIL', 5: 'MAIO', 6: 'JUNHO',
           7: 'JULHO', 8: 'AGOSTO', 9: 'SETEMBRO', 10: 'OUTUBRO', 11: 'NOVEMBRO', 12: 'DEZEMBRO'}

rs = lambda x: 'R$ ' + f'{x:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
esc = lambda s: (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def hoje_sp():
    """Data em São Paulo (UTC-3) — o runner do GitHub roda em UTC."""
    return (datetime.now(timezone.utc) - timedelta(hours=3)).date()


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


def levantar(corte, hoje):
    """corte = 'YYYY-MM-01'. Devolve as parcelas vencidas antes do corte."""
    vendas = {v['id']: v for v in q('vendas?select=*&order=data_venda')}
    pags = q('pagamentos_venda?select=*&order=data_prevista')
    reemb = q('reembolsos?select=*&order=created_at')
    abertos = {r['venda_id'] for r in reemb if (r.get('status') or '') != 'resolvido'}

    itens = []
    for p in pags:
        if p['status'] != 'pendente' or not (p.get('data_prevista') or '') < corte:
            continue
        v = vendas.get(p['venda_id']) or {}
        d = p['data_prevista']
        itens.append({
            'cliente': v.get('nome_cliente', '?'),
            'valor': p['valor_bruto'] or 0,
            'venc': d,
            'atraso': (hoje - date(int(d[:4]), int(d[5:7]), int(d[8:10]))).days,
            'gateway': p.get('gateway') or v.get('gateway') or 'não informado',
            'metodo': p.get('metodo') or '',
            'closer': APELIDO.get(v.get('closer'), (v.get('closer') or '—').split(' ')[0]),
            'venda': v.get('data_venda') or '',
            'reembolso': v.get('status') == 'reembolso' or p['venda_id'] in abertos,
        })
    itens.sort(key=lambda x: x['venc'])
    return itens


def montar(itens, ref):
    total = sum(i['valor'] for i in itens)
    rb = sum(i['valor'] for i in itens if i['reembolso'])
    cobrar = total - rb

    por_closer = defaultdict(list)
    for i in itens:
        por_closer[i['closer']].append(i)
    ordem = sorted(por_closer.items(),
                   key=lambda kv: (any(x['reembolso'] for x in kv[1]),
                                   -sum(x['valor'] for x in kv[1])))

    h = [f"<body><strong>{len(itens)} parcelas venceram até o fim de {MES_EXT[ref.month]}/{ref.year} e não entraram.</strong>",
         "<ul>",
         f"<li>Total em aberto: <strong>{rs(total)}</strong></li>"]
    if rb:
        h.append(f"<li>Em reembolso (não cobrar): {rs(rb)}</li>")
    h.append(f"<li><strong>Cobrança real: {rs(cobrar)}</strong></li></ul>")

    for closer, lista in ordem:
        s = sum(x['valor'] for x in lista)
        so_rb = all(x['reembolso'] for x in lista)
        titulo = f"{closer} — {rs(s)}" + (" (reembolso em aberto, NÃO cobrar)" if so_rb else "")
        h.append(f"<h2>{esc(titulo)}</h2><ul>")
        for x in lista:
            marca = " — caso de reembolso, decidir desfecho" if x['reembolso'] else ""
            atraso = f"<strong>{x['atraso']} dias de atraso</strong>" if x['atraso'] >= 15 else f"{x['atraso']} dias"
            h.append(f"<li><strong>{esc(x['cliente'])}</strong> · {rs(x['valor'])} · venceu "
                     f"{x['venc'][8:10]}/{x['venc'][5:7]} · {atraso} · {esc(x['gateway'])} {esc(x['metodo'])} · "
                     f"venda de {x['venda'][8:10]}/{x['venda'][5:7]}{marca}</li>")
        h.append("</ul>")

    h.append("<hr/><strong>Origem:</strong> base da Central PMM — parcelas com status diferente de "
             f"recebido e vencimento anterior a 01/{ref.month % 12 + 1:02d}. Gerado automaticamente.")
    h.append("</body>")

    nome = f"Cobranças atrasadas — fechamento de {MES_EXT[ref.month]}/{ref.year} ({rs(cobrar)} a cobrar)"
    return nome, "".join(h), {'total': total, 'reembolso': rb, 'cobrar': cobrar, 'parcelas': len(itens)}


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
    nome = f"⚠️ Fechamento mensal de cobranças falhou — {hoje.strftime('%d/%m')}"
    try:
        if nome in nomes_no_projeto():
            return
        criar_tarefa(nome, f'<body><strong>O fechamento mensal não conseguiu rodar.</strong>'
                           f'\n<strong>Erro:</strong><pre>{esc(erro[:1500])}</pre>'
                           f'<hr/>Rodar de novo em GitHub → central-pmm → Actions → '
                           f'"Cobranças mensal" → Run workflow.</body>', hoje.isoformat())
    except Exception:
        pass  # sem Asana não dá pra avisar — o job falha e o GitHub manda e-mail


def main():
    hoje = hoje_sp()
    if len(sys.argv) > 1:                       # corte manual YYYY-MM
        ano, mes = (int(x) for x in sys.argv[1].split('-'))
    else:
        ano, mes = hoje.year, hoje.month
    corte = f'{ano:04d}-{mes:02d}-01'
    # o mês de referência do fechamento é o anterior ao corte
    ref = date(ano if mes > 1 else ano - 1, mes - 1 if mes > 1 else 12, 1)
    # o Ricardo quer ver os atrasados na virada do mês: prazo é sempre dia 2
    due = f'{ano:04d}-{mes:02d}-02'

    itens = levantar(corte, hoje)
    if not itens:
        print(json.dumps({'vazio': True, 'corte': corte}, ensure_ascii=False))
        return
    nome, notes, tot = montar(itens, ref)

    if not TOKEN:
        print(json.dumps({'nome': nome, 'html_notes': notes, 'resumo': tot,
                          'corte': corte, 'due_on': due, 'itens': itens},
                         ensure_ascii=False, indent=1))
        return

    print(f"{corte} · {tot['parcelas']} parcelas · {rs(tot['cobrar'])} a cobrar")
    if nome in nomes_no_projeto():
        print(f'· já aberta: {nome}')
    else:
        print(f'+ criada: {nome} → {criar_tarefa(nome, notes, due)}')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        erro = traceback.format_exc()
        print(erro, file=sys.stderr)
        avisar_falha(erro)
        sys.exit(1)
