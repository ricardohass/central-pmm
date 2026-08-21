# -*- coding: utf-8 -*-
"""Extrato completo de um cliente no Asaas — inclusive o que já foi pago.

Existe porque `asaas_links.py` só enxerga cobrança VIVA (PENDING/OVERDUE), que é
o que interessa pra cobrar. Quando a pergunta é "esse cliente já pagou alguma
parcela?", aquela lista não responde: a cobrança paga sai do radar. Foi o caso da
Marina Campofiorito em 21/08/2026 — uma única cobrança órfã descrita como "Última
Parcela", sem venda na Central e sem como saber o que veio antes dela.

    NOME="marina" python3 asaas_extrato.py

Busca por pedaço do nome, sem acento e sem case, e imprime TODAS as cobranças de
cada cliente que casar — pagas, abertas, canceladas, estornadas — em ordem de
vencimento, com o total por situação. Não escreve nada: é leitura pra decidir.

Precisa de ASAAS_TOKEN no ambiente (secret do repo).

NÃO mostra cobrança EXCLUÍDA: testado em 21/08/2026, o Asaas ignora silenciosamente
`deletedOnly=true` em /payments e devolve a mesma lista de sempre — o que faz a
excluída parecer viva em dobro, não aparecer de verdade. Então cobrança que alguém
apagou some daqui sem deixar rastro, e "cliente sem nenhuma cobrança" não distingue
"nunca teve" de "apagaram". Essa diferença só na tela do Asaas, no filtro de
excluídas.
"""
import json
import os
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ASAAS = 'https://api.asaas.com/v3/'
TOKEN = os.environ.get('ASAAS_TOKEN', '').strip()
NOME = os.environ.get('NOME', '').strip()

# Pago de verdade x prometido x morto. RECEIVED e CONFIRMED são ambos dinheiro
# reconhecido pelo Asaas (CONFIRMED = cartão aprovado antes do repasse cair).
PAGOS = ('RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH')
ABERTOS = ('PENDING', 'OVERDUE', 'AWAITING_RISK_ANALYSIS')


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


def main():
    if not TOKEN:
        raise SystemExit('ASAAS_TOKEN ausente — sem chave não há o que buscar.')
    if not NOME:
        raise SystemExit('NOME ausente — passe um pedaço do nome do cliente.')

    alvo = cru(NOME)
    achados = [c for c in asaas('customers') if alvo in cru(c.get('name'))]
    if not achados:
        raise SystemExit(f'Nenhum cliente no Asaas com "{NOME}" no nome.')

    for c in achados:
        print(f'\n=== {c.get("name")}  ({c["id"]})')
        print(f'    cpfCnpj={c.get("cpfCnpj") or "—"}  email={c.get("email") or "—"}'
              f'  criado={dia(c.get("dateCreated"))}')

        pgs = sorted(asaas('payments', customer=c['id']),
                     key=lambda p: p.get('dueDate') or '')
        if not pgs:
            print('    sem nenhuma cobrança registrada.')
            continue

        for p in pgs:
            pago = dia(p.get('paymentDate') or p.get('clientPaymentDate'))
            print(f'    {dia(p.get("dueDate")):>10}  R$ {br(p.get("value") or 0):>10}'
                  f'  {p.get("status"):<18} pago em {pago:>10}'
                  f'  {p.get("billingType"):<10} {(p.get("description") or "—")[:40]}')

        soma = lambda f: sum(p.get('value') or 0 for p in pgs if f(p))
        conta = lambda f: sum(1 for p in pgs if f(p))
        pago_f = lambda p: p.get('status') in PAGOS
        aberto_f = lambda p: p.get('status') in ABERTOS
        outro_f = lambda p: p.get('status') not in PAGOS + ABERTOS
        print(f'    ── {len(pgs)} cobranças · R$ {br(soma(lambda p: True))} no total')
        print(f'       pagas:    {conta(pago_f):>3} · R$ {br(soma(pago_f))}')
        print(f'       abertas:  {conta(aberto_f):>3} · R$ {br(soma(aberto_f))}')
        if conta(outro_f):
            mortos = sorted({p.get('status') for p in pgs if outro_f(p)})
            print(f'       outras:   {conta(outro_f):>3} · R$ {br(soma(outro_f))}'
                  f'  ({", ".join(mortos)})')


if __name__ == '__main__':
    main()
