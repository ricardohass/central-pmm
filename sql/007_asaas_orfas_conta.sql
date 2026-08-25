-- De qual conta Asaas veio a cobrança órfã.
--
-- POR QUE ISSO EXISTE: até 25/08/2026 a Central falava com uma conta Asaas só, a
-- do Grupo Prø. As cobranças da Wonder Prø são emitidas em outra conta, no CNPJ
-- da Wonder — invisíveis pro job. Agora ele consulta as duas, e sem esta coluna
-- as órfãs das duas cairiam misturadas aqui, sem dizer em qual gateway a operadora
-- deve procurar a cobrança pra resolver.
--
-- Ela também é o que mantém o snapshot honesto: o job apaga o que não apareceu na
-- rodada FILTRANDO POR CONTA. Rodada que consultou só o Grupo Prø (chave da Wonder
-- ausente, API fora do ar) não pode zerar as órfãs da Wonder e fazer o buraco
-- parecer resolvido.

alter table asaas_orfas
  add column if not exists conta text not null default 'pmm';

-- O default cobre o retroativo: tudo que já está na tabela veio do Grupo Prø,
-- porque até hoje era a única conta consultada.

comment on column asaas_orfas.conta is
  'Conta Asaas de origem: pmm (Grupo Prø — PMM e 2M) ou wonder (Wonder Prø). '
  'Definida pelo produto da venda em .github/scripts/asaas_contas.py.';

-- `tem_venda_na_central` passa a valer DENTRO da conta: a busca por venda
-- correspondente só olha as vendas do produto daquela conta. Cobrança da Wonder
-- cujo nome só existe numa venda de PMM continua marcada como sem venda — é o
-- que ela é, do ponto de vista do contrato que gerou a cobrança.

create index if not exists idx_asaas_orfas_conta on asaas_orfas (conta);
