# -*- coding: utf-8 -*-
"""Novo cancelamento/reembolso — uma tarefa do Asana pra cancelar a cobrança nos gateways.

Congelar ou cancelar parcela na Central é só no app: a recorrência continua
programada no Asaas / Hotmart / Cispay e o cliente segue sendo debitado. Esta
rotina fecha esse buraco — a cada caso novo na aba Reembolsos, abre uma tarefa
lembrando de matar a cobrança na plataforma externa.

    python3 cancelamentos_gateway.py           # só casos abertos a partir de DESDE
    python3 cancelamentos_gateway.py --todos   # inclui o histórico (backfill)

Sem a variável ASANA_TOKEN no ambiente o script não toca no Asana: só imprime o
JSON do levantamento. É assim que ele roda na mão, pra conferir.

O nome da tarefa é determinístico (cliente + data de abertura do caso) e serve de
chave de deduplicação: se já existe tarefa com aquele nome no projeto (concluída
ou não), o caso já foi avisado e não se abre de novo. Por isso pode rodar de
hora em hora sem duplicar nada.
"""
import base64
import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

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

# Data em que a rotina entrou no ar. Caso mais antigo que isso não vira tarefa,
# senão a primeira execução abriria uma pilha de casos velhos de uma vez.
# Pra puxar o histórico de propósito: --todos
DESDE = '2026-08-10'

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


def levantar(todos):
    """Casos de cancelamento/reembolso que ainda precisam de baixa no gateway."""
    casos = q('reembolsos?select=*&order=created_at')
    vendas = {v['id']: v for v in q('vendas?select=*')}
    pags = {}
    for p in q('pagamentos_venda?select=*&order=data_prevista'):
        pags.setdefault(p['venda_id'], []).append(p)

    itens = []
    for c in casos:
        aberto = (c.get('created_at') or '')[:10]
        if not todos and aberto < DESDE:
            continue
        # caso revertido é como se nunca tivesse existido: a venda voltou a valer
        if (c.get('status_reembolso') or '') == 'revertido':
            continue

        v = vendas.get(c.get('venda_id')) or {}
        ps = pags.get(c.get('venda_id')) or []
        # o que não foi recebido é o que pode seguir programado lá fora —
        # tanto 'pendente' (caso em aberto, parcela congelada) quanto
        # 'cancelada' (caso resolvido), porque cancelar aqui não cancela lá
        viva = [p for p in ps if p['status'] != 'recebido']

        # nenhum campo de plataforma é confiável sozinho: o do caso, o da venda e
        # o das parcelas divergem na base. Junta todos e confere um por um.
        plats = []
        for nome in ([c.get('plataforma_pagamento'), v.get('gateway')]
                     + [p.get('gateway') for p in ps]):
            if nome and nome not in plats:
                plats.append(nome)

        itens.append({
            'caso_id': c['id'],
            'cliente': c.get('nome_cliente') or v.get('nome_cliente') or '?',
            'tipo': c.get('tipo') or 'cancelamento',
            'status': c.get('status') or '—',
            'status_reembolso': c.get('status_reembolso') or '—',
            'aberto_em': aberto,
            'aberto_por': c.get('aberto_por') or '—',
            'solicitado_em': c.get('data_solicitacao') or '',
            'motivo': c.get('motivo') or '—',
            'valor_caso': c.get('valor_total') or 0,
            'closer': APELIDO.get(c.get('closer') or v.get('closer'),
                                  ((c.get('closer') or v.get('closer')) or '—').split(' ')[0]),
            'whatsapp': c.get('whatsapp_cliente') or '—',
            'plataformas': plats or ['não informada'],
            'plataforma_caso': c.get('plataforma_pagamento') or '—',
            'formato': c.get('formato_pagamento') or v.get('forma_pagamento') or '—',
            'tem_venda': bool(v),
            'data_venda': v.get('data_venda') or c.get('data_compra') or '',
            'valor_venda': v.get('valor_bruto') or 0,
            'abertas_n': len(viva),
            'abertas_valor': sum(p['valor_bruto'] or 0 for p in viva),
            'abertas': [{'venc': p.get('data_prevista') or '', 'valor': p['valor_bruto'] or 0,
                         'metodo': p.get('metodo') or '', 'status': p['status'],
                         'gateway': p.get('gateway') or v.get('gateway') or 'não informado'}
                        for p in viva],
        })
    itens.sort(key=lambda x: x['aberto_em'])
    return itens


