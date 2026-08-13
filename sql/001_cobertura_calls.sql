-- ============================================================================
-- COBERTURA DE GRAVAÇÃO DAS CALLS  (agenda do Google  x  Meetrox)
--
-- Objetivo: saber se toda call que aconteceu foi gravada pelo Meetrox, e
-- quando não foi, separar "o bot falhou" de "o lead não apareceu".
--
-- Quem escreve: job no GitHub Actions (cobertura-calls), 1x por dia.
-- Quem lê: aba Cobertura na Central.
--
-- Rodar uma vez no SQL Editor do Supabase. É idempotente.
-- ============================================================================


-- ── 1. Quem é auditado ──────────────────────────────────────────────────────
-- Lista fica em tabela, não no código: incluir o estrategista006 é um INSERT,
-- sem mexer no job nem republicar a Central.
create table if not exists agendas_closers (
  email        text primary key,
  nome         text not null,          -- tem que bater com vendas.closer da Central
  ativo        boolean not null default true,
  inicio_em    date,                   -- não audita nada antes disso (ex: entrou no time depois)
  criado_em    timestamptz not null default now()
);

comment on column agendas_closers.nome is
  'Nome exatamente como aparece em vendas.closer e registros_performance — é a chave de ligação com o resto da Central.';

insert into agendas_closers (email, nome) values
  ('estrategista001@grupoprooficial.com', 'Amanda Duarte'),
  ('estrategista002@grupoprooficial.com', 'Gabriel Rocha'),
  ('estrategista003@grupoprooficial.com', 'Caroline Neiva'),
  ('estrategista004@grupoprooficial.com', 'Janaina Xavier'),
  ('estrategista005@grupoprooficial.com', 'Lígia Oliveira')
on conflict (email) do nothing;


-- ── 2. O resultado do cruzamento ────────────────────────────────────────────
-- Uma linha por call. A unidade é o EVENTO da agenda; quando a call existe no
-- Meetrox mas não na agenda, a linha nasce do lado do Meetrox.
create table if not exists cobertura_calls (
  -- chave de upsert montada pelo job: 'ev:<google_event_id>' ou 'mr:<meetrox_id>'
  chave              text primary key,

  closer             text not null,
  closer_email       text,
  data               date not null,            -- dia em America/Sao_Paulo
  inicio             timestamptz,
  fim                timestamptz,
  titulo             text,

  -- chave de cruzamento: código do Meet (ex: 'nwz-fwmb-zgc').
  -- 206/206 calls do Meetrox têm; casa com hangoutLink do evento.
  meet_code          text,

  -- lado agenda
  evento_id          text,
  convidados_ext     int,                      -- convidados fora de @grupoprooficial.com
  evento_cancelado   boolean not null default false,
  closer_recusou     boolean not null default false,

  -- lado Meetrox
  meetrox_call_id    bigint,
  meetrox_url        text,
  gravada            boolean not null default false,
  duracao_gravacao   int,                      -- segundos

  -- lado log de auditoria do Meet (quem realmente entrou)
  meet_entrou_closer boolean,
  meet_entrou_ext    boolean,
  meet_dur_ext_seg   int,                      -- tempo do participante externo
  meet_apurado       boolean not null default false,  -- false = log indisponível

  -- veredito do job
  status             text not null,
  motivo             text,

  -- correção humana: quando preenchido, manda no relatório
  status_manual      text,
  status_manual_por  text,
  status_manual_em   timestamptz,

  atualizado_em      timestamptz not null default now(),

  constraint cobertura_status_ok check (status in (
    'ok',              -- aconteceu e o Meetrox gravou
    'sem_gravacao',    -- aconteceu e NÃO gravou  → falha do bot
    'no_show',         -- só o closer entrou      → não conta contra ninguém
    'nao_aconteceu',   -- ninguém entrou
    'fora_da_agenda',  -- gravou mas não havia evento
    'indeterminado'    -- sem log de auditoria pra decidir
  )),
  constraint cobertura_status_manual_ok check (status_manual is null or status_manual in (
    'ok','sem_gravacao','no_show','nao_aconteceu','fora_da_agenda'
  ))
);

comment on table  cobertura_calls is 'Cruzamento agenda x Meetrox x log do Meet. Escrita pelo job cobertura-calls (GitHub Actions).';
comment on column cobertura_calls.meet_apurado is 'false quando o log de auditoria do Meet não estava disponível — nesse caso status fica indeterminado e precisa de classificação manual.';
comment on column cobertura_calls.status_manual is 'Correção humana. Quando preenchido, prevalece sobre status nos relatórios.';

create index if not exists idx_cobertura_data       on cobertura_calls (data desc);
create index if not exists idx_cobertura_closer_dia on cobertura_calls (closer, data desc);
create index if not exists idx_cobertura_status     on cobertura_calls (status);
create index if not exists idx_cobertura_meetcode   on cobertura_calls (meet_code);


-- ── 3. Leitura pronta pra tela ──────────────────────────────────────────────
-- status_final = correção humana quando existe, senão o veredito do job.
create or replace view cobertura_calls_v as
select
  c.*,
  coalesce(c.status_manual, c.status) as status_final,
  (coalesce(c.status_manual, c.status) = 'sem_gravacao') as e_falha_bot
from cobertura_calls c;

-- Resumo por closer/dia — é o que a aba Cobertura vai consumir.
create or replace view cobertura_resumo_v as
select
  closer,
  data,
  count(*) filter (where status_final in ('ok','sem_gravacao'))  as calls_realizadas,
  count(*) filter (where status_final = 'ok')                    as gravadas,
  count(*) filter (where status_final = 'sem_gravacao')          as sem_gravacao,
  count(*) filter (where status_final = 'no_show')               as no_shows,
  count(*) filter (where status_final = 'fora_da_agenda')        as fora_da_agenda,
  count(*) filter (where status_final = 'indeterminado')         as indeterminados,
  round(
    100.0 * count(*) filter (where status_final = 'ok')
    / nullif(count(*) filter (where status_final in ('ok','sem_gravacao')), 0)
  , 1) as pct_cobertura
from cobertura_calls_v
group by closer, data;

-- NOTA DE SEGURANÇA: estas tabelas seguem o mesmo modelo do resto do banco,
-- que hoje está sem RLS. Não estou introduzindo política só aqui pra não criar
-- um modelo inconsistente — a correção precisa ser feita no banco inteiro,
-- de uma vez. Fica registrado que este dado (título de reunião e e-mail de
-- lead) é legível por qualquer um com a anon key, igual ao restante.
