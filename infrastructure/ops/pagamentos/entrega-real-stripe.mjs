// Prova de entrega real Stripe -> Cloudflare -> Caddy -> asc-commercial, com a assinatura verificada pelo
// STRIPE_WEBHOOK_SECRET. Sem dinheiro e sem tocar no outro projeto da conta:
//   - só o NOSSO endpoint passa a assinar customer.created/customer.deleted, temporariamente (restaurado no finally);
//   - um cliente descartável (sem e-mail) é criado e apagado;
//   - o serviço só grava o id do evento em stripe_events DEPOIS de validar a assinatura.
import { DatabaseSync } from 'node:sqlite'
import Stripe from 'stripe'

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY, { maxNetworkRetries: 2, timeout: 20_000 })
const OURS = 'https://getclipper.howto.software/api/stripe/webhook'
const EXTRA = ['customer.created', 'customer.deleted']
const { data } = await stripe.webhookEndpoints.list({ limit: 20 })
const ours = data.find(e => e.url === OURS)
const others = data.filter(e => e.url !== OURS)
for (const o of others) {
  if (o.enabled_events.includes('*') || EXTRA.some(t => o.enabled_events.includes(t))) { console.log('ABORTADO: outro endpoint assina estes eventos'); process.exit(1) }
}
const original = [...ours.enabled_events]
const sameSet = (a, b) => a.length === b.length && a.every(x => b.includes(x))
let customer
try {
  await stripe.webhookEndpoints.update(ours.id, { enabled_events: [...new Set([...original, ...EXTRA])] })
  console.log(`endpoint temporariamente com +${EXTRA.join(', ')}`)
  customer = await stripe.customers.create({ name: 'ASC verificação de webhook (descartável)', metadata: { asc_selftest: 'webhook' } })
  await stripe.customers.del(customer.id)
  console.log('cliente descartável criado e apagado')
  const db = new DatabaseSync('/data/commercial.sqlite', { readOnly: true })
  const deadline = Date.now() + 90_000
  let found = []
  let events = []
  while (Date.now() < deadline) {
    events = []
    for (const type of EXTRA) {
      const list = await stripe.events.list({ type, limit: 10, created: { gte: Math.floor(Date.now() / 1000) - 600 } })
      events.push(...list.data.filter(ev => ev.data.object.id === customer.id))
    }
    found = events.filter(ev => db.prepare('SELECT 1 FROM stripe_events WHERE id = ?').get(ev.id))
    if (events.length === EXTRA.length && found.length === events.length && events.every(ev => ev.pending_webhooks === 0)) break
    await new Promise(r => setTimeout(r, 3000))
  }
  for (const ev of events) console.log(`  ${ev.type}: ${found.some(f => f.id === ev.id) ? 'RECEBIDO e assinatura válida' : 'NÃO recebido'}; entregas pendentes no Stripe: ${ev.pending_webhooks}`)
  console.log(events.length === EXTRA.length && found.length === events.length ? 'ENTREGA REAL DO STRIPE OK' : 'ENTREGA REAL DO STRIPE FALHOU')
  db.close()
} finally {
  await stripe.webhookEndpoints.update(ours.id, { enabled_events: original })
  const after = await stripe.webhookEndpoints.retrieve(ours.id)
  console.log(`endpoint restaurado: ${sameSet(after.enabled_events, original) ? 'sim, mesmos ' + original.length + ' eventos' : 'NÃO, conferir'} (status ${after.status})`)
}
