-- Link da cobrança (Asaas) por parcela.
--
-- A operadora precisa do link de pagamento na mão, dentro da aba Cobranças, sem
-- ter que entrar no Asaas toda vez. O link vem por dois caminhos:
--
--   'asaas'  → preenchido pelo job .github/scripts/asaas_links.py (GitHub Actions),
--              que casa a parcela com a cobrança do Asaas e grava o invoiceUrl.
--   'manual' → colado à mão no card da parcela. NUNCA é sobrescrito pelo job:
--              se alguém colou, é porque o automático errou ou não achou.
--
-- asaas_payment_id guarda o id da cobrança no Asaas (pay_xxx) pro job saber que
-- aquela cobrança já foi consumida e não casá-la com uma segunda parcela.

alter table pagamentos_venda
  add column if not exists link_cobranca      text,
  add column if not exists asaas_payment_id   text,
  add column if not exists link_origem        text,
  add column if not exists link_atualizado_em timestamptz;

-- O job consulta por asaas_payment_id pra não duplicar casamento entre execuções.
create index if not exists idx_pagamentos_venda_asaas_payment
  on pagamentos_venda (asaas_payment_id)
  where asaas_payment_id is not null;
