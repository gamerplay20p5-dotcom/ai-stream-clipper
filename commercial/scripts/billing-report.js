// Read-only summary of subscriptions and invoices recorded by the Stripe webhook.
//   node scripts/billing-report.js [--emails]
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { DatabaseSync } from 'node:sqlite'

const root = fileURLToPath(new URL('../', import.meta.url))
const db = new DatabaseSync(resolve(root, process.env.COMMERCIAL_DB ?? 'data/waitlist.sqlite'), { readOnly: true })
const showEmails = process.argv.includes('--emails')
const mask = email => showEmails || !email ? email : email.replace(/^(.).*(@.*)$/, '$1***$2')

const tables = new Set(db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all().map(row => row.name))
if (!tables.has('subscriptions')) {
  console.log('No billing data yet: start the server with Stripe configured and receive a webhook first')
  process.exit(0)
}

console.log('Subscriptions by plan and status')
console.table(db.prepare('SELECT plan, market, status, COUNT(*) AS total FROM subscriptions GROUP BY plan, market, status ORDER BY plan, market, status').all())
console.log('Latest subscriptions')
console.table(db.prepare(`SELECT subscription_id, email, plan, market, status, cancel_at_period_end,
  datetime(current_period_end, 'unixepoch') AS period_end, updated_at FROM subscriptions ORDER BY updated_at DESC LIMIT 20`).all()
  .map(row => ({ ...row, email: mask(row.email) })))
console.log('Invoices needing attention')
console.table(db.prepare(`SELECT invoice_id, subscription_id, status, amount_due, currency, attempt_count, updated_at
  FROM invoices WHERE status IN ('payment_failed', 'open', 'uncollectible') ORDER BY updated_at DESC LIMIT 20`).all())
db.close()
