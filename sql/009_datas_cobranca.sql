-- Agenda das três tentativas de cobrança de cada parcela.
--
-- POR QUE ISSO EXISTE: a parcela já guardava SE cada cobrança foi feita
-- (cobranca_1/2/3), mas não QUANDO ela deveria acontecer. Sem data, a operadora
-- cobrava por impulso — várias vezes na mesma semana em quem não ia pagar, e
-- nenhuma em quem tinha esquecido. Gasta o dia e não traz dinheiro.
--
-- A regra combinada: quem paga, paga na 1ª ou na 2ª tentativa; quem não paga
-- precisa de um intervalo maior antes da 3ª, que é a incisiva.
--   1ª → no vencimento     2ª → vencimento + 2     3ª → vencimento + 10
--
-- As datas são SUGESTÃO calculada na tela, a partir do vencimento e sempre a
-- partir dele (nunca em cascata: adiar a 2ª não empurra a 3ª). Estas colunas
-- guardam só o OVERRIDE: enquanto ficam nulas, a tela mostra a sugestão; assim
-- que alguém digita uma data, a manual passa a mandar naquela tentativa e o
-- cálculo não a reescreve mais. Mexer numa não afeta as outras duas.
--
-- Sem rodar isto, a tela continua funcionando com as datas sugeridas — só o
-- ajuste manual é que avisa que a coluna não existe.

alter table pagamentos_venda
  add column if not exists cobranca_1_data date,
  add column if not exists cobranca_2_data date,
  add column if not exists cobranca_3_data date;
