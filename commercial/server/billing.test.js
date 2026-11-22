import assert from 'node:assert/strict'
import { once } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { test } from 'node:test'
import Stripe from 'stripe'
import { STRIPE_GRACE_DAYS, STRIPE_RENEWAL_LEEWAY } from './access.js'
import { createApp } from './app.js'
import { lookupKey, parseLookupKey } from './billing.js'
import { createOutbox } from './outbox.js'
import { fakeMain, internalPost, LINK_SECRET } from './test-helpers.js'

const WEBHOOK_SECRET = 'whsec_test_secret_for_unit_tests'
const settings = { secretKey: 'sk_test_unit', webhookSecret: WEBHOOK_SECRET, origin: 'https://clipper.example', appUrl: 'https://app.clipper.example', automaticTax: false }
// Real signature helpers from the official SDK; no network calls are made
const signer = new Stripe('sk_test_unit').webhooks
const quiet = { warn() {}, error() {} }
const DAY = 86_400
const START = 1_780_000_000
const END = START + 30 * DAY

const prices = {
  asc_basic_usd_monthly: { id: 'price_basic_usd', unit_amount: 1999, currency: 'usd', recurring: { interval: 'month', interval_count: 1 } },
  asc_pro_usd_monthly: { id: 'price_pro_usd', unit_amount: 8999, currency: 'usd', recurring: { interval: 'month', interval_count: 1 } },
  // Deliberately wrong: Stripe says 49.99 but the catalog says 44.99
  asc_creator_usd_monthly: { id: 'price_creator_usd', unit_amount: 4999, currency: 'usd', recurring: { interval: 'month', interval_count: 1 } },
}

function subscription(id, { status = 'active', key = 'asc_pro_usd_monthly', start = START, end = END, cancel = false, account = '42' } = {}) {
  return { id, object: 'subscription', customer: 'cus_1', status, cancel_at_period_end: cancel, metadata: { account_id: account },
    items: { data: [{ current_period_start: start, current_period_end: end, price: { lookup_key: key } }] } }
}

function fakeStripe() {
  const calls = { sessions: [], portals: [] }
  return {
    calls,
    webhooks: signer,
    prices: { list: async ({ lookup_keys: [key] }) => ({ data: prices[key] ? [prices[key]] : [] }) },
    checkout: { sessions: { create: async params => { calls.sessions.push(params); return { id: 'cs_test_created', url: 'https://checkout.stripe.com/c/pay/cs_test_created' } } } },
    billingPortal: { sessions: { create: async params => { calls.portals.push(params); return { url: 'https://billing.stripe.com/p/session/test_1' } } } },
    subscriptions: { retrieve: async id => subscription(id) },
    invoices: { retrieve: async id => ({ id, subscription: 'sub_1' }) },
    charges: { retrieve: async id => ({ id, customer: 'cus_1', invoice: 'in_1', amount: 8999 }) },
  }
}

async function start(t, overrides = {}) {
  const directory = await mkdtemp(join(tmpdir(), 'asc-billing-'))
  const databasePath = join(directory, 'test.sqlite')
  const stripe = 'stripe' in overrides ? overrides.stripe : fakeStripe()
  const main = fakeMain()
  const link = overrides.link ?? { secret: LINK_SECRET, send: main.send }
  const { app, close, dispatch } = createApp({ databasePath, stripe, billing: overrides.billing ?? settings, log: quiet, link })
  const server = app.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const extra = []
  t.after(async () => {
    await new Promise(resolve => server.close(resolve))
    close()
    for (const handle of extra) { try { handle.close() } catch {} }
    await rm(directory, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 })
  })
  const base = `http://127.0.0.1:${server.address().port}`
  const read = sql => { const db = new DatabaseSync(databasePath, { readOnly: true }); try { return db.prepare(sql).all().map(row => ({ ...row })) } finally { db.close() } }
  return { base, stripe, main, dispatch, read, databasePath, extra }
}

const checkout = (base, body, options) => internalPost(base, '/internal/billing/checkout', { account_id: 42, email: 'buyer@example.com', plan: 'pro', market: 'US', language: 'en', ...body }, options)

