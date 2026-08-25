# -*- coding: utf-8 -*-
"""Link de cobrança do Asaas nas parcelas pendentes da Central.

Roda pelo GitHub Actions: lê as cobranças em aberto no Asaas, casa cada uma com a
parcela correspondente em `pagamentos_venda` e grava o `invoiceUrl` na coluna
`link_cobranca`. A operadora abre a aba Cobranças e copia o link direto de lá.

DUAS CONTAS: o grupo cobra em dois Asaas — o do Grupo Prø (PMM e 2M) e o da
Wonder, no CNPJ da Wonder Prø. Cada venda é procurada SÓ na conta do seu produto
(`asaas_contas.conta_da_venda`), e cada conta roda o casamento inteiro por conta
própria: nome de cliente só compete com nome da mesma conta, e link de um CNPJ
nunca cai na parcela do outro. Conta sem chave no ambiente é PULADA por inteiro —
as parcelas dela não são casadas nem limpas, porque não olhar não é o mesmo que
olhar e não achar.

Roda aqui e não no navegador porque a chave do Asaas não pode encostar no
index.html: a Central é página estática e pública, o fonte fica exposto.

Grava também, em `asaas_orfas`, as cobranças abertas no Asaas que não casam com
nenhuma parcela da Central — dinheiro sendo cobrado no gateway sem contrapartida
no controle comercial. Antes essa lista só existia no log desta execução.

    python3 asaas_links.py          # casa e grava
    python3 asaas_links.py --dry    # só imprime o que casaria, não escreve

Precisa de ASAAS_TOKEN (Grupo Prø) e ASAAS_TOKEN_WONDER (Wonder) no ambiente,
secrets do repo. Sem chave nenhuma, para na hora; com uma só, roda a que tem e
diz no relatório qual ficou de fora.

CASAMENTO — a Central só guarda `nome_cliente` (não tem CPF nem e-mail), então a
chave é nome + vencimento + valor, em três níveis de confiança. Parcela ambígua
fica de fora de propósito: link errado na mão da operadora é pior que link nenhum
(ela cobra o cliente certo pelo boleto do errado). O que não casar, cola-se à mão
no card, e o manual nunca é sobrescrito.
"""
import base64
import json
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from asaas_contas import CONTAS, asaas, conta_da_venda, contas_ativas

