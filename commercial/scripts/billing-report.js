// Read-only summary of what the payment webhooks recorded: Stripe (US, USD) and InfinitePay (BR, BRL).
//   npm run billing:report [-- --emails]
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { DatabaseSync } from 'node:sqlite'

const root = fileURLToPath(new URL('../', import.meta.url))
const db = new DatabaseSync(resolve(root, process.env.COMMERCIAL_DB ?? 'data/waitlist.sqlite'), { readOnly: true })
const showEmails = process.argv.includes('--emails')
const mask = email => showEmails || !email ? email : email.replace(/^(.).*(@.*)$/, '$1***$2')
const tables = new Set(db.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all().map(row => row.name))
const rows = sql => db.prepare(sql).all().map(row => ({ ...row, ...(row.email !== undefined ? { email: mask(row.email) } : {}) }))

if (tables.has('subscriptions')) {
  console.log('\nStripe (US) — subscriptions by plan and status')
  console.table(rows('SELECT plan, status, COUNT(*) AS total FROM subscriptions GROUP BY plan, status ORDER BY plan, status'))
  console.log('Stripe — latest subscriptions')
  console.table(rows(`SELECT subscription_id, email, plan, status, cancel_at_period_end,
    datetime(current_period_end, 'unixepoch') AS period_end, updated_at FROM subscriptions ORDER BY updated_at DESC LIMIT 20`))
  console.log('Stripe — invoices needing attention')
  console.table(rows(`SELECT invoice_id, subscription_id, status, amount_due, currency, attempt_count, updated_at
    FROM invoices WHERE status IN ('payment_failed', 'open', 'uncollectible') ORDER BY updated_at DESC LIMIT 20`))
}

if (tables.has('br_subscriptions')) {
  console.log('\nInfinitePay (BR) — subscriptions by plan and status')
  console.table(rows('SELECT plan, status, COUNT(*) AS total FROM br_subscriptions GROUP BY plan, status ORDER BY plan, status'))
  console.log('InfinitePay — access ending in the next 7 days or overdue')
  console.table(rows(`SELECT id, email, plan, status, datetime(paid_until, 'unixepoch') AS paid_until FROM br_subscriptions
    WHERE status IN ('active', 'past_due') AND paid_until < unixepoch() + 7 * 86400 ORDER BY paid_until`))
  // Without SMTP these links must be sent by hand; they are single-use and expire on valid_until
  console.log('InfinitePay — open renewal links not emailed yet')
  console.table(rows(`SELECT c.order_nsu, s.email, c.plan, c.amount, c.link_url, datetime(c.valid_until, 'unixepoch') AS valid_until
    FROM br_charges c JOIN br_subscriptions s ON s.id = c.subscription_id
    WHERE c.kind = 'renewal' AND c.status = 'open' AND c.emailed_at IS NULL ORDER BY c.created_at`))
}

if (!tables.has('subscriptions') && !tables.has('br_subscriptions')) {
  console.log('No payment data yet: configure Stripe or InfinitePay and receive a payment first')
}
db.close()
