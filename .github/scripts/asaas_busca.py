# -*- coding: utf-8 -*-
"""Busca ampla no Asaas — quando o nome não acha o cliente.

`asaas_extrato.py` só casa por pedaço do nome. Isso não responde quando o
cadastro está em outro nome (empresa, cônjuge, apelido) e a única pista que
sobrou é o telefone que o cliente deu na venda ou o valor da parcela. Foi o caso
do Tarcisio Cardoso Tonha Filho em 24/08/2026: nenhum cliente com "tarcisio" no
nome, mas ainda era preciso descartar cobrança viva antes de fechar o caso de
reembolso dizendo "não existe no Asaas".

    TERMO="tonha,cardoso" TELEFONE="11980909885" VALOR="3300,39600" python3 asaas_busca.py

Todos os campos são opcionais e somam: cada um é uma frente de busca própria.
  TERMO    — pedaços de nome/e-mail/CPF-CNPJ, separados por vírgula
  TELEFONE — só os dígitos importam; casa com phone e mobilePhone
  VALOR    — valores exatos de cobrança, separados por vírgula. Filtrado AQUI,
             não na API: testado em 24/08/2026, o Asaas ignora `value` em
             /payments e devolve a lista inteira — o que faz qualquer valor
             parecer que "achou". Comparação exata em centavos.

Não escreve nada: é leitura pra decidir. Mesma cegueira do extrato — cobrança
EXCLUÍDA no Asaas não aparece por API, só na tela, no filtro de excluídas.
"""
import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ASAAS = 'https://api.asaas.com/v3/'
TOKEN = os.environ.get('ASAAS_TOKEN', '').strip()

TERMOS = [t.strip() for t in os.environ.get('TERMO', '').split(',') if t.strip()]
TELEFONE = re.sub(r'\D', '', os.environ.get('TELEFONE', ''))
VALORES = [v.strip().replace('.', '').replace(',', '.')
           for v in os.environ.get('VALOR', '').split(',') if v.strip()]


def cru(s):
    s = unicodedata.normalize('NFKD', s or '')
    return ''.join(c for c in s if not unicodedata.combining(c)).lower().strip()


def asaas(recurso, **params):
    """GET paginado. O Asaas devolve {data, hasMore, limit, offset}, limit máx 100."""
    out, off = [], 0
    while True:
        p = dict(params, limit=100, offset=off)
        url = f'{ASAAS}{recurso}?' + urllib.parse.urlencode(p, doseq=True)
        req = urllib.request.Request(url, headers={
            'access_token': TOKEN,
            'User-Agent': 'central-pmm',        # o Asaas exige User-Agent próprio
            'Content-Type': 'application/json'})
        try:
            d = json.load(urllib.request.urlopen(req))
        except urllib.error.HTTPError as e:
            corpo = e.read().decode('utf-8', 'replace')[:400]
            raise SystemExit(f'Asaas {recurso} devolveu HTTP {e.code}: {corpo}')
        out += d.get('data', [])
        if not d.get('hasMore'):
            return out
        off += 100


def br(v):
    return f'{v:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def dia(s):
    return f'{s[8:10]}/{s[5:7]}/{s[0:4]}' if s else '—'


def linha_pagamento(p, quem=''):
    pago = dia(p.get('paymentDate') or p.get('clientPaymentDate'))
    print(f'    {dia(p.get("dueDate")):>10}  R$ {br(p.get("value") or 0):>10}'
          f'  {p.get("status"):<18} pago em {pago:>10}'
          f'  {(p.get("billingType") or "—"):<10} {(p.get("description") or "—")[:40]}'
          f'{("  ← " + quem) if quem else ""}')


def main():
    if not TOKEN:
        raise SystemExit('ASAAS_TOKEN ausente — sem chave não há o que buscar.')
    if not (TERMOS or TELEFONE or VALORES):
        raise SystemExit('Passe ao menos um de TERMO, TELEFONE ou VALOR.')

    clientes = asaas('customers')
    print(f'{len(clientes)} clientes cadastrados no Asaas.\n')
    nomes = {c['id']: c.get('name') for c in clientes}

    # --- frente 1: nome, e-mail, CPF/CNPJ -------------------------------------
    if TERMOS:
        print(f'== por TERMO ({", ".join(TERMOS)})')
        achados = [c for c in clientes
                   if any(cru(t) in cru(c.get('name')) or cru(t) in cru(c.get('email'))
                          or re.sub(r'\D', '', t) and re.sub(r'\D', '', t) in (c.get('cpfCnpj') or '')
                          for t in TERMOS)]
        if not achados:
            print('   nenhum cliente casou.')
        for c in achados:
            print(f'   {c.get("name")}  ({c["id"]})  cpfCnpj={c.get("cpfCnpj") or "—"}'
                  f'  email={c.get("email") or "—"}')
            for p in sorted(asaas('payments', customer=c['id']),
                            key=lambda p: p.get('dueDate') or ''):
                linha_pagamento(p)
        print()

    # --- frente 2: telefone ---------------------------------------------------
    if TELEFONE:
        print(f'== por TELEFONE ({TELEFONE})')
        # Casa pelos 8 dígitos finais: o cadastro varia em DDI, DDD e no 9 extra.
        cauda = TELEFONE[-8:]
        achados = [c for c in clientes
                   if cauda and (cauda in re.sub(r'\D', '', c.get('phone') or '')
                                 or cauda in re.sub(r'\D', '', c.get('mobilePhone') or ''))]
        if not achados:
            print('   nenhum cliente com esse telefone.')
        for c in achados:
            print(f'   {c.get("name")}  ({c["id"]})  phone={c.get("phone") or "—"}'
                  f'  mobile={c.get("mobilePhone") or "—"}')
            for p in sorted(asaas('payments', customer=c['id']),
                            key=lambda p: p.get('dueDate') or ''):
                linha_pagamento(p)
        print()

    # --- frente 3: valor exato da cobrança ------------------------------------
    if VALORES:
        todas = asaas('payments')
        print(f'{len(todas)} cobranças na conta (o filtro de valor é aplicado aqui).\n')
        for v in VALORES:
            print(f'== por VALOR (R$ {v})')
            alvo = round(float(v) * 100)
            pgs = sorted((p for p in todas
                          if round((p.get('value') or 0) * 100) == alvo),
                         key=lambda p: p.get('dueDate') or '')
            if not pgs:
                print('   nenhuma cobrança com esse valor exato.')
            for p in pgs:
                linha_pagamento(p, nomes.get(p.get('customer'), p.get('customer')))
            print()


if __name__ == '__main__':
    main()
