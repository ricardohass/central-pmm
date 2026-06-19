-- =====================================================================
-- FUNIL COMERCIAL PMM — criação das 2 tabelas no Supabase
-- Rode este SQL no Supabase → SQL Editor → New query → Run
-- =====================================================================

-- 1) Registro DIÁRIO do topo de funil (agregado do time de pré-vendas)
create table if not exists public.funil_prevendas (
  id                 bigint generated always as identity primary key,
  data               date    not null unique,         -- 1 linha por dia (chave do upsert)
  leads_recebidos    integer not null default 0,
  leads_qualificados integer not null default 0,
  leads_contatados   integer not null default 0,
  respostas          integer not null default 0,
  leads_origem       jsonb   not null default '{}',   -- {"Aplicação":12,"Social Selling":8,...}
  created_at         timestamptz not null default now()
);

-- 2) Metas do funil (por mês): meta de leads em nº + 6 taxas-alvo em %
create table if not exists public.metas_funil (
  id                  bigint generated always as identity primary key,
  ano                 integer not null,
  mes                 integer not null,               -- 1-12
  meta_faturamento    numeric not null default 0,     -- meta final do mês (R$)
  meta_leads          integer not null default 0,
  taxa_qualificacao   numeric not null default 60,
  taxa_contato        numeric not null default 80,
  taxa_resposta       numeric not null default 50,
  taxa_agendamento    numeric not null default 40,
  taxa_comparecimento numeric not null default 80,
  taxa_fechamento     numeric not null default 25,
  created_at          timestamptz not null default now(),
  unique (ano, mes)                                    -- chave do upsert
);

-- Permissões para a central (mesmo padrão das demais tabelas, via anon key)
grant select, insert, update, delete on public.funil_prevendas to anon, authenticated;
grant select, insert, update, delete on public.metas_funil      to anon, authenticated;
grant usage, select on all sequences in schema public to anon, authenticated;

-- Para instalações que já criaram metas_funil antes da meta de faturamento:
alter table public.metas_funil
  add column if not exists meta_faturamento numeric not null default 0;
