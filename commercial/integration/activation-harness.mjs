// Cross-process integration harness (used by tests/test_payment_activation_e2e.py). No network, no money:
// the REAL commercial app (checkout routes, webhooks, outbox, HMAC link) runs here with Stripe and InfinitePay
// simulated, and delivers entitlement snapshots over HTTP to a REAL main API started by the Python test.
//
//   ASC_MAIN_API_URL=http://127.0.0.1:<api port> ASC_BILLING_SECRET=... PAYMENTS_OPEN=true node integration/activation-harness.mjs
//
// Prints one JSON line {"app": <port>, "control": <port>} and serves a small control API on the second port:
//   POST /stripe/pay {account_id, plan}   Stripe confirms a subscription (signed checkout.session.completed)
//   POST /infinitepay/pay {account_id}    InfinitePay confirms the account's open first charge (webhook + payment_check)
//   POST /infinitepay/notice {account_id} webhook for an UNPAID charge (payment_check says paid: false)
//   POST /state {refs}                    reconciliation read from the main API (signed, read-only)
//   POST /dispatch                        delivers pending snapshots now
import { once } from 'node:events'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import express from 'express'
import Stripe from 'stripe'
import catalog from '../catalog.json' with { type: 'json' }
import { createApp } from '../server/app.js'
import { infinitePaySettings } from '../server/infinitepay.js'
import { httpSender, httpStateReader, outboxSettings } from '../server/outbox.js'

const WEBHOOK_SECRET = 'whsec_integration_only'
const TOKEN = 'tok_integration_0123456789abcdefghij'
const signer = new Stripe('sk_test_integration').webhooks
const DAY = 86_400
const now = () => Math.floor(Date.now() / 1000)
const quiet = { warn() {}, error: (...args) => console.error(...args) }

const directory = mkdtempSync(join(tmpdir(), 'asc-e2e-'))
const databasePath = join(directory, 'commercial.sqlite')

// Stripe, simulated with the shapes the webhook handler reads; prices match catalog.json
const subscriptions = new Map()
const stripe = {
  webhooks: signer,
  prices: { list: async ({ lookup_keys: [key] }) => {
    const [, plan, currency] = key.split('_')
    return { data: [{ id: `price_${plan}`, lookup_key: key, unit_amount: catalog.plans[plan].monthlyMinor.US, currency, recurring: { interval: 'month', interval_count: 1 } }] }
  } },
  checkout: { sessions: { create: async params => ({ id: `cs_test_${params.metadata.account_id}`, url: `https://checkout.stripe.com/c/pay/cs_test_${params.metadata.account_id}` }) } },
  subscriptions: { retrieve: async id => subscriptions.get(id) },
  invoices: { retrieve: async id => ({ id, subscription: null }) },
}

// InfinitePay, simulated after the documented contract (no sandbox exists)
const payments = new Map()
const infinitepayRequest = async (path, body) => {
  if (path === '/links') return { url: `https://checkout.infinitepay.io/hts/${body.order_nsu}` }
  if (path === '/payment_check') return payments.get(body.order_nsu) ?? { success: true, paid: false }
  throw new Error(`undocumented endpoint ${path}`)
}
const infinitepay = infinitePaySettings({ INFINITEPAY_HANDLE: 'hts', INFINITEPAY_API_KEY: 'inchk_integration01', INFINITEPAY_WEBHOOK_TOKEN: TOKEN, COMMERCIAL_ORIGIN: 'https://getclipper.example', ASC_APP_URL: 'https://clipper.example' })
const billing = { secretKey: 'sk_test_integration', webhookSecret: WEBHOOK_SECRET, origin: 'https://getclipper.example', appUrl: 'https://clipper.example', automaticTax: false }

