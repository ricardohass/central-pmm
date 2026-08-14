# -*- coding: utf-8 -*-
"""Parcelas com mais de 25 dias de atraso — uma tarefa do Asana por caso.

Roda todo dia pelo GitHub Actions: puxa o Supabase da Central, pega as parcelas
pendentes cujo vencimento já passou de 25 dias e abre no Asana uma tarefa por
caso ainda não aberto.

    python3 cobrancas_25dias.py        # corte padrão: 25 dias
    python3 cobrancas_25dias.py 40     # corte manual em dias

Sem a variável ASANA_TOKEN no ambiente o script não toca no Asana: só imprime o
JSON do levantamento. É assim que ele roda na mão, pra conferir.

O nome da tarefa é determinístico (cliente + vencimento + closer + valor), então
serve de chave de deduplicação: se já existe tarefa com aquele nome no projeto
(concluída ou não), o caso já foi aberto e não se abre de novo.
"""
import base64
import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

SUPA = 'https://ebcydqqhvdapruhnwbce.supabase.co/rest/v1/'
KEY = base64.b64decode(
    'ZXlKaGJHY2lPaUpJVXpJMU5pSXNJblI1Y0NJNklrcFhWQ0o5LmV5SnBjM01pT2lKemRYQmhZbUZ6WlNJc0lu'
    'SmxaaUk2SW1WaVkzbGtjWEZvZG1SaGNISjFhRzUzWW1ObElpd2ljbTlzWlNJNkltRnViMjRpTENKcFlYUWlP'
    'akUzTnprNU9ETXlOaklzSW1WNGNDSTZNakE1TlRVMU9USTJNbjAuME9SaTlGUlpWU3Q2V09iM1EzVnhWWXoy'
    'VWtYR1JDYlptcTJCVTUwWEpGMA==').decode()

ASANA = 'https://app.asana.com/api/1.0/'
PROJETO = '1215606464571136'  # projeto PMM
# .strip() porque token colado no secret costuma vir com quebra de linha junto,
# e aí o header sai malformado e o Asana devolve 401
TOKEN = os.environ.get('ASANA_TOKEN', '').strip()

APELIDO = {'Caroline Neiva': 'Carol', 'Gabriel Mor': 'Mor', 'Gabriel Rocha': 'Rocha',
           'Janaina Xavier': 'Jana', 'Lígia Oliveira': 'Lígia', 'Amanda Duarte': 'Amanda',
           'Bruno Martins': 'Bruno'}

