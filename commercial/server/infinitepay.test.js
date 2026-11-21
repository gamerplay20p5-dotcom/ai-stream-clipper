import assert from 'node:assert/strict'
import { once } from 'node:events'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { test } from 'node:test'
import { createApp } from './app.js'
import { addMonth, createInfinitePay, INFINITEPAY_API, infinitePayClient, infinitePaySettings, linkUrl, WEBHOOK_EVENTS } from './infinitepay.js'

// Contract from https://www.infinitepay.io/checkout-documentacao (no sandbox exists, so it is simulated here)
const DOC = {
  linkBody: ['handle', 'items', 'order_nsu', 'redirect_url', 'webhook_url', 'webhook', 'customer', 'address', 'options'],
  item: ['quantity', 'price', 'description'],
  customer: ['name', 'email', 'phone_number'],
  options: ['assured_installments', 'require_delivery_address', 'single_payment', 'valid_until', 'payment_methods_enabled', 'discounts'],
  paymentMethods: ['credit_card', 'pix'],
  webhookEvents: ['created', 'paid', 'settled', 'canceled', 'refunded', 'payment_failed'],
  paymentCheck: ['handle', 'order_nsu', 'transaction_nsu', 'slug'],
}

const TOKEN = 'tok_0123456789abcdefghijklmnopqrstuv'
const OLD_TOKEN = 'old_0123456789abcdefghijklmnopqrstuv'
const KEY = 'inchk_secretkey123'
const DAY = 86_400
const T0 = 1_780_000_000
const env = extra => ({ INFINITEPAY_HANDLE: '$hts', INFINITEPAY_API_KEY: KEY, INFINITEPAY_WEBHOOK_TOKEN: TOKEN, COMMERCIAL_ORIGIN: 'https://clipper.example', ...extra })
const settings = (extra = {}) => infinitePaySettings(env(extra))

/** Fake InfinitePay following the documented contract; identical /links requests return the same link. */
function fakeApi() {
  const api = { calls: [], payments: new Map(), failLinks: 0, failChecks: false, hangLinks: false, nextLink: null }
  api.request = async (path, body) => {
    api.calls.push({ path, body: structuredClone(body) })
    if (path === '/links') {
      if (api.hangLinks) throw Object.assign(new Error('socket hang up'), { code: 'ECONNRESET' })
      if (api.failLinks > 0) { api.failLinks--; throw Object.assign(new Error('503'), { code: 'http_503' }) }
      if (api.nextLink) { const value = api.nextLink; api.nextLink = null; return value }
      return { url: `https://checkout.infinitepay.io/hts/${body.order_nsu}` }
    }
    if (path === '/payment_check') {
      if (api.failChecks) throw Object.assign(new Error('timeout'), { code: 'ETIMEDOUT' })
      return api.payments.get(body.order_nsu) ?? { success: true, paid: false }
    }
    throw new Error(`undocumented endpoint ${path}`)
  }
  api.pay = (order, extra = {}) => api.payments.set(order, { success: true, paid: true, amount: 13990, paid_amount: 13990, installments: 1, capture_method: 'pix', ...extra })
  return api
}

function captureLog() {
  const lines = []
  const keep = (...args) => lines.push(args.map(String).join(' '))
  return { lines, log: { warn: keep, error: keep } }
}

async function start(t, { ip = settings(), api = fakeApi(), mailer, logger = captureLog(), directory } = {}) {
  const dir = directory ?? await mkdtemp(join(tmpdir(), 'asc-ip-'))
  const handles = []
  const databasePath = join(dir, 'test.sqlite')
  const time = { now: T0 }
  const mails = []
  const { app, close, runRenewals } = createApp({
    databasePath, infinitepay: ip, infinitepayRequest: api.request, log: logger.log, clock: () => time.now,
    mailer: mailer ?? (async message => { mails.push(message) }),
  })
  const server = app.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const stop = async () => { await new Promise(resolve => server.close(resolve)); close() }
  t.after(async () => {
    await stop().catch(() => {})
    // Close every extra connection before deleting the database (Windows keeps open files locked)
    for (const handle of handles) { try { await handle.close() } catch {} }
    if (!directory) await rm(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 })
  })
  const base = `http://127.0.0.1:${server.address().port}`
  const read = (sql, ...params) => { const db = new DatabaseSync(databasePath, { readOnly: true }); try { return db.prepare(sql).all(...params).map(row => ({ ...row })) } finally { db.close() } }
  // A second instance on the same database, as the admin CLI or another worker would be
  const admin = () => {
    const db = new DatabaseSync(databasePath, { timeout: 2_000 })
    handles.push(db)
    return createInfinitePay({ db, settings: ip, request: api.request, log: logger.log, now: () => time.now })
  }
  return { base, api, time, read, runRenewals, mails, logger, admin, stop, dir, databasePath, handles }
}

