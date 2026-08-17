# -*- coding: utf-8 -*-
"""Link de cobrança do Asaas nas parcelas pendentes da Central.

Roda pelo GitHub Actions: lê as cobranças em aberto no Asaas, casa cada uma com a
parcela correspondente em `pagamentos_venda` e grava o `invoiceUrl` na coluna
`link_cobranca`. A operadora abre a aba Cobranças e copia o link direto de lá.

Roda aqui e não no navegador porque a chave do Asaas não pode encostar no
index.html: a Central é página estática e pública, o fonte fica exposto.

    python3 asaas_links.py          # casa e grava
    python3 asaas_links.py --dry    # só imprime o que casaria, não escreve

Precisa de ASAAS_TOKEN no ambiente (secret do repo). Sem ele, para na hora.

CASAMENTO — a Central só guarda `nome_cliente` (não tem CPF nem e-mail), então a
chave é nome + vencimento + valor, em três níveis de confiança. Parcela ambígua
fica de fora de propósito: link errado na mão da operadora é pior que link nenhum
(ela cobra o cliente certo pelo boleto do errado). O que não casar, cola-se à mão
no card, e o manual nunca é sobrescrito.
"""
import base64
import json
import os
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

SUPA = 'https://ebcydqqhvdapruhnwbce.supabase.co/rest/v1/'
KEY = base64.b64decode(
    'ZXlKaGJHY2lPaUpJVXpJMU5pSXNJblI1Y0NJNklrcFhWQ0o5LmV5SnBjM01pT2lKemRYQmhZbUZ6WlNJc0lu'
    'SmxaaUk2SW1WaVkzbGtjWEZvZG1SaGNISjFhRzUzWW1ObElpd2ljbTlzWlNJNkltRnViMjRpTENKcFlYUWlP'
    'akUzTnprNU9ETXlOaklzSW1WNGNDSTZNakE1TlRVMU9USTJNbjAuME9SaTlGUlpWU3Q2V09iM1EzVnhWWXoy'
    'VWtYR1JDYlptcTJCVTUwWEpGMA==').decode()

ASAAS = 'https://api.asaas.com/v3/'
# .strip() porque token colado no secret costuma vir com quebra de linha junto,
# e aí o header sai malformado e o Asaas devolve 401
TOKEN = os.environ.get('ASAAS_TOKEN', '').strip()

# Cobrança viva no Asaas: ainda dá pra pagar, logo ainda tem link pra mandar.
STATUS_ABERTOS = ('PENDING', 'OVERDUE', 'AWAITING_RISK_ANALYSIS')

# O campo `gateway` da Central é o da VENDA, não o da parcela do mês: cliente que
# fechou na Cispay e teve a recorrência migrada pro Asaas fica marcado como Cispay
# e mesmo assim tem cobrança aberta no Asaas (Michelle Soares, Márcio Isabella,
# Adriana Lima e Juliano Amantea são exatamente isso). Filtrar por gateway
# derrubava casamento bom, então ele não filtra nada — quem separa é vencimento
# e valor, que é evidência de verdade.

TOLERANCIA_DIAS = 7          # nível 3: vencimento remarcado de um lado só
TOLERANCIA_UNICA_DIAS = 45   # nível 4: janela do "sobrou uma de cada"
TOLERANCIA_VALOR = 0.01      # centavo de arredondamento

# Partículas de nome que não identificam ninguém e sufixos de razão social. Ficam
# de fora da conta de "token em comum": 'de' e 'ltda' batendo não significam nada.
VAZIOS = {'de', 'da', 'do', 'dos', 'das', 'e', 'di', 'du', 'del', 'la', 'ltda',
          'me', 'epp', 'eireli', 'sa', 'ss', 'mei', 'cnpj', 'nota', 'no', 'na'}

# Cliente que cobra na razão social da empresa: nenhum algoritmo tira "Marcia
# Donadussi" de "MD Clínica Médica Dermatológica", isso é conhecimento de quem
# vende. Escrito à mão, nome normalizado dos dois lados (sem acento, minúsculo).
#     'nome na Central': 'nome do cadastro no Asaas'
APELIDOS_MANUAIS = {
    'marcia donadussi': 'md clinica medica dermatologica',
}

