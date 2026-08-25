# -*- coding: utf-8 -*-
"""As duas contas Asaas do grupo, e por qual delas cada venda é cobrada.

POR QUE ISSO EXISTE: até 25/08/2026 os scripts assumiam que existia UMA conta
Asaas. Existem duas — o Asaas do Grupo Prø (PMM e Aceleração 2M) e o Asaas da
Wonder, aberto no CNPJ da Wonder Prø, onde as cobranças da agência são emitidas
separadamente. Consultar só a primeira faz o Wonder parecer que não tem cobrança
nenhuma: a parcela fica sem link e a cobrança some do radar das órfãs, que é
justamente o relatório que deveria denunciar dinheiro cobrado fora do controle.

Cada conta tem token próprio (chave de API é por conta no Asaas, não há uma que
leia as duas), guardado num secret próprio do repositório.

QUEM VAI PRA ONDE: pelo `produto` da venda, não pelo `gateway`. O gateway é o da
venda e não acompanha migração de recorrência — a lição que já custou casamento
bom no asaas_links.py vale aqui igual. Produto é o que define de qual CNPJ sai a
nota, e portanto em qual Asaas a cobrança nasce.

Script novo que fale com o Asaas importa daqui:

    from asaas_contas import CONTAS, contas_ativas, asaas, conta_da_venda

    for conta in contas_ativas():          # só as que têm token no ambiente
        clientes = asaas(conta, 'customers')
"""
import json
import os
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ASAAS = 'https://api.asaas.com/v3/'


class Conta:
    """Uma conta Asaas: como chamá-la, de onde vem o token, o que ela cobra."""

    def __init__(self, slug, rotulo, env, produtos):
        self.slug = slug          # identificador curto, é o que vai pro banco
        self.rotulo = rotulo      # como aparece no log e no relatório
        self.env = env            # nome do secret/variável de ambiente
        self.produtos = produtos  # o que essa conta cobra, pra explicar no log

    @property
    def token(self):
        # .strip() porque token colado no secret costuma vir com quebra de linha
        # junto, e aí o header sai malformado e o Asaas devolve 401
        return os.environ.get(self.env, '').strip()

    def __repr__(self):
        return f'<Conta {self.slug}>'


# A ordem importa só pro log sair sempre igual.
CONTAS = (
    Conta('pmm', 'Grupo Prø', 'ASAAS_TOKEN', 'PMM e Aceleração 2M'),
    Conta('wonder', 'Wonder Prø', 'ASAAS_TOKEN_WONDER', 'Wonder Prø'),
)

CONTA_PADRAO = 'pmm'   # venda sem produto preenchido é PMM: são 104 de 112.

POR_SLUG = {c.slug: c for c in CONTAS}


def contas_ativas():
    """As contas que têm token no ambiente.

    Rodar com uma chave só é situação legítima (consulta pontual na máquina de
    alguém, secret ainda não criado), e não pode derrubar o que a outra conta
    resolveria sozinha. Quem chama avisa no relatório qual ficou de fora — o
    silêncio é que seria perigoso: "Wonder sem cobrança aberta" e "não olhei o
    Asaas da Wonder" são conclusões opostas e se parecem no log.
    """
    return [c for c in CONTAS if c.token]


def conta_da_venda(produto):
    """Slug da conta que cobra essa venda. Casa por 'wonder' no nome do produto.

    O valor no banco é '[Wonder] Wonder Prø', mas o rótulo já mudou de forma
    antes ('Wonder Pro', 'Wonder Prø') e vai mudar de novo; procurar a palavra
    aguenta isso, comparar a string inteira não.
    """
    p = unicodedata.normalize('NFKD', produto or '').lower()
    return 'wonder' if 'wonder' in p else CONTA_PADRAO


def asaas(conta, recurso, **params):
    """GET paginado numa conta. O Asaas devolve {data, hasMore, limit, offset}."""
    out, off = [], 0
    while True:
        p = dict(params, limit=100, offset=off)
        url = f'{ASAAS}{recurso}?' + urllib.parse.urlencode(p, doseq=True)
        req = urllib.request.Request(url, headers={
            'access_token': conta.token,
            'User-Agent': 'central-pmm',        # o Asaas exige User-Agent próprio
            'Content-Type': 'application/json'})
        try:
            d = json.load(urllib.request.urlopen(req))
        except urllib.error.HTTPError as e:
            corpo = e.read().decode('utf-8', 'replace')[:400]
            # O slug no erro é o que separa "a chave da Wonder está errada" de "a
            # chave do PMM expirou" — sem ele, 401 é 401 e o susto é dobrado.
            raise SystemExit(f'Asaas[{conta.slug}] {recurso} devolveu '
                             f'HTTP {e.code}: {corpo}')
        out += d.get('data', [])
        if not d.get('hasMore'):
            return out
        off += 100


def escolhidas(pedido):
    """Contas pedidas por nome ('pmm', 'wonder', 'ambas'/vazio), já filtradas
    pelas que têm token. Serve os scripts de consulta manual, onde o operador
    pode querer olhar só um lado."""
    pedido = (pedido or '').strip().lower()
    if pedido in ('', 'ambas', 'todas', 'all'):
        return contas_ativas()
    if pedido not in POR_SLUG:
        raise SystemExit(f'Conta "{pedido}" não existe. Use: '
                         + ', '.join(POR_SLUG) + ' ou "ambas".')
    conta = POR_SLUG[pedido]
    if not conta.token:
        # Pediu uma conta específica e ela não tem chave: o erro precisa dizer QUAL
        # secret falta, senão vira "sem chave nenhuma" e manda procurar no lugar errado.
        raise SystemExit(f'Conta "{pedido}" ({conta.rotulo}) pedida, mas '
                         f'{conta.env} não está no ambiente.')
    return [conta]
