// payment_check com chave e handle INVÁLIDOS (comparação): mostra se o teste anterior distingue chave válida
import { INFINITEPAY_API, infinitePaySettings } from './server/infinitepay.js'
const ip = infinitePaySettings()
for (const [label, apiKey, handle] of [['chave inválida, handle real', 'inchk_chaveinvalida000', ip.handle], ['chave real, handle inexistente', ip.apiKey, 'asc-handle-inexistente-x'], ['chave real, handle real', ip.apiKey, ip.handle]]) {
  const r = await fetch(`${INFINITEPAY_API}/payment_check`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Api-Key': apiKey }, body: JSON.stringify({ handle, order_nsu: 'asc-00000000-0000-4000-8000-000000000000', transaction_nsu: 'asc-validacao', slug: 'asc-validacao' }), signal: AbortSignal.timeout(10_000) })
  const body = await r.json().catch(() => ({}))
  console.log(`${label}: HTTP ${r.status} ${JSON.stringify(Object.fromEntries(Object.entries(body).map(([k, v]) => [k, typeof v === 'string' ? v.slice(0, 60) : v])))}`)
}
