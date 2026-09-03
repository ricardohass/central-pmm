-- Próximo touch point de cada pendência da guia de Pendências.
--
-- POR QUE ISSO EXISTE: a guia calcula as pendências ao vivo, cruzando vendas e
-- pagamentos — nada de pendência é gravado, senão a lista envelhece e passa a
-- cobrar coisa que já foi resolvida. O que precisa sobreviver ao recarregamento
-- é só a decisão de gestão em cima da pendência: quando voltar a olhar aquilo e
-- por quê. É isso que esta tabela guarda.
--
-- A chave é montada na tela e identifica a pendência, não a linha:
--   cadastro:<venda_id>:<tipo>     tipo = gateway | sdr | comprovante | juridico | central
--   cronograma:<venda_id>          venda sem nenhuma parcela cadastrada
--   nada_recebido:<venda_id>       cronograma montado, zero recebido
--   cobranca:<pagamento_id>        parcela vencida e não paga
--
-- Resolvida a pendência na origem (o campo foi preenchido, a parcela entrou),
-- ela some da guia sozinha e a linha daqui vira órfã — inofensiva, e o histórico
-- fica disponível se a mesma pendência voltar a aparecer.

create table if not exists pendencias_touchpoint (
  chave              text primary key,
  proximo_touchpoint date,
  observacao         text,
  atualizado_em      timestamptz default now(),
  atualizado_por     text
);

-- A guia ordena por touch point vencido, então o índice acompanha a consulta.
create index if not exists pendencias_touchpoint_data_idx
  on pendencias_touchpoint (proximo_touchpoint);
