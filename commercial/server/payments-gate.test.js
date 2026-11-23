// COMMERCIAL_PAYMENTS_OPEN: with every credential configured, the public still cannot pay until the operator opens
// payments. Webhooks, the signed link and the operator status keep working while closed.
import assert from 'node:assert/strict'
import { once } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import Stripe from 'stripe'
import { createApp } from './app.js'
import { infinitePaySettings } from './infinitepay.js'
import { fakeMain, internalPost, LINK_SECRET } from './test-helpers.js'

const WEBHOOK_SECRET = 'whsec_gate_test_secret'
const signer = new Stripe('sk_test_unit').webhooks
const billing = { secretKey: 'sk_test_unit', webhookSecret: WEBHOOK_SECRET, origin: 'https://clipper.example', appUrl: 'https://app.clipper.example', automaticTax: false }
const TOKEN = 'tok_0123456789abcdefghijklmnopqrstuv'
const infinitepay = infinitePaySettings({ INFINITEPAY_HANDLE: 'hts', INFINITEPAY_API_KEY: 'inchk_secretkey123', INFINITEPAY_WEBHOOK_TOKEN: TOKEN, COMMERCIAL_ORIGIN: 'https://clipper.example', ASC_APP_URL: 'https://app.clipper.example' })
const quiet = { warn() {}, error() {} }

async function start(t, paymentsOpen, testAccounts = []) {
  const directory = await mkdtemp(join(tmpdir(), 'asc-gate-'))
  const calls = { sessions: 0, links: 0 }
  const stripe = {
    webhooks: signer,
    prices: { list: async () => ({ data: [{ id: 'price_pro', unit_amount: 8999, currency: 'usd', recurring: { interval: 'month', interval_count: 1 } }] }) },
    checkout: { sessions: { create: async () => { calls.sessions++; return { id: 'cs_test_1', url: 'https://checkout.stripe.com/c/pay/cs_test_1' } } } },
  }
  const request = async path => {
    if (path === '/links') { calls.links++; return { url: 'https://checkout.infinitepay.io/hts/x' } }
    return { success: true, paid: false }
  }
  const main = fakeMain()
  const { app, close } = createApp({ databasePath: join(directory, 'gate.sqlite'), stripe, billing, infinitepay, infinitepayRequest: request, log: quiet, link: { secret: LINK_SECRET, send: main.send }, paymentsOpen, testAccounts })
  const server = app.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(async () => {
    await new Promise(resolve => server.close(resolve))
    close()
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 })
  })
  return { base: `http://127.0.0.1:${server.address().port}`, calls }
}

const buy = (base, market, account = 7) => internalPost(base, '/internal/billing/checkout', { account_id: account, email: 'buyer@example.com', plan: 'pro', market, language: 'en' })

test('closed payments: configured but nobody can pay; webhooks and status still work', async t => {
  const { base, calls } = await start(t, false)
  const config = await (await fetch(`${base}/api/billing/config`)).json()
  assert.deepEqual(config.markets, { BR: false, US: false })
  for (const market of ['US', 'BR']) {
    const r = await buy(base, market)
    assert.equal(r.status, 503)
    assert.deepEqual(await r.json(), { error: 'billing_disabled' })
  }
  assert.deepEqual(calls, { sessions: 0, links: 0 }, 'no Stripe session and no InfinitePay link while closed')

  const status = await (await internalPost(base, '/internal/billing/status', {})).json()
  assert.deepEqual(status.ready, { BR: true, US: true })
  assert.equal(status.open, false)
  assert.deepEqual(status.markets, { BR: false, US: false })
  assert.equal(typeof status.outbox.pending, 'number')
  // The status is signed: wrong secret or caller is refused
  assert.equal((await internalPost(base, '/internal/billing/status', {}, { secret: 'x'.repeat(40) })).status, 401)
  assert.equal((await internalPost(base, '/internal/billing/status', {}, { caller: 'commercial' })).status, 401)

  // Stripe webhook: valid signature accepted, bad signature refused
  const payload = JSON.stringify({ id: 'evt_gate_1', object: 'event', type: 'customer.created', created: 1_700_000_000, data: { object: {} } })
  const good = await fetch(`${base}/api/stripe/webhook`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Stripe-Signature': signer.generateTestHeaderString({ payload, secret: WEBHOOK_SECRET }) }, body: payload })
  assert.equal(good.status, 200)
  const bad = await fetch(`${base}/api/stripe/webhook`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Stripe-Signature': signer.generateTestHeaderString({ payload, secret: 'whsec_wrong' }) }, body: payload })
  assert.equal(bad.status, 400)
  // InfinitePay webhook: wrong token is 404, the right token reaches verification
  assert.equal((await fetch(`${base}/api/infinitepay/webhook/${'w'.repeat(40)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).status, 404)
  assert.notEqual((await fetch(`${base}/api/infinitepay/webhook/${TOKEN}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).status, 404)
})

test('open payments: the same configuration sells in both markets', async t => {
  const { base, calls } = await start(t, true)
  assert.deepEqual((await (await fetch(`${base}/api/billing/config`)).json()).markets, { BR: true, US: true })
  assert.equal((await buy(base, 'US')).status, 201)
  assert.equal((await buy(base, 'BR')).status, 201)
  assert.deepEqual(calls, { sessions: 1, links: 1 })
  assert.equal((await (await internalPost(base, '/internal/billing/status', {})).json()).open, true)
})

test('controlled purchase: only the listed test account can pay while payments stay closed', async t => {
  const { base, calls } = await start(t, false, ['7', 'not-an-id'])
  assert.deepEqual((await (await fetch(`${base}/api/billing/config`)).json()).markets, { BR: false, US: false })
  assert.equal((await buy(base, 'US', 7)).status, 201)
  assert.equal((await buy(base, 'BR', 7)).status, 201)
  assert.equal((await buy(base, 'US', 8)).status, 503)
  assert.equal((await buy(base, 'BR', 8)).status, 503)
  assert.deepEqual(calls, { sessions: 1, links: 1 })
  const status = await (await internalPost(base, '/internal/billing/status', {})).json()
  assert.equal(status.open, false)
  assert.equal(status.test_accounts, 1)
})
