// Somente leitura (Stripe e SQLite local). Não imprime segredos nem dados de clientes.
import { DatabaseSync } from 'node:sqlite'
import Stripe from 'stripe'

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY, { maxNetworkRetries: 1, timeout: 15_000 })
const OURS = 'https://getclipper.howto.software/api/stripe/webhook'
const { data } = await stripe.webhookEndpoints.list({ limit: 20 })
const ours = data.find(e => e.url === OURS)
console.log(`nosso endpoint criado em ${new Date(ours.created * 1000).toISOString()}`)
for (const e of data.filter(x => x.url !== OURS)) {
  const events = e.enabled_events
  console.log(`outro endpoint (${new URL(e.url).host}): ${events.includes('*') ? 'TODOS os eventos (*)' : `${events.length} eventos`}; checkout.session.expired: ${events.includes('*') || events.includes('checkout.session.expired') ? 'SIM' : 'não'}`)
}
// Eventos da conta desde a criação do nosso endpoint, dos tipos que ele assina
const types = ours.enabled_events.includes('*') ? null : ours.enabled_events
let total = 0, pending = 0
const byType = {}
for await (const ev of stripe.events.list({ created: { gte: ours.created }, limit: 100, ...(types ? { types: types.slice(0, 20) } : {}) })) {
  total++
  byType[ev.type] = (byType[ev.type] ?? 0) + 1
  if (ev.pending_webhooks > 0) pending++
  if (total >= 300) break
}
console.log(`eventos assinados desde então: ${total} (com entrega pendente em algum endpoint: ${pending}) ${JSON.stringify(byType)}`)
const db = new DatabaseSync('/data/commercial.sqlite', { readOnly: true })
const rows = db.prepare('SELECT type, COUNT(*) AS n, MIN(received_at) AS first, MAX(received_at) AS last FROM stripe_events GROUP BY type').all()
console.log(`eventos recebidos e com assinatura válida no nosso banco: ${rows.length ? JSON.stringify(rows) : 'nenhum'}`)