rs = lambda x: 'R$ ' + f'{x:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
esc = lambda s: (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
br = lambda d: f'{d[8:10]}/{d[5:7]}/{d[:4]}' if d else '—'


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


def levantar(limite, hoje):
    """Parcelas pendentes com atraso > limite dias."""
    vendas = {v['id']: v for v in q('vendas?select=*&order=data_venda')}
    pags = q('pagamentos_venda?select=*&order=data_prevista')
    reemb = q('reembolsos?select=*&order=created_at')
    abertos = {r['venda_id'] for r in reemb if (r.get('status') or '') != 'resolvido'}

    # índice da parcela dentro da venda (a base não guarda numeração)
    ordem = {}
    for p in sorted(pags, key=lambda x: (x['venda_id'], x.get('data_prevista') or '')):
        ordem.setdefault(p['venda_id'], []).append(p['id'])

    itens = []
    for p in pags:
        # só 'pendente' é cobrança viva: 'cancelada' vem de reembolso resolvido
        d = p.get('data_prevista') or ''
        if p['status'] != 'pendente' or not d:
            continue
        atraso = (hoje - date(int(d[:4]), int(d[5:7]), int(d[8:10]))).days
        if atraso <= limite:
            continue
        v = vendas.get(p['venda_id']) or {}
        irmas = ordem.get(p['venda_id'], [])
        itens.append({
            'cliente': v.get('nome_cliente', '?'),
            'valor': p['valor_bruto'] or 0,
            'venc': d,
            'atraso': atraso,
            'parcela': f"{irmas.index(p['id']) + 1}/{len(irmas)}" if p['id'] in irmas else '?',
            'cobrancas': sum(1 for k in ('cobranca_1', 'cobranca_2', 'cobranca_3') if p.get(k)),
            'gateway': p.get('gateway') or v.get('gateway') or 'não informado',
            'metodo': p.get('metodo') or '',
            'closer': APELIDO.get(v.get('closer'), (v.get('closer') or '—').split(' ')[0]),
            'venda': v.get('data_venda') or '',
            'reembolso': v.get('status') == 'reembolso' or p['venda_id'] in abertos,
        })
    itens.sort(key=lambda x: x['venc'])
    return itens


def montar(i):
    """Nome (determinístico, serve de chave de dedup) + html_notes da tarefa."""
    nome = (f"⚠️ Cobrança atrasada — {i['cliente']} · venceu {i['venc'][8:10]}/{i['venc'][5:7]} · "
            f"{i['closer']} · {rs(i['valor'])}")
    notes = (
        f"<body><strong>⚠️ Parcela com {i['atraso']} dias de atraso.</strong>"
        "<ul>"
        f"<li>Cliente: <strong>{esc(i['cliente'])}</strong></li>"
        f"<li>Data do vencimento: <strong>{br(i['venc'])}</strong></li>"
        f"<li>Closer: <strong>{esc(i['closer'])}</strong></li>"
        f"<li>Valor da parcela: <strong>{rs(i['valor'])}</strong></li>"
        f"<li>Atraso: <strong>{i['atraso']} dias</strong></li>"
        f"<li>Parcela {esc(i['parcela'])} · {esc(i['gateway'])} {esc(i['metodo'])} · "
        f"{i['cobrancas']} de 3 cobranças já marcadas na aba</li>"
        f"<li>Venda de {br(i['venda'])}</li>"
        "</ul>"
        "<hr/><strong>Origem:</strong> base da Central PMM — parcela pendente há mais de 25 dias "
        "do vencimento. Gerado automaticamente.</body>")
    return nome, notes


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
    nome = f"⚠️ Rotina de cobranças 25d+ falhou — {hoje.strftime('%d/%m')}"
    try:
        if nome in nomes_no_projeto():
            return
        criar_tarefa(nome, f'<body><strong>A rotina diária não conseguiu rodar.</strong>'
                           f'\n<strong>Erro:</strong><pre>{esc(erro[:1500])}</pre>'
                           f'<hr/>Rodar de novo em GitHub → central-pmm → Actions → '
                           f'"Cobranças 25d+" → Run workflow.</body>', hoje.isoformat())
    except Exception:
        # Sem Asana não dá pra avisar por tarefa, mas engolir calado esconde a
        # causa: foi assim que o 400 do <p> passou horas sem ninguém ver por quê.
        print('avisar_falha não conseguiu abrir a tarefa:\n' + traceback.format_exc(),
              file=sys.stderr)


def main():
    limite = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    hoje = hoje_sp()
    itens = levantar(limite, hoje)
    cobrar = [i for i in itens if not i['reembolso']]
    for i in cobrar:
        i['nome'], i['html_notes'] = montar(i)
    reembolso = [i for i in itens if i['reembolso']]

    if not TOKEN:
        print(json.dumps({'hoje': hoje.isoformat(), 'due_on': hoje.isoformat(),
                          'limite_dias': limite, 'vazio': not cobrar, 'tarefas': cobrar,
                          'reembolso_nao_cobrar': reembolso}, ensure_ascii=False, indent=1))
        return

    print(f'{hoje.isoformat()} · corte de {limite} dias · {len(cobrar)} caso(s) em atraso')
    if not cobrar:
        print('Nenhuma parcela passando do corte hoje. Nada a abrir.')
    else:
        existentes = nomes_no_projeto()
        for i in cobrar:
            if i['nome'] in existentes:
                print(f"· já aberta: {i['cliente']} ({i['atraso']}d)")
                continue
            link = criar_tarefa(i['nome'], i['html_notes'], hoje.isoformat())
            print(f"+ criada: {i['cliente']} · {i['closer']} · venceu {br(i['venc'])} · "
                  f"{rs(i['valor'])} · {i['atraso']}d → {link}")
    for i in reembolso:
        print(f"! reembolso em aberto, não vira cobrança: {i['cliente']} ({i['atraso']}d)")


# A rotina de cancelamento nos gateways já rodou de carona aqui, na época em que
# o .yml dela não subia (o token do Mac não tinha escopo `workflow`). O workflow
# próprio está no ar desde 13/08 e roda de 2 em 2 horas — a carona virou execução
# duplicada, e pior: engolia o returncode, então uma falha lá deixava este job
# verde. Removida. Se precisar rodar na mão:
#     python3 .github/scripts/cancelamentos_gateway.py


if __name__ == '__main__':
    try:
        main()
    except Exception:
        erro = traceback.format_exc()
        print(erro, file=sys.stderr)
        avisar_falha(erro)
        sys.exit(1)
