// HTS promotion codes: definitions, Stripe mirroring (simulated), InfinitePay amounts (simulated), limits under
// concurrency, start-of-subscription vs recurring codes, expiry, refunds and the account review guard.
// No real Stripe or InfinitePay call is made.
import assert from 'node:assert/strict'
import { once } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { test } from 'node:test'
import Stripe from 'stripe'
import { createApp } from './app.js'
import { createInfinitePay, infinitePaySettings } from './infinitepay.js'
import { createOutbox } from './outbox.js'
import { createPromotions, parseDefinition, priceWith } from './promotions.js'
import { fakeMain, internalPost } from './test-helpers.js'

const DAY = 86_400
const T0 = 1_780_000_000
const WEBHOOK_SECRET = 'whsec_test_secret_for_unit_tests'
const signer = new Stripe('sk_test_unit').webhooks
const quiet = { warn() {}, error() {} }
const ip = infinitePaySettings({ INFINITEPAY_HANDLE: '$hts', INFINITEPAY_API_KEY: 'inchk_secretkey123', INFINITEPAY_WEBHOOK_TOKEN: 'tok_0123456789abcdefghijklmnopqrstuv',
  COMMERCIAL_ORIGIN: 'https://clipper.example', ASC_APP_URL: 'https://app.clipper.example' })
const billing = { secretKey: 'sk_test_unit', webhookSecret: WEBHOOK_SECRET, origin: 'https://clipper.example', appUrl: 'https://app.clipper.example', automaticTax: false }
const prices = Object.fromEntries([['basic', 1999], ['creator', 4499], ['pro', 8999]].map(([plan, amount]) =>
  [`asc_${plan}_usd_monthly`, { id: `price_${plan}_usd`, unit_amount: amount, currency: 'usd', recurring: { interval: 'month', interval_count: 1 } }]))

function fakeStripe() {
  const calls = { coupons: [], promotions: [], promotionUpdates: [], sessions: [], subscriptionUpdates: [] }
  const stripe = {
    calls, failCoupons: 0,
    webhooks: signer,
    prices: { list: async ({ lookup_keys: [key] }) => ({ data: prices[key] ? [prices[key]] : [] }) },
    coupons: { create: async (params, options) => {
      if (stripe.failCoupons > 0) { stripe.failCoupons--; throw Object.assign(new Error('api down'), { type: 'StripeConnectionError' }) }
      calls.coupons.push({ params, options }); return { id: `co_${params.name}` }
    } },
    promotionCodes: {
      create: async (params, options) => { calls.promotions.push({ params, options }); return { id: `promo_${params.code}` } },
      update: async (id, params) => { calls.promotionUpdates.push({ id, params }); return { id } },
    },
    checkout: { sessions: { create: async params => { calls.sessions.push(params); return { id: `cs_${calls.sessions.length}`, url: `https://checkout.stripe.com/c/pay/cs_${calls.sessions.length}` } } } },
    subscriptions: {
      retrieve: async id => ({ id, object: 'subscription', customer: 'cus_1', status: 'active', cancel_at_period_end: false, metadata: { account_id: '7' },
        items: { data: [{ current_period_start: T0, current_period_end: T0 + 30 * DAY, price: { lookup_key: 'asc_pro_usd_monthly' } }] } }),
      update: async (id, params, options) => { calls.subscriptionUpdates.push({ id, params, options }); return { id } },
    },
    billingPortal: { sessions: { create: async () => ({ url: 'https://billing.stripe.com/p/session/x' }) } },
  }
  return stripe
}

function fakeInfinitePay() {
  const api = { calls: [], payments: new Map() }
  api.request = async (path, body) => {
    api.calls.push({ path, body: structuredClone(body) })
    if (path === '/links') return { url: `https://checkout.infinitepay.io/hts/${body.order_nsu}` }
    if (path === '/payment_check') return api.payments.get(body.order_nsu) ?? { success: true, paid: false }
    throw new Error(`undocumented endpoint ${path}`)
  }
  api.pay = (order, amount) => api.payments.set(order, { success: true, paid: true, amount, paid_amount: amount, installments: 1, capture_method: 'pix' })
  return api
}

