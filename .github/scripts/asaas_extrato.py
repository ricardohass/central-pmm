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

Varre AS DUAS contas Asaas do grupo (Grupo Prø e Wonder) e diz em qual cada
cliente apareceu — quem consulta raramente sabe de antemão em qual CNPJ o cliente
foi cadastrado, e essa é justamente a pergunta que trouxe a pessoa aqui. Pra olhar
uma só, CONTA="pmm" ou CONTA="wonder".

Precisa de ASAAS_TOKEN e/ou ASAAS_TOKEN_WONDER no ambiente (secrets do repo).

NÃO mostra cobrança EXCLUÍDA: testado em 21/08/2026, o Asaas ignora silenciosamente
`deletedOnly=true` em /payments e devolve a mesma lista de sempre — o que faz a
excluída parecer viva em dobro, não aparecer de verdade. Então cobrança que alguém
apagou some daqui sem deixar rastro, e "cliente sem nenhuma cobrança" não distingue
"nunca teve" de "apagaram". Essa diferença só na tela do Asaas, no filtro de
excluídas.
"""
import os
import unicodedata

from asaas_contas import CONTAS, asaas, escolhidas

NOME = os.environ.get('NOME', '').strip()
CONTA = os.environ.get('CONTA', '').strip()

# Pago de verdade x prometido x morto. RECEIVED e CONFIRMED são ambos dinheiro
# reconhecido pelo Asaas (CONFIRMED = cartão aprovado antes do repasse cair).
PAGOS = ('RECEIVED', 'CONFIRMED', 'RECEIVED_IN_CASH')
ABERTOS = ('PENDING', 'OVERDUE', 'AWAITING_RISK_ANALYSIS')


def cru(s):
    s = unicodedata.normalize('NFKD', s or '')
    return ''.join(c for c in s if not unicodedata.combining(c)).lower().strip()


def br(v):
    return f'{v:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def dia(s):
    return f'{s[8:10]}/{s[5:7]}/{s[0:4]}' if s else '—'


def main():
    contas = escolhidas(CONTA)
    if not contas:
        raise SystemExit('Nenhuma chave de Asaas no ambiente ('
                         + ', '.join(c.env for c in CONTAS) + ').')
    if not NOME:
        raise SystemExit('NOME ausente — passe um pedaço do nome do cliente.')

    # Conta fora da consulta é dito em voz alta: "não achei" e "não procurei lá"
    # levam a decisões opostas sobre um caso de reembolso.
    fora = [c for c in CONTAS if c not in contas]
    if fora:
        motivo = f'pedido CONTA={CONTA}' if CONTA else 'sem chave no ambiente'
        print('Contas NÃO consultadas (' + motivo + '): '
              + ', '.join(c.rotulo for c in fora))

    alvo = cru(NOME)
    achou = False
    for conta in contas:
        print(f'\n######## Asaas {conta.rotulo} — cobra {conta.produtos}')
        achados = [c for c in asaas(conta, 'customers') if alvo in cru(c.get('name'))]
        if not achados:
            print(f'    nenhum cliente com "{NOME}" no nome nesta conta.')
            continue
        achou = True
        imprimir(conta, achados)

    if not achou:
        # Resposta legítima da consulta, não falha: o cliente pode estar cadastrado
        # com outro nome (a Marcia Donadussi está como "MD Clínica Médica
        # Dermatológica"). Sair com erro aqui pintava a execução de vermelho no
        # Actions e disparava e-mail de "Run failed" pra uma busca que funcionou.
        print(f'\nNenhum cliente com "{NOME}" no nome em nenhuma conta consultada.')
        print('Pode estar cadastrado como empresa: tente um pedaço do CNPJ ou do e-mail.')


def imprimir(conta, achados):
    for c in achados:
        print(f'\n=== {c.get("name")}  ({c["id"]})  [{conta.slug}]')
        print(f'    cpfCnpj={c.get("cpfCnpj") or "—"}  email={c.get("email") or "—"}'
              f'  criado={dia(c.get("dateCreated"))}')

        pgs = sorted(asaas(conta, 'payments', customer=c['id']),
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
        quantas = lambda f: sum(1 for p in pgs if f(p))
        pago_f = lambda p: p.get('status') in PAGOS
        aberto_f = lambda p: p.get('status') in ABERTOS
        outro_f = lambda p: p.get('status') not in PAGOS + ABERTOS
        print(f'    ── {len(pgs)} cobranças · R$ {br(soma(lambda p: True))} no total')
        print(f'       pagas:    {quantas(pago_f):>3} · R$ {br(soma(pago_f))}')
        print(f'       abertas:  {quantas(aberto_f):>3} · R$ {br(soma(aberto_f))}')
        if quantas(outro_f):
            mortos = sorted({p.get('status') for p in pgs if outro_f(p)})
            print(f'       outras:   {quantas(outro_f):>3} · R$ {br(soma(outro_f))}'
                  f'  ({", ".join(mortos)})')


if __name__ == '__main__':
    main()