const post = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
const buy = (base, extra = {}) => post(`${base}/api/checkout`, { plan: 'creator', market: 'BR', language: 'pt-BR', email: 'Streamer@Example.com', ...extra })
const hook = (base, body, token = TOKEN) => post(`${base}/api/infinitepay/webhook/${token}`, body)
const notice = (order, slug = 'slug1', tx = 'tx-1') => ({ invoice_slug: slug, amount: 13990, paid_amount: 13990, installments: 1, capture_method: 'pix', transaction_nsu: tx, order_nsu: order, receipt_url: 'https://comprovante.example/1', items: [] })
const confirm = (base, order, slug = 'slug1', tx = 'tx-1') => post(`${base}/api/infinitepay/confirm`, { orderNsu: order, slug, transactionNsu: tx })

/** Buys, pays and confirms; returns the order and the resulting paid_until. */
async function activate(ctx, email = 'streamer@example.com') {
  await buy(ctx.base, { email })
  const [{ order_nsu: order }] = ctx.read("SELECT c.order_nsu FROM ip_charges c JOIN ip_subscriptions s ON s.id = c.subscription_id WHERE s.email = ? AND c.kind = 'first'", email)
  ctx.api.pay(order)
  await confirm(ctx.base, order)
  const [subscription] = ctx.read('SELECT * FROM ip_subscriptions WHERE email = ?', email)
  return { order, subscription }
}

test('dates: one calendar month, clamped to the last day', () => {
  assert.equal(new Date(addMonth(Date.UTC(2026, 0, 31) / 1000) * 1000).toISOString(), '2026-02-28T00:00:00.000Z')
  assert.equal(new Date(addMonth(Date.UTC(2028, 0, 31) / 1000) * 1000).toISOString(), '2028-02-29T00:00:00.000Z')
  assert.equal(new Date(addMonth(Date.UTC(2026, 11, 15, 12) / 1000) * 1000).toISOString(), '2027-01-15T12:00:00.000Z')
})

test('the API key is mandatory: no checkout with reusable or never-expiring links', async t => {
  for (const missing of [{ INFINITEPAY_API_KEY: '' }, { INFINITEPAY_API_KEY: 'sk_wrong' }, { INFINITEPAY_WEBHOOK_TOKEN: 'short' }, { COMMERCIAL_ORIGIN: 'http://clipper.example' }, { INFINITEPAY_HANDLE: 'bad handle!' }]) {
    const { base } = await start(t, { ip: settings(missing) })
    assert.deepEqual((await (await fetch(`${base}/api/billing/config`)).json()).markets.BR, false, JSON.stringify(missing))
    assert.equal((await buy(base)).status, 503)
  }
  // Plain http is accepted only for local development
  assert.equal(settings({ COMMERCIAL_ORIGIN: 'http://127.0.0.1:8790' }).origin, 'http://127.0.0.1:8790')
})

