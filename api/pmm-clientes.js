export default async function handler(req, res) {
  const { nome, id, cpf, cnpj } = req.query;
  if (!nome && !id && !cpf && !cnpj) {
    return res.status(400).json({ ok: false, erro: 'informe nome, id, cpf ou cnpj' });
  }
  const params = new URLSearchParams({
    fields: 'id,nome,cpf,cnpj,email,contato,cep,end_rua,end_numero,end_cidade,end_estado',
    limit: '50'
  });
  if (nome) params.set('nome', nome);
  if (id) params.set('id', id);
  // Documento é chave forte: dois clientes podem ter o mesmo nome, nunca o mesmo CPF.
  if (cpf) params.set('cpf', cpf);
  if (cnpj) params.set('cnpj', cnpj);

  const r = await fetch(`https://pmmcentral.entrepro.com.br/api/v1/clientes?${params}`, {
    headers: { Authorization: `Bearer ${process.env.PMM_TOKEN}` }
  });
  const data = await r.json();
  res.status(r.status).json(data);
}