async function start(t, { stripe = fakeStripe(), withStripe = true } = {}) {
  const dir = await mkdtemp(join(tmpdir(), 'asc-promo-'))
  const databasePath = join(dir, 'test.sqlite')
  const time = { now: T0 }
  const api = fakeInfinitePay()
  const main = fakeMain()
  const created = createApp({
    databasePath, stripe: withStripe ? stripe : null, billing: withStripe ? billing : undefined, infinitepay: ip, infinitepayRequest: api.request,
    log: quiet, clock: () => time.now, mailer: async () => {}, link: { secret: 'billing-link-secret-0123456789abcdef', send: main.send },
  })
  const server = created.app.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(async () => {
    await new Promise(resolve => server.close(resolve))
    created.close()
    await rm(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 })
  })
  const base = `http://127.0.0.1:${server.address().port}`
  const read = (sql, ...params) => { const db = new DatabaseSync(databasePath, { readOnly: true }); try { return db.prepare(sql).all(...params).map(row => ({ ...row })) } finally { db.close() } }
  const write = sql => { const db = new DatabaseSync(databasePath); try { return db.exec(sql) } finally { db.close() } }
  const call = async (path, body) => { const r = await internalPost(base, path, body); return { status: r.status, body: await r.json() } }
  const define = definition => call('/internal/promotions/create', { definition, actor: 'admin@hts.example' })
  const checkout = (account, extra = {}) => call('/internal/billing/checkout', { account_id: account, email: `a${account}@example.com`, plan: 'creator', market: 'BR', language: 'pt-BR', ...extra })
  return { base, time, api, stripe, main, read, write, call, define, checkout, databasePath, runRenewals: created.runRenewals, promotions: created.promotions }
}

function deliver(base, event) {
  const payload = JSON.stringify(event)
  return fetch(`${base}/api/stripe/webhook`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Stripe-Signature': signer.generateTestHeaderString({ payload, secret: WEBHOOK_SECRET }) }, body: payload })
}
const event = (id, type, object, created = T0) => ({ id, object: 'event', type, created, data: { object } })
const lastCharge = (ctx, account) => ctx.read(`SELECT c.* FROM ip_charges c JOIN ip_subscriptions s ON s.id = c.subscription_id WHERE s.account_id = ? ORDER BY c.created_at DESC, c.rowid DESC LIMIT 1`, String(account))[0]
const confirmPaid = async (ctx, charge, amount = charge.amount) => {
  ctx.api.pay(charge.order_nsu, amount)
  return (await fetch(`${ctx.base}/api/infinitepay/confirm`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ orderNsu: charge.order_nsu, slug: 'slug1', transactionNsu: 'tx-1' }) })).json()
}
const base20 = { code: 'MALAIO20', kind: 'percent', percent: 20, plans: ['basic', 'creator', 'pro'], markets: ['BR', 'US'] }

