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

# Parcela com gateway de outra plataforma não recebe link do Asaas, mesmo que o
# nome bata. Cliente que comprou na Cispay e depois no Asaas tem parcela nos dois.
GATEWAY_ASAAS = 'Asaas'

TOLERANCIA_DIAS = 7          # nível 3: vencimento remarcado de um lado só
TOLERANCIA_UNICA_DIAS = 45   # nível 4: janela do "sobrou uma de cada"
TOLERANCIA_VALOR = 0.01      # centavo de arredondamento

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
    for p in parcelas:
        p['_cands'] = porNome.get(p['_nome'], [])

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
    return pares, [(p, disponiveis(p)) for p in livres]


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
        gw = p.get('gateway') or v.get('gateway') or ''
        # gateway em branco entra (muita parcela antiga não tem o campo preenchido);
        # gateway de outra plataforma fica de fora.
        if gw and gw != GATEWAY_ASAAS:
            continue
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

    pares, ambiguas = casar(parcelas, cobrancas)

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

    print(json.dumps({
        'data': str(hoje_sp()),
        'dry': DRY,
        'cobrancas_abertas_asaas': len(cobrancas),
        'parcelas_candidatas': len(parcelas),
        'casadas': len(pares),
        'ja_estavam_certas': iguais,
        'gravadas': len(escritas),
        'limpas': len(limpas),
        'sem_casamento': len(ambiguas),
        'detalhe_gravadas': escritas,
        'detalhe_sem_casamento': [
            {'cliente': p['_cliente'], 'venc': str(p['_venc']), 'valor': p['_valor'],
             'cobrancas_no_asaas': n} for p, n in ambiguas],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
