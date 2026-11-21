import assert from 'node:assert/strict'
import { once } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { test } from 'node:test'
import { createApp } from './app.js'
import { addMonth, infinitePaySettings } from './infinitepay.js'

const TOKEN = 'tok_0123456789abcdefghijklmnopqrstuv'
const DAY = 86_400
const quiet = { warn() {}, error() {} }
const settings = (extra = {}) => infinitePaySettings({ INFINITEPAY_HANDLE: '$hts', INFINITEPAY_WEBHOOK_TOKEN: TOKEN, COMMERCIAL_ORIGIN: 'https://clipper.example', ...extra })

/** Fake InfinitePay: records requests and answers payment_check from a table the test controls. */
function fakeInfinitePay() {
  const calls = []
  const payments = new Map()
  const request = async (path, body) => {
    calls.push({ path, body })
    if (path === '/links') {
      if (fakeInfinitePay.nextLink) { const value = fakeInfinitePay.nextLink; fakeInfinitePay.nextLink = null; return value }
      return { url: `https://checkout.infinitepay.io/hts/${body.order_nsu}` }
    }
    if (path === '/payment_check') {
      if (payments.get('fail')) throw Object.assign(new Error('timeout'), { code: 'ETIMEDOUT' })
      return payments.get(body.order_nsu) ?? { success: true, paid: false }
    }
    throw new Error(`unexpected ${path}`)
  }
  return { request, calls, payments }
}

async function start(t, { ip = settings(), fake = fakeInfinitePay(), mails = [], clock } = {}) {
  const directory = await mkdtemp(join(tmpdir(), 'asc-infinitepay-'))
  const databasePath = join(directory, 'test.sqlite')
  const time = { now: clock ?? 1_780_000_000 }
  const { app, close, runRenewals } = createApp({
    databasePath, infinitepay: ip, infinitepayRequest: fake.request, log: quiet,
    mailer: async message => { mails.push(message) }, clock: () => time.now,
  })
  const server = app.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(async () => {
    await new Promise(resolve => server.close(resolve))
    close()
    await rm(directory, { recursive: true, force: true })
  })
  const base = `http://127.0.0.1:${server.address().port}`
  const db = () => new DatabaseSync(databasePath, { readOnly: true })
  const read = sql => { const handle = db(); try { return handle.prepare(sql).all().map(row => ({ ...row })) } finally { handle.close() } }
  return { base, fake, mails, time, runRenewals, read }
}

const post = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
const buy = (base, extra = {}) => post(`${base}/api/checkout`, { plan: 'creator', market: 'BR', language: 'pt-BR', email: 'Streamer@Example.com', ...extra })

test('a month later, clamped to the last day of the month', () => {
  assert.equal(new Date(addMonth(Date.UTC(2026, 0, 31) / 1000) * 1000).toISOString(), '2026-02-28T00:00:00.000Z')
  assert.equal(new Date(addMonth(Date.UTC(2026, 11, 15, 12) / 1000) * 1000).toISOString(), '2027-01-15T12:00:00.000Z')
})

test('settings reject malformed handles, keys and short webhook tokens', () => {
  const s = infinitePaySettings({ INFINITEPAY_HANDLE: 'bad handle!', INFINITEPAY_API_KEY: 'sk_x', INFINITEPAY_WEBHOOK_TOKEN: 'short' })
  assert.deepEqual([s.handle, s.apiKey, s.webhookToken], ['', '', ''])
  assert.equal(settings().handle, 'hts')
})

test('Brazil stays off without InfinitePay configuration', async t => {
  const { base } = await start(t, { ip: infinitePaySettings({}) })
  assert.deepEqual(await (await fetch(`${base}/api/billing/config`)).json(), { markets: { BR: false, US: false }, portalUrl: '' })
  assert.equal((await buy(base)).status, 503)
})