// ------------------------------------------------------------------ definitions and amounts
test('definitions are validated on the server and new-subscription codes discount only the first payment', () => {
  const ok = parseDefinition({ ...base20, code: ' malaio20 ', duration: 'forever' }, T0)
  assert.equal(ok.code, 'MALAIO20')
  assert.equal(ok.applies_to, 'new')
  assert.equal(ok.duration, 'once', 'a start-of-subscription code never repeats on renewals')
  const recurring = parseDefinition({ ...base20, code: 'FIEL10', applies_to: 'existing', duration: 'repeating', duration_months: 3 }, T0)
  assert.deepEqual([recurring.applies_to, recurring.duration, recurring.duration_months], ['existing', 'repeating', 3])
  const bad = [
    [{ ...base20, code: 'x' }, 'code'], [{ ...base20, code: 'MAL-AIO' }, 'code'], [{ ...base20, percent: 0 }, 'percent'],
    [{ ...base20, percent: 101 }, 'percent'], [{ ...base20, percent: -5 }, 'percent'], [{ ...base20, plans: ['gold'] }, 'plans'],
    [{ ...base20, markets: ['EU'] }, 'markets'], [{ ...base20, kind: 'amount', amount_brl: 6000, amount_usd: 500 }, 'amount_br'],
    [{ ...base20, kind: 'amount', amount_brl: -1, amount_usd: 500 }, 'amount_br'], [{ ...base20, kind: 'free_days', free_days: 91 }, 'free_days'],
    [{ ...base20, applies_to: 'existing', kind: 'free_days', free_days: 7 }, 'free_days_new_only'],
    [{ ...base20, applies_to: 'existing', duration: 'once' }, 'duration'], [{ ...base20, ends_at: T0 - 1 }, 'ends_at'],
    [{ ...base20, max_redemptions: 0 }, 'max_redemptions'],
  ]
  for (const [definition, reason] of bad) assert.throws(() => parseDefinition(definition, T0), error => error.reason === reason, reason)
  // amounts come from catalog.json only
  assert.deepEqual(priceWith({ kind: 'percent', percent: 20 }, 'creator', 'BR'), { list: 13990, discount: 2798, final: 11192, currency: 'brl', freeDays: 0 })
  assert.deepEqual(priceWith({ kind: 'amount', amount_usd: 1000 }, 'basic', 'US').final, 999)
  assert.equal(priceWith({ kind: 'percent', percent: 100 }, 'pro', 'BR').final, 0)
})

test('admin routes are signed, terms are immutable and codes are never deleted', async t => {
  const ctx = await start(t)
  assert.equal((await fetch(`${ctx.base}/internal/promotions/list`, { method: 'POST' })).status, 401, 'unsigned calls are refused')
  assert.equal((await ctx.define(base20)).status, 201)
  assert.deepEqual((await ctx.define(base20)).body, { error: 'invalid_promotion', reason: 'code_taken' })
  assert.equal((await ctx.define({ ...base20, code: 'NEG', percent: -10 })).status, 422)
  assert.throws(() => ctx.write("UPDATE promo_codes SET percent = 90 WHERE code = 'MALAIO20'"), /immutable/)
  assert.throws(() => ctx.write("DELETE FROM promo_codes WHERE code = 'MALAIO20'"), /never deleted/)
  const updated = await ctx.call('/internal/promotions/update', { changes: { code: 'MALAIO20', active: false, max_redemptions: 5 }, actor: 'admin@hts.example' })
  assert.equal(updated.body.active, false)
  assert.deepEqual(ctx.stripe.calls.promotionUpdates.at(-1).params, { active: false })
  const types = ctx.read("SELECT type FROM promo_events WHERE code = 'MALAIO20' ORDER BY id").map(row => row.type)
  assert.deepEqual(types, ['created', 'stripe_synced', 'updated'])
  assert.throws(() => ctx.write('DELETE FROM promo_events'), /append-only/)
})

// ------------------------------------------------------------------ Stripe (USD)
test('Stripe: the code is mirrored as an official coupon + promotion code, idempotently, and retried after a failure', async t => {
  const ctx = await start(t)
  ctx.stripe.failCoupons = 1
  const first = await ctx.define({ ...base20, markets: ['US'], max_redemptions: 50, ends_at: T0 + 30 * DAY })
  assert.equal(first.body.stripe.state, 'error', 'a Stripe failure is visible, never hidden')
  assert.equal((await ctx.call('/internal/billing/quote', { account_id: 7, plan: 'pro', market: 'US', promo_code: 'MALAIO20' })).body.reason, 'not_synced')
  const synced = await ctx.call('/internal/promotions/sync', { code: 'MALAIO20', actor: 'admin@hts.example' })
  assert.equal(synced.body.stripe.state, 'synced')
  const [{ params: coupon, options }] = ctx.stripe.calls.coupons
  assert.deepEqual(coupon, { name: 'MALAIO20', percent_off: 20, duration: 'once', metadata: { asc_code: 'MALAIO20' } })
  assert.equal(options.idempotencyKey, 'asc-coupon-MALAIO20')
  const [{ params: promotion }] = ctx.stripe.calls.promotions
  assert.deepEqual(promotion.promotion, { type: 'coupon', coupon: 'co_MALAIO20' })
  assert.deepEqual([promotion.code, promotion.max_redemptions, promotion.expires_at], ['MALAIO20', 50, T0 + 30 * DAY])
})

