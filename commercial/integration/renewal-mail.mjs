// InfinitePay renewal e-mail through the REAL mailer (nodemailer, STARTTLS, login) to the SMTP server named by
// SMTP_* (the local test sink of tests/test_email_flows_smtp.py). No network besides that server, no money:
// InfinitePay is simulated. Steps: account-bound checkout, paid first charge, clock moved into the renewal window,
// hourly job -> renewal link created and e-mailed. Prints one JSON line with what happened.
//
//   SMTP_HOST=localhost SMTP_PORT=... SMTP_USER=... SMTP_PASS=... SMTP_FROM=... RECIPIENT=... node integration/renewal-mail.mjs
import { mkdtempSync, rmSync } from 'node:fs'
import { once } from 'node:events'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { createApp } from '../server/app.js'
import { infinitePaySettings } from '../server/infinitepay.js'
import { createMailer, mailSettings } from '../server/mailer.js'
import { internalPost, LINK_SECRET } from '../server/test-helpers.js'

const DAY = 86_400
const TOKEN = 'tok_renewal_0123456789abcdefghijklmno'
const directory = mkdtempSync(join(tmpdir(), 'asc-renewal-'))
const databasePath = join(directory, 'commercial.sqlite')
const time = { now: Math.floor(Date.now() / 1000) }
const payments = new Map()
const request = async (path, body) => {
  if (path === '/links') return { url: `https://checkout.infinitepay.io/hts/${body.order_nsu}` }
  if (path === '/payment_check') return payments.get(body.order_nsu) ?? { success: true, paid: false }
  throw new Error(`undocumented endpoint ${path}`)
}
const mailer = createMailer(mailSettings(process.env))
if (!mailer) throw new Error('set SMTP_HOST and SMTP_FROM')
const { app, close, runRenewals } = createApp({
  databasePath, clock: () => time.now, mailer, log: { warn() {}, error: (...a) => console.error(...a) },
  infinitepay: infinitePaySettings({ INFINITEPAY_HANDLE: 'hts', INFINITEPAY_API_KEY: 'inchk_renewaltest01', INFINITEPAY_WEBHOOK_TOKEN: TOKEN, COMMERCIAL_ORIGIN: 'https://getclipper.example', ASC_APP_URL: 'https://clipper.example' }),
  infinitepayRequest: request, link: { secret: LINK_SECRET, send: async () => 200 },
})
const server = app.listen(0, '127.0.0.1')
await once(server, 'listening')
const base = `http://127.0.0.1:${server.address().port}`
const read = (sql, ...p) => { const db = new DatabaseSync(databasePath, { readOnly: true }); try { return db.prepare(sql).all(...p) } finally { db.close() } }
try {
  const email = process.env.RECIPIENT
  const buy = await internalPost(base, '/internal/billing/checkout', { account_id: 51, email, plan: 'creator', market: 'BR', language: 'pt-BR' })
  if (buy.status !== 201) throw new Error(`checkout ${buy.status}`)
  const [{ order_nsu: order, amount }] = read("SELECT order_nsu, amount FROM ip_charges WHERE kind = 'first'")
  payments.set(order, { success: true, paid: true, amount, paid_amount: amount, installments: 1, capture_method: 'pix' })
  const hook = await fetch(`${base}/api/infinitepay/webhook/${TOKEN}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ invoice_slug: 'slug-1', transaction_nsu: 'tx-1', order_nsu: order, amount, paid_amount: amount }) })
  const [{ paid_until: paidUntil }] = read('SELECT paid_until FROM ip_subscriptions')
  time.now = paidUntil - 4 * DAY // inside the renewal window (5 days before by default)
  const run = await runRenewals()
  const charges = read("SELECT kind, status, link_url, emailed_at FROM ip_charges ORDER BY rowid")
  console.log(JSON.stringify({ firstPaid: hook.status === 200, run, renewal: charges.find(c => c.kind === 'renewal') ?? null }))
} finally {
  server.close()
  close()
  rmSync(directory, { recursive: true, force: true })
}