test('contract: requests use only documented endpoints and fields, always single-use and expiring', async t => {
  const ctx = await start(t)
  await activate(ctx)
  ctx.time.now = ctx.read('SELECT paid_until FROM ip_subscriptions')[0].paid_until - 4 * DAY
  await ctx.runRenewals()
  const links = ctx.api.calls.filter(call => call.path === '/links')
  assert.equal(links.length, 2)
  for (const { body } of links) {
    assert.deepEqual(Object.keys(body).filter(key => !DOC.linkBody.includes(key)), [])
    assert.equal(body.handle, 'hts')
    assert.match(body.order_nsu, /^asc-[0-9a-f-]{36}$/)
    for (const item of body.items) assert.deepEqual(Object.keys(item).sort(), [...DOC.item].sort())
    assert.ok(Number.isInteger(body.items[0].price) && body.items[0].price === 13990, 'price in centavos from the catalog')
    assert.deepEqual(Object.keys(body.customer).filter(key => !DOC.customer.includes(key)), [])
    assert.deepEqual(Object.keys(body.options).filter(key => !DOC.options.includes(key)), [])
    assert.equal(body.options.single_payment, true)
    assert.ok(body.options.payment_methods_enabled.every(method => DOC.paymentMethods.includes(method)))
    const validUntil = Date.parse(body.options.valid_until) / 1000
    const createdAt = ctx.read('SELECT created_at FROM ip_charges WHERE order_nsu = ?', body.order_nsu)[0].created_at
    assert.ok(validUntil > createdAt && validUntil <= createdAt + 365 * DAY, 'valid_until in the future and within one year')
    assert.deepEqual(body.webhook, { url: `https://clipper.example/api/infinitepay/webhook/${TOKEN}`, events: WEBHOOK_EVENTS })
    assert.ok(WEBHOOK_EVENTS.every(event => DOC.webhookEvents.includes(event)))
    assert.equal(body.webhook_url, undefined)
  }
  for (const { body } of ctx.api.calls.filter(call => call.path === '/payment_check')) assert.deepEqual(Object.keys(body).sort(), [...DOC.paymentCheck].sort())

  // The real client: documented base URL, POST, JSON and the key only in the X-Api-Key header
  const seen = []
  const client = infinitePayClient(settings(), async (url, init) => { seen.push({ url, init }); return new Response('{"url":"https://checkout.infinitepay.io/x"}', { status: 200 }) })
  await client('/links', { handle: 'hts' })
  assert.equal(seen[0].url, `${INFINITEPAY_API}/links`)
  assert.equal(seen[0].init.method, 'POST')
  assert.equal(seen[0].init.headers['X-Api-Key'], KEY)
  assert.ok(!seen[0].init.body.includes(KEY))
  // Unknown response shapes or foreign URLs are never used as payment links
  assert.equal(linkUrl({ url: 'https://evil.example/pay' }), '')
  assert.equal(linkUrl({ link: 'http://checkout.infinitepay.io/x' }), '')
  assert.equal(linkUrl({ checkout_url: 'https://checkout.infinitepay.io/x' }), 'https://checkout.infinitepay.io/x')
})

test('checkout: catalog price, email required, double clicks reuse the link, customers stay isolated', async t => {
  const ctx = await start(t)
  assert.equal((await buy(ctx.base, { email: 'not-an-email' })).status, 422)
  const first = await (await buy(ctx.base, { price: 1, amount: 1 })).json()
  const again = await (await buy(ctx.base)).json()
  assert.equal(again.url, first.url)
  assert.equal(ctx.api.calls.filter(call => call.path === '/links').length, 1)
  const other = await (await buy(ctx.base, { email: 'someone@example.com' })).json()
  assert.notEqual(other.url, first.url)
  const pro = await (await buy(ctx.base, { plan: 'pro' })).json()
  assert.notEqual(pro.url, first.url)
  assert.deepEqual(ctx.read("SELECT plan, amount FROM ip_charges ORDER BY created_at, plan").map(row => row.amount).sort(), [13990, 13990, 27990])

  // Link creation failure: the buyer gets 503 and the charge is closed as failed (nothing reusable)
  ctx.api.failLinks = 1
  assert.equal((await buy(ctx.base, { email: 'fail@example.com' })).status, 503)
  ctx.api.nextLink = { url: 'https://evil.example/pay' }
  assert.equal((await buy(ctx.base, { email: 'evil@example.com' })).status, 503)
  assert.equal(ctx.read("SELECT COUNT(*) AS n FROM ip_charges WHERE status = 'failed'")[0].n, 2)
})

