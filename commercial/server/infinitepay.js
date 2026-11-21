// InfinitePay "Checkout Integrado" for the Brazilian market (BRL, Pix and card).
// Docs: https://www.infinitepay.io/checkout-documentacao
//
// The API only creates one-off payment links: it has no recurring billing, no refunds and no sandbox,
// and its webhook is NOT signed. So this module:
//   - creates one single-use link per month of access, priced from catalog.json;
//   - never trusts a webhook or a redirect by itself: every payment is confirmed with POST /payment_check
//     (paid: true and the charged amount), and the webhook URL carries a secret token as an extra filter;
//   - grants one calendar month per confirmed payment and runs its own renewal schedule, emailing the
//     next link a few days before access ends.
import { randomUUID, timingSafeEqual } from 'node:crypto'
import express from 'express'
import { rateLimit } from 'express-rate-limit'
import catalog from '../catalog.json' with { type: 'json' }

export const INFINITEPAY_MARKET = 'BR'
const API = 'https://api.checkout.infinitepay.io'
const DAY = 86_400
const ORDER = /^asc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const SLUG = /^[A-Za-z0-9_-]{1,120}$/

export function infinitePaySettings(env = process.env) {
  const handle = (env.INFINITEPAY_HANDLE ?? '').replace(/^\$/, '')
  const apiKey = env.INFINITEPAY_API_KEY ?? ''
  const token = env.INFINITEPAY_WEBHOOK_TOKEN ?? ''
  return {
    handle: /^[A-Za-z0-9_.-]{2,60}$/.test(handle) ? handle : '',
    apiKey: /^inchk_[A-Za-z0-9_-]{8,}$/.test(apiKey) ? apiKey : '',
    webhookToken: /^[A-Za-z0-9_-]{32,128}$/.test(token) ? token : '',
    origin: env.COMMERCIAL_ORIGIN ? new URL(env.COMMERCIAL_ORIGIN).origin : '',
    renewDaysBefore: Number(env.INFINITEPAY_RENEW_DAYS_BEFORE ?? 5),
    graceDays: Number(env.INFINITEPAY_GRACE_DAYS ?? 3),
    linkValidDays: 7,
  }
}

