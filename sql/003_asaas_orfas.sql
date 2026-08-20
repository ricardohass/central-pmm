-- Cobranças abertas no Asaas que não correspondem a nenhuma parcela da Central.
--
-- POR QUE ISSO EXISTE: o job .github/scripts/asaas_links.py já sabia calcular
-- essa lista desde 17/08/2026, mas só a imprimia no log do GitHub Actions e
-- jogava fora. Resultado prático: ninguém conseguia consultar sem abrir o log,
-- não dava pra acompanhar se o buraco crescia ou fechava, e decisão acabava
-- sendo tomada em cima de anotação velha — em 20/08/2026 uma cobrança legítima
-- da Marcia Donadussi (cadastrada no Asaas como "MD Clínica Médica
-- Dermatológica") foi excluída por constar numa lista de três dias atrás.
--
-- Agora cada rodada grava o retrato do momento aqui.
--
-- SEMÂNTICA DE SNAPSHOT: a tabela reflete exatamente o que está aberto no Asaas
-- sem contrapartida na Central AGORA. Cobrança que foi cadastrada, paga ou
-- excluída some da tabela na rodada seguinte. Quem quiser histórico consulta
-- `primeira_vez_em`, que sobrevive às regravações.

create table if not exists asaas_orfas (
  asaas_payment_id  text primary key,
  nome_asaas        text not null,
  asaas_customer_id text,
  valor             numeric(12,2),
  vencimento        date,
  status_asaas      text,
  descricao         text,
  invoice_url       text,

  -- A distinção que importa na operação, resolvida pelo mesmo casamento por
  -- token que o job usa pros links (inclusive os apelidos manuais):
  --   true  → existe venda na Central, mas sem parcela pendente que case.
  --           Cronograma faltando ou incompleto. É venda de verdade.
  --   false → não existe venda nenhuma com esse nome. Ou é de outro
  --           produto/negócio que nunca deveria estar aqui, ou ninguém cadastrou.
  tem_venda_na_central boolean not null default false,
  nome_na_central      text,

  -- Quando essa cobrança apareceu órfã pela primeira vez. NÃO é reescrito nas
  -- rodadas seguintes (o upsert do job não manda essa coluna), então mede há
  -- quanto tempo o buraco está aberto.
  primeira_vez_em   timestamptz not null default now(),
  -- Carimbo da rodada. O job apaga o que ficou com carimbo anterior ao dele,
  -- que é como a tabela vira snapshot em vez de acumular lixo.
  apurado_em        timestamptz not null default now()
);

create index if not exists idx_asaas_orfas_venc on asaas_orfas (vencimento);
create index if not exists idx_asaas_orfas_nome on asaas_orfas (nome_asaas);
