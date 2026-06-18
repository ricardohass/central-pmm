# Funil Comercial — passo a passo de ativação

Fluxo: **pré-vendas preenche a planilha diariamente → Apps Script joga na Central → aba Funil mostra tudo com metas.**

## 1. Criar as tabelas no banco (uma vez só)
1. Entre no Supabase → **SQL Editor** → **New query**
2. Cole o conteúdo de [`01-supabase-tabelas.sql`](01-supabase-tabelas.sql) e clique **Run**
3. Deve aparecer "Success". Cria `funil_prevendas` e `metas_funil`.

## 2. Montar a planilha de coleta diária
Crie uma planilha no Google Sheets com uma aba chamada **`Funil`** e este cabeçalho na **linha 1** (exatamente nesta ordem):

| A | B | C | D | E | F | G | H | I | J | K | L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Data | Leads recebidos | Aplicação | Social Selling | Febracis | Indicação | Método CIS | Gratuidade | Mentoria Prø | Qualificados | Contatados | Respostas |

- **Data**: formato data (uma linha por dia).
- **Leads recebidos**: total do dia.
- **Colunas C–I**: quantos leads vieram de cada canal (a soma idealmente bate com "Leads recebidos").
- **Qualificados / Contatados / Respostas**: números do dia.

> Os números de **Agenda marcada, Calls realizadas e Vendas** NÃO entram aqui — já vêm do registro diário dos closers e da aba Vendas.

## 3. Conectar a planilha à Central
1. Na planilha: **Extensões → Apps Script**
2. Cole o conteúdo de [`02-apps-script.gs`](02-apps-script.gs) e salve
3. Rode `enviarFunilParaCentral` uma vez e **autorize**
4. Para subir sozinho todo dia: **Acionadores (relógio) → Adicionar acionador →** função `enviarFunilParaCentral`, **baseado em tempo**, diário (ex.: 07h)
5. Também aparece um menu **"Central PMM → Enviar funil para a Central"** para envio manual

O envio é *upsert por data*: reenviar o mesmo dia **atualiza** em vez de duplicar.

## 4. Definir as metas (na Central)
Aba **Funil → botão "🎯 Metas do funil"**: defina a **meta de leads do mês (nº)** e as **6 taxas-alvo (%)**. A Central calcula sozinha a meta absoluta de cada etapa (cascata) e a meta de vendas.

## 5. Ler toda segunda até 10h
Aba **Funil**, seletor de período:
- **Mês corrente** (navega entre meses) — para o fechamento do mês;
- **Semana anterior** — a última semana fechada (seg–dom), para o ritual de segunda.

---

### Observações de cálculo
- As 4 etapas de topo (leads, qualificados, contatados, respostas) vêm da planilha.
- **Agenda marcada** e **Calls realizadas** vêm de `registros_performance` (registro dos closers).
- **Vendas / faturamento / ticket** vêm da aba Vendas (exclui canceladas).
- Na visão **semanal**, a meta de leads é a do mês ÷ nº de semanas (~4,3). Dá para refinar depois para meta semanal própria, se quiser.
