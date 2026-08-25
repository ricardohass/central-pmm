-- Nome do cadastro do PMM de onde o telefone veio.
--
-- POR QUE ISSO EXISTE: o telefone só vale se for da pessoa certa, e os dois
-- sistemas nem sempre chamam o cliente do mesmo jeito. A venda costuma estar no
-- nome da pessoa e o cadastro do PMM no nome da empresa — foi assim com a Marcia
-- Donadussi, cadastrada no gateway como "MD Clínica Médica Dermatológica". Sem
-- guardar o nome de lá, ninguém tem como perceber que o número na tela pertence a
-- outro cadastro antes de mandar a cobrança.
--
-- Guardado só quando telefone_pmm_status='encontrado' (nos outros estados não há
-- cliente escolhido). O card de Cobranças compara com vendas.nome_cliente,
-- ignorando acento, caixa e espaço sobrando, e só avisa quando os dois divergem —
-- confirmar o que já bate seria ruído em cima de toda parcela.

alter table vendas
  add column if not exists nome_pmm text;