SUPA = 'https://ebcydqqhvdapruhnwbce.supabase.co/rest/v1/'
KEY = base64.b64decode(
    'ZXlKaGJHY2lPaUpJVXpJMU5pSXNJblI1Y0NJNklrcFhWQ0o5LmV5SnBjM01pT2lKemRYQmhZbUZ6WlNJc0lu'
    'SmxaaUk2SW1WaVkzbGtjWEZvZG1SaGNISjFhRzUzWW1ObElpd2ljbTlzWlNJNkltRnViMjRpTENKcFlYUWlP'
    'akUzTnprNU9ETXlOaklzSW1WNGNDSTZNakE1TlRVMU9USTJNbjAuME9SaTlGUlpWU3Q2V09iM1EzVnhWWXoy'
    'VWtYR1JDYlptcTJCVTUwWEpGMA==').decode()

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
# A chave é o nome NA CENTRAL, e como cada venda só é procurada na conta do seu
# produto, um apelido daqui nunca é testado contra o CNPJ da outra conta.
#     'nome na Central': 'nome do cadastro no Asaas'
APELIDOS_MANUAIS = {
    'marcia donadussi': 'md clinica medica dermatologica',
    # Wonder, 25/08/2026 — as três primeiras cobranças da conta da Wonder eram
    # PJ e nenhuma casava por nome. Evidência de cada uma:
    #   Zero Grau: mesma data (31/08) e mesmo valor (R$ 4.000) da parcela.
    #   Studio CS: o e-mail do cadastro é anacarolina.alonso@icloud.com.
    'gabriel zero grau': 'zero grau industria e comercio ltda',
    'ana carolina alonso': 'studio cs arquitetura e design ltda',
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


def upsert(tabela, linhas):
    """POST com merge-duplicates: cria o que é novo, atualiza o que já existe.

    `primeira_vez_em` fica fora do corpo de propósito. Coluna que não vai no
    payload não é tocada pelo ON CONFLICT DO UPDATE, então o carimbo original
    sobrevive às regravações e vira a idade do buraco.
    """
    req = urllib.request.Request(
        f'{SUPA}{tabela}', data=json.dumps(linhas).encode(), method='POST',
        headers={'apikey': KEY, 'Authorization': 'Bearer ' + KEY,
                 'Content-Type': 'application/json',
                 'Prefer': 'resolution=merge-duplicates,return=minimal'})
    urllib.request.urlopen(req).read()


def apagar_orfas_antigas(agora, conta):
    """Tira da tabela o que não apareceu nesta rodada — cobrança paga, excluída
    ou finalmente cadastrada na Central. É o que faz `asaas_orfas` ser retrato do
    agora, e não um depósito que só cresce.

    Só apaga o que é DESTA conta: rodada que consultou apenas o Grupo Prø não
    pode limpar as órfãs da Wonder, ou o relatório da conta não consultada zera
    sozinho e o buraco parece resolvido."""
    req = urllib.request.Request(
        f'{SUPA}asaas_orfas?apurado_em=lt.{urllib.parse.quote(agora)}'
        f'&conta=eq.{urllib.parse.quote(conta)}',
        method='DELETE',
        headers={'apikey': KEY, 'Authorization': 'Bearer ' + KEY,
                 'Prefer': 'return=minimal'})
    urllib.request.urlopen(req).read()


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


def carregar_asaas(conta):
    """Cadastros e cobranças vivas de uma conta, já normalizados pro casamento."""
    clientes = {c['id']: c.get('name') or '' for c in asaas(conta, 'customers')}
    cobrancas = []
    for st in STATUS_ABERTOS:
        cobrancas += asaas(conta, 'payments', status=st)
    for c in cobrancas:
        c['_nome'] = norm(clientes.get(c.get('customer'), ''))
        c['_venc'] = dia(c.get('dueDate'))
        c['_valor'] = float(c.get('value') or 0)
    # Sem nome ou sem vencimento não dá pra casar com nada; sem link não há o que gravar.
    cobrancas = [c for c in cobrancas if c['_nome'] and c['_venc'] and c.get('invoiceUrl')]
    return clientes, cobrancas


def processar_conta(conta, parcelas, vendas, agora):
    """Casa, grava e apura as órfãs de UMA conta Asaas.

    Tudo aqui dentro é fechado na conta: as parcelas já chegam filtradas pelo
    produto, as cobranças vêm de um CNPJ só e as órfãs são comparadas com as
    vendas daquele mesmo produto. Devolve o pedaço do relatório dessa conta.
    """
    clientes, cobrancas = carregar_asaas(conta)
    pares, ambiguas, apelido = casar(parcelas, cobrancas)

    # ── gravação ──
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
    # Só as parcelas DESTA conta chegam aqui, então nada é limpo por causa de uma
    # cobrança que na verdade está viva no outro CNPJ.
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

    # ── órfãs: cobrança aberta no Asaas sem parcela pendente que case ──
    # Não é falha do casamento — é venda que não está cadastrada, ou está sem
    # cronograma. Até 20/08/2026 isso só saía no log e evaporava; agora vai pra
    # `asaas_orfas` (sql/003), pra dar pra consultar sem abrir o Actions e pra
    # medir se o buraco cresce ou fecha.
    casados_asaas = set(apelido.values())
    orfas = [c for c in cobrancas if c['_nome'] not in casados_asaas]

    # Segunda passada de nomes, agora contra TODAS as vendas do produto desta
    # conta e não só as que têm parcela pendente. É o que separa "venda existe mas
    # sem cronograma" de "não existe venda nenhuma" — duas conclusões que dão
    # trabalho bem diferente. Fica no produto de propósito: cobrança da Wonder que
    # só acha nome numa venda de PMM não é venda encontrada, é outra coisa.
    nomes_venda = {norm(v.get('nome_cliente')): v.get('nome_cliente')
                   for v in vendas.values() if norm(v.get('nome_cliente'))}
    liga = resolver_nomes(set(nomes_venda), {c['_nome'] for c in orfas})
    venda_por_asaas = {a: c for c, a in liga.items()}

    linhas = []
    for c in orfas:
        central = venda_por_asaas.get(c['_nome'])
        linhas.append({
            'asaas_payment_id': c['id'],
            'conta': conta.slug,
            'nome_asaas': clientes.get(c.get('customer')) or c['_nome'],
            'asaas_customer_id': c.get('customer'),
            'valor': c['_valor'],
            'vencimento': str(c['_venc']),
            'status_asaas': c.get('status'),
            'descricao': (c.get('description') or '')[:500] or None,
            'invoice_url': c.get('invoiceUrl'),
            'tem_venda_na_central': bool(central),
            'nome_na_central': nomes_venda.get(central) if central else None,
            'apurado_em': agora,
        })

    # Gravar as órfãs é acessório: se `asaas_orfas` ainda não existir (migração
    # sql/003 não rodada) ou o Supabase engasgar, o job NÃO pode cair — o serviço
    # principal é o link de pagamento na mão da operadora, e ele já foi gravado
    # acima. O erro sai no relatório em vez de derrubar a rodada.
    erro_orfas = None
    if not DRY:
        try:
            # Grava antes de apagar: se a rodada morrer no meio, sobra linha velha
            # (denunciada pelo apurado_em) em vez de tabela vazia mentindo que zerou.
            if linhas:
                upsert('asaas_orfas', linhas)
            apagar_orfas_antigas(agora, conta.slug)
        except Exception as e:
            erro_orfas = f'{type(e).__name__}: {e}'

    orfaos = {}
    for c in orfas:
        orfaos[c['_nome']] = orfaos.get(c['_nome'], 0) + 1

    return {
        'conta': conta.slug,
        'rotulo': conta.rotulo,
        'cobra': conta.produtos,
        'cobrancas_abertas_asaas': len(cobrancas),
        'parcelas_candidatas': len(parcelas),
        'clientes_ligados': len(apelido),
        'casadas': len(pares),
        'ja_estavam_certas': iguais,
        'gravadas': len(escritas),
        'limpas': len(limpas),
        'sem_casamento': len(ambiguas),
        'nomes_ligados_por_aproximacao': [{'central': c, 'asaas': a} for c, a in ligados],
        'orfas_gravadas': 0 if (DRY or erro_orfas) else len(linhas),
        'erro_ao_gravar_orfas': erro_orfas,
        'asaas_sem_parcela_na_central': sorted(
            ({'nome': n, 'cobrancas': qtd} for n, qtd in orfaos.items()),
            key=lambda x: -x['cobrancas']),
        'detalhe_orfas': sorted(
            ({'nome': l['nome_asaas'], 'venc': l['vencimento'], 'valor': l['valor'],
              'status': l['status_asaas'], 'tem_venda': l['tem_venda_na_central'],
              'na_central': l['nome_na_central'], 'asaas': l['asaas_payment_id']}
             for l in linhas), key=lambda x: (x['venc'] or '', x['nome'])),
        'detalhe_gravadas': escritas,
        'detalhe_sem_casamento': [
            {'cliente': p['_cliente'], 'venc': str(p['_venc']), 'valor': p['_valor'],
             'cobrancas_no_asaas': n} for p, n in ambiguas],
    }


def main():
    ativas = contas_ativas()
    if not ativas:
        raise SystemExit('Nenhuma chave de Asaas no ambiente ('
                         + ', '.join(c.env for c in CONTAS)
                         + ') — sem chave não há o que buscar.')
    slugs = {c.slug for c in ativas}

    # ── lado Central ──
    vendas = {v['id']: v for v in q('vendas?select=id,nome_cliente,gateway,produto')}
    todas = q('pagamentos_venda?status=eq.pendente&select=*')
    parcelas = {s: [] for s in slugs}
    pulou_por_falta_de_chave = 0
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
        slug = conta_da_venda(v.get('produto'))
        if slug not in slugs:
            # Conta sem chave nesta rodada. A parcela nem entra: sem consultar o
            # gateway não dá pra casar, e muito menos pra limpar link que existe.
            pulou_por_falta_de_chave += 1
            continue
        parcelas[slug].append(p)

    agora = datetime.now(timezone.utc).isoformat()
    por_conta = []
    for conta in ativas:
        do_produto = {i: v for i, v in vendas.items()
                      if conta_da_venda(v.get('produto')) == conta.slug}
        por_conta.append(processar_conta(conta, parcelas[conta.slug], do_produto, agora))

    somar = lambda campo: sum(r[campo] for r in por_conta)
    print(json.dumps({
        'data': str(hoje_sp()),
        'dry': DRY,
        'contas_consultadas': [c.slug for c in ativas],
        # Conta sem secret não é "conta sem cobrança": o relatório precisa dizer
        # em voz alta o que não foi olhado, senão a ausência vira conclusão.
        'contas_sem_chave': [{'conta': c.slug, 'secret': c.env, 'cobra': c.produtos}
                             for c in CONTAS if c.slug not in slugs],
        'parcelas_puladas_sem_chave': pulou_por_falta_de_chave,
        'cobrancas_abertas_asaas': somar('cobrancas_abertas_asaas'),
        'parcelas_candidatas': somar('parcelas_candidatas'),
        'casadas': somar('casadas'),
        'gravadas': somar('gravadas'),
        'limpas': somar('limpas'),
        'sem_casamento': somar('sem_casamento'),
        'orfas_gravadas': somar('orfas_gravadas'),
        'por_conta': por_conta,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