test('checkout creates a single-use link priced from the catalog and never from the browser', async t => {
  const { base, fake, read } = await start(t, { ip: settings({ INFINITEPAY_API_KEY: 'inchk_secretkey123' }) })
  assert.deepEqual((await (await fetch(`${base}/api/billing/config`)).json()).markets, { BR: true, US: false })
  assert.equal((await buy(base, { email: 'not-an-email' })).status, 422)
  assert.equal((await buy(base, { market: 'US' })).status, 503)

  const response = await buy(base, { price: 1, amount: 1 })
  assert.equal(response.status, 201)
  const { url } = await response.json()
  const { body } = fake.calls.find(call => call.path === '/links')
  assert.equal(url, `https://checkout.infinitepay.io/hts/${body.order_nsu}`)
  assert.match(body.order_nsu, /^asc-[0-9a-f-]{36}$/)
  assert.equal(body.handle, 'hts')
  assert.deepEqual(body.items, [{ quantity: 1, price: 13990, description: 'AI Stream Clipper Creator - 1 mês' }])
  assert.equal(body.redirect_url, 'https://clipper.example/pagamento/infinitepay/pt-BR')
  assert.equal(body.webhook_url, `https://clipper.example/api/infinitepay/webhook/${TOKEN}`)
  assert.deepEqual(body.customer, { email: 'streamer@example.com' })
  assert.equal(body.options.single_payment, true)
  assert.deepEqual(body.options.payment_methods_enabled, ['credit_card', 'pix'])
  assert.deepEqual(read('SELECT status, plan, amount, kind FROM br_charges'), [{ status: 'open', plan: 'creator', amount: 13990, kind: 'first' }])
  assert.deepEqual(read('SELECT email, status, paid_until FROM br_subscriptions'), [{ email: 'streamer@example.com', status: 'pending', paid_until: 0 }])

  // A response without an InfinitePay https URL is refused and the charge is marked failed
  fakeInfinitePay.nextLink = { url: 'https://evil.example/pay' }
  assert.equal((await buy(base)).status, 503)
  assert.equal(read("SELECT COUNT(*) AS n FROM br_charges WHERE status = 'failed'")[0].n, 1)
})

test('the return route forwards only validated identifiers', async t => {
  const { base } = await start(t)
  const order = 'asc-11111111-2222-3333-4444-555555555555'
  const response = await fetch(`${base}/pagamento/infinitepay/en?order_nsu=${order}&slug=abc123&transaction_nsu=tx-1&receipt_url=https://evil.example&capture_method=pix`, { redirect: 'manual' })
  assert.equal(response.status, 303)
  assert.equal(response.headers.get('location'), `/?lang=en&market=BR&checkout=success&provider=infinitepay&order_nsu=${order}&slug=abc123&transaction_nsu=tx-1`)
  const bad = await fetch(`${base}/pagamento/infinitepay/xx?order_nsu=<script>`, { redirect: 'manual' })
  assert.equal(bad.headers.get('location'), '/?lang=pt-BR&market=BR&checkout=success&provider=infinitepay')
})