const link = outboxSettings(process.env)
if (!link.secret || !link.mainUrl) throw new Error('set ASC_MAIN_API_URL and ASC_BILLING_SECRET')
const { app, dispatch, close } = createApp({
  databasePath, stripe, billing, infinitepay, infinitepayRequest, log: quiet,
  mailer: async () => {}, paymentsOpen: process.env.PAYMENTS_OPEN === 'true',
  link: { secret: link.secret, send: httpSender(link) },
})
const server = app.listen(0, '127.0.0.1')
await once(server, 'listening')
const base = `http://127.0.0.1:${server.address().port}`
const read = (sql, ...params) => { const db = new DatabaseSync(databasePath, { readOnly: true }); try { return db.prepare(sql).all(...params) } finally { db.close() } }
const post = (url, body, headers = {}) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: typeof body === 'string' ? body : JSON.stringify(body) })

const control = express()
control.use(express.json())
control.post('/stripe/pay', async (req, res) => {
  const account = String(req.body.account_id)
  const plan = req.body.plan
  const id = `sub_e2e_${account}`
  const t = now()
  subscriptions.set(id, { id, object: 'subscription', customer: `cus_e2e_${account}`, status: 'active', cancel_at_period_end: false,
    metadata: { account_id: account, plan, market: 'US' },
    items: { data: [{ current_period_start: t, current_period_end: t + 30 * DAY, price: { lookup_key: `asc_${plan}_usd_monthly` } }] } })
  const payload = JSON.stringify({ id: `evt_e2e_${account}_${t}`, object: 'event', type: 'checkout.session.completed', created: t,
    data: { object: { id: `cs_test_${account}`, object: 'checkout.session', mode: 'subscription', subscription: id, customer: `cus_e2e_${account}`,
      payment_status: 'paid', customer_details: { email: `e2e-${account}@example.com` }, metadata: { account_id: account, plan, market: 'US' } } } })
  const hook = await post(`${base}/api/stripe/webhook`, payload, { 'Stripe-Signature': signer.generateTestHeaderString({ payload, secret: WEBHOOK_SECRET }) })
  await dispatch()
  res.json({ webhook: hook.status, subscription: id })
})
const firstCharge = account => read("SELECT c.order_nsu FROM ip_charges c JOIN ip_subscriptions s ON s.id = c.subscription_id WHERE s.account_id = ? AND c.kind = 'first' ORDER BY c.rowid DESC LIMIT 1", String(account))[0]?.order_nsu
const notice = (order, paid) => {
  const amount = 0
  return { invoice_slug: `slug-${order}`, amount, paid_amount: amount, installments: 1, capture_method: 'pix', transaction_nsu: `tx-${order}`, order_nsu: order, paid }
}
control.post('/infinitepay/pay', async (req, res) => {
  const order = firstCharge(req.body.account_id)
  if (!order) return res.status(404).json({ error: 'no charge' })
  const [{ amount }] = read('SELECT amount FROM ip_charges WHERE order_nsu = ?', order)
  payments.set(order, { success: true, paid: true, amount, paid_amount: amount, installments: 1, capture_method: 'pix' })
  const hook = await post(`${base}/api/infinitepay/webhook/${TOKEN}`, notice(order, true))
  await dispatch()
  res.json({ webhook: hook.status, order, result: (await hook.json()).result })
})
control.post('/infinitepay/notice', async (req, res) => {
  const order = firstCharge(req.body.account_id)
  if (!order) return res.status(404).json({ error: 'no charge' })
  const hook = await post(`${base}/api/infinitepay/webhook/${TOKEN}`, notice(order, false))
  await dispatch()
  res.json({ webhook: hook.status, order, result: (await hook.json()).result })
})
control.post('/state', async (req, res) => {
  try { res.json({ items: await httpStateReader(link)(req.body.refs) }) } catch (error) { res.status(502).json({ error: error.code }) }
})
control.post('/dispatch', async (_req, res) => { await dispatch(); res.json({ ok: true }) })
const controlServer = control.listen(0, '127.0.0.1')
await once(controlServer, 'listening')
console.log(JSON.stringify({ app: server.address().port, control: controlServer.address().port }))

function shutdown() {
  controlServer.close()
  server.close(() => { close(); rmSync(directory, { recursive: true, force: true }); process.exit(0) })
}
process.once('SIGTERM', shutdown)
process.once('SIGINT', shutdown)
process.stdin.on('end', shutdown)
process.stdin.resume()
