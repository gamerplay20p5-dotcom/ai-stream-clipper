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

if (tables.has('ip_subscriptions')) {
  console.log('\nInfinitePay (BR) — subscriptions by plan and status')
  console.table(rows('SELECT plan, status, COUNT(*) AS total FROM ip_subscriptions GROUP BY plan, status ORDER BY plan, status'))
  // Payments are recorded automatically, but dashboard access is still granted and revoked by hand
  // Access follows automatically through the outbox; this is the audit trail of what the API was told
  console.log('InfinitePay — ACCESS CHANGES sent to the main API (last 30 days)')
  console.table(rows(`SELECT datetime(l.created_at, 'unixepoch') AS at, s.email, s.plan,
      CASE WHEN l.type = 'payment_confirmed' AND c.review = 'after_cancel' THEN 'DO NOT GRANT: paid after cancellation (financial review)'
           WHEN l.type = 'payment_confirmed' THEN 'GRANT/EXTEND until ' || datetime(c.period_end, 'unixepoch')
           WHEN l.type IN ('status_expired', 'status_refunded', 'status_canceled') THEN 'REVOKE (' || substr(l.type, 8) || ')'
           ELSE l.type END AS action
    FROM ip_ledger l JOIN ip_subscriptions s ON s.id = l.subscription_id LEFT JOIN ip_charges c ON c.order_nsu = l.order_nsu
    WHERE l.created_at >= unixepoch() - 30 * 86400 AND l.type IN ('payment_confirmed', 'status_expired', 'status_refunded', 'status_canceled')
    ORDER BY l.id`))
  console.log('InfinitePay — NEEDS REVIEW: possible refunds, amount mismatches, payments after cancel, gave-up links or emails')
  console.table(rows(`SELECT datetime(l.created_at, 'unixepoch') AS at, l.type, l.order_nsu, s.email, l.amount, l.detail
    FROM ip_ledger l JOIN ip_subscriptions s ON s.id = l.subscription_id
    WHERE l.type IN ('reversal_reported', 'amount_mismatch', 'payment_after_cancel', 'link_gave_up', 'email_gave_up')
      AND NOT EXISTS (SELECT 1 FROM ip_ledger r WHERE r.order_nsu = l.order_nsu AND r.type IN ('refund_recorded', 'review_cleared'))
    ORDER BY l.id DESC LIMIT 50`))
  console.log('InfinitePay — access ending in the next 7 days or overdue')
  console.table(rows(`SELECT id, email, plan, status, datetime(paid_until, 'unixepoch') AS paid_until FROM ip_subscriptions
    WHERE status IN ('active', 'past_due') AND paid_until < unixepoch() + 7 * 86400 ORDER BY paid_until`))
  // Without SMTP (or after 5 failed attempts) these single-use links must be sent by hand before valid_until
  console.log('InfinitePay — open renewal links not emailed yet')
  console.table(rows(`SELECT c.order_nsu, s.email, c.plan, c.amount, c.email_attempts, c.link_url, datetime(c.valid_until, 'unixepoch') AS valid_until
    FROM ip_charges c JOIN ip_subscriptions s ON s.id = c.subscription_id
    WHERE c.kind = 'renewal' AND c.status = 'open' AND c.emailed_at IS NULL ORDER BY c.created_at`))
}

if (tables.has('entitlement_outbox')) {
  // Snapshots not yet accepted by the main API: access there may be behind until they are delivered
  console.log('\nEntitlement outbox (to the main API) - details, alerts and audited resend: npm run outbox -- report')
  console.table(rows(`SELECT SUM(delivered_at IS NULL AND rejected_at IS NULL) AS pending, SUM(rejected_at IS NOT NULL) AS rejected,
    datetime(MIN(CASE WHEN delivered_at IS NULL AND rejected_at IS NULL THEN created_at END), 'unixepoch') AS oldest_pending FROM entitlement_outbox`))
  console.table(rows(`SELECT seq, provider, subscription_ref, attempts, last_error, datetime(created_at, 'unixepoch') AS created
    FROM entitlement_outbox WHERE rejected_at IS NOT NULL OR (delivered_at IS NULL AND attempts > 0) ORDER BY seq DESC LIMIT 20`))
}

if (!tables.has('subscriptions') && !tables.has('ip_subscriptions')) {
  console.log('No payment data yet: configure Stripe or InfinitePay and receive a payment first')
}
db.close()
