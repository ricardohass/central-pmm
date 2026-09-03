-- Dispensa: tirar uma pendência da fila sem que ela tenha sido resolvida.
--
-- POR QUE ISSO EXISTE: parte do que a guia acusa é passivo que ninguém vai
-- correr atrás — venda antiga, cliente que já não é da casa, campo que não vale
-- mais a pena preencher. Sem uma saída para esses casos, a fila vira ruído e
-- para de ser olhada, que é exatamente o problema que ela veio resolver.
--
-- Não é o mesmo que resolver: a pendência continua existindo na origem, só
-- deixa de aparecer. Por isso guarda quem dispensou, quando e por quê — e por
-- isso dá para reativar, zerando dispensada_em.

alter table pendencias_touchpoint
  add column if not exists dispensada_em   timestamptz,
  add column if not exists dispensada_por  text,
  add column if not exists dispensa_motivo text;

create index if not exists pendencias_touchpoint_dispensa_idx
  on pendencias_touchpoint (dispensada_em);
