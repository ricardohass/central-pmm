-- Documento do cliente na venda (CPF ou CNPJ).
--
-- POR QUE ISSO EXISTE: até aqui a venda só guardava o NOME do cliente, e nome é
-- chave fraca. Dois clientes podem se chamar "João Silva" — nenhum deles tem o
-- mesmo CPF. Sem documento, casar a venda com o cadastro do PMM (telefone) ou com
-- a cobrança do Asaas depende de bater string de nome, que erra dos dois lados:
-- não acha quando os sistemas escrevem diferente, e casa errado quando coincidem.
-- O código já reconhecia esse buraco no casamento do Asaas ("a venda não guarda
-- CPF nem e-mail, então o que fica ambíguo não casa").
--
-- Guardado SÓ EM DÍGITOS, sem ponto, barra ou traço — é o formato que as APIs
-- esperam e o único que compara de forma confiável. 11 dígitos = CPF, 14 = CNPJ.
--
-- Preenchimento é opcional e vai acontecendo nas vendas novas; o que já existe
-- continua casando por nome, como antes.

alter table vendas
  add column if not exists cpf_cnpj text,
  -- Por qual chave o telefone foi casado: 'documento' ou 'nome'. Muda o que o card
  -- diz quando o nome do PMM é diferente do nome da venda. Casou por CPF: nome
  -- diferente é só a empresa do cliente, e o card informa sem alarme. Casou por
  -- nome: nada confirma que é a mesma pessoa, e aí o alerta é vermelho.
  add column if not exists telefone_pmm_via text;

-- Casamento por documento precisa ser barato.
create index if not exists idx_vendas_cpf_cnpj
  on vendas (cpf_cnpj)
  where cpf_cnpj is not null;