DRY = '--dry' in sys.argv


def hoje_sp():
    """Data em São Paulo (UTC-3) — o runner do GitHub roda em UTC."""
    return (datetime.now(timezone.utc) - timedelta(hours=3)).date()


def norm(s):
    """Nome comparável: sem acento, sem caixa, sem pontuação, sem espaço duplo."""
    s = unicodedata.normalize('NFKD', s or '')
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = ''.join(c if c.isalnum() else ' ' for c in s.lower())
    return ' '.join(s.split())


def uteis(nome):
    """Tokens que realmente identificam alguém — sem 'de', 'da', 'ltda' e afins."""
    return {t for t in nome.split() if t not in VAZIOS}


def resolver_nomes(centrais, asaas):
    """Liga o nome curto da Central ao nome completo do cadastro do Asaas.

    A Central digita como se chama o cliente ('Germana Araújo'); o Asaas guarda o
    nome do documento ('Germana Gabriella Pereira da Costa Araujo'). Comparar
    string com string acha só uma fração, então a ligação é por token, em três
    níveis, e cada nível só fecha o par que é único NOS DOIS SENTIDOS.

    O primeiro nome sozinho não basta e não pode bastar: a base tem 'Juliano
    Amantea' e 'Juliano Alarcon Fabricio', 'Marina Cominetti' e 'Marina Alves'.
    Por isso todo nível exige o primeiro nome MAIS um segundo token de verdade.
    """
    par, sobra_c, sobra_a = {}, set(centrais), set(asaas)

    # Apelido escrito à mão vence qualquer heurística e sai do jogo antes das rodadas,
    # pra não competir com nome parecido nem ser roubado por ele.
    for c, a in APELIDOS_MANUAIS.items():
        if c in sobra_c and a in sobra_a:
            par[c] = a
            sobra_c.discard(c)
            sobra_a.discard(a)

    def rodada(criterio):
        achados = []
        for c in sobra_c:
            cs = [a for a in sobra_a if criterio(c, a)]
            if len(cs) == 1:
                # a volta também tem que ser única, senão dois nomes da Central
                # apontam pro mesmo cadastro do Asaas e o desempate seria sorteio
                se = [x for x in sobra_c if criterio(x, cs[0])]
                if len(se) == 1:
                    achados.append((c, cs[0]))
        for c, a in achados:
            par[c] = a
            sobra_c.discard(c)
            sobra_a.discard(a)

    prim = lambda n: (n.split() or [''])[0]
    ult = lambda n: (n.split() or [''])[-1]

    # 1. o nome está escrito igual dos dois lados.
    rodada(lambda c, a: c == a)
    # 2. primeiro e último nome iguais — 'Taise Torres' ↔ 'Taise Deodora dos Santos Torres'.
    rodada(lambda c, a: prim(c) == prim(a) and ult(c) == ult(a) and prim(c))
    # 3. primeiro nome igual e mais um sobrenome em comum — pega 'Camilla Santana'
    #    ↔ 'Camilla Santana Estil da Camara', e recusa 'Juliano Amantea' ↔
    #    'Juliano Alarcon Fabricio', que só compartilham o primeiro nome.
    rodada(lambda c, a: prim(c) == prim(a) and prim(c)
           and len(uteis(c) & uteis(a)) >= 2)
    return par


def dia(s):
    """'2026-08-17T00:00:00' e '2026-08-17' viram date. Vazio vira None."""
    s = (s or '')[:10]
    if len(s) != 10:
        return None
    try:
        return date(int(s[:4]), int(s[5:7]), int(s[8:10]))
    except ValueError:
        return None


# ── SUPABASE ──

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


def patch(pid, corpo):
    req = urllib.request.Request(
        f'{SUPA}pagamentos_venda?id=eq.{urllib.parse.quote(str(pid))}',
        data=json.dumps(corpo).encode(), method='PATCH',
        headers={'apikey': KEY, 'Authorization': 'Bearer ' + KEY,
                 'Content-Type': 'application/json', 'Prefer': 'return=minimal'})
    urllib.request.urlopen(req).read()