test('payments count only after payment_check confirms the stored amount', async t => {
  const ctx = await start(t)
  await buy(ctx.base)
  const [{ order_nsu: order }] = ctx.read('SELECT order_nsu FROM ip_charges')
  assert.equal((await hook(ctx.base, notice(order), TOKEN.replace('t', 'x'))).status, 404)
  // Forged notice: not paid at InfinitePay
  assert.equal((await (await hook(ctx.base, notice(order))).json()).result, 'processing')
  // Paid, but less than the charge
  ctx.api.pay(order, { amount: 100, paid_amount: 100 })
  assert.equal((await (await hook(ctx.base, notice(order))).json()).result, 'processing')
  // paid_amount lower than amount
  ctx.api.pay(order, { amount: 13990, paid_amount: 13000 })
  assert.equal((await (await confirm(ctx.base, order)).json()).result, 'processing')
  assert.deepEqual(ctx.read("SELECT type FROM ip_ledger WHERE type = 'amount_mismatch'"), [{ type: 'amount_mismatch' }])
  assert.equal(ctx.read('SELECT status FROM ip_subscriptions')[0].status, 'pending')
  // Unknown and malformed orders reveal nothing
  assert.deepEqual(await (await confirm(ctx.base, 'asc-00000000-0000-0000-0000-000000000000')).json(), { result: 'unknown', plan: '' })
  assert.deepEqual(await (await confirm(ctx.base, "' OR 1=1 --")).json(), { result: 'unknown', plan: '' })
  // Network failure while checking: 400 so InfinitePay delivers again
  ctx.api.failChecks = true
  assert.equal((await hook(ctx.base, notice(order))).status, 400)
  ctx.api.failChecks = false

  // Real payment with card fees passed to the buyer
  ctx.api.pay(order, { paid_amount: 14410, installments: 3, capture_method: 'credit_card' })
  // Webhook and browser at the same time, plus a duplicate webhook: one payment, one month
  const results = await Promise.all([hook(ctx.base, notice(order)), confirm(ctx.base, order), hook(ctx.base, notice(order))].map(async r => (await r).json()))
  assert.ok(results.every(r => r.result === 'confirmed'))
  assert.deepEqual(ctx.read('SELECT status, paid_until FROM ip_subscriptions'), [{ status: 'active', paid_until: addMonth(T0) }])
  assert.equal(ctx.read("SELECT COUNT(*) AS n FROM ip_ledger WHERE type = 'payment_confirmed'")[0].n, 1)
  assert.deepEqual(ctx.read('SELECT status, paid_amount, installments, period_start, period_end FROM ip_charges'),
    [{ status: 'paid', paid_amount: 14410, installments: 3, period_start: T0, period_end: addMonth(T0) }])
  // The confirm endpoint returns only outcome and plan
  assert.deepEqual(Object.keys(await (await confirm(ctx.base, order)).json()).sort(), ['plan', 'result'])
})

test('one logical charge per cycle across concurrent workers, crashes and retries', async t => {
  const ctx = await start(t)
  const { subscription } = await activate(ctx)
  ctx.time.now = subscription.paid_until - 4 * DAY
  // Two processes on the same database run the job at the same time
  const other = ctx.admin()
  await Promise.all([ctx.runRenewals(), other.runRenewals(), ctx.runRenewals()])
  assert.equal(ctx.read("SELECT COUNT(*) AS n FROM ip_charges WHERE kind = 'renewal'")[0].n, 1)
  assert.equal(ctx.api.calls.filter(call => call.path === '/links').length, 2)

  // Restart in the middle of a renewal: the link request failed and the process died
  const second = await activate(ctx, 'second@example.com')
  ctx.time.now = second.subscription.paid_until - 4 * DAY
  ctx.api.hangLinks = true
  await ctx.runRenewals()
  ctx.api.hangLinks = false
  const [stuck] = ctx.read("SELECT order_nsu, status, valid_until FROM ip_charges WHERE kind = 'renewal' AND subscription_id = ?", second.subscription.id)
  assert.equal(stuck.status, 'creating')
  await ctx.stop()
  const restarted = await start(t, { api: ctx.api, directory: ctx.dir })
  ctx.handles.push({ close: () => restarted.stop().catch(() => {}) })
  restarted.time.now = ctx.time.now + 5 * 60
  await restarted.runRenewals()
  assert.equal(restarted.read('SELECT status FROM ip_charges WHERE order_nsu = ?', stuck.order_nsu)[0].status, 'creating', 'not retried while it may still be in flight')
  restarted.time.now = ctx.time.now + 11 * 60
  await restarted.runRenewals()
  const retried = ctx.api.calls.filter(call => call.path === '/links' && call.body.order_nsu === stuck.order_nsu)
  assert.equal(retried.length, 2, 'retried once more')
  assert.deepEqual(retried[1].body, retried[0].body, 'identical body, so InfinitePay returns the same link')
  assert.equal(restarted.read('SELECT status FROM ip_charges WHERE order_nsu = ?', stuck.order_nsu)[0].status, 'open')
  assert.equal(restarted.read("SELECT COUNT(*) AS n FROM ip_charges WHERE kind = 'renewal' AND subscription_id = ?", second.subscription.id)[0].n, 1)
})

