// InfinitePay "Checkout Integrado" for the Brazilian market (BRL, Pix and card).
// Official docs: https://www.infinitepay.io/checkout-documentacao
//
// What the API does NOT offer, and how this module compensates:
//   - no recurring billing     -> one single-use, expiring payment link per subscription cycle,
//                                 created by an hourly job and emailed before access ends;
//   - unsigned webhook         -> nothing is recorded from a notice alone: every payment is confirmed
//                                 with POST /payment_check (paid: true and the stored amount); the
//                                 webhook URL also carries a secret token;
//   - no refunds via API, no documented refund signal -> a paid charge that payment_check later reports
//                                 as unpaid goes to manual review; the operator records the refund with
//                                 `npm run infinitepay -- refund`, which adjusts access and the ledger;
//   - no sandbox               -> automated tests simulate the documented contract.
//
// Confirmed payments are recorded here. Granting or revoking access to the AI Stream Clipper dashboard is
// still a manual HTS step (see `npm run billing:report`).
import { randomUUID, timingSafeEqual } from 'node:crypto'
import express from 'express'
import { rateLimit } from 'express-rate-limit'
import catalog from '../catalog.json' with { type: 'json' }

export const INFINITEPAY_MARKET = 'BR'
export const INFINITEPAY_API = 'https://api.checkout.infinitepay.io'
const DAY = 86_400
const ORDER = /^asc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const SLUG = /^[A-Za-z0-9_-]{1,120}$/
const TOKEN = /^[A-Za-z0-9_-]{32,128}$/
// Webhook events requested from InfinitePay (documented: created, paid, settled, canceled, refunded, payment_failed)
export const WEBHOOK_EVENTS = ['paid', 'refunded', 'canceled']
const ACTIVE_CHARGE = "('creating', 'open', 'paid', 'reversal_review')"
const MAX_LINK_ATTEMPTS = 5
const MAX_EMAIL_ATTEMPTS = 5
const RETRY_CREATING_AFTER = 10 * 60

function isSecureOrigin(origin) {
  if (!origin) return false
  const url = new URL(origin)
  return url.protocol === 'https:' || url.hostname === '127.0.0.1' || url.hostname === 'localhost'
}

export function infinitePaySettings(env = process.env) {
  const handle = (env.INFINITEPAY_HANDLE ?? '').replace(/^\$/, '')
  const origin = env.COMMERCIAL_ORIGIN ? new URL(env.COMMERCIAL_ORIGIN).origin : ''
  const previous = env.INFINITEPAY_WEBHOOK_TOKEN_PREVIOUS ?? ''
  return {
    handle: /^[A-Za-z0-9_.-]{2,60}$/.test(handle) ? handle : '',
    // Required: without the key InfinitePay ignores single_payment and valid_until (reusable, never-expiring links)
    apiKey: /^inchk_[A-Za-z0-9_-]{8,}$/.test(env.INFINITEPAY_API_KEY ?? '') ? env.INFINITEPAY_API_KEY : '',
    webhookToken: TOKEN.test(env.INFINITEPAY_WEBHOOK_TOKEN ?? '') ? env.INFINITEPAY_WEBHOOK_TOKEN : '',
    // Links keep the webhook URL they were created with, so the previous token stays valid during rotation
    previousWebhookToken: TOKEN.test(previous) ? previous : '',
    origin: isSecureOrigin(origin) ? origin : '',
    renewDaysBefore: Number(env.INFINITEPAY_RENEW_DAYS_BEFORE ?? 5),
    graceDays: Number(env.INFINITEPAY_GRACE_DAYS ?? 3),
    firstLinkDays: 7,
  }
}

/** Which settings are missing, for the startup log (never the values themselves). */
export function missingInfinitePaySettings(settings) {
  return [['INFINITEPAY_HANDLE', settings.handle], ['INFINITEPAY_API_KEY', settings.apiKey],
    ['INFINITEPAY_WEBHOOK_TOKEN', settings.webhookToken], ['COMMERCIAL_ORIGIN (https)', settings.origin]]
    .filter(([, value]) => !value).map(([name]) => name)
}