test('Stripe checkout applies only the validated HTS code; completion redeems it, expiry releases it', async t => {
  const ctx = await start(t)
  await ctx.define({ ...base20, markets: ['US'] })
  await ctx.define({ code: 'SEMANA', kind: 'free_days', free_days: 7, plans: ['pro'], markets: ['US'] })
  const r = await ctx.checkout(7, { plan: 'pro', market: 'US', language: 'en', promo_code: 'malaio20' })
  assert.equal(r.status, 201, JSON.stringify(r.body))
  const session = ctx.stripe.calls.sessions.at(-1)
  assert.deepEqual(session.discounts, [{ promotion_code: 'promo_MALAIO20' }])
  assert.equal('allow_promotion_codes' in session, false, 'no other Stripe code can be typed')
  const [redemption] = ctx.read('SELECT * FROM promo_redemptions')
  assert.deepEqual([redemption.status, redemption.list_amount, redemption.discount_amount, redemption.final_amount], ['reserved', 8999, 1800, 7199])
  assert.equal(session.metadata.promo_redemption, redemption.id)
  // a repeated click reopens the same Stripe checkout: one session, one reservation
  const again = await ctx.checkout(7, { plan: 'pro', market: 'US', language: 'en', promo_code: 'MALAIO20' })
  assert.equal(again.body.url, r.body.url)
  assert.equal(ctx.stripe.calls.sessions.length, 1)
  assert.equal(ctx.read('SELECT COUNT(*) AS n FROM promo_redemptions')[0].n, 1)
  await deliver(ctx.base, event('evt_c1', 'checkout.session.completed', { id: 'cs_1', mode: 'subscription', subscription: 'sub_7', metadata: session.metadata }))
  assert.deepEqual(ctx.read('SELECT status, subscription_ref FROM promo_redemptions'), [{ status: 'redeemed', subscription_ref: 'sub_7' }])
  // another account's checkout expires: its use comes back
  await ctx.checkout(8, { plan: 'pro', market: 'US', language: 'en', promo_code: 'MALAIO20' })
  const other = ctx.stripe.calls.sessions.at(-1)
  await deliver(ctx.base, event('evt_x1', 'checkout.session.expired', { id: 'cs_3', metadata: other.metadata }))
  assert.equal(ctx.read("SELECT status FROM promo_redemptions WHERE account_id = '8'")[0].status, 'released')
  // free days use Stripe's official trial
  await ctx.checkout(9, { plan: 'pro', market: 'US', language: 'en', promo_code: 'SEMANA' })
  const trial = ctx.stripe.calls.sessions.at(-1)
  assert.equal(trial.subscription_data.trial_period_days, 7)
  assert.equal(trial.discounts, undefined)
})