test('a renewal link that keeps failing gives up after five attempts and is reported', async t => {
  const ctx = await start(t)
  const { subscription } = await activate(ctx)
  ctx.time.now = subscription.paid_until - 4 * DAY
  ctx.api.failLinks = 99
  for (let i = 0; i < 6; i++) { await ctx.runRenewals(); ctx.time.now += 11 * 60 }
  assert.equal(ctx.read("SELECT status, link_attempts FROM ip_charges WHERE kind = 'renewal'")[0].status, 'failed')
  assert.equal(ctx.read("SELECT link_attempts FROM ip_charges WHERE kind = 'renewal'")[0].link_attempts, 5)
  assert.equal(ctx.read("SELECT COUNT(*) AS n FROM ip_ledger WHERE type = 'link_gave_up'")[0].n, 1)
})

test('renewal state machine: links only for active and past due, valid until the end of grace', async t => {
  const ctx = await start(t)
  // pending: no renewal link, abandoned once its first link expires
  await buy(ctx.base, { email: 'never-pays@example.com' })
  const { subscription } = await activate(ctx)
  const due = subscription.paid_until

  ctx.time.now = due - 10 * DAY
  assert.equal((await ctx.runRenewals()).created, 0)
  ctx.time.now = due - 4 * DAY
  assert.equal((await ctx.runRenewals()).created, 1)
  const [renewal] = ctx.read("SELECT order_nsu, valid_until, cycle_key FROM ip_charges WHERE kind = 'renewal'")
  assert.equal(renewal.valid_until, due + 3 * DAY)
  assert.equal(renewal.cycle_key, `due:${due}`)
  assert.equal(ctx.read("SELECT status FROM ip_subscriptions WHERE email = 'never-pays@example.com'")[0].status, 'abandoned')

  // Due date passes unpaid: past due, still no second link for the same cycle
  ctx.time.now = due + DAY
  await ctx.runRenewals()
  assert.equal(ctx.read('SELECT status FROM ip_subscriptions WHERE id = ?', subscription.id)[0].status, 'past_due')
  assert.equal(ctx.read("SELECT COUNT(*) AS n FROM ip_charges WHERE kind = 'renewal'")[0].n, 1)

  // Paying late (inside grace) extends from the due date, not from today
  ctx.api.pay(renewal.order_nsu)
  await confirm(ctx.base, renewal.order_nsu)
  const [extended] = ctx.read('SELECT status, paid_until FROM ip_subscriptions WHERE id = ?', subscription.id)
  assert.deepEqual(extended, { status: 'active', paid_until: addMonth(due) })

  // Next cycle unpaid: link expires together with the grace period and the subscription expires
  ctx.time.now = extended.paid_until - 4 * DAY
  await ctx.runRenewals()
  ctx.time.now = extended.paid_until + 3 * DAY
  await ctx.runRenewals()
  assert.equal(ctx.read('SELECT status FROM ip_subscriptions WHERE id = ?', subscription.id)[0].status, 'expired')
  assert.equal(ctx.read("SELECT status FROM ip_charges WHERE cycle_key = ?", `due:${extended.paid_until}`)[0].status, 'expired')
  // Expired subscriptions never get new links
  ctx.time.now += 40 * DAY
  assert.equal((await ctx.runRenewals()).created, 0)

  // A payment that arrives for the expired link anyway (race at the deadline) is honored from today
  const [late] = ctx.read("SELECT order_nsu FROM ip_charges WHERE cycle_key = ?", `due:${extended.paid_until}`)
  ctx.api.pay(late.order_nsu)
  await hook(ctx.base, notice(late.order_nsu))
  assert.deepEqual(ctx.read('SELECT status, paid_until FROM ip_subscriptions WHERE id = ?', subscription.id), [{ status: 'active', paid_until: addMonth(ctx.time.now) }])
})

