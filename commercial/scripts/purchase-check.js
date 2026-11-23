// Controlled real purchase, commercial side (read-only). For one account: subscriptions and charges, Stripe events
// received, entitlement snapshots in the outbox and what the main API applied (signed /internal/billing/state).
// No e-mail, card or secret is printed.
//
//   sudo asc-ctl exec asc-commercial -- node scripts/purchase-check.js <account_id>
import { DatabaseSync } from 'node:sqlite'
import { httpStateReader, outboxSettings } from '../server/outbox.js'

const account = String(process.argv[2] ?? '')
if (!/^[1-9][0-9]{0,11}$/.test(account)) {
  console.error('usage: node scripts/purchase-check.js <account_id>')
  process.exit(2)
}
const db = new DatabaseSync(process.env.COMMERCIAL_DB ?? 'data/waitlist.sqlite', { readOnly: true })
const all = (sql, ...p) => { try { return db.prepare(sql).all(...p) } catch (error) { console.log(`ERRO de consulta: ${error.message}`); return [] } }
const iso = t => (t ? new Date(Number(t) * 1000).toISOString().replace('.000', '') : '-')
const money = (minor, cur) => `${(Number(minor) / 100).toFixed(2)} ${String(cur || '').toUpperCase()}`

console.log(`== conta ${account}: serviço comercial`)
const stripeSubs = all('SELECT subscription_id, plan, market, status, current_period_start AS period_start, current_period_end AS period_end, cancel_at_period_end AS cancel_at_end, review FROM subscriptions WHERE account_id = ?', account)
for (const s of stripeSubs) {
  console.log(`Stripe ${s.subscription_id}: ${s.plan}/${s.market} status=${s.status} período ${iso(s.period_start)} -> ${iso(s.period_end)}${s.cancel_at_end ? ' (cancela no fim)' : ''}${s.review ? ` revisão=${s.review}` : ''}`)
  for (const i of all('SELECT invoice_id, status, amount_paid AS paid, amount_due AS due, currency, last_event_at AS event_at FROM invoices WHERE subscription_id = ? ORDER BY last_event_at', s.subscription_id)) {
    console.log(`  fatura ${i.invoice_id}: ${i.status}, pago ${money(i.paid, i.currency)} de ${money(i.due, i.currency)} em ${iso(i.event_at)}`)
  }
}
const ipSubs = all('SELECT id, plan, status, paid_until FROM ip_subscriptions WHERE account_id = ?', account)
for (const s of ipSubs) {
  console.log(`InfinitePay ${s.id}: ${s.plan} status=${s.status} pago até ${iso(s.paid_until)}`)
  for (const c of all('SELECT order_nsu, kind, status, amount, paid_amount, capture_method FROM ip_charges WHERE subscription_id = ? ORDER BY rowid', s.id)) {
    console.log(`  cobrança ${c.order_nsu} (${c.kind}): ${c.status}, ${money(c.amount, 'brl')}${c.paid_amount ? `, pago ${money(c.paid_amount, 'brl')} via ${c.capture_method}` : ''}`)
  }
}
if (!stripeSubs.length && !ipSubs.length) console.log('nenhuma assinatura ainda para esta conta')
const events = all('SELECT type, COUNT(*) AS n, MAX(received_at) AS last FROM stripe_events GROUP BY type ORDER BY last DESC LIMIT 12')
console.log(`eventos Stripe recebidos (assinatura válida): ${events.map(e => `${e.type}×${e.n}`).join(', ') || 'nenhum'}`)

const refs = [...stripeSubs.map(s => ({ provider: 'stripe', subscription_ref: s.subscription_id })), ...ipSubs.map(s => ({ provider: 'infinitepay', subscription_ref: s.id }))]
const snaps = refs.flatMap(r => all('SELECT seq, subscription_ref, delivered_at, rejected_at, superseded_by, error_class FROM entitlement_outbox WHERE provider = ? AND subscription_ref = ? ORDER BY seq', r.provider, r.subscription_ref))
for (const o of snaps) console.log(`snapshot seq ${o.seq} (${o.subscription_ref}): ${o.delivered_at ? 'entregue' : o.rejected_at ? 'REJEITADO' : o.superseded_by ? 'substituído' : `pendente ${o.error_class}`}`)
if (refs.length) {
  const link = outboxSettings()
  try {
    const items = await httpStateReader(link)(refs)
    for (const i of items) console.log(`API aplicou ${i.provider} ${i.subscription_ref}: plano ${i.plan}, acesso ${i.access_status} até ${i.access_until ?? '-'}, último seq ${i.last_seq}`)
    if (!items.length) console.log('API: ainda não recebeu nada destas assinaturas')
  } catch (error) {
    console.log(`API: leitura falhou (${error.code ?? error.name})`)
  }
}
db.close()