test('the unsigned webhook only counts after payment_check confirms the stored amount', async t => {
  const { base, fake, time, read } = await start(t)
  await buy(base)
  const order = read('SELECT order_nsu FROM br_charges')[0].order_nsu
  const notice = { invoice_slug: 'slug1', amount: 13990, paid_amount: 13990, installments: 1, capture_method: 'pix', transaction_nsu: 'tx-1', order_nsu: order }
  const hook = (body, token = TOKEN) => post(`${base}/api/infinitepay/webhook/${token}`, body)

  assert.equal((await hook(notice, TOKEN.replace('t', 'x'))).status, 404)
  // Forged notice: InfinitePay says it is not paid
  assert.deepEqual(await (await hook(notice)).json(), { received: true, result: 'processing' })
  // Paid, but for a lower amount than the stored charge
  fake.payments.set(order, { success: true, paid: true, amount: 100, paid_amount: 100, installments: 1, capture_method: 'pix' })
  assert.equal((await (await hook(notice)).json()).result, 'processing')
  assert.equal(read('SELECT status FROM br_subscriptions')[0].status, 'pending')
  // Unknown orders are acknowledged without effect
  assert.equal((await (await hook({ ...notice, order_nsu: 'asc-00000000-0000-0000-0000-000000000000' })).json()).result, 'unknown')
  // Network failure while checking: 400 so InfinitePay retries
  fake.payments.set('fail', true)
  assert.equal((await hook(notice)).status, 400)
  fake.payments.delete('fail')

  // Real payment, with card fees passed to the buyer (paid_amount > amount)
  fake.payments.set(order, { success: true, paid: true, amount: 13990, paid_amount: 14410, installments: 3, capture_method: 'credit_card' })
  assert.equal((await (await hook(notice)).json()).result, 'confirmed')
  const [subscription] = read('SELECT status, paid_until FROM br_subscriptions')
  assert.deepEqual(subscription, { status: 'active', paid_until: addMonth(time.now) })
  assert.deepEqual(read('SELECT status, paid_amount, installments, capture_method, slug FROM br_charges'),
    [{ status: 'paid', paid_amount: 14410, installments: 3, capture_method: 'credit_card', slug: 'slug1' }])

  // Duplicates (webhook again or the browser confirming) do not extend access twice nor call InfinitePay again
  const checks = fake.calls.filter(call => call.path === '/payment_check').length
  assert.equal((await (await hook(notice)).json()).result, 'confirmed')
  const confirm = await post(`${base}/api/infinitepay/confirm`, { orderNsu: order, slug: 'slug1', transactionNsu: 'tx-1' })
  assert.deepEqual(await confirm.json(), { result: 'confirmed', plan: 'creator' })
  assert.equal(fake.calls.filter(call => call.path === '/payment_check').length, checks)
  assert.equal(read('SELECT paid_until FROM br_subscriptions')[0].paid_until, subscription.paid_until)
})

test('renewal links go out before access ends, extend from the due date and expire after the grace period', async t => {
  const mails = []
  const { base, fake, time, read, runRenewals } = await start(t, { mails })
  await buy(base)
  const first = read('SELECT order_nsu FROM br_charges')[0].order_nsu
  fake.payments.set(first, { success: true, paid: true, amount: 13990, paid_amount: 13990, installments: 1, capture_method: 'pix' })
  await post(`${base}/api/infinitepay/confirm`, { orderNsu: first, slug: 's1', transactionNsu: 't1' })
  const paidUntil = read('SELECT paid_until FROM br_subscriptions')[0].paid_until

  time.now = paidUntil - 10 * DAY
  assert.deepEqual(await runRenewals(), { created: 0, emailed: 0 })

  time.now = paidUntil - 4 * DAY
  assert.deepEqual(await runRenewals(), { created: 1, emailed: 1 })
  assert.deepEqual(await runRenewals(), { created: 0, emailed: 0 })
  const renewal = read("SELECT order_nsu, link_url FROM br_charges WHERE kind = 'renewal'")[0]
  assert.equal(mails[0].to, 'streamer@example.com')
  assert.match(mails[0].subject, /Creator/)
  assert.ok(mails[0].text.includes(renewal.link_url))
  assert.ok(mails[0].text.includes('R$ 139,90'))

  // Paying early extends from the current due date, not from today
  fake.payments.set(renewal.order_nsu, { success: true, paid: true, amount: 13990, paid_amount: 13990, installments: 1, capture_method: 'pix' })
  await post(`${base}/api/infinitepay/confirm`, { orderNsu: renewal.order_nsu, slug: 's2', transactionNsu: 't2' })
  const extended = read('SELECT paid_until FROM br_subscriptions')[0].paid_until
  assert.equal(extended, addMonth(paidUntil))

  // Not paying the next one: past due after the due date, expired after 3 days of grace
  time.now = extended - 4 * DAY
  await runRenewals()
  time.now = extended + DAY
  await runRenewals()
  assert.equal(read('SELECT status FROM br_subscriptions')[0].status, 'past_due')
  time.now = extended + 4 * DAY
  await runRenewals()
  assert.equal(read('SELECT status FROM br_subscriptions')[0].status, 'expired')
})