test('SMTP failures are retried, give up after five attempts and never block the job', async t => {
  let fail = true
  const sent = []
  const ctx = await start(t, { mailer: async message => { if (fail) throw Object.assign(new Error('421'), { code: 'ECONNECTION' }); sent.push(message) } })
  const { subscription } = await activate(ctx)
  ctx.time.now = subscription.paid_until - 4 * DAY
  assert.deepEqual(await ctx.runRenewals(), { created: 1, emailed: 0 })
  assert.equal(ctx.read('SELECT email_attempts FROM ip_charges WHERE kind = ?', 'renewal')[0].email_attempts, 1)
  fail = false
  ctx.time.now += 3600
  assert.equal((await ctx.runRenewals()).emailed, 1)
  assert.equal(sent.length, 1)
  assert.match(sent[0].text, /R\$ 139,90/)
  assert.ok(sent[0].text.includes(ctx.read("SELECT link_url FROM ip_charges WHERE kind = 'renewal'")[0].link_url))
  ctx.time.now += 3600
  assert.equal((await ctx.runRenewals()).emailed, 0, 'sent once')

  // Permanent failure on another subscription: five attempts, then reported for manual sending
  fail = true
  const second = await activate(ctx, 'second@example.com')
  ctx.time.now = second.subscription.paid_until - 4 * DAY
  for (let i = 0; i < 7; i++) { await ctx.runRenewals(); ctx.time.now += 3600 }
  assert.equal(ctx.read('SELECT email_attempts FROM ip_charges WHERE kind = ? AND subscription_id = ?', 'renewal', second.subscription.id)[0].email_attempts, 5)
  assert.equal(ctx.read("SELECT COUNT(*) AS n FROM ip_ledger WHERE type = 'email_gave_up'")[0].n, 1)
})

test('refunds: review on reversal signals, operator records the refund, access shrinks exactly and history is kept', async t => {
  const ctx = await start(t)
  const { order, subscription } = await activate(ctx)
  const admin = ctx.admin()
  // InfinitePay now reports the paid charge as unpaid (refund in the app or chargeback)
  ctx.api.payments.set(order, { success: true, paid: false })
  assert.equal((await (await hook(ctx.base, notice(order))).json()).result, 'review')
  assert.equal(ctx.read('SELECT status FROM ip_charges WHERE order_nsu = ?', order)[0].status, 'reversal_review')
  // Access is not removed automatically: the API does not document refund semantics
  assert.equal(ctx.read('SELECT status FROM ip_subscriptions')[0].status, 'active')
  // A forged notice with other identifiers for a paid charge does not trigger anything
  assert.equal((await (await hook(ctx.base, notice(order, 'other', 'other'))).json()).result, 'confirmed')

  const { changed, subscription: after } = admin.refund(order, { reason: 'Estorno feito no app em 21/11' })
  assert.equal(changed, true)
  assert.deepEqual([after.status, after.paid_until], ['refunded', subscription.paid_until - (addMonth(T0) - T0)])
  assert.equal(admin.refund(order, { reason: 'again' }).changed, false, 'idempotent')
  assert.deepEqual(await (await confirm(ctx.base, order)).json(), { result: 'refunded', plan: 'creator' })
  assert.throws(() => admin.refund('asc-00000000-0000-0000-0000-000000000000', { reason: 'x' }), /Unknown order/)

  const history = ctx.read('SELECT type, amount FROM ip_ledger WHERE subscription_id = ? ORDER BY id', subscription.id)
  assert.deepEqual(history.map(row => row.type), ['link_created', 'payment_confirmed', 'status_active', 'reversal_reported', 'refund_recorded', 'status_refunded'])
  assert.equal(history.find(row => row.type === 'refund_recorded').amount, -13990)
  // The ledger is append-only
  const raw = new DatabaseSync(ctx.databasePath)
  assert.throws(() => raw.exec('DELETE FROM ip_ledger'), /append-only/)
  assert.throws(() => raw.exec("UPDATE ip_ledger SET amount = 0"), /append-only/)
  raw.close()
})

