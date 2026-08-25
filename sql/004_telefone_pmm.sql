-- Telefone do cliente, puxado da base de clientes do PMM.
--
-- POR QUE ISSO EXISTE: quem opera as Cobranças precisa ligar/mandar WhatsApp pro
-- cliente da parcela sem sair da Central e sem procurar o número em outro sistema.
-- O telefone não é da Central — ele vive na base do PMM e chega pela rota
-- /api/pmm-clientes (campo "contato"). Consultar essa rota a cada abertura do
-- calendário seria uma chamada externa por card, então o número é resolvido UMA
-- vez, na criação da venda, e fica gravado aqui.
--
-- telefone_pmm_status explica POR QUE o telefone está vazio — sem ele não dá pra
-- distinguir "ninguém buscou ainda" de "buscou e o PMM não tem":
--
--   null             → nunca foi buscado. Venda antiga (anterior a esta feature) ou
--                      a chamada falhou por rede. O card mostra "Buscar telefone".
--   'encontrado'     → exatamente um cliente bateu com o nome; telefone_pmm preenchido.
--   'nao_encontrado' → a base do PMM não tem ninguém com esse nome.
--   'ambiguo'        → mais de um cliente bate com o nome. NÃO gravamos telefone:
--                      chutar qual é dá cobrança no número do cliente errado.
--                      O card oferece rebuscar digitando o nome exato.

alter table vendas
  add column if not exists telefone_pmm        text,
  add column if not exists telefone_pmm_status text;

-- O botão "Buscar telefone" do card e qualquer varredura futura de vendas antigas
-- procuram pelas que ainda não foram resolvidas.
create index if not exists idx_vendas_telefone_pmm_pendente
  on vendas (id)
  where telefone_pmm_status is null;