/** Minimal JSON client for the two documented endpoints. */
export function infinitePayClient(settings, fetchImpl = fetch) {
  return async (path, body) => {
    const response = await fetchImpl(`${API}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(settings.apiKey ? { 'X-Api-Key': settings.apiKey } : {}) },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw Object.assign(new Error(`InfinitePay ${path} answered ${response.status}`), { code: data?.error ?? `http_${response.status}` })
    return data
  }
}

/** Same day next month in UTC, clamped to the month's last day (31 Jan -> 28/29 Feb). */
export function addMonth(seconds) {
  const date = new Date(seconds * 1000)
  const day = date.getUTCDate()
  date.setUTCDate(1)
  date.setUTCMonth(date.getUTCMonth() + 1)
  const last = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate()
  date.setUTCDate(Math.min(day, last))
  return Math.floor(date.getTime() / 1000)
}

function linkUrl(response) {
  // The response field is not documented; accept the usual names but only an https InfinitePay URL
  for (const value of [response?.url, response?.link, response?.checkout_url, response?.payment_url, response?.data?.url]) {
    if (typeof value !== 'string') continue
    try {
      const url = new URL(value)
      if (url.protocol === 'https:' && (url.hostname === 'infinitepay.io' || url.hostname.endsWith('.infinitepay.io'))) return url.href
    } catch { /* not a URL */ }
  }
  return ''
}

function migrate(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS br_subscriptions (
      id TEXT PRIMARY KEY,
      email TEXT NOT NULL,
      plan TEXT NOT NULL,
      language TEXT NOT NULL,
      status TEXT NOT NULL,
      paid_until INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    ) STRICT;
    CREATE TABLE IF NOT EXISTS br_charges (
      order_nsu TEXT PRIMARY KEY,
      subscription_id TEXT NOT NULL REFERENCES br_subscriptions(id),
      kind TEXT NOT NULL,
      plan TEXT NOT NULL,
      amount INTEGER NOT NULL,
      status TEXT NOT NULL,
      link_url TEXT NOT NULL DEFAULT '',
      valid_until INTEGER,
      emailed_at INTEGER,
      slug TEXT NOT NULL DEFAULT '',
      transaction_nsu TEXT NOT NULL DEFAULT '',
      capture_method TEXT NOT NULL DEFAULT '',
      paid_amount INTEGER,
      installments INTEGER,
      receipt_url TEXT NOT NULL DEFAULT '',
      created_at INTEGER NOT NULL,
      paid_at INTEGER
    ) STRICT;
    CREATE INDEX IF NOT EXISTS br_charges_subscription ON br_charges(subscription_id, status);
  `)
}

const copy = {
  'pt-BR': {
    item: plan => `AI Stream Clipper ${plan} - 1 mês`,
    subject: plan => `Renove seu plano ${plan} do AI Stream Clipper`,
    body: ({ plan, amount, until, url }) => `Olá!\n\nSeu acesso ao plano ${plan} do AI Stream Clipper vai até ${until}.\n\nPara renovar por mais um mês (${amount}), pague com Pix ou cartão neste link:\n${url}\n\nO link vale por 7 dias. Para não renovar, basta não pagar: o acesso termina na data acima.\n\nHTS`,
    date: 'pt-BR',
  },
  en: {
    item: plan => `AI Stream Clipper ${plan} - 1 month`,
    subject: plan => `Renew your AI Stream Clipper ${plan} plan`,
    body: ({ plan, amount, until, url }) => `Hi!\n\nYour AI Stream Clipper ${plan} access runs until ${until}.\n\nTo renew for another month (${amount}), pay by Pix or card here:\n${url}\n\nThe link is valid for 7 days. If you don't want to renew, just don't pay: access ends on the date above.\n\nHTS`,
    date: 'en-US',
  },
}

export function createInfinitePay({ db, settings, request, mailer = null, log = console, now = () => Math.floor(Date.now() / 1000) }) {
  migrate(db)
  const enabled = Boolean(settings.handle && settings.origin && settings.webhookToken && request)
  const webhookUrl = `${settings.origin}/api/infinitepay/webhook/${settings.webhookToken}`

  const q = {
    insertSubscription: db.prepare('INSERT INTO br_subscriptions (id, email, plan, language, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)'),
    insertCharge: db.prepare('INSERT INTO br_charges (order_nsu, subscription_id, kind, plan, amount, status, valid_until, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'),
    setLink: db.prepare("UPDATE br_charges SET link_url = ?, status = 'open' WHERE order_nsu = ?"),
    failCharge: db.prepare("UPDATE br_charges SET status = 'failed' WHERE order_nsu = ?"),
    charge: db.prepare('SELECT * FROM br_charges WHERE order_nsu = ?'),
    subscription: db.prepare('SELECT * FROM br_subscriptions WHERE id = ?'),
    markPaid: db.prepare(`UPDATE br_charges SET status = 'paid', slug = ?, transaction_nsu = ?, capture_method = ?, paid_amount = ?, installments = ?, receipt_url = ?, paid_at = ?
      WHERE order_nsu = ? AND status <> 'paid'`),
    extend: db.prepare("UPDATE br_subscriptions SET status = 'active', paid_until = ?, updated_at = ? WHERE id = ?"),
    dueForRenewal: db.prepare(`SELECT s.* FROM br_subscriptions s WHERE s.status IN ('active', 'past_due') AND s.paid_until - ? <= ?
      AND NOT EXISTS (SELECT 1 FROM br_charges c WHERE c.subscription_id = s.id AND c.kind = 'renewal' AND c.status IN ('creating', 'open') AND (c.valid_until IS NULL OR c.valid_until > ?))`),
    unsent: db.prepare(`SELECT c.*, s.email, s.language, s.paid_until FROM br_charges c JOIN br_subscriptions s ON s.id = c.subscription_id
      WHERE c.kind = 'renewal' AND c.status = 'open' AND c.emailed_at IS NULL AND (c.valid_until IS NULL OR c.valid_until > ?)`),
    markEmailed: db.prepare('UPDATE br_charges SET emailed_at = ? WHERE order_nsu = ?'),
    pastDue: db.prepare("UPDATE br_subscriptions SET status = 'past_due', updated_at = ? WHERE status = 'active' AND paid_until < ?"),
    expire: db.prepare("UPDATE br_subscriptions SET status = 'expired', updated_at = ? WHERE status = 'past_due' AND paid_until < ?"),
    expireLinks: db.prepare("UPDATE br_charges SET status = 'expired' WHERE status IN ('creating', 'open') AND valid_until IS NOT NULL AND valid_until <= ?"),
    abandon: db.prepare("UPDATE br_subscriptions SET status = 'abandoned', updated_at = ? WHERE status = 'pending' AND created_at < ?"),
  }

  /** Creates a single-use payment link for one month of the plan and stores it as an open charge. */
  async function createLink(subscription, kind) {
    const orderNsu = `asc-${randomUUID()}`
    const amount = catalog.plans[subscription.plan].monthlyMinor[INFINITEPAY_MARKET]
    const created = now()
    const validUntil = settings.apiKey ? created + settings.linkValidDays * DAY : null
    q.insertCharge.run(orderNsu, subscription.id, kind, subscription.plan, amount, 'creating', validUntil, created)
    const t = copy[subscription.language] ?? copy['pt-BR']
    try {
      const response = await request('/links', {
        handle: settings.handle,
        items: [{ quantity: 1, price: amount, description: t.item(catalog.plans[subscription.plan].name) }],
        order_nsu: orderNsu,
        // A path without a query string, so it does not matter how InfinitePay appends its parameters
        redirect_url: `${settings.origin}/pagamento/infinitepay/${subscription.language}`,
        webhook_url: webhookUrl,
        customer: { email: subscription.email },
        ...(settings.apiKey ? {
          options: {
            single_payment: true,
            valid_until: new Date(validUntil * 1000).toISOString(),
            payment_methods_enabled: ['credit_card', 'pix'],
          },
        } : {}),
      })
      const url = linkUrl(response)
      if (!url) throw Object.assign(new Error('InfinitePay link response without a usable URL'), { code: 'invalid_link_response' })
      q.setLink.run(url, orderNsu)
      return { orderNsu, url, amount, validUntil }
    } catch (error) {
      q.failCharge.run(orderNsu)
      throw error
    }
  }

  async function createCheckout({ plan, language, email }) {
    if (!enabled) throw Object.assign(new Error('InfinitePay is not configured'), { code: 'disabled' })
    const id = randomUUID()
    q.insertSubscription.run(id, email, plan, language, 'pending', now(), now())
    const { url } = await createLink({ id, plan, language, email }, 'first')
    return url
  }

  /**
   * Confirms a charge with POST /payment_check and, only if InfinitePay reports it paid for the
   * stored amount, records the payment and extends access by one month. Idempotent.
   */
  async function verify({ orderNsu, slug, transactionNsu }) {
    if (!ORDER.test(orderNsu ?? '') || !SLUG.test(slug ?? '') || !SLUG.test(transactionNsu ?? '')) return { result: 'unknown', plan: '' }
    const charge = q.charge.get(orderNsu)
    if (!charge) return { result: 'unknown', plan: '' }
    if (charge.status === 'paid') return { result: 'confirmed', plan: charge.plan }
    const check = await request('/payment_check', { handle: settings.handle, order_nsu: orderNsu, transaction_nsu: transactionNsu, slug })
    const amount = Number(check?.amount)
    const paidAmount = Number(check?.paid_amount)
    // paid_amount can exceed amount when card fees are passed to the buyer, never be lower
    if (check?.success !== true || check?.paid !== true || amount !== charge.amount || !(paidAmount >= charge.amount)) {
      return { result: 'processing', plan: charge.plan }
    }
    const paidAt = now()
    db.exec('BEGIN IMMEDIATE')
    try {
      const changed = q.markPaid.run(slug, transactionNsu, String(check.capture_method ?? '').slice(0, 20), paidAmount,
        Number.isInteger(check.installments) ? check.installments : null, '', paidAt, orderNsu).changes
      if (changed) {
        const subscription = q.subscription.get(charge.subscription_id)
        q.extend.run(addMonth(Math.max(paidAt, subscription.paid_until)), paidAt, subscription.id)
      }
      db.exec('COMMIT')
    } catch (error) {
      db.exec('ROLLBACK')
      throw error
    }
    return { result: 'confirmed', plan: charge.plan }
  }

  function formatAmount(minor) {
    // Plain-text email: use a regular space instead of the no-break space Intl inserts after R$
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(minor / 100).replace(/ /g, ' ')
  }

  async function sendRenewal(charge, subscription) {
    if (!mailer) return false
    const t = copy[subscription.language] ?? copy['pt-BR']
    const plan = catalog.plans[charge.plan].name
    await mailer({
      to: subscription.email,
      subject: t.subject(plan),
      text: t.body({ plan, amount: formatAmount(charge.amount), until: new Date(subscription.paid_until * 1000).toLocaleDateString(t.date, { timeZone: 'America/Sao_Paulo' }), url: charge.link_url ?? charge.url }),
    })
    return true
  }

  /** Hourly job: status transitions, renewal links and reminder emails. Safe to run repeatedly. */
  async function runRenewals() {
    if (!enabled) return { created: 0, emailed: 0 }
    const at = now()
    q.expireLinks.run(at)
    q.pastDue.run(at, at)
    q.expire.run(at, at - settings.graceDays * DAY)
    q.abandon.run(at, at - (settings.linkValidDays + 1) * DAY)
    let created = 0
    for (const subscription of q.dueForRenewal.all(settings.renewDaysBefore * DAY, at, at)) {
      try {
        await createLink(subscription, 'renewal')
        created++
      } catch (error) {
        log.error('InfinitePay renewal link failed', error.code ?? error.name)
      }
    }
    let emailed = 0
    for (const charge of q.unsent.all(at)) {
      try {
        if (await sendRenewal(charge, charge)) { q.markEmailed.run(now(), charge.order_nsu); emailed++ }
      } catch (error) {
        log.error('Renewal email failed', error.code ?? error.name)
      }
    }
    return { created, emailed }
  }

  const router = express.Router()
  const noStore = (_req, res, next) => { res.set('Cache-Control', 'no-store'); next() }
  const limited = limit => rateLimit({ windowMs: 10 * 60 * 1000, limit, standardHeaders: 'draft-8', legacyHeaders: false, message: { error: 'too_many_requests' } })

  // Return from InfinitePay: forward only the validated identifiers to the page, which confirms them
  router.get('/pagamento/infinitepay/:lang', noStore, (req, res) => {
    const language = catalog.languages.includes(req.params.lang) ? req.params.lang : 'pt-BR'
    const query = new URLSearchParams({ lang: language, market: INFINITEPAY_MARKET, checkout: 'success', provider: 'infinitepay' })
    const { order_nsu: orderNsu, slug, transaction_nsu: transactionNsu } = req.query
    if (typeof orderNsu === 'string' && ORDER.test(orderNsu)) query.set('order_nsu', orderNsu)
    if (typeof slug === 'string' && SLUG.test(slug)) query.set('slug', slug)
    if (typeof transactionNsu === 'string' && SLUG.test(transactionNsu)) query.set('transaction_nsu', transactionNsu)
    res.redirect(303, `/?${query}`)
  })

  router.post('/api/infinitepay/confirm', noStore, limited(30), express.json({ limit: '2kb', inflate: false }), async (req, res) => {
    if (!enabled) return res.status(503).json({ error: 'billing_disabled' })
    const { orderNsu, slug, transactionNsu } = req.body ?? {}
    try {
      return res.json(await verify({ orderNsu, slug, transactionNsu }))
    } catch (error) {
      log.error('InfinitePay payment check failed', error.code ?? error.name)
      return res.status(503).json({ error: 'check_unavailable' })
    }
  })

  router.post('/api/infinitepay/webhook/:token', express.json({ limit: '16kb', inflate: false }), async (req, res) => {
    if (!enabled) return res.status(503).json({ error: 'billing_disabled' })
    const given = Buffer.from(req.params.token)
    const expected = Buffer.from(settings.webhookToken)
    if (given.length !== expected.length || !timingSafeEqual(given, expected)) return res.status(404).json({ error: 'not_found' })
    const body = req.body ?? {}
    try {
      // The payload is only a hint: verify() asks InfinitePay before anything is recorded
      const { result } = await verify({ orderNsu: body.order_nsu, slug: body.invoice_slug, transactionNsu: body.transaction_nsu })
      return res.status(200).json({ received: true, result })
    } catch (error) {
      // 400 makes InfinitePay deliver the notice again later
      log.error('InfinitePay webhook verification failed', error.code ?? error.name)
      return res.status(400).json({ error: 'verification_failed' })
    }
  })

  return { router, enabled, createCheckout, verify, runRenewals }
}