test('refunding an earlier month keeps later paid months, and cancel stops renewals without reactivation', async t => {
  const ctx = await start(t)
  const { order, subscription } = await activate(ctx)
  ctx.time.now = subscription.paid_until - 4 * DAY
  await ctx.runRenewals()
  const [renewal] = ctx.read("SELECT order_nsu FROM ip_charges WHERE kind = 'renewal'")
  ctx.api.pay(renewal.order_nsu)
  await confirm(ctx.base, renewal.order_nsu)
  const twoMonths = ctx.read('SELECT paid_until FROM ip_subscriptions')[0].paid_until
  const admin = ctx.admin()
  const { subscription: after } = admin.refund(order, { reason: 'primeiro mÃªs estornado' })
  assert.equal(after.status, 'active')
  assert.equal(after.paid_until, twoMonths - (addMonth(T0) - T0))

  admin.cancel(subscription.id, 'cliente pediu')
  ctx.time.now = after.paid_until - 4 * DAY
  assert.equal((await ctx.runRenewals()).created, 0)
  // If an old link is paid after the cancellation, the payment is recorded but access is not reactivated
  const cycle = `due:${after.paid_until}`
  assert.equal(ctx.read('SELECT COUNT(*) AS n FROM ip_charges WHERE cycle_key = ?', cycle)[0].n, 0)
  const third = await activate(ctx, 'third@example.com')
  admin.cancel(third.subscription.id, 'teste')
  ctx.time.now = third.subscription.paid_until - 4 * DAY
  assert.equal((await ctx.runRenewals()).created, 0)
})

test('payment after cancellation is recorded for review and does not reactivate', async t => {
  const ctx = await start(t)
  await buy(ctx.base)
  const [{ order_nsu: order, subscription_id: id }] = ctx.read('SELECT order_nsu, subscription_id FROM ip_charges')
  ctx.admin().cancel(id, 'desistiu antes de pagar')
  ctx.api.pay(order)
  await hook(ctx.base, notice(order))
  assert.equal(ctx.read('SELECT status FROM ip_subscriptions')[0].status, 'canceled')
  assert.deepEqual(ctx.read("SELECT type FROM ip_ledger WHERE type IN ('payment_confirmed', 'payment_after_cancel') ORDER BY id").map(row => row.type), ['payment_confirmed', 'payment_after_cancel'])
})

test('webhook token: previous token accepted during rotation, never logged, return route forwards only validated ids', async t => {
  const logger = captureLog()
  const ctx = await start(t, { ip: settings({ INFINITEPAY_WEBHOOK_TOKEN_PREVIOUS: OLD_TOKEN }), logger })
  await buy(ctx.base)
  const [{ order_nsu: order }] = ctx.read('SELECT order_nsu FROM ip_charges')
  ctx.api.pay(order)
  assert.equal((await hook(ctx.base, notice(order), OLD_TOKEN)).status, 200)
  // Force error paths that log
  ctx.api.failChecks = true
  await hook(ctx.base, notice(order, 's', 't'))
  ctx.api.failLinks = 1
  await buy(ctx.base, { email: 'x@example.com' })
  const logs = logger.lines.join('\n')
  for (const secret of [TOKEN, OLD_TOKEN, KEY, 'streamer@example.com', 'x@example.com']) assert.ok(!logs.includes(secret), `log leaks ${secret.slice(0, 6)}`)

  const response = await fetch(`${ctx.base}/pagamento/infinitepay/en?order_nsu=${order}&slug=abc123&transaction_nsu=tx-1&receipt_url=https://evil.example`, { redirect: 'manual' })
  assert.equal(response.headers.get('location'), `/?lang=en&market=BR&checkout=success&provider=infinitepay&order_nsu=${order}&slug=abc123&transaction_nsu=tx-1`)
  assert.equal((await fetch(`${ctx.base}/pagamento/infinitepay/xx?order_nsu=<script>`, { redirect: 'manual' })).headers.get('location'), '/?lang=pt-BR&market=BR&checkout=success&provider=infinitepay')
})