// ------------------------------------------------------------------ InfinitePay (BRL)
test('InfinitePay: the discounted amount is frozen in the link and confirmed by payment_check', async t => {
  const ctx = await start(t)
  await ctx.define({ ...base20, markets: ['BR'] })
  const quote = await ctx.call('/internal/billing/quote', { account_id: 11, plan: 'creator', market: 'BR', promo_code: 'MALAIO20' })
  assert.deepEqual([quote.body.list, quote.body.discount, quote.body.final], [13990, 2798, 11192])
  assert.equal('stripePromotion' in quote.body, false)
  assert.equal((await ctx.checkout(11, { promo_code: 'MALAIO20' })).status, 201)
  const link = ctx.api.calls.find(call => call.path === '/links').body
  assert.equal(link.items[0].price, 11192)
  assert.equal(link.options.discounts, undefined, "InfinitePay's own Pix/card discounts are not our coupons")
  const charge = lastCharge(ctx, 11)
  assert.deepEqual([charge.amount, charge.list_amount, charge.discount_amount, charge.promo_code], [11192, 13990, 2798, 'MALAIO20'])
  assert.equal(JSON.parse(charge.request_json).items[0].price, 11192, 'retries resend the same frozen amount')
  // InfinitePay reports a different amount: never confirmed
  ctx.api.pay(charge.order_nsu, 13990)
  ctx.api.payments.get(charge.order_nsu).amount = 13990
  const mismatch = await (await fetch(`${ctx.base}/api/infinitepay/confirm`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ orderNsu: charge.order_nsu, slug: 'slug1', transactionNsu: 'tx-1' }) })).json()
  assert.equal(mismatch.result, 'processing')
  assert.equal((await confirmPaid(ctx, charge)).result, 'confirmed')
  assert.equal(ctx.read('SELECT status FROM promo_redemptions')[0].status, 'redeemed')
  // a start-of-subscription code never discounts the renewal
  const [sub] = ctx.read("SELECT * FROM ip_subscriptions WHERE account_id = '11'")
  ctx.time.now = sub.paid_until - 4 * DAY
  await ctx.runRenewals()
  const renewal = lastCharge(ctx, 11)
  assert.deepEqual([renewal.kind, renewal.amount, renewal.discount_amount], ['renewal', 13990, 0])
})

test('InfinitePay: 100% discount or free days grant access without any charge', async t => {
  const ctx = await start(t)
  await ctx.define({ code: 'GRATIS', kind: 'percent', percent: 100, plans: ['creator'], markets: ['BR'] })
  await ctx.define({ code: 'TESTE7', kind: 'free_days', free_days: 7, plans: ['creator'], markets: ['BR'] })
  const r = await ctx.checkout(21, { promo_code: 'GRATIS' })
  assert.deepEqual(r.body, { free: true })
  assert.equal(ctx.api.calls.filter(call => call.path === '/links').length, 0, 'no artificial charge')
  const [sub] = ctx.read("SELECT * FROM ip_subscriptions WHERE account_id = '21'")
  assert.equal(sub.status, 'active')
  assert.ok(sub.paid_until > T0 + 27 * DAY)
  assert.equal(ctx.read("SELECT COUNT(*) AS n FROM ip_charges").at(0).n, 0)
  assert.equal(ctx.read("SELECT type FROM ip_ledger WHERE subscription_id = ? AND type = 'promo_free'", sub.id).length, 1)
  await ctx.checkout(22, { promo_code: 'TESTE7' })
  const [trial] = ctx.read("SELECT * FROM ip_subscriptions WHERE account_id = '22'")
  assert.equal(trial.paid_until, T0 + 7 * DAY)
})

test('InfinitePay: removing or changing the code issues a new link with the right price', async t => {
  const ctx = await start(t)
  await ctx.define({ ...base20, markets: ['BR'] })
  await ctx.checkout(31)
  const full = lastCharge(ctx, 31)
  assert.equal(full.amount, 13990)
  await ctx.checkout(31, { promo_code: 'MALAIO20' })
  const discounted = lastCharge(ctx, 31)
  assert.equal(discounted.amount, 11192)
  assert.equal(ctx.read('SELECT status FROM ip_charges WHERE order_nsu = ?', full.order_nsu)[0].status, 'expired')
  await ctx.checkout(31)
  assert.equal(lastCharge(ctx, 31).amount, 13990)
  assert.equal(ctx.read('SELECT status FROM promo_redemptions')[0].status, 'released', 'the use reserved by the dropped link comes back')
})