function deliver(base, event, secret = WEBHOOK_SECRET) {
  const payload = JSON.stringify(event)
  const signature = signer.generateTestHeaderString({ payload, secret })
  return fetch(`${base}/api/stripe/webhook`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Stripe-Signature': signature }, body: payload })
}

const event = (id, type, object, created = 1_700_000_000) => ({ id, object: 'event', type, created, data: { object } })
const snapshots = ctx => ctx.read('SELECT payload FROM entitlement_outbox ORDER BY seq').map(row => JSON.parse(row.payload))

test('lookup keys round-trip only for catalog plans and markets', () => {
  assert.equal(lookupKey('pro', 'US'), 'asc_pro_usd_monthly')
  assert.deepEqual(parseLookupKey('asc_pro_usd_monthly'), { plan: 'pro', market: 'US' })
  assert.equal(parseLookupKey('asc_enterprise_usd_monthly'), null)
  assert.equal(parseLookupKey('asc_pro_eur_monthly'), null)
  assert.equal(parseLookupKey(undefined), null)
})

test('no anonymous checkout: the public route is gone and markets stay closed without the account link', async t => {
  const ctx = await start(t, { link: {} })
  assert.deepEqual(await (await fetch(`${ctx.base}/api/billing/config`)).json(), { markets: { BR: false, US: false }, appUrl: 'https://app.clipper.example' })
  assert.equal((await fetch(`${ctx.base}/api/checkout`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"plan":"pro","market":"US","language":"en"}' })).status, 404)
  assert.equal((await checkout(ctx.base, {})).status, 503, 'no secret configured: the signed route refuses')
})

test('signed checkout for an account: prices from Stripe lookup keys checked against the catalog', async t => {
  const ctx = await start(t)
  assert.deepEqual((await (await fetch(`${ctx.base}/api/billing/config`)).json()).markets, { BR: false, US: true })

  const response = await checkout(ctx.base, { price: 'price_cheap', amount: 1 })
  assert.equal(response.status, 201)
  assert.deepEqual(await response.json(), { url: 'https://checkout.stripe.com/c/pay/cs_test_created' })
  const params = ctx.stripe.calls.sessions[0]
  assert.equal(params.mode, 'subscription')
  assert.deepEqual(params.line_items, [{ price: 'price_pro_usd', quantity: 1 }])
  assert.deepEqual(params.metadata, { plan: 'pro', market: 'US', language: 'en', account_id: '42' })
  assert.deepEqual(params.subscription_data.metadata, params.metadata)
  assert.equal(params.client_reference_id, 'account-42')
  assert.equal(params.customer_email, 'buyer@example.com')
  // Returns go to the dashboard, which shows the backend's state: the return itself grants nothing
  assert.equal(params.success_url, 'https://app.clipper.example/conta/assinatura?checkout=returned')
  assert.equal(params.cancel_url, 'https://app.clipper.example/conta/assinatura?checkout=cancelled')

  // Signature, freshness, body integrity and caller are all required
  assert.equal((await checkout(ctx.base, {}, { secret: 'wrong-secret-0123456789abcdefghijkl' })).status, 401)
  assert.equal((await checkout(ctx.base, {}, { now: Date.now() - 5 * 60_000 })).status, 401)
  assert.equal((await checkout(ctx.base, {}, { caller: 'worker' })).status, 401)
  assert.equal((await checkout(ctx.base, {}, { tamper: raw => raw.replace('"pro"', '"basic"') })).status, 401)
  for (const body of [{ account_id: 0 }, { account_id: 'x' }, { email: 'nope' }, { plan: 'enterprise' }, { market: 'toString' }, { language: 'fr' }]) {
    assert.equal((await checkout(ctx.base, body)).status, 422, JSON.stringify(body))
  }
  // BRL never reaches Stripe; a price that differs from the catalog blocks the sale; a missing price too
  assert.equal((await checkout(ctx.base, { market: 'BR', language: 'pt-BR' })).status, 503)
  assert.equal((await checkout(ctx.base, { plan: 'creator' })).status, 503)
  assert.equal(ctx.stripe.calls.sessions.length, 1)
})

test('webhook state becomes ordered, idempotent entitlement snapshots for the account in the metadata', async t => {
  const ctx = await start(t)
  assert.equal((await fetch(`${ctx.base}/api/stripe/webhook`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).status, 400)
  assert.equal((await deliver(ctx.base, event('evt_bad', 'invoice.paid', {}), 'whsec_wrong')).status, 400)

  const completed = event('evt_1', 'checkout.session.completed', { id: 'cs_test_1', mode: 'subscription', subscription: 'sub_1', customer_details: { email: 'buyer@example.com' } }, 1_700_000_100)
  assert.equal((await deliver(ctx.base, completed)).status, 200)
  assert.deepEqual(await (await deliver(ctx.base, completed)).json(), { received: true, duplicate: true })
  let all = snapshots(ctx)
  assert.equal(all.length, 1, 'a duplicate webhook produces no second snapshot')
  assert.deepEqual({ provider: all[0].provider, ref: all[0].subscription_ref, account: all[0].account_id, plan: all[0].plan, financial: all[0].financial_status },
    { provider: 'stripe', ref: 'sub_1', account: 42, plan: 'pro', financial: 'active' })
  assert.deepEqual(all[0].access, { status: 'active', until: END + STRIPE_RENEWAL_LEEWAY, period_start: START, period_end: END, renews_at: END, cancel_at_period_end: false })
  assert.deepEqual(all[0].limits, { hours: 200, channels: 4, simultaneous: 2, storage_gb: 25, export_4k: 30, features: { ai: 'premium', secondPass: true, variants: true, priority: true, editing: true } })
  assert.equal(all[0].manage, 'stripe_portal')

  // Renewal failure: grace of 3 days from the start of the unpaid period, then canceled ends access
  await deliver(ctx.base, event('evt_2', 'customer.subscription.updated', subscription('sub_1', { status: 'past_due', start: END, end: END + 30 * DAY }), 1_700_000_200))
  // An older event arriving late must not overwrite newer state
  await deliver(ctx.base, event('evt_3', 'customer.subscription.updated', subscription('sub_1', { status: 'incomplete' }), 1_700_000_150))
  all = snapshots(ctx)
  assert.equal(all.at(-1).access.status, 'grace')
  assert.equal(all.at(-1).access.until, END + STRIPE_GRACE_DAYS * DAY)
  assert.ok(all.map(item => item.seq).every((seq, i, list) => i === 0 || seq > list[i - 1]), 'monotonic sequence')

  await deliver(ctx.base, event('evt_4', 'invoice.paid', { id: 'in_1', customer: 'cus_1', subscription: 'sub_1', status: 'paid', amount_due: 8999, amount_paid: 8999, currency: 'usd' }, 1_700_000_300))
  assert.deepEqual(snapshots(ctx).at(-1).payments, [{ id: 'in_1', at: 1_700_000_300, amount: 8999, currency: 'usd', status: 'paid' }])
  await deliver(ctx.base, event('evt_5', 'customer.subscription.deleted', subscription('sub_1', { status: 'canceled' }), 1_700_000_400))
  assert.equal(snapshots(ctx).at(-1).access.status, 'ended')
  // Subscriptions on prices we did not issue are ignored
  await deliver(ctx.base, event('evt_6', 'customer.subscription.created', subscription('sub_other', { key: 'someone_elses_price' }), 1_700_000_500))
  assert.ok(!snapshots(ctx).some(item => item.subscription_ref === 'sub_other'))
})

test('refunds and disputes: full refund ends access, partial refund and open disputes only flag a review', async t => {
  const ctx = await start(t)
  await deliver(ctx.base, event('evt_1', 'customer.subscription.created', subscription('sub_1'), 1_700_000_100))
  await deliver(ctx.base, event('evt_2', 'charge.refunded', { id: 'ch_1', customer: 'cus_1', invoice: 'in_1', amount: 8999, amount_refunded: 1000, refunded: false }, 1_700_000_200))
  let last = snapshots(ctx).at(-1)
  assert.deepEqual([last.review, last.access.status], ['partial_refund', 'active'])
  await deliver(ctx.base, event('evt_3', 'charge.dispute.created', { id: 'dp_1', charge: 'ch_1', status: 'needs_response' }, 1_700_000_300))
  last = snapshots(ctx).at(-1)
  assert.deepEqual([last.review, last.access.status], ['dispute', 'active'])
  await deliver(ctx.base, event('evt_4', 'charge.dispute.closed', { id: 'dp_1', charge: 'ch_1', status: 'lost' }, 1_700_000_400))
  last = snapshots(ctx).at(-1)
  assert.deepEqual([last.financial_status, last.access.status], ['refunded', 'ended'])

  const second = await start(t)
  await deliver(second.base, event('evt_1', 'customer.subscription.created', subscription('sub_1'), 1_700_000_100))
  await deliver(second.base, event('evt_2', 'charge.refunded', { id: 'ch_1', customer: 'cus_1', invoice: 'in_1', amount: 8999, amount_refunded: 8999, refunded: true }, 1_700_000_200))
  assert.deepEqual([snapshots(second).at(-1).financial_status, snapshots(second).at(-1).access.status], ['refunded', 'ended'])
  // A later subscription.updated cannot resurrect a refunded subscription
  await deliver(second.base, event('evt_3', 'customer.subscription.updated', subscription('sub_1'), 1_700_000_300))
  assert.equal(snapshots(second).at(-1).access.status, 'ended')
})

test('a processing failure returns 500 so Stripe retries, without a partial snapshot', async t => {
  const stripe = fakeStripe()
  stripe.subscriptions.retrieve = async () => { throw Object.assign(new Error('timeout'), { code: 'ETIMEDOUT' }) }
  const ctx = await start(t, { stripe })
  const completed = event('evt_retry', 'checkout.session.completed', { id: 'cs_test_r', mode: 'subscription', subscription: 'sub_r' })
  assert.equal((await deliver(ctx.base, completed)).status, 500)
  assert.equal(snapshots(ctx).length, 0)
  stripe.subscriptions.retrieve = async id => subscription(id)
  assert.deepEqual(await (await deliver(ctx.base, completed)).json(), { received: true })
  assert.equal(snapshots(ctx).length, 1)
})

test('customer portal only for the account that owns the Stripe customer', async t => {
  const ctx = await start(t)
  assert.equal((await internalPost(ctx.base, '/internal/billing/portal', { account_id: 42 })).status, 404)
  await deliver(ctx.base, event('evt_1', 'customer.subscription.created', subscription('sub_1'), 1_700_000_100))
  const response = await internalPost(ctx.base, '/internal/billing/portal', { account_id: 42 })
  assert.deepEqual(await response.json(), { url: 'https://billing.stripe.com/p/session/test_1' })
  assert.deepEqual(ctx.stripe.calls.portals[0], { customer: 'cus_1', return_url: 'https://app.clipper.example/conta/assinatura' })
  assert.equal((await internalPost(ctx.base, '/internal/billing/portal', { account_id: 7 })).status, 404, 'another account gets nothing')
  assert.equal((await internalPost(ctx.base, '/internal/billing/portal', { account_id: 42 }, { secret: 'x'.repeat(40) })).status, 401)
})

test('outbox: delivered in order, retried after failures and restarts, permanent errors set aside', async t => {
  const ctx = await start(t)
  await deliver(ctx.base, event('evt_1', 'customer.subscription.created', subscription('sub_1'), 1_700_000_100))
  await deliver(ctx.base, event('evt_2', 'customer.subscription.updated', subscription('sub_1', { status: 'past_due' }), 1_700_000_200))
  // Main backend down: nothing lost, and a single probe per cycle instead of one request per queued event
  ctx.main.fail = true
  assert.deepEqual(await ctx.dispatch(), { delivered: 0, failed: 1 })
  // Next cycle: the first is in backoff, the second is probed and fails too
  assert.deepEqual(await ctx.dispatch(), { delivered: 0, failed: 1 })
  ctx.main.fail = false
  // Both in backoff: not retried immediately
  assert.equal((await ctx.dispatch()).delivered, 0)

  // A restart (new outbox on the same database) with the clock past the backoff delivers in order
  const db = new DatabaseSync(ctx.databasePath)
  ctx.extra.push(db)
  const later = createOutbox({ db, send: ctx.main.send, log: quiet, now: () => Math.floor(Date.now() / 1000) + 3600 })
  assert.deepEqual(await later.dispatch(), { delivered: 2, failed: 0 })
  assert.deepEqual(ctx.main.received.map(item => item.access.status), ['active', 'grace'])
  assert.equal(new Set(ctx.main.received.map(item => item.event_id)).size, 2)
  assert.equal((await later.dispatch()).delivered, 0, 'delivered once')

  // A 422 (permanently invalid) is set aside for the operator; 409/5xx are retried
  await deliver(ctx.base, event('evt_3', 'customer.subscription.updated', subscription('sub_1', { status: 'active' }), 1_700_000_300))
  await deliver(ctx.base, event('evt_4', 'customer.subscription.updated', subscription('sub_1', { status: 'canceled' }), 1_700_000_400))
  ctx.main.statuses.push(422, 409)
  assert.deepEqual(await later.dispatch(), { delivered: 0, failed: 2 })
  const stats = later.stats()
  assert.deepEqual([stats.pending, stats.rejected], [1, 1])
})
