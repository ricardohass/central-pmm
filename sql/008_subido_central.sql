-- "Subido na Central" no lugar de "Contrato assinado" na guia de Vendas.
--
-- POR QUE ISSO EXISTE: as duas marcações da tabela de vendas existem para
-- acompanhar o que ainda falta em cada venda fechada. "Contrato assinado"
-- deixou de ser o passo que o time precisa enxergar; o que interessa agora é
-- se os dados da venda já foram subidos na Central.
--
-- A coluna contrato_assinado NÃO é apagada: ela guarda o histórico do que já
-- foi marcado e simplesmente sai da tela. Se um dia a assinatura voltar a ser
-- acompanhada, o dado antigo continua lá.
--
-- BACKFILL: quem já tinha as infos enviadas ao jurídico também já teve os
-- dados subidos na Central — a decisão é do Ricardo, e vale só para o que
-- existe hoje. As demais vendas ficam em branco, para o time marcar à mão.

alter table vendas
  add column if not exists subido_central boolean not null default false;

update vendas
   set subido_central = true
 where contrato_enviado is true
   and subido_central is false;