test('limits: invalid, expired, inactive, not eligible, below the minimum and exhausted under concurrency', async t => {
  const ctx = await start(t)
  await ctx.define({ ...base20, markets: ['BR'], max_redemptions: 1 })
  await ctx.define({ code: 'SOPRO', kind: 'percent', percent: 10, plans: ['pro'], markets: ['BR'] })
  await ctx.define({ code: 'QUASE', kind: 'percent', percent: 99, plans: ['basic'], markets: ['BR'] })
  await ctx.define({ code: 'DEPOIS', kind: 'percent', percent: 10, plans: ['creator'], markets: ['BR'], starts_at: T0 + DAY })
  await ctx.define({ code: 'CURTO', kind: 'percent', percent: 10, plans: ['creator'], markets: ['BR'], ends_at: T0 + 60 })
  await ctx.define({ code: 'DESLIGADO', kind: 'percent', percent: 10, plans: ['creator'], markets: ['BR'] })
  await ctx.call('/internal/promotions/update', { changes: { code: 'DESLIGADO', active: false } })
  ctx.time.now = T0 + 120
  const reason = async (code, plan = 'creator', market = 'BR') => (await ctx.call('/internal/billing/quote', { account_id: 41, plan, market, promo_code: code })).body.reason
  assert.equal(await reason('NAOEXISTE'), 'not_found')
  assert.equal(await reason('SOPRO'), 'plan_not_eligible')
  assert.equal(await reason('MALAIO20', 'creator', 'US'), 'market_not_eligible')
  assert.equal(await reason('QUASE', 'basic'), 'below_minimum', 'R$ 0,60 would be below the minimum charge')
  assert.equal(await reason('DEPOIS'), 'not_started')
  assert.equal(await reason('CURTO'), 'expired')
  assert.equal(await reason('DESLIGADO'), 'inactive')
  // two different accounts race for the single use: exactly one wins
  const results = await Promise.all([ctx.checkout(42, { promo_code: 'MALAIO20' }), ctx.checkout(43, { promo_code: 'MALAIO20' })])
  assert.deepEqual(results.map(r => r.status).sort(), [201, 422])
  assert.equal(results.find(r => r.status === 422).body.reason, 'exhausted')
  assert.equal(ctx.read("SELECT COUNT(*) AS n FROM promo_redemptions WHERE status = 'reserved'")[0].n, 1)
  // the same account clicking twice reuses its link and its single reservation
  const winner = results.find(r => r.status === 201)
  const again = await ctx.checkout(winner === results[0] ? 42 : 43, { promo_code: 'MALAIO20' })
  assert.equal(again.body.url, winner.body.url)
})

test('an unpaid link releases its use; a refunded promotional purchase keeps it counted', async t => {
  const ctx = await start(t)
  await ctx.define({ ...base20, markets: ['BR'], max_per_account: 1 })
  await ctx.checkout(51, { promo_code: 'MALAIO20' })
  ctx.time.now = T0 + 8 * DAY // the first link lasts 7 days
  await ctx.runRenewals()
  assert.equal(ctx.read("SELECT status FROM promo_redemptions WHERE account_id = '51'")[0].status, 'released')
  await ctx.checkout(52, { promo_code: 'MALAIO20' })
  const paid = lastCharge(ctx, 52)
  await confirmPaid(ctx, paid)
  // the operator records the refund (same wiring as scripts/infinitepay-admin.js)
  const db = new DatabaseSync(ctx.databasePath, { timeout: 2_000 })
  const admin = createInfinitePay({ db, settings: ip, request: ctx.api.request, log: quiet, now: () => ctx.time.now,
    outbox: createOutbox({ db, log: quiet, now: () => ctx.time.now }), promotions: createPromotions({ db, log: quiet, now: () => ctx.time.now }) })
  admin.refund(paid.order_nsu, { reason: 'pedido do cliente' })
  assert.equal(ctx.read("SELECT status FROM promo_redemptions WHERE account_id = '52'")[0].status, 'refunded')
  const quote = await ctx.call('/internal/billing/quote', { account_id: 52, plan: 'creator', market: 'BR', promo_code: 'MALAIO20' })
  db.close()
  assert.ok(['account_limit', 'account_review'].includes(quote.body.reason), 'refunding never frees the code for another try')
})

