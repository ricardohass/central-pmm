-- Call recém-encerrada não é falha do Meetrox.
--
-- O Meetrox sobe, transcreve e analisa antes de publicar a call na API. A
-- apuração roda de 2 em 2 horas e alcança a call minutos depois de acabar, com
-- o bot ainda na sala: o veredito saía "o Meetrox não gerou a gravação" e se
-- desfazia sozinho na rodada seguinte. O status 'processando' segura esse
-- intervalo — fora do denominador de cobertura e fora da conta de falhas.

alter table cobertura_calls drop constraint if exists cobertura_status_ok;
alter table cobertura_calls add  constraint cobertura_status_ok check (status in (
  'ok',              -- aconteceu e o Meetrox gravou
  'sem_gravacao',    -- aconteceu e NÃO gravou  → falha do bot
  'processando',     -- acabou agora, o Meetrox ainda está subindo a gravação
  'no_show',         -- só o closer entrou      → não conta contra ninguém
  'nao_aconteceu',   -- ninguém entrou
  'fora_da_agenda',  -- gravou mas não havia evento
  'indeterminado'    -- sem log de auditoria pra decidir
));

-- A view de resumo passa a mostrar quantas estão em processamento, e elas
-- continuam fora de calls_realizadas — o desfecho ainda não existe.
create or replace view cobertura_resumo_v
with (security_invoker = true) as
select
  closer,
  data,
  count(*) filter (where status_final in ('ok','sem_gravacao'))  as calls_realizadas,
  count(*) filter (where status_final = 'ok')                    as gravadas,
  count(*) filter (where status_final = 'sem_gravacao')          as sem_gravacao,
  count(*) filter (where status_final = 'processando')           as processando,
  count(*) filter (where status_final = 'no_show')               as no_shows,
  count(*) filter (where status_final = 'fora_da_agenda')        as fora_da_agenda,
  count(*) filter (where status_final = 'indeterminado')         as indeterminados,
  round(
    100.0 * count(*) filter (where status_final = 'ok')
    / nullif(count(*) filter (where status_final in ('ok','sem_gravacao')), 0)
  , 1) as pct_cobertura
from cobertura_calls_v
group by closer, data;