def montar(i):
    """Nome (determinístico, serve de chave de dedup) + html_notes da tarefa.

    O Asana só aceita um punhado de tags no html_notes: body, strong, em, u, s,
    code, ol, ul, li, a, blockquote, pre e (só em tarefa) h1, h2, hr. Qualquer
    outra — <p> e <br> inclusive — devolve 400 xml_parsing_error, mesmo o XML
    sendo bem formado. Pra separar bloco, use \\n; não reintroduza <p>.
    """
    nome = (f"🚫 Cancelar cobrança nas plataformas — {i['cliente']} · "
            f"{' + '.join(i['plataformas'])} · aberto {br(i['aberto_em'])[:5]}")

    if i['abertas']:
        linhas = ''.join(
            f"<li>{br(p['venc'])} · {rs(p['valor'])} · {esc(p['metodo'])} · "
            f"<strong>{esc(p['gateway'])}</strong>"
            f"{' (já cancelada na Central)' if p['status'] == 'cancelada' else ''}</li>"
            for p in i['abertas'])
        bloco = (f"\n<strong>{i['abertas_n']} cobrança(s) ainda não recebida(s) — "
                 f"{rs(i['abertas_valor'])}:</strong><ul>{linhas}</ul>")
    elif i['tem_venda']:
        bloco = ("\n<strong>Nenhuma parcela em aberto na Central</strong> — mesmo assim vale "
                 "conferir na plataforma se sobrou recorrência ativa.\n")
    else:
        bloco = ("\n<strong>Caso sem venda vinculada na Central</strong> — não dá pra listar as "
                 "parcelas. Conferir direto na plataforma.\n")

    plats = ', '.join(i['plataformas'])
    notes = (
        f"<body><strong>Novo {esc(i['tipo'])} registrado. Cancelar a cobrança nas plataformas "
        f"externas ({esc(plats)}).</strong>"
        "<ul>"
        f"<li>Cliente: <strong>{esc(i['cliente'])}</strong> · {esc(i['whatsapp'])}</li>"
        f"<li>Valor do caso: <strong>{rs(i['valor_caso'])}</strong></li>"
        f"<li>Venda: {br(i['data_venda'])} · {rs(i['valor_venda'])} · {esc(i['formato'])}</li>"
        f"<li>Plataforma(s): <strong>{esc(plats)}</strong> "
        f"(no caso: {esc(i['plataforma_caso'])})</li>"
        f"<li>Closer: {esc(i['closer'])}</li>"
        f"<li>Motivo: {esc(i['motivo'])}</li>"
        f"<li>Solicitado em {br(i['solicitado_em'])} · caso aberto em {br(i['aberto_em'])} "
        f"por {esc(i['aberto_por'])}</li>"
        f"<li>Status do caso: {esc(i['status'])} · reembolso: {esc(i['status_reembolso'])}</li>"
        "</ul>"
        f"{bloco}"
        "<hr/><strong>Por que esta tarefa existe:</strong> congelar ou cancelar parcela na Central "
        "não cancela a recorrência no Asaas, Hotmart ou Cispay — sem baixa manual na plataforma o "
        "cliente continua sendo debitado. Gerado automaticamente a partir da aba Reembolsos.</body>")
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
    nome = f"⚠️ Rotina de cancelamento nos gateways falhou — {hoje.strftime('%d/%m')}"
    try:
        if nome in nomes_no_projeto():
            return
        criar_tarefa(nome, f'<body><strong>A rotina não conseguiu rodar.</strong>'
                           f'\n<strong>Erro:</strong><pre>{esc(erro[:1500])}</pre>'
                           f'<hr/>Rodar de novo em GitHub → central-pmm → Actions → '
                           f'"Cancelamento nos gateways" → Run workflow.</body>', hoje.isoformat())
    except Exception:
        # Sem Asana não dá pra avisar por tarefa, mas engolir calado esconde a
        # causa: foi assim que o 400 do <p> passou horas sem ninguém ver por quê.
        print('avisar_falha não conseguiu abrir a tarefa:\n' + traceback.format_exc(),
              file=sys.stderr)


def main():
    todos = '--todos' in sys.argv
    hoje = hoje_sp()
    itens = levantar(todos)
    for i in itens:
        i['nome'], i['html_notes'] = montar(i)

    if not TOKEN:
        print(json.dumps({'hoje': hoje.isoformat(), 'due_on': hoje.isoformat(),
                          'desde': 'histórico completo' if todos else DESDE,
                          'vazio': not itens, 'tarefas': itens}, ensure_ascii=False, indent=1))
        return

    print(f"{hoje.isoformat()} · {len(itens)} caso(s) candidato(s) "
          f"({'histórico completo' if todos else 'abertos a partir de ' + DESDE})")
    if not itens:
        print('Nenhum caso novo. Nada a abrir.')
        return

    existentes = nomes_no_projeto()
    for i in itens:
        if i['nome'] in existentes:
            print(f"· já avisado: {i['cliente']} (caso de {br(i['aberto_em'])})")
            continue
        link = criar_tarefa(i['nome'], i['html_notes'], hoje.isoformat())
        print(f"+ criada: {i['cliente']} · {' + '.join(i['plataformas'])} · "
              f"{i['abertas_n']} cobrança(s) em aberto ({rs(i['abertas_valor'])}) → {link}")


if __name__ == '__main__':
    try:
        main()
    except Exception:
        erro = traceback.format_exc()
        print(erro, file=sys.stderr)
        avisar_falha(erro)
        sys.exit(1)