test('accounts under review never use codes', async t => {
  const ctx = await start(t)
  await ctx.define({ ...base20, markets: ['BR'] })
  await ctx.checkout(61)
  const charge = lastCharge(ctx, 61)
  await confirmPaid(ctx, charge, 13990)
  ctx.write(`UPDATE ip_charges SET status = 'reversal_review' WHERE order_nsu = '${charge.order_nsu}'`)
  const r = await ctx.call('/internal/billing/quote', { account_id: 61, plan: 'creator', market: 'BR', promo_code: 'MALAIO20' })
  assert.equal(r.body.reason, 'account_review')
})

// ------------------------------------------------------------------ recurring codes for running subscriptions
test('recurring codes apply only to running subscriptions and discount the next renewals (N months)', async t => {
  const ctx = await start(t)
  await ctx.define({ ...base20, markets: ['BR'] })
  await ctx.define({ code: 'FIEL10', kind: 'percent', percent: 10, applies_to: 'existing', duration: 'repeating', duration_months: 2, plans: ['creator'], markets: ['BR'] })
  assert.deepEqual((await ctx.checkout(71, { promo_code: 'FIEL10' })).body, { error: 'promo_invalid', reason: 'existing_only' }, 'never at checkout')
  assert.equal((await ctx.call('/internal/billing/apply-promo', { account_id: 71, promo_code: 'FIEL10' })).status, 404, 'no running subscription yet')
  await ctx.checkout(71)
  await confirmPaid(ctx, lastCharge(ctx, 71))
  assert.equal((await ctx.call('/internal/billing/apply-promo', { account_id: 71, promo_code: 'MALAIO20' })).body.reason, 'new_only')
  const preview = await ctx.call('/internal/billing/apply-promo', { account_id: 71, promo_code: 'FIEL10', preview: true })
  assert.deepEqual([preview.body.final, preview.body.applied], [12591, undefined])
  assert.equal(ctx.read('SELECT COUNT(*) AS n FROM promo_redemptions')[0].n, 0, 'preview reserves nothing')
  const applied = await ctx.call('/internal/billing/apply-promo', { account_id: 71, promo_code: 'FIEL10' })
  assert.equal(applied.body.applied, true)
  assert.equal((await ctx.call('/internal/billing/apply-promo', { account_id: 71, promo_code: 'FIEL10' })).body.reason, 'account_limit')
  const amounts = []
  for (let cycle = 0; cycle < 3; cycle++) {
    const [sub] = ctx.read("SELECT * FROM ip_subscriptions WHERE account_id = '71'")
    ctx.time.now = sub.paid_until - 4 * DAY
    await ctx.runRenewals()
    const renewal = lastCharge(ctx, 71)
    amounts.push(renewal.amount)
    await confirmPaid(ctx, renewal)
  }
  assert.deepEqual(amounts, [12591, 12591, 13990], '2 discounted renewals, then the normal price')
})

test('Stripe: a recurring code is attached to the running subscription (next invoices only)', async t => {
  const ctx = await start(t)
  await ctx.define({ code: 'FIELUSD', kind: 'percent', percent: 15, applies_to: 'existing', duration: 'forever', plans: ['pro'], markets: ['US'] })
  await deliver(ctx.base, event('evt_s1', 'checkout.session.completed', { id: 'cs_9', mode: 'subscription', subscription: 'sub_7', metadata: {} }))
  const r = await ctx.call('/internal/billing/apply-promo', { account_id: 7, promo_code: 'FIELUSD' })
  assert.equal(r.body.applied, true, JSON.stringify(r.body))
  assert.deepEqual(ctx.stripe.calls.subscriptionUpdates[0].params, { discounts: [{ promotion_code: 'promo_FIELUSD' }] })
  assert.deepEqual(ctx.stripe.calls.coupons.at(-1).params.duration, 'forever')
  assert.equal(ctx.read("SELECT status FROM promo_redemptions WHERE subscription_ref = 'sub_7'")[0].status, 'redeemed')
})
