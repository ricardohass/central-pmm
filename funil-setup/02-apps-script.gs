/**
 * FUNIL COMERCIAL PMM — envio da planilha diária para a Central (Supabase)
 *
 * Como instalar:
 *  1. Abra a planilha do funil → Extensões → Apps Script
 *  2. Cole este arquivo inteiro e salve
 *  3. Rode "enviarFunilParaCentral" uma vez e autorize o acesso
 *  4. (opcional) Acionar → Adicionar acionador → enviarFunilParaCentral,
 *     baseado em tempo, diário (ex.: 07h) para subir sozinho todo dia
 *
 * A planilha precisa ter uma aba chamada "Funil" com o cabeçalho EXATO (linha 1):
 *  A: Data | B: Leads recebidos | C: Aplicação | D: Social Selling |
 *  E: Febracis | F: Indicação | G: Método CIS | H: Gratuidade |
 *  I: Mentoria Prø | J: Qualificados | K: Contatados | L: Respostas
 */

const SUPA_URL = 'https://ebcydqqhvdapruhnwbce.supabase.co';
const SUPA_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImViY3lkcXFodmRhcHJ1aG53YmNlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk5ODMyNjIsImV4cCI6MjA5NTU1OTI2Mn0.0ORi9FRZVSt6WOb3Q3VxVYz2UkXGRCbZmq2BU50XJF0';
const ABA = 'Funil';
const CANAIS = ['Aplicação','Social Selling','Febracis','Indicação','Método CIS','Gratuidade','Mentoria Prø'];

function enviarFunilParaCentral() {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(ABA);
  if (!sh) { throw new Error('Aba "' + ABA + '" não encontrada.'); }
  const rows = sh.getDataRange().getValues();
  rows.shift(); // remove cabeçalho

  const payload = [];
  rows.forEach(function (r) {
    if (!r[0]) return;               // pula linhas sem data
    const origem = {};
    CANAIS.forEach(function (c, i) { origem[c] = Number(r[2 + i]) || 0; });
    payload.push({
      data:               formatarData(r[0]),
      leads_recebidos:    Number(r[1])  || 0,
      leads_origem:       origem,
      leads_qualificados: Number(r[9])  || 0,
      leads_contatados:   Number(r[10]) || 0,
      respostas:          Number(r[11]) || 0
    });
  });

  if (!payload.length) { Logger.log('Nada para enviar.'); return; }

  const res = UrlFetchApp.fetch(SUPA_URL + '/rest/v1/funil_prevendas', {
    method: 'post',
    contentType: 'application/json',
    headers: {
      apikey: SUPA_KEY,
      Authorization: 'Bearer ' + SUPA_KEY,
      Prefer: 'resolution=merge-duplicates'   // upsert por data (não duplica)
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });

  const code = res.getResponseCode();
  Logger.log(code + ' — ' + res.getContentText());
  if (code >= 300) { throw new Error('Falha ao enviar (' + code + '): ' + res.getContentText()); }
  Logger.log('OK — ' + payload.length + ' dia(s) enviado(s).');
}

function formatarData(d) {
  if (Object.prototype.toString.call(d) === '[object Date]') {
    return Utilities.formatDate(d, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  }
  return String(d).trim(); // aceita texto já no formato yyyy-MM-dd
}

// Cria um menu "Central PMM" na planilha para enviar manualmente
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Central PMM')
    .addItem('Enviar funil para a Central', 'enviarFunilParaCentral')
    .addToUi();
}
