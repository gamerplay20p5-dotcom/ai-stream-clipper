import assert from 'node:assert/strict'
import { once } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { test } from 'node:test'
import Stripe from 'stripe'
import { createApp } from './app.js'
import { lookupKey, parseLookupKey } from './billing.js'

const WEBHOOK_SECRET = 'whsec_test_secret_for_unit_tests'
const settings = { secretKey: 'sk_test_unit', webhookSecret: WEBHOOK_SECRET, origin: 'https://clipper.example', portalUrl: '', automaticTax: false }
// Real signature helpers from the official SDK; no network calls are made
const signer = new Stripe('sk_test_unit').webhooks
const quiet = { warn() {}, error() {} }

const prices = {
  asc_basic_brl_monthly: { id: 'price_basic_brl', unit_amount: 5990, currency: 'brl', recurring: { interval: 'month', interval_count: 1 } },
  asc_creator_brl_monthly: { id: 'price_creator_brl', unit_amount: 13990, currency: 'brl', recurring: { interval: 'month', interval_count: 1 } },
  asc_pro_usd_monthly: { id: 'price_pro_usd', unit_amount: 8999, currency: 'usd', recurring: { interval: 'month', interval_count: 1 } },
  // Deliberately wrong: Stripe says 49.99 but the catalog says 44.99
  asc_creator_usd_monthly: { id: 'price_creator_usd', unit_amount: 4999, currency: 'usd', recurring: { interval: 'month', interval_count: 1 } },
}

function subscription(id, { status = 'active', key = 'asc_pro_usd_monthly', periodEnd = 1_800_000_000, cancel = false } = {}) {
  return { id, object: 'subscription', customer: 'cus_1', status, cancel_at_period_end: cancel, metadata: {}, items: { data: [{ current_period_end: periodEnd, price: { lookup_key: key } }] } }
}

function fakeStripe() {
  const calls = { sessions: [] }
  return {
    calls,
    webhooks: signer,
    prices: { list: async ({ lookup_keys: [key] }) => ({ data: prices[key] ? [prices[key]] : [] }) },
    checkout: {
      sessions: {
        create: async params => { calls.sessions.push(params); return { id: 'cs_test_created', url: 'https://checkout.stripe.com/c/pay/cs_test_created' } },
        retrieve: async id => {
          if (id === 'cs_test_missingsession') throw Object.assign(new Error('No such session'), { statusCode: 404, code: 'resource_missing' })
          return { id, status: 'complete', payment_status: 'paid', metadata: { plan: 'pro' }, customer_details: { email: 'secret@example.com' } }
        },
      },
    },
    subscriptions: { retrieve: async id => subscription(id) },
  }
}

async function start(t, overrides = {}) {
  const directory = await mkdtemp(join(tmpdir(), 'asc-billing-'))
  const databasePath = join(directory, 'test.sqlite')
  const stripe = 'stripe' in overrides ? overrides.stripe : fakeStripe()
  const { app, close } = createApp({ databasePath, stripe, billing: overrides.billing ?? settings, log: quiet })
  const server = app.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(async () => {
    await new Promise(resolve => server.close(resolve))
    close()
    await rm(directory, { recursive: true, force: true })
  })
  const base = `http://127.0.0.1:${server.address().port}`
  return { base, stripe, databasePath }
}

const checkout = (base, body, headers = {}) => fetch(`${base}/api/checkout`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) })

