// Dentro do asc-commercial. Nada é gravado: assinatura inválida é recusada antes de qualquer escrita e o aviso
// da InfinitePay com corpo vazio termina em "unknown" antes de tocar no banco. O token nunca é impresso.
const token = process.env.INFINITEPAY_WEBHOOK_TOKEN
const direct = 'http://127.0.0.1:8790'
const pub = 'https://getclipper.howto.software'
const UAS = { 'Stripe': 'Stripe/1.0 (+https://stripe.com/docs/webhooks)', 'genérico (node)': undefined, 'navegador': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36' }
async function hit(base, path, ua, body = '{}', extra = {}) {
  const r = await fetch(`${base}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...(ua ? { 'User-Agent': ua } : {}), ...extra }, body, signal: AbortSignal.timeout(15_000) })
  const text = await r.text()
  const who = r.headers.get('server') === 'cloudflare' && r.headers.get('content-type')?.includes('html') ? 'Cloudflare' : 'site'
  let short = text.slice(0, 80).replace(/\s+/g, ' ')
  if (who === 'Cloudflare') short = (text.match(/<title>([^<]*)/)?.[1] ?? 'página HTML').trim()
  return `HTTP ${r.status} [${who}] ${short}`
}
const badSig = { 'Stripe-Signature': 't=1700000000,v1=00' }
console.log('== Stripe /api/stripe/webhook com assinatura inválida (esperado: 400 do site)')
console.log(`  direto:  ${await hit(direct, '/api/stripe/webhook', UAS.Stripe, '{}', badSig)}`)
for (const [name, ua] of Object.entries(UAS)) console.log(`  público, UA ${name}: ${await hit(pub, '/api/stripe/webhook', ua, '{}', badSig)}`)
console.log('== InfinitePay /api/infinitepay/webhook/<token>')
console.log(`  direto, token errado (esperado 404):  ${await hit(direct, '/api/infinitepay/webhook/' + 'x'.repeat(43), undefined)}`)
console.log(`  direto, token certo, aviso vazio (esperado 200 unknown):  ${await hit(direct, '/api/infinitepay/webhook/' + token, undefined)}`)
for (const [name, ua] of Object.entries(UAS)) console.log(`  público, token certo, UA ${name}: ${await hit(pub, '/api/infinitepay/webhook/' + token, ua)}`)