/** Minimal JSON client for the two documented endpoints. */
export function infinitePayClient(settings, fetchImpl = fetch) {
  return async (path, body) => {
    const response = await fetchImpl(`${INFINITEPAY_API}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Api-Key': settings.apiKey },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(8_000),
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw Object.assign(new Error(`InfinitePay ${path} answered ${response.status}`), { code: typeof data?.error === 'string' ? data.error.slice(0, 60) : `http_${response.status}` })
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

export function linkUrl(response) {
  // The response field name is not documented; accept the usual names but only an https InfinitePay URL
  for (const value of [response?.url, response?.link, response?.checkout_url, response?.payment_url, response?.data?.url]) {
    if (typeof value !== 'string') continue
    try {
      const url = new URL(value)
      if (url.protocol === 'https:' && (url.hostname === 'infinitepay.io' || url.hostname.endsWith('.infinitepay.io'))) return url.href
    } catch { /* not a URL */ }
  }
  return ''
}

export function migrateInfinitePay(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS ip_subscriptions (
      id TEXT PRIMARY KEY,
      email TEXT NOT NULL,
      plan TEXT NOT NULL,
      language TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'past_due', 'expired', 'abandoned', 'refunded', 'canceled')),
      paid_until INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    ) STRICT;
    CREATE INDEX IF NOT EXISTS ip_subscriptions_email ON ip_subscriptions(email, plan, status);
    CREATE TABLE IF NOT EXISTS ip_charges (
      order_nsu TEXT PRIMARY KEY,
      subscription_id TEXT NOT NULL REFERENCES ip_subscriptions(id),
      cycle_key TEXT NOT NULL,
      kind TEXT NOT NULL CHECK (kind IN ('first', 'renewal')),
      plan TEXT NOT NULL,
      amount INTEGER NOT NULL CHECK (amount > 0),
      status TEXT NOT NULL CHECK (status IN ('creating', 'open', 'paid', 'expired', 'failed', 'refunded', 'reversal_review')),
      link_url TEXT NOT NULL DEFAULT '',
      valid_until INTEGER NOT NULL,
      link_attempts INTEGER NOT NULL DEFAULT 0,
      emailed_at INTEGER,
      email_attempts INTEGER NOT NULL DEFAULT 0,
      slug TEXT NOT NULL DEFAULT '',
      transaction_nsu TEXT NOT NULL DEFAULT '',
      capture_method TEXT NOT NULL DEFAULT '',
      paid_amount INTEGER,
      installments INTEGER,
      period_start INTEGER,
      period_end INTEGER,
      created_at INTEGER NOT NULL,
      paid_at INTEGER,
      refunded_at INTEGER
    ) STRICT;
    -- One logical charge per subscription and cycle, across restarts, retries and concurrent workers
    CREATE UNIQUE INDEX IF NOT EXISTS ip_charges_one_per_cycle ON ip_charges(subscription_id, cycle_key)
      WHERE status IN ${ACTIVE_CHARGE};
    -- Append-only financial history
    CREATE TABLE IF NOT EXISTS ip_ledger (
      id INTEGER PRIMARY KEY,
      subscription_id TEXT NOT NULL,
      order_nsu TEXT NOT NULL DEFAULT '',
      type TEXT NOT NULL,
      amount INTEGER,
      detail TEXT NOT NULL DEFAULT '',
      created_at INTEGER NOT NULL
    ) STRICT;
    CREATE UNIQUE INDEX IF NOT EXISTS ip_ledger_once ON ip_ledger(order_nsu, type)
      WHERE type IN ('payment_confirmed', 'refund_recorded', 'reversal_reported', 'amount_mismatch', 'payment_after_cancel', 'email_gave_up', 'link_gave_up');
    CREATE TRIGGER IF NOT EXISTS ip_ledger_no_update BEFORE UPDATE ON ip_ledger BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS ip_ledger_no_delete BEFORE DELETE ON ip_ledger BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
  `)
}

const copy = {
  'pt-BR': {
    item: plan => `AI Stream Clipper ${plan} - 1 mês`,
    subject: plan => `Renove seu plano ${plan} do AI Stream Clipper`,
    body: ({ plan, amount, until, url, linkUntil }) => `Olá!\n\nSeu acesso ao plano ${plan} do AI Stream Clipper vai até ${until}.\n\nPara renovar por mais um mês (${amount}), pague com Pix ou cartão neste link, válido até ${linkUntil}:\n${url}\n\nPara não renovar, basta não pagar: o acesso termina na data acima.\n\nHTS`,
    date: 'pt-BR',
  },
  en: {
    item: plan => `AI Stream Clipper ${plan} - 1 month`,
    subject: plan => `Renew your AI Stream Clipper ${plan} plan`,
    body: ({ plan, amount, until, url, linkUntil }) => `Hi!\n\nYour AI Stream Clipper ${plan} access runs until ${until}.\n\nTo renew for another month (${amount}), pay by Pix or card with this link, valid until ${linkUntil}:\n${url}\n\nIf you don't want to renew, just don't pay: access ends on the date above.\n\nHTS`,
    date: 'en-US',
  },
}

export function createInfinitePay({ db, settings, request, mailer = null, log = console, now = () => Math.floor(Date.now() / 1000) }) {
  migrateInfinitePay(db)
  const enabled = Boolean(settings.handle && settings.apiKey && settings.webhookToken && settings.origin && request)
  const webhookUrl = `${settings.origin}/api/infinitepay/webhook/${settings.webhookToken}`
  let renewalsRunning = false

  const q = {
    insertSubscription: db.prepare("INSERT INTO ip_subscriptions (id, email, plan, language, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)"),
    reusableFirst: db.prepare(`SELECT c.* FROM ip_charges c JOIN ip_subscriptions s ON s.id = c.subscription_id
      WHERE s.email = ? AND s.plan = ? AND s.language = ? AND s.status = 'pending' AND c.kind = 'first' AND c.status = 'open' AND c.valid_until > ?
      ORDER BY c.created_at DESC LIMIT 1`),
    insertCharge: db.prepare(`INSERT INTO ip_charges (order_nsu, subscription_id, cycle_key, kind, plan, amount, status, valid_until, created_at)
      VALUES (?, ?, ?, ?, ?, ?, 'creating', ?, ?)`),
    activeForCycle: db.prepare(`SELECT * FROM ip_charges WHERE subscription_id = ? AND cycle_key = ? AND status IN ${ACTIVE_CHARGE}`),
    opened: db.prepare("UPDATE ip_charges SET link_url = ?, status = 'open', link_attempts = link_attempts + 1 WHERE order_nsu = ? AND status = 'creating'"),
    attemptFailed: db.prepare('UPDATE ip_charges SET link_attempts = link_attempts + 1 WHERE order_nsu = ?'),
    setChargeStatus: db.prepare('UPDATE ip_charges SET status = ? WHERE order_nsu = ? AND status = ?'),
    charge: db.prepare('SELECT * FROM ip_charges WHERE order_nsu = ?'),
    subscription: db.prepare('SELECT * FROM ip_subscriptions WHERE id = ?'),
    markPaid: db.prepare(`UPDATE ip_charges SET status = 'paid', slug = ?, transaction_nsu = ?, capture_method = ?, paid_amount = ?, installments = ?,
      period_start = ?, period_end = ?, paid_at = ? WHERE order_nsu = ? AND status IN ('creating', 'open', 'expired', 'failed')`),
    setSubscription: db.prepare('UPDATE ip_subscriptions SET status = ?, paid_until = ?, updated_at = ? WHERE id = ?'),
    ledger: db.prepare('INSERT OR IGNORE INTO ip_ledger (subscription_id, order_nsu, type, amount, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)'),
    expiringCharges: db.prepare("SELECT order_nsu, subscription_id, status FROM ip_charges WHERE status IN ('creating', 'open') AND valid_until <= ?"),
    toPastDue: db.prepare("SELECT id FROM ip_subscriptions WHERE status = 'active' AND paid_until <= ?"),
    toExpired: db.prepare("SELECT id FROM ip_subscriptions WHERE status = 'past_due' AND paid_until + ? <= ?"),
    toAbandoned: db.prepare(`SELECT s.id FROM ip_subscriptions s WHERE s.status = 'pending'
      AND NOT EXISTS (SELECT 1 FROM ip_charges c WHERE c.subscription_id = s.id AND c.status IN ${ACTIVE_CHARGE})`),
    dueForRenewal: db.prepare(`SELECT * FROM ip_subscriptions WHERE status IN ('active', 'past_due') AND paid_until - ? <= ?`),
    stuckCreating: db.prepare("SELECT * FROM ip_charges WHERE status = 'creating' AND kind = 'renewal' AND created_at <= ? AND valid_until > ?"),
    unsent: db.prepare(`SELECT c.*, s.email, s.language, s.paid_until AS access_until FROM ip_charges c JOIN ip_subscriptions s ON s.id = c.subscription_id
      WHERE c.kind = 'renewal' AND c.status = 'open' AND c.emailed_at IS NULL AND c.email_attempts < ? AND c.valid_until > ?
        AND s.status IN ('active', 'past_due')`),
    emailed: db.prepare('UPDATE ip_charges SET emailed_at = ?, email_attempts = email_attempts + 1 WHERE order_nsu = ?'),
    emailFailed: db.prepare('UPDATE ip_charges SET email_attempts = email_attempts + 1 WHERE order_nsu = ? RETURNING email_attempts'),
    paidCharges: db.prepare("SELECT * FROM ip_charges WHERE status = 'paid' AND paid_at >= ? ORDER BY paid_at"),
  }

  function record(subscriptionId, orderNsu, type, amount = null, detail = '') {
    q.ledger.run(subscriptionId, orderNsu, type, amount, String(detail).slice(0, 200), now())
  }

  function transaction(work) {
    db.exec('BEGIN IMMEDIATE')
    try {
      const result = work()
      db.exec('COMMIT')
      return result
    } catch (error) {
      db.exec('ROLLBACK')
      throw error
    }
  }

  function setStatus(subscription, status, paidUntil = subscription.paid_until) {
    q.setSubscription.run(status, paidUntil, now(), subscription.id)
    if (status !== subscription.status) record(subscription.id, '', `status_${status}`, null, `from ${subscription.status}`)
  }

  /** The documented /links body, rebuilt identically from the stored charge (the API treats identical requests idempotently). */
  function linkBody(charge, subscription) {
    const t = copy[subscription.language] ?? copy['pt-BR']
    return {
      handle: settings.handle,
      items: [{ quantity: 1, price: charge.amount, description: t.item(catalog.plans[charge.plan].name) }],
      order_nsu: charge.order_nsu,
      // A path without a query string, so it does not matter how InfinitePay appends its parameters
      redirect_url: `${settings.origin}/pagamento/infinitepay/${subscription.language}`,
      webhook: { url: webhookUrl, events: WEBHOOK_EVENTS },
      customer: { email: subscription.email },
      options: {
        single_payment: true,
        valid_until: new Date(charge.valid_until * 1000).toISOString(),
        payment_methods_enabled: ['credit_card', 'pix'],
      },
    }
  }

  async function requestLink(charge, subscription) {
    try {
      const url = linkUrl(await request('/links', linkBody(charge, subscription)))
      if (!url) throw Object.assign(new Error('InfinitePay link response without a usable URL'), { code: 'invalid_link_response' })
      if (q.opened.run(url, charge.order_nsu).changes) record(subscription.id, charge.order_nsu, 'link_created', charge.amount)
      return url
    } catch (error) {
      q.attemptFailed.run(charge.order_nsu)
      const attempts = charge.link_attempts + 1
      // First links fail fast (the buyer is waiting); renewals keep the same order_nsu and are retried later
      if (charge.kind === 'first' || attempts >= MAX_LINK_ATTEMPTS) {
        q.setChargeStatus.run('failed', charge.order_nsu, 'creating')
        record(subscription.id, charge.order_nsu, charge.kind === 'first' ? 'link_failed' : 'link_gave_up', charge.amount, error.code ?? error.name)
      }
      throw error
    }
  }

  /** Reserves the cycle's charge. Returns null when another worker already holds it. */
  function reserveCharge(subscription, kind, cycleKey, validUntil) {
    const orderNsu = `asc-${randomUUID()}`
    const amount = catalog.plans[subscription.plan].monthlyMinor[INFINITEPAY_MARKET]
    try {
      q.insertCharge.run(orderNsu, subscription.id, cycleKey, kind, subscription.plan, amount, validUntil, now())
    } catch (error) {
      if (String(error.message).includes('UNIQUE')) return null
      throw error
    }
    return q.charge.get(orderNsu)
  }

  async function createCheckout({ plan, language, email }) {
    if (!enabled) throw Object.assign(new Error('InfinitePay is not configured'), { code: 'disabled' })
    // A repeated click for the same email, plan and language reuses the open link instead of creating another charge
    const existing = q.reusableFirst.get(email, plan, language, now() + 60 * 60)
    if (existing) return existing.link_url
    const id = randomUUID()
    q.insertSubscription.run(id, email, plan, language, now(), now())
    const subscription = q.subscription.get(id)
    const charge = reserveCharge(subscription, 'first', 'first', now() + settings.firstLinkDays * DAY)
    return requestLink(charge, subscription)
  }

  function applyPayment(charge, check, slug, transactionNsu) {
    return transaction(() => {
      const current = q.charge.get(charge.order_nsu)
      if (current.status === 'paid' || current.status === 'reversal_review') return 'confirmed'
      if (current.status === 'refunded') return 'refunded'
      const subscription = q.subscription.get(charge.subscription_id)
      const paidAt = now()
      // Access continues through the grace period, so active and past-due subscriptions extend from the due
      // date (no free days); expired, pending or abandoned ones start a new month today
      const continuing = (subscription.status === 'active' || subscription.status === 'past_due') && subscription.paid_until > 0
      const periodStart = continuing ? subscription.paid_until : paidAt
      const periodEnd = addMonth(periodStart)
      const changed = q.markPaid.run(slug, transactionNsu, String(check.capture_method ?? '').slice(0, 20), Number(check.paid_amount),
        Number.isInteger(check.installments) ? check.installments : null, periodStart, periodEnd, paidAt, charge.order_nsu).changes
      if (!changed) return 'processing'
      record(subscription.id, charge.order_nsu, 'payment_confirmed', Number(check.paid_amount), `${check.capture_method ?? ''} ${check.installments ?? ''}x`)
      if (subscription.status === 'canceled') {
        // Never reactivate a subscription the operator canceled: flag it for a manual refund or decision
        record(subscription.id, charge.order_nsu, 'payment_after_cancel', Number(check.paid_amount))
      } else {
        setStatus(subscription, 'active', periodEnd)
      }
      return 'confirmed'
    })
  }

  /**
   * Confirms a charge with POST /payment_check. Only paid: true for the stored amount records a payment.
   * Webhook notices for an already paid charge are re-checked: if InfinitePay now reports it unpaid, the
   * charge goes to manual review (a refund or chargeback is likely, but the API does not document it).
   */
  async function verify({ orderNsu, slug, transactionNsu, source = 'browser' }) {
    if (!ORDER.test(orderNsu ?? '') || !SLUG.test(slug ?? '') || !SLUG.test(transactionNsu ?? '')) return { result: 'unknown', plan: '' }
    const charge = q.charge.get(orderNsu)
    if (!charge) return { result: 'unknown', plan: '' }
    if (charge.status === 'refunded') return { result: 'refunded', plan: charge.plan }
    const settled = charge.status === 'paid' || charge.status === 'reversal_review'
    if (settled && source === 'browser') return { result: 'confirmed', plan: charge.plan }
    if (settled && (slug !== charge.slug || transactionNsu !== charge.transaction_nsu)) return { result: 'confirmed', plan: charge.plan }

    const check = await request('/payment_check', { handle: settings.handle, order_nsu: orderNsu, transaction_nsu: transactionNsu, slug })
    if (settled) {
      if (check?.success === true && check?.paid === false && charge.status === 'paid') {
        transaction(() => {
          q.setChargeStatus.run('reversal_review', orderNsu, 'paid')
          record(charge.subscription_id, orderNsu, 'reversal_reported', charge.paid_amount, 'payment_check reports the paid charge as unpaid')
        })
        log.warn('InfinitePay charge needs refund review', orderNsu.slice(0, 12))
        return { result: 'review', plan: charge.plan }
      }
      return { result: 'confirmed', plan: charge.plan }
    }

    const amount = Number(check?.amount)
    const paidAmount = Number(check?.paid_amount)
    if (check?.success !== true || check?.paid !== true) return { result: 'processing', plan: charge.plan }
    // paid_amount can exceed amount when card fees are passed to the buyer, never be lower
    if (amount !== charge.amount || !(paidAmount >= charge.amount)) {
      record(charge.subscription_id, orderNsu, 'amount_mismatch', Number.isFinite(paidAmount) ? paidAmount : null, `amount ${check?.amount} paid ${check?.paid_amount}`)
      log.warn('InfinitePay amount mismatch', orderNsu.slice(0, 12))
      return { result: 'processing', plan: charge.plan }
    }
    return { result: applyPayment(charge, check, slug, transactionNsu), plan: charge.plan }
  }

  /**
   * Records a refund or chargeback done in the InfinitePay app (the API cannot refund). Removes the refunded
   * month from the subscription's access and appends to the ledger. Idempotent.
   */
  function refund(orderNsu, { reason = '', cancel = false } = {}) {
    return transaction(() => {
      const charge = q.charge.get(orderNsu)
      if (!charge) throw Object.assign(new Error('Unknown order'), { code: 'not_found' })
      const subscription = q.subscription.get(charge.subscription_id)
      if (charge.status === 'refunded') return { changed: false, subscription }
      if (charge.status !== 'paid' && charge.status !== 'reversal_review') throw Object.assign(new Error('Only paid charges can be refunded'), { code: 'not_paid' })
      q.setChargeStatus.run('refunded', orderNsu, charge.status)
      db.prepare('UPDATE ip_charges SET refunded_at = ? WHERE order_nsu = ?').run(now(), orderNsu)
      record(subscription.id, orderNsu, 'refund_recorded', -(charge.paid_amount ?? charge.amount), reason)
      // Remove exactly the refunded period; later paid periods move back by the same length
      const paidUntil = Math.max(0, subscription.paid_until - (charge.period_end - charge.period_start))
      const otherPaid = db.prepare("SELECT COUNT(*) AS n FROM ip_charges WHERE subscription_id = ? AND status = 'paid'").get(subscription.id).n
      const status = cancel ? 'canceled'
        : subscription.status === 'canceled' ? 'canceled'
          : paidUntil > now() ? 'active' : otherPaid ? 'expired' : 'refunded'
      setStatus(subscription, status, paidUntil)
      return { changed: true, subscription: q.subscription.get(subscription.id) }
    })
  }

  /** Operator decision after a review: the charge was not refunded after all. */
  function clearReview(orderNsu, reason = '') {
    return transaction(() => {
      const charge = q.charge.get(orderNsu)
      if (charge?.status !== 'reversal_review') return false
      q.setChargeStatus.run('paid', orderNsu, 'reversal_review')
      record(charge.subscription_id, orderNsu, 'review_cleared', null, reason)
      return true
    })
  }

  function cancel(subscriptionId, reason = '') {
    return transaction(() => {
      const subscription = q.subscription.get(subscriptionId)
      if (!subscription) throw Object.assign(new Error('Unknown subscription'), { code: 'not_found' })
      if (subscription.status !== 'canceled') setStatus(subscription, 'canceled')
      record(subscriptionId, '', 'cancel_recorded', null, reason)
      return q.subscription.get(subscriptionId)
    })
  }

  /** Re-checks recent paid charges with payment_check and flags any that InfinitePay now reports unpaid. */
  async function reconcile({ sinceDays = 120 } = {}) {
    const flagged = []
    for (const charge of q.paidCharges.all(now() - sinceDays * DAY)) {
      const { result } = await verify({ orderNsu: charge.order_nsu, slug: charge.slug, transactionNsu: charge.transaction_nsu, source: 'reconcile' })
      if (result === 'review') flagged.push(charge.order_nsu)
    }
    return flagged
  }

  function formatAmount(minor) {
    // Plain-text email: use a regular space instead of the no-break space Intl inserts after R$
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(minor / 100).replace(/ /g, ' ')
  }

  async function sendRenewal(row) {
    const t = copy[row.language] ?? copy['pt-BR']
    const plan = catalog.plans[row.plan].name
    const date = seconds => new Date(seconds * 1000).toLocaleDateString(t.date, { timeZone: 'America/Sao_Paulo' })
    await mailer({ to: row.email, subject: t.subject(plan), text: t.body({ plan, amount: formatAmount(row.amount), until: date(row.access_until), url: row.link_url, linkUntil: date(row.valid_until) }) })
  }

  /**
   * Hourly job. State machine:
   *   pending   -> abandoned  when its first link expired unpaid
   *   active    -> past_due   when paid_until is reached
   *   past_due  -> expired    after the grace period
   *   active/past_due get one renewal link per cycle (cycle = current paid_until), valid until the end of grace;
   *   expired, abandoned, refunded and canceled never get links (the customer subscribes again on the site).
   * Safe to run concurrently or repeatedly: the cycle unique index and the in-process flag prevent duplicates.
   */
  async function runRenewals() {
    if (!enabled || renewalsRunning) return { created: 0, emailed: 0, skipped: true }
    renewalsRunning = true
    try {
      const at = now()
      for (const charge of q.expiringCharges.all(at)) {
        if (q.setChargeStatus.run('expired', charge.order_nsu, charge.status).changes) record(charge.subscription_id, charge.order_nsu, 'link_expired')
      }
      for (const { id } of q.toPastDue.all(at)) setStatus(q.subscription.get(id), 'past_due')
      for (const { id } of q.toExpired.all(settings.graceDays * DAY, at)) setStatus(q.subscription.get(id), 'expired')
      for (const { id } of q.toAbandoned.all()) setStatus(q.subscription.get(id), 'abandoned')

      let created = 0
      for (const subscription of q.dueForRenewal.all(settings.renewDaysBefore * DAY, at)) {
        const cycleKey = `due:${subscription.paid_until}`
        const validUntil = subscription.paid_until + settings.graceDays * DAY
        if (validUntil <= at + 60 * 60 || q.activeForCycle.get(subscription.id, cycleKey)) continue
        const charge = reserveCharge(subscription, 'renewal', cycleKey, validUntil)
        if (!charge) continue
        try { await requestLink(charge, subscription); created++ } catch (error) { log.error('InfinitePay renewal link failed', error.code ?? error.name) }
      }
      // Renewal links left in 'creating' by a crash or a network error: same order_nsu, identical body
      for (const charge of q.stuckCreating.all(at - RETRY_CREATING_AFTER, at)) {
        try { await requestLink(charge, q.subscription.get(charge.subscription_id)); created++ } catch (error) { log.error('InfinitePay renewal retry failed', error.code ?? error.name) }
      }

      let emailed = 0
      if (mailer) {
        for (const row of q.unsent.all(MAX_EMAIL_ATTEMPTS, at)) {
          try {
            await sendRenewal(row)
            q.emailed.run(now(), row.order_nsu)
            record(row.subscription_id, row.order_nsu, 'email_sent')
            emailed++
          } catch (error) {
            const { email_attempts: attempts } = q.emailFailed.get(row.order_nsu)
            record(row.subscription_id, row.order_nsu, attempts >= MAX_EMAIL_ATTEMPTS ? 'email_gave_up' : 'email_failed', null, error.code ?? error.name)
            log.error('Renewal email failed', error.code ?? error.name)
          }
        }
      }
      return { created, emailed }
    } finally {
      renewalsRunning = false
    }
  }

  const router = express.Router()
  const noStore = (_req, res, next) => { res.set('Cache-Control', 'no-store'); next() }
  const limited = limit => rateLimit({ windowMs: 10 * 60 * 1000, limit, standardHeaders: 'draft-8', legacyHeaders: false, message: { error: 'too_many_requests' } })
  const tokens = [settings.webhookToken, settings.previousWebhookToken].filter(Boolean).map(value => Buffer.from(value))

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
      const { result, plan } = await verify({ orderNsu, slug, transactionNsu, source: 'browser' })
      // Only the outcome and the plan name: no email, amounts or subscription ids
      return res.json({ result, plan })
    } catch (error) {
      log.error('InfinitePay payment check failed', error.code ?? error.name)
      return res.status(503).json({ error: 'check_unavailable' })
    }
  })

  router.post('/api/infinitepay/webhook/:token', limited(300), express.json({ limit: '16kb', inflate: false }), async (req, res) => {
    if (!enabled) return res.status(503).json({ error: 'billing_disabled' })
    const given = Buffer.from(req.params.token)
    if (!tokens.some(expected => expected.length === given.length && timingSafeEqual(expected, given))) return res.status(404).json({ error: 'not_found' })
    const body = req.body ?? {}
    try {
      // The payload is only a hint (documented fields: invoice_slug, transaction_nsu, order_nsu, amount, paid_amount…)
      const { result } = await verify({ orderNsu: body.order_nsu, slug: body.invoice_slug, transactionNsu: body.transaction_nsu, source: 'webhook' })
      return res.status(200).json({ received: true, result })
    } catch (error) {
      // 400 makes InfinitePay deliver the notice again later
      log.error('InfinitePay webhook verification failed', error.code ?? error.name)
      return res.status(400).json({ error: 'verification_failed' })
    }
  })

  return { router, enabled, createCheckout, verify, refund, clearReview, cancel, reconcile, runRenewals }
}