function deliver(base, event, secret = WEBHOOK_SECRET) {
  const payload = JSON.stringify(event)
  const signature = signer.generateTestHeaderString({ payload, secret })
  return fetch(`${base}/api/stripe/webhook`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Stripe-Signature': signature }, body: payload })
}

const event = (id, type, object, created = 1_700_000_000) => ({ id, object: 'event', type, created, data: { object } })

test('lookup keys round-trip only for catalog plans and markets', () => {
  assert.equal(lookupKey('creator', 'BR'), 'asc_creator_brl_monthly')
  assert.deepEqual(parseLookupKey('asc_pro_usd_monthly'), { plan: 'pro', market: 'US' })
  assert.equal(parseLookupKey('asc_enterprise_usd_monthly'), null)
  assert.equal(parseLookupKey('asc_pro_eur_monthly'), null)
  assert.equal(parseLookupKey(undefined), null)
})

test('checkout stays off and the waitlist keeps working without Stripe configuration', async t => {
  const { base } = await start(t, { stripe: null, billing: { ...settings, secretKey: '' } })
  assert.deepEqual(await (await fetch(`${base}/api/billing/config`)).json(), { enabled: false, portalUrl: '' })
  assert.equal((await checkout(base, { plan: 'pro', market: 'US', language: 'en' })).status, 503)
  assert.equal((await deliver(base, event('evt_off', 'invoice.paid', {}))).status, 503)
})

test('checkout prices come from Stripe lookup keys checked against the catalog, never from the browser', async t => {
  const { base, stripe } = await start(t)
  assert.deepEqual(await (await fetch(`${base}/api/billing/config`)).json(), { enabled: true, portalUrl: '' })

  const response = await checkout(base, { plan: 'creator', market: 'BR', language: 'pt-BR', price: 'price_cheap', amount: 1 }, { Host: 'attacker.example' })
  assert.equal(response.status, 201)
  assert.deepEqual(await response.json(), { url: 'https://checkout.stripe.com/c/pay/cs_test_created' })
  const params = stripe.calls.sessions[0]
  assert.equal(params.mode, 'subscription')
  assert.deepEqual(params.line_items, [{ price: 'price_creator_brl', quantity: 1 }])
  assert.equal(params.locale, 'pt-BR')
  assert.deepEqual(params.metadata, { plan: 'creator', market: 'BR', language: 'pt-BR' })
  assert.deepEqual(params.subscription_data.metadata, params.metadata)
  // Return URLs use the configured origin, not the Host header
  assert.equal(params.success_url, 'https://clipper.example/?lang=pt-BR&market=BR&checkout=success&session_id={CHECKOUT_SESSION_ID}')
  assert.equal(params.cancel_url, 'https://clipper.example/?lang=pt-BR&market=BR&checkout=cancelled#planos')

  for (const body of [{ plan: 'enterprise', market: 'US', language: 'en' }, { plan: 'pro', market: 'toString', language: 'en' }, { plan: 'pro', market: 'US', language: 'fr' }, {}]) {
    assert.equal((await checkout(base, body)).status, 422)
  }
  assert.equal((await checkout(base, null)).status, 400)
  assert.equal((await fetch(`${base}/api/checkout`, { method: 'POST', body: 'plan=pro' })).status, 415)
  // A Stripe price that differs from catalog.json blocks the sale instead of charging the wrong amount
  assert.equal((await checkout(base, { plan: 'creator', market: 'US', language: 'en' })).status, 503)
  // A plan without a Stripe price yet is also refused
  assert.equal((await checkout(base, { plan: 'basic', market: 'US', language: 'en' })).status, 503)
  assert.equal(stripe.calls.sessions.length, 1)
})

test('session status returns no personal data and validates the id', async t => {
  const { base } = await start(t)
  const ok = await fetch(`${base}/api/checkout/session?id=cs_test_a1b2c3d4e5f6`)
  assert.deepEqual(await ok.json(), { status: 'complete', paymentStatus: 'paid', plan: 'pro' })
  assert.equal((await fetch(`${base}/api/checkout/session?id=../../etc`)).status, 422)
  assert.equal((await fetch(`${base}/api/checkout/session?id=cs_test_missingsession`)).status, 404)
})

test('webhook verifies signatures, records subscriptions and invoices, and ignores duplicates and stale events', async t => {
  const { base, databasePath } = await start(t)
  const read = () => {
    const db = new DatabaseSync(databasePath, { readOnly: true })
    try {
      return {
        subscriptions: db.prepare('SELECT subscription_id, email, plan, market, status, current_period_end, cancel_at_period_end FROM subscriptions').all().map(row => ({ ...row })),
        invoices: db.prepare('SELECT invoice_id, subscription_id, status, amount_due, amount_paid, currency, attempt_count FROM invoices').all().map(row => ({ ...row })),
        events: db.prepare('SELECT COUNT(*) AS total FROM stripe_events').get().total,
      }
    } finally { db.close() }
  }

  // Unsigned, wrongly signed and replayed-with-tampering payloads are rejected
  assert.equal((await fetch(`${base}/api/stripe/webhook`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).status, 400)
  assert.equal((await deliver(base, event('evt_bad', 'invoice.paid', {}), 'whsec_wrong')).status, 400)
  const payload = JSON.stringify(event('evt_tamper', 'invoice.paid', { id: 'in_x' }))
  const signature = signer.generateTestHeaderString({ payload, secret: WEBHOOK_SECRET })
  assert.equal((await fetch(`${base}/api/stripe/webhook`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Stripe-Signature': signature }, body: payload.replace('in_x', 'in_y') })).status, 400)

  const completed = event('evt_1', 'checkout.session.completed', { id: 'cs_test_1', object: 'checkout.session', mode: 'subscription', subscription: 'sub_1', customer_details: { email: 'creator@example.com' } }, 1_700_000_100)
  assert.equal((await deliver(base, completed)).status, 200)
  // Same event delivered twice is acknowledged without being processed again
  assert.deepEqual(await (await deliver(base, completed)).json(), { received: true, duplicate: true })
  assert.deepEqual(read().subscriptions, [{ subscription_id: 'sub_1', email: 'creator@example.com', plan: 'pro', market: 'US', status: 'active', current_period_end: 1_800_000_000, cancel_at_period_end: 0 }])

  await deliver(base, event('evt_2', 'invoice.payment_failed', { id: 'in_1', customer: 'cus_1', parent: { subscription_details: { subscription: 'sub_1' } }, status: 'open', amount_due: 8999, amount_paid: 0, currency: 'usd', attempt_count: 1 }, 1_700_000_200))
  await deliver(base, event('evt_3', 'customer.subscription.updated', subscription('sub_1', { status: 'past_due' }), 1_700_000_200))
  // An older event arriving late must not overwrite newer state
  await deliver(base, event('evt_4', 'customer.subscription.updated', subscription('sub_1', { status: 'incomplete' }), 1_700_000_150))
  let state = read()
  assert.equal(state.subscriptions[0].status, 'past_due')
  assert.deepEqual(state.invoices, [{ invoice_id: 'in_1', subscription_id: 'sub_1', status: 'payment_failed', amount_due: 8999, amount_paid: 0, currency: 'usd', attempt_count: 1 }])

  await deliver(base, event('evt_5', 'invoice.paid', { id: 'in_1', customer: 'cus_1', subscription: 'sub_1', status: 'paid', amount_due: 8999, amount_paid: 8999, currency: 'usd', attempt_count: 2, hosted_invoice_url: 'https://invoice.stripe.com/i/x' }, 1_700_000_300))
  await deliver(base, event('evt_6', 'customer.subscription.updated', subscription('sub_1', { status: 'active', cancel: true }), 1_700_000_300))
  await deliver(base, event('evt_7', 'customer.subscription.deleted', subscription('sub_1', { status: 'canceled', cancel: true }), 1_700_000_400))
  // Subscriptions on prices we did not issue are ignored
  await deliver(base, event('evt_8', 'customer.subscription.created', subscription('sub_other', { key: 'someone_elses_price' }), 1_700_000_500))
  state = read()
  assert.equal(state.subscriptions.length, 1)
  assert.equal(state.subscriptions[0].status, 'canceled')
  assert.equal(state.subscriptions[0].cancel_at_period_end, 1)
  assert.equal(state.invoices[0].status, 'paid')
  assert.equal(state.invoices[0].amount_paid, 8999)
  assert.equal(state.events, 8)
})

test('a failure while processing returns 500 so Stripe retries, and the event is not marked as handled', async t => {
  const stripe = fakeStripe()
  stripe.subscriptions.retrieve = async () => { throw Object.assign(new Error('timeout'), { code: 'ETIMEDOUT' }) }
  const { base } = await start(t, { stripe })
  const completed = event('evt_retry', 'checkout.session.completed', { id: 'cs_test_r', mode: 'subscription', subscription: 'sub_r' })
  assert.equal((await deliver(base, completed)).status, 500)
  stripe.subscriptions.retrieve = async id => subscription(id)
  assert.deepEqual(await (await deliver(base, completed)).json(), { received: true })
})