# ── ASAAS ──

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


# ── CASAMENTO ──

def casar(parcelas, cobrancas):
    """Casa parcela ↔ cobrança do Asaas por nome, em quatro níveis de confiança.

    Cada nível só olha o que os anteriores deixaram sobrando, e só fecha o que é
    único NOS DOIS SENTIDOS: a parcela tem um candidato só, e aquele candidato é
    disputado por uma parcela só. Empate devolve todo mundo pro nível seguinte —
    e o que chegar ao fim empatado fica sem link de propósito.

    Duas parcelas iguais do mesmo cliente (mesmo dia, mesmo valor) contra uma
    cobrança só é justamente o caso em que a unicidade de um lado engana: cada
    parcela vê "um candidato" e as duas apontam pro mesmo lugar.
    """
    porNome = {}
    for c in cobrancas:
        porNome.setdefault(c['_nome'], []).append(c)
    # O nome da Central quase nunca é igual ao do cadastro do Asaas: resolve antes,
    # e só então compara vencimento e valor dentro do cliente já identificado.
    apelido = resolver_nomes({p['_nome'] for p in parcelas}, set(porNome))
    for p in parcelas:
        p['_cands'] = porNome.get(apelido.get(p['_nome'], ''), [])

    pares, usadas = [], set()

    def rodada(livres, criterio, nivel):
        claims, resto = {}, []
        for p in livres:
            cs = [c for c in p['_cands'] if id(c) not in usadas and criterio(p, c)]
            if len(cs) == 1:
                claims.setdefault(id(cs[0]), (cs[0], []))[1].append(p)
            else:
                resto.append(p)
        for cand, grupo in claims.values():
            if len(grupo) == 1:
                pares.append((grupo[0], cand, nivel))
                usadas.add(id(cand))
            else:
                resto += grupo  # dois pretendentes pra mesma cobrança: nenhum leva
        return resto

    mesmoValor = lambda p, c: abs(c['_valor'] - p['_valor']) <= TOLERANCIA_VALOR
    perto = lambda p, c, d: abs((c['_venc'] - p['_venc']).days) <= d

    # 1. vencimento e valor batendo — o caso normal.
    livres = rodada(parcelas, lambda p, c: c['_venc'] == p['_venc'] and mesmoValor(p, c), 'venc+valor')
    # 2. vencimento batendo, valor não — juros, desconto, renegociação.
    livres = rodada(livres, lambda p, c: c['_venc'] == p['_venc'], 'venc')
    # 3. valor batendo, vencimento remarcado de um lado só.
    livres = rodada(livres, lambda p, c: mesmoValor(p, c) and perto(p, c, TOLERANCIA_DIAS), 'valor+prox')
    # 4. sobrou uma parcela e uma cobrança do cliente, na mesma janela de tempo.
    #    A janela existe pra não colar uma cobrancinha avulsa de dezembro numa
    #    parcela de setembro só porque são as duas únicas em aberto daquele nome.
    livres = rodada(livres, lambda p, c: perto(p, c, TOLERANCIA_UNICA_DIAS), 'unica')

    disponiveis = lambda p: sum(1 for c in p['_cands'] if id(c) not in usadas)
    return pares, [(p, disponiveis(p)) for p in livres], apelido


def main():
    if not TOKEN:
        raise SystemExit('ASAAS_TOKEN ausente — sem chave não há o que buscar.')

    # ── lado Asaas ──
    clientes = {c['id']: c.get('name') or '' for c in asaas('customers')}
    cobrancas = []
    for st in STATUS_ABERTOS:
        cobrancas += asaas('payments', status=st)
    for c in cobrancas:
        c['_nome'] = norm(clientes.get(c.get('customer'), ''))
        c['_venc'] = dia(c.get('dueDate'))
        c['_valor'] = float(c.get('value') or 0)
    # Sem nome ou sem vencimento não dá pra casar com nada; sem link não há o que gravar.
    cobrancas = [c for c in cobrancas if c['_nome'] and c['_venc'] and c.get('invoiceUrl')]

    # ── lado Central ──
    vendas = {v['id']: v for v in q('vendas?select=id,nome_cliente,gateway')}
    todas = q('pagamentos_venda?status=eq.pendente&select=*')
    parcelas = []
    for p in todas:
        v = vendas.get(p.get('venda_id')) or {}
        if (p.get('link_origem') or '') == 'manual':
            continue  # link colado à mão manda: o job não escreve por cima
        venc = dia(p.get('data_prevista'))
        nome = norm(v.get('nome_cliente'))
        if not venc or not nome:
            continue
        p['_nome'], p['_venc'] = nome, venc
        p['_valor'] = float(p.get('valor_bruto') or 0)
        p['_cliente'] = v.get('nome_cliente')
        parcelas.append(p)

    pares, ambiguas, apelido = casar(parcelas, cobrancas)

    # ── gravação ──
    agora = datetime.now(timezone.utc).isoformat()
    escritas, iguais = [], 0
    for p, c, nivel in pares:
        if p.get('link_cobranca') == c['invoiceUrl'] and p.get('asaas_payment_id') == c['id']:
            iguais += 1
            continue
        item = {'parcela': p['id'], 'cliente': p['_cliente'], 'venc': str(p['_venc']),
                'valor': p['_valor'], 'asaas': c['id'], 'nivel': nivel, 'url': c['invoiceUrl']}
        if not DRY:
            patch(p['id'], {'link_cobranca': c['invoiceUrl'], 'asaas_payment_id': c['id'],
                            'link_origem': 'asaas', 'link_atualizado_em': agora})
        escritas.append(item)

    # Parcela que já teve link do Asaas mas cuja cobrança sumiu (paga por fora,
    # removida, reemitida) fica com link morto. Limpa — só o que o job mesmo pôs.
    vivos = {c['id'] for c in cobrancas}
    limpas = []
    casadas = {p['id'] for p, _, _ in pares}
    for p in parcelas:
        if (p.get('link_origem') == 'asaas' and p.get('asaas_payment_id')
                and p['id'] not in casadas and p['asaas_payment_id'] not in vivos):
            if not DRY:
                patch(p['id'], {'link_cobranca': None, 'asaas_payment_id': None,
                                'link_origem': None, 'link_atualizado_em': agora})
            limpas.append({'parcela': p['id'], 'cliente': p['_cliente']})

    # Nome que a Central escreve diferente do cadastro do Asaas sai no relatório de
    # propósito: é a única parte do casamento que ninguém consegue conferir de
    # cabeça, e é onde um erro colocaria o boleto de um cliente no card de outro.
    ligados = sorted((c, a) for c, a in apelido.items() if c != a)

    # Cliente que tem cobrança aberta no Asaas e nenhuma parcela pendente na Central.
    # Não é falha do casamento: é venda que não está cadastrada (ou já quitada aqui).
    orfaos = {}
    for c in cobrancas:
        if c['_nome'] not in set(apelido.values()):
            orfaos[c['_nome']] = orfaos.get(c['_nome'], 0) + 1

    print(json.dumps({
        'data': str(hoje_sp()),
        'dry': DRY,
        'cobrancas_abertas_asaas': len(cobrancas),
        'parcelas_candidatas': len(parcelas),
        'clientes_ligados': len(apelido),
        'casadas': len(pares),
        'ja_estavam_certas': iguais,
        'gravadas': len(escritas),
        'limpas': len(limpas),
        'sem_casamento': len(ambiguas),
        'nomes_ligados_por_aproximacao': [{'central': c, 'asaas': a} for c, a in ligados],
        'asaas_sem_parcela_na_central': sorted(
            ({'nome': n, 'cobrancas': q} for n, q in orfaos.items()),
            key=lambda x: -x['cobrancas']),
        'detalhe_gravadas': escritas,
        'detalhe_sem_casamento': [
            {'cliente': p['_cliente'], 'venc': str(p['_venc']), 'valor': p['_valor'],
             'cobrancas_no_asaas': n} for p, n in ambiguas],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
