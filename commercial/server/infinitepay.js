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
// Every financial change is written together with an entitlement snapshot in the outbox (server/outbox.js);
// the main backend turns it into access for the account that started the checkout.
import { randomUUID, timingSafeEqual } from 'node:crypto'
import express from 'express'
import { rateLimit } from 'express-rate-limit'
import catalog from '../catalog.json' with { type: 'json' }
import { infinitePayAccess, limitsFor } from './access.js'
import { MIN_CHARGE, renewalPrice } from './promotions.js'

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
    // Where buyers land after paying: the dashboard, which shows the access granted by the backend
    appUrl: env.ASC_APP_URL ? new URL(env.ASC_APP_URL).origin : '',
    renewDaysBefore: Number(env.INFINITEPAY_RENEW_DAYS_BEFORE ?? 5),
    graceDays: Number(env.INFINITEPAY_GRACE_DAYS ?? 3),
    firstLinkDays: 7,
  }
}

/** Which settings are missing, for the startup log (never the values themselves). */
export function missingInfinitePaySettings(settings) {
  return [['INFINITEPAY_HANDLE', settings.handle], ['INFINITEPAY_API_KEY', settings.apiKey],
    ['INFINITEPAY_WEBHOOK_TOKEN', settings.webhookToken], ['COMMERCIAL_ORIGIN (https)', settings.origin], ['ASC_APP_URL', settings.appUrl]]
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
    -- Simultaneous checkouts of the same account, plan and language share one pending subscription
    DROP INDEX IF EXISTS ip_subscriptions_one_pending;
    CREATE TABLE IF NOT EXISTS ip_charges (
      order_nsu TEXT PRIMARY KEY,
      subscription_id TEXT NOT NULL REFERENCES ip_subscriptions(id),
      cycle_key TEXT NOT NULL,
      kind TEXT NOT NULL CHECK (kind IN ('first', 'renewal')),
      plan TEXT NOT NULL,
      amount INTEGER NOT NULL CHECK (amount > 0),
      status TEXT NOT NULL CHECK (status IN ('creating', 'open', 'paid', 'expired', 'failed', 'refunded', 'reversal_review')),
      link_url TEXT NOT NULL DEFAULT '',
      -- Exact /links payload, frozen at reservation: retries resend it byte for byte (idempotency), even
      -- after a webhook token rotation or any other configuration change
      request_json TEXT NOT NULL DEFAULT '',
      -- '' or 'after_cancel': a confirmed payment that must not grant access and needs financial review
      review TEXT NOT NULL DEFAULT '',
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
  // Databases created by the first version of this module lack the newer columns
  const columns = new Set(db.prepare('PRAGMA table_info(ip_charges)').all().map(column => column.name))
  if (!columns.has('request_json')) db.exec("ALTER TABLE ip_charges ADD COLUMN request_json TEXT NOT NULL DEFAULT ''")
  if (!columns.has('review')) db.exec("ALTER TABLE ip_charges ADD COLUMN review TEXT NOT NULL DEFAULT ''")
  const subscriptionColumns = new Set(db.prepare('PRAGMA table_info(ip_subscriptions)').all().map(column => column.name))
  if (!subscriptionColumns.has('account_id')) db.exec("ALTER TABLE ip_subscriptions ADD COLUMN account_id TEXT NOT NULL DEFAULT ''")
  // HTS promotion codes (server/promotions.js): what each charge would have cost, the discount and the code; the
  // subscription keeps the code and how many discounted renewals are left (-1 = forever)
  if (!columns.has('list_amount')) db.exec('ALTER TABLE ip_charges ADD COLUMN list_amount INTEGER')
  if (!columns.has('discount_amount')) db.exec('ALTER TABLE ip_charges ADD COLUMN discount_amount INTEGER NOT NULL DEFAULT 0')
  if (!columns.has('promo_code')) db.exec("ALTER TABLE ip_charges ADD COLUMN promo_code TEXT NOT NULL DEFAULT ''")
  if (!columns.has('redemption_id')) db.exec("ALTER TABLE ip_charges ADD COLUMN redemption_id TEXT NOT NULL DEFAULT ''")
  if (!subscriptionColumns.has('promo_code')) db.exec("ALTER TABLE ip_subscriptions ADD COLUMN promo_code TEXT NOT NULL DEFAULT ''")
  if (!subscriptionColumns.has('promo_remaining')) db.exec('ALTER TABLE ip_subscriptions ADD COLUMN promo_remaining INTEGER NOT NULL DEFAULT 0')
  db.exec("CREATE UNIQUE INDEX IF NOT EXISTS ip_subscriptions_one_pending_account ON ip_subscriptions(account_id, plan, language) WHERE status = 'pending'")
  db.exec('CREATE INDEX IF NOT EXISTS ip_subscriptions_account ON ip_subscriptions(account_id)')
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

export function createInfinitePay({ db, settings, request, mailer = null, outbox = null, promotions = null, log = console, now = () => Math.floor(Date.now() / 1000) }) {
  migrateInfinitePay(db)
  const enabled = Boolean(settings.handle && settings.apiKey && settings.webhookToken && settings.origin && settings.appUrl && request)
  const webhookUrl = `${settings.origin}/api/infinitepay/webhook/${settings.webhookToken}`
  let renewalsRunning = false

  const q = {
    insertSubscription: db.prepare("INSERT INTO ip_subscriptions (id, account_id, email, plan, language, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)"),
    pendingFor: db.prepare("SELECT * FROM ip_subscriptions WHERE account_id = ? AND plan = ? AND language = ? AND status = 'pending'"),
    latestPeriod: db.prepare("SELECT period_start FROM ip_charges WHERE subscription_id = ? AND status IN ('paid', 'reversal_review') AND review = '' ORDER BY period_end DESC LIMIT 1"),
    openRenewal: db.prepare("SELECT link_url, valid_until FROM ip_charges WHERE subscription_id = ? AND kind = 'renewal' AND status = 'open' AND valid_until > ? ORDER BY created_at DESC LIMIT 1"),
    reviewOpen: db.prepare(`SELECT 1 FROM ip_charges c WHERE c.subscription_id = ? AND (c.status = 'reversal_review' OR c.review <> '')
      AND NOT EXISTS (SELECT 1 FROM ip_ledger l WHERE l.order_nsu = c.order_nsu AND l.type IN ('review_cleared', 'refund_recorded')) LIMIT 1`),
    payments: db.prepare("SELECT order_nsu, paid_at, refunded_at, paid_amount, status FROM ip_charges WHERE subscription_id = ? AND paid_at IS NOT NULL ORDER BY paid_at DESC LIMIT 12"),
    insertCharge: db.prepare(`INSERT INTO ip_charges (order_nsu, subscription_id, cycle_key, kind, plan, amount, status, request_json, valid_until, created_at)
      VALUES (?, ?, ?, ?, ?, ?, 'creating', ?, ?, ?)`),
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
    // First and renewal charges left in 'creating' by a crash or network error, for live subscriptions only
    stuckCreating: db.prepare(`SELECT c.* FROM ip_charges c JOIN ip_subscriptions s ON s.id = c.subscription_id
      WHERE c.status = 'creating' AND c.created_at <= ? AND c.valid_until > ? AND s.status IN ('pending', 'active', 'past_due')`),
    setReview: db.prepare('UPDATE ip_charges SET review = ? WHERE order_nsu = ?'),
    unsent: db.prepare(`SELECT c.*, s.email, s.language, s.paid_until AS access_until FROM ip_charges c JOIN ip_subscriptions s ON s.id = c.subscription_id
      WHERE c.kind = 'renewal' AND c.status = 'open' AND c.emailed_at IS NULL AND c.email_attempts < ? AND c.valid_until > ?
        AND s.status IN ('active', 'past_due')`),
    emailed: db.prepare('UPDATE ip_charges SET emailed_at = ?, email_attempts = email_attempts + 1 WHERE order_nsu = ?'),
    emailFailed: db.prepare('UPDATE ip_charges SET email_attempts = email_attempts + 1 WHERE order_nsu = ? RETURNING email_attempts'),
    paidCharges: db.prepare("SELECT * FROM ip_charges WHERE status = 'paid' AND paid_at >= ? ORDER BY paid_at"),
    setPromo: db.prepare('UPDATE ip_subscriptions SET promo_code = ?, promo_remaining = ?, updated_at = ? WHERE id = ?'),
    usePromoCycle: db.prepare('UPDATE ip_subscriptions SET promo_remaining = promo_remaining - 1 WHERE id = ? AND promo_remaining > 0'),
  }

  /** Discounted renewals left after the first discounted month: once 0, repeating N -> N-1, forever -1. */
  const remainingAfterFirst = quoted => (quoted.duration === 'forever' ? -1 : quoted.duration === 'repeating' ? Math.max(0, (quoted.months ?? 1) - 1) : 0)

  /** A charge that will never be paid gives its promotion use back (only while it was just reserved). */
  function releasePromo(charge, reason) {
    if (promotions && charge?.redemption_id) promotions.release(charge.redemption_id, reason)
  }

  function record(subscriptionId, orderNsu, type, amount = null, detail = '') {
    q.ledger.run(subscriptionId, orderNsu, type, amount, String(detail).slice(0, 200), now())
  }

  let depth = 0
  /** BEGIN IMMEDIATE transaction; nested calls join the outer one. */
  function transaction(work) {
    if (depth > 0) return work()
    db.exec('BEGIN IMMEDIATE')
    depth++
    try {
      const result = work()
      db.exec('COMMIT')
      return result
    } catch (error) {
      db.exec('ROLLBACK')
      throw error
    } finally {
      depth--
    }
  }

  /** Full, provider-neutral state of one subscription for the main backend. */
  function snapshot(subscriptionId) {
    const row = q.subscription.get(subscriptionId)
    if (!row?.account_id) return null
    const renewal = q.openRenewal.get(row.id, now())
    return {
      provider: 'infinitepay',
      subscription_ref: row.id,
      account_id: Number(row.account_id),
      plan: row.plan,
      market: INFINITEPAY_MARKET,
      financial_status: row.status,
      review: q.reviewOpen.get(row.id) ? 'payment_review' : '',
      access: infinitePayAccess(row, q.latestPeriod.get(row.id)?.period_start, settings.graceDays),
      limits: limitsFor(row.plan),
      // No automatic renewal in Brazil: the next month is a new Pix/card payment through this link
      renewal: renewal ? { url: renewal.link_url, valid_until: renewal.valid_until } : null,
      manage: 'infinitepay_links',
      payments: q.payments.all(row.id).map(charge => ({
        id: charge.order_nsu, at: charge.paid_at, amount: charge.paid_amount, currency: 'brl',
        status: charge.status === 'refunded' ? 'refunded' : 'paid',
      })),
    }
  }

  /** Queues the subscription's snapshot; always inside the transaction that changed it. */
  function publish(subscriptionId) {
    if (!outbox) return
    transaction(() => {
      const value = snapshot(subscriptionId)
      if (value) outbox.enqueue(value)
    })
  }

  function setStatus(subscription, status, paidUntil = subscription.paid_until) {
    transaction(() => {
      q.setSubscription.run(status, paidUntil, now(), subscription.id)
      if (status !== subscription.status) record(subscription.id, '', `status_${status}`, null, `from ${subscription.status}`)
      publish(subscription.id)
    })
  }

  /** The documented /links body. Built once, when the charge is reserved, and stored in request_json. */
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
      // Always the stored payload: a retry must be identical to the first attempt
      const body = charge.request_json ? JSON.parse(charge.request_json) : linkBody(charge, subscription)
      const url = linkUrl(await request('/links', body))
      if (!url) throw Object.assign(new Error('InfinitePay link response without a usable URL'), { code: 'invalid_link_response' })
      transaction(() => {
        if (q.opened.run(url, charge.order_nsu).changes) {
          record(subscription.id, charge.order_nsu, 'link_created', charge.amount)
          // A new renewal link is shown in the dashboard
          if (charge.kind === 'renewal') publish(subscription.id)
        }
      })
      return url
    } catch (error) {
      q.attemptFailed.run(charge.order_nsu)
      const attempts = q.charge.get(charge.order_nsu).link_attempts
      // The charge stays 'creating' with the same order_nsu: the buyer's next click or the hourly job resends the
      // identical payload, which InfinitePay may answer with the same link; after five attempts it is closed
      if (attempts >= MAX_LINK_ATTEMPTS) {
        transaction(() => {
          q.setChargeStatus.run('failed', charge.order_nsu, 'creating')
          record(subscription.id, charge.order_nsu, 'link_gave_up', charge.amount, error.code ?? error.name)
          releasePromo(q.charge.get(charge.order_nsu), 'link creation gave up')
        })
      }
      throw error
    }
  }

  /**
   * Reserves the cycle's charge. Returns null when another worker already holds it. `price` (from a promotion quote or
   * a discounted renewal) sets the amount; it is frozen in request_json and payment_check must confirm exactly it.
   */
  function reserveCharge(subscription, kind, cycleKey, validUntil, price = null) {
    const orderNsu = `asc-${randomUUID()}`
    const list = catalog.plans[subscription.plan].monthlyMinor[INFINITEPAY_MARKET]
    const amount = price ? price.final : list
    if (!(amount >= MIN_CHARGE[INFINITEPAY_MARKET])) throw Object.assign(new Error('Charge below the minimum'), { code: 'below_minimum' })
    const requestJson = JSON.stringify(linkBody({ order_nsu: orderNsu, plan: subscription.plan, amount, valid_until: validUntil }, subscription))
    try {
      q.insertCharge.run(orderNsu, subscription.id, cycleKey, kind, subscription.plan, amount, requestJson, validUntil, now())
    } catch (error) {
      if (String(error.message).includes('UNIQUE')) return null
      throw error
    }
    if (price) {
      const redemption = price.quote && promotions
        ? promotions.reserve({ quote: price.quote, accountId: subscription.account_id, plan: subscription.plan, market: INFINITEPAY_MARKET, provider: 'infinitepay', ref: orderNsu })
        : ''
      db.prepare('UPDATE ip_charges SET list_amount = ?, discount_amount = ?, promo_code = ?, redemption_id = ? WHERE order_nsu = ?')
        .run(list, list - amount, price.code ?? '', redemption, orderNsu)
    }
    return q.charge.get(orderNsu)
  }

  /**
   * 100% discount or free days: access without any charge (no artificial Pix/card payment). Inside the checkout
   * transaction. The open first link of the pending subscription, if any, is retired.
   */
  function grantFree(pending, quoted, accountId) {
    const at = now()
    const open = q.activeForCycle.get(pending.id, 'first')
    if (open && (open.status === 'open' || open.status === 'creating')) {
      q.setChargeStatus.run('expired', open.order_nsu, open.status)
      record(pending.id, open.order_nsu, 'link_expired', null, 'replaced by a free promotion')
      releasePromo(open, 'replaced by a free promotion')
    }
    const redemption = promotions.reserve({ quote: quoted, accountId, plan: pending.plan, market: INFINITEPAY_MARKET, provider: 'infinitepay', ref: pending.id })
    promotions.redeem(redemption, pending.id)
    const paidUntil = quoted.freeDays ? at + quoted.freeDays * DAY : addMonth(at)
    q.setPromo.run(quoted.code, quoted.freeDays ? 0 : remainingAfterFirst(quoted), at, pending.id)
    record(pending.id, '', 'promo_free', 0, `${quoted.code} ${quoted.freeDays ? `${quoted.freeDays} days` : '1 month'}`)
    setStatus(pending, 'active', paidUntil)
  }

  /**
   * Checkout link for an account (or, with a 100% / free-days promotion, access right away: returns { free: true }).
   * `promoCode` is validated here, in the same transaction that reserves its use: never trusted from the browser.
   */
  async function createCheckout({ plan, language, email, accountId, promoCode = '' }) {
    if (!enabled) throw Object.assign(new Error('InfinitePay is not configured'), { code: 'disabled' })
    if (!/^[1-9][0-9]{0,11}$/.test(String(accountId ?? ''))) throw Object.assign(new Error('Account required'), { code: 'account_required' })
    if (promoCode && !promotions) throw Object.assign(new Error('Promotions disabled'), { code: 'promo_invalid', reason: 'not_found' })
    const account = String(accountId)
    // One transaction (BEGIN IMMEDIATE serializes writers across processes): the same account, plan and language
    // always share one pending subscription and one first charge, so double clicks, simultaneous requests and a
    // retry after a crash reuse the same order_nsu instead of creating a second charge
    const { subscription, charge, url, free } = transaction(() => {
      const at = now()
      const requested = String(promoCode ?? '').trim().toUpperCase()
      let pending = q.pendingFor.get(account, plan, language)
      // A repeated click with the same code reuses the open discounted link (its use is already reserved)
      const open = pending && requested ? q.activeForCycle.get(pending.id, 'first') : null
      if (open?.promo_code === requested && open.status === 'open' && open.valid_until > at + 60 * 60) return { subscription: pending, charge: open, url: open.link_url }
      if (open?.promo_code === requested && open.status === 'creating') return { subscription: pending, charge: open }
      const quoted = promoCode ? promotions.quote({ code: promoCode, plan, market: INFINITEPAY_MARKET, accountId: account, at }) : null
      if (quoted && !quoted.ok) throw Object.assign(new Error('Invalid promotion'), { code: 'promo_invalid', reason: quoted.reason })
      if (!pending) {
        q.insertSubscription.run(randomUUID(), account, email, plan, language, at, at)
        pending = q.pendingFor.get(account, plan, language)
      }
      if (quoted && (quoted.final === 0 || quoted.freeDays > 0)) {
        grantFree(pending, quoted, account)
        return { free: true }
      }
      const amount = quoted ? quoted.final : catalog.plans[plan].monthlyMinor[INFINITEPAY_MARKET]
      const code = quoted ? quoted.code : ''
      let current = q.activeForCycle.get(pending.id, 'first')
      const same = current && current.amount === amount && (current.promo_code ?? '') === code
      if (same && current.status === 'open' && current.valid_until > at + 60 * 60) return { subscription: pending, charge: current, url: current.link_url }
      if (current && (current.status === 'open' || (current.status === 'creating' && !same))) {
        // About to expire, or the code changed (added, removed or another one): retire it locally (a payment that
        // still arrives for it is honored at its own frozen amount) and issue a fresh link with the right price
        q.setChargeStatus.run('expired', current.order_nsu, current.status)
        record(pending.id, current.order_nsu, 'link_expired', null, same ? 'replaced before checkout' : 'price changed by a promotion code')
        releasePromo(current, 'replaced before checkout')
        current = null
      }
      const price = quoted ? { final: quoted.final, code: quoted.code, quote: quoted } : null
      return { subscription: pending, charge: current ?? reserveCharge(pending, 'first', 'first', at + settings.firstLinkDays * DAY, price) ?? q.activeForCycle.get(pending.id, 'first') }
    })
    if (free) return { free: true }
    if (url) return url
    // 'creating': first attempt, or recovery of an attempt interrupted by a crash or network error
    return requestLink(charge, subscription)
  }

  /** What the buyer and the webhook are told about a settled charge. */
  function outcome(charge) {
    if (charge.status === 'refunded') return 'refunded'
    if (charge.status === 'reversal_review' || charge.review) return 'review'
    return 'confirmed'
  }

  function applyPayment(charge, check, slug, transactionNsu) {
    return transaction(() => {
      const current = q.charge.get(charge.order_nsu)
      if (['paid', 'reversal_review', 'refunded'].includes(current.status)) return outcome(current)
      const subscription = q.subscription.get(charge.subscription_id)
      const afterCancel = subscription.status === 'canceled'
      const paidAt = now()
      // Access continues through the grace period, so active and past-due subscriptions extend from the due
      // date (no free days); expired, pending or abandoned ones start a new month today
      const continuing = (subscription.status === 'active' || subscription.status === 'past_due') && subscription.paid_until > 0
      // A payment after an administrative cancellation covers no period: it grants nothing and a later refund
      // must not shorten anything else
      const periodStart = afterCancel ? paidAt : continuing ? subscription.paid_until : paidAt
      const periodEnd = afterCancel ? paidAt : addMonth(periodStart)
      const changed = q.markPaid.run(slug, transactionNsu, String(check.capture_method ?? '').slice(0, 20), Number(check.paid_amount),
        Number.isInteger(check.installments) ? check.installments : null, periodStart, periodEnd, paidAt, charge.order_nsu).changes
      if (!changed) return 'processing'
      record(subscription.id, charge.order_nsu, 'payment_confirmed', Number(check.paid_amount), `${check.capture_method ?? ''} ${check.installments ?? ''}x`)
      if (current.redemption_id && promotions) {
        // the first purchase with a code: the use is final and the subscription keeps the code for its renewals
        promotions.redeem(current.redemption_id, subscription.id)
        const terms = promotions.terms(current.promo_code)
        if (terms) q.setPromo.run(current.promo_code, remainingAfterFirst({ duration: terms.duration, months: terms.duration_months }), paidAt, subscription.id)
      } else if (current.kind === 'renewal' && current.discount_amount > 0) {
        q.usePromoCycle.run(subscription.id)
      }
      if (afterCancel) {
        // Never reactivate a subscription the operator canceled: the buyer is told it is under financial review
        q.setReview.run('after_cancel', charge.order_nsu)
        record(subscription.id, charge.order_nsu, 'payment_after_cancel', Number(check.paid_amount))
        log.warn('InfinitePay payment after cancellation needs review', charge.order_nsu.slice(0, 12))
        publish(subscription.id)
        return 'review'
      }
      setStatus(subscription, 'active', periodEnd)
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
    if (settled && source === 'browser') return { result: outcome(charge), plan: charge.plan }
    if (settled && (slug !== charge.slug || transactionNsu !== charge.transaction_nsu)) return { result: outcome(charge), plan: charge.plan }

    const check = await request('/payment_check', { handle: settings.handle, order_nsu: orderNsu, transaction_nsu: transactionNsu, slug })
    if (settled) {
      if (check?.success === true && check?.paid === false && charge.status === 'paid') {
        transaction(() => {
          q.setChargeStatus.run('reversal_review', orderNsu, 'paid')
          record(charge.subscription_id, orderNsu, 'reversal_reported', charge.paid_amount, 'payment_check reports the paid charge as unpaid')
          publish(charge.subscription_id)
        })
        log.warn('InfinitePay charge needs refund review', orderNsu.slice(0, 12))
        return { result: 'review', plan: charge.plan }
      }
      return { result: outcome(charge), plan: charge.plan }
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
      // a refunded promotional purchase keeps its use counted (refunding never frees a code for another try)
      if (charge.redemption_id && promotions) promotions.markRefunded(charge.redemption_id)
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
      if (charge?.status === 'reversal_review') q.setChargeStatus.run('paid', orderNsu, 'reversal_review')
      else if (charge?.review !== 'after_cancel') return false
      record(charge.subscription_id, orderNsu, 'review_cleared', null, reason)
      publish(charge.subscription_id)
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
        transaction(() => {
          if (q.setChargeStatus.run('expired', charge.order_nsu, charge.status).changes) {
            record(charge.subscription_id, charge.order_nsu, 'link_expired')
            releasePromo(q.charge.get(charge.order_nsu), 'link expired unpaid')
          }
        })
      }
      for (const { id } of q.toPastDue.all(at)) setStatus(q.subscription.get(id), 'past_due')
      for (const { id } of q.toExpired.all(settings.graceDays * DAY, at)) setStatus(q.subscription.get(id), 'expired')
      for (const { id } of q.toAbandoned.all()) setStatus(q.subscription.get(id), 'abandoned')

      let created = 0
      for (const subscription of q.dueForRenewal.all(settings.renewDaysBefore * DAY, at)) {
        const cycleKey = `due:${subscription.paid_until}`
        const validUntil = subscription.paid_until + settings.graceDays * DAY
        if (validUntil <= at + 60 * 60 || q.activeForCycle.get(subscription.id, cycleKey)) continue
        // A code redeemed at the first purchase keeps its terms for the renewals it covers (even if deactivated later)
        const discounted = subscription.promo_code && promotions
          ? renewalPrice(promotions.terms(subscription.promo_code), subscription.plan, subscription.promo_remaining)
          : null
        if (discounted && discounted.final === 0) {
          // 100% for this month: no artificial charge, the month is granted and one discounted renewal is used
          transaction(() => {
            const fresh = q.subscription.get(subscription.id)
            if (fresh.paid_until !== subscription.paid_until) return
            q.usePromoCycle.run(subscription.id)
            record(subscription.id, '', 'promo_free', 0, `${subscription.promo_code} renewal`)
            setStatus(fresh, 'active', addMonth(fresh.paid_until))
          })
          continue
        }
        const price = discounted && discounted.final >= MIN_CHARGE[INFINITEPAY_MARKET] ? { final: discounted.final, code: subscription.promo_code } : null
        const charge = transaction(() => reserveCharge(subscription, 'renewal', cycleKey, validUntil, price))
        if (!charge) continue
        try { await requestLink(charge, subscription); created++ } catch (error) { log.error('InfinitePay renewal link failed', error.code ?? error.name) }
      }
      // First and renewal links left in 'creating' by a crash or a network error: same order_nsu, stored payload
      for (const charge of q.stuckCreating.all(at - RETRY_CREATING_AFTER, at)) {
        try { await requestLink(charge, q.subscription.get(charge.subscription_id)); created++ } catch (error) { log.error('InfinitePay link retry failed', error.code ?? error.name) }
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
  // Links keep the webhook URL frozen in their payload, so the previous token must stay accepted until they expire
  const tokens = [settings.webhookToken, settings.previousWebhookToken].filter(Boolean).map(value => Buffer.from(value))

  // Return from InfinitePay. The redirect itself proves nothing: the server asks payment_check (the same
  // verification as the webhook) and then sends the buyer to the dashboard, which shows the backend's state.
  router.get('/pagamento/infinitepay/:lang', noStore, limited(60), async (req, res) => {
    const { order_nsu: orderNsu, slug, transaction_nsu: transactionNsu } = req.query
    if (enabled && [orderNsu, slug, transactionNsu].every(value => typeof value === 'string')) {
      try {
        await verify({ orderNsu, slug, transactionNsu, source: 'browser' })
      } catch (error) {
        log.error('InfinitePay return check failed', error.code ?? error.name)
      }
    }
    res.redirect(303, `${settings.appUrl || ''}/conta/assinatura?checkout=returned`)
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

  /**
   * A RECURRING HTS code on the account's running subscription (active or past due): the next renewal links are
   * created with the discount (N months or forever). An open renewal link at the old price is retired so the hourly
   * job issues the discounted one; a month already paid is never changed. `preview` changes nothing.
   */
  function applyRecurringPromo(accountId, code, preview = false) {
    if (!promotions) return { ok: false, reason: 'not_found' }
    return transaction(() => {
      const subscription = db.prepare("SELECT * FROM ip_subscriptions WHERE account_id = ? AND status IN ('active', 'past_due') ORDER BY paid_until DESC LIMIT 1").get(String(accountId))
      if (!subscription) return { ok: false, reason: 'no_subscription' }
      if (q.reviewOpen.get(subscription.id)) return { ok: false, reason: 'account_review' }
      const quoted = promotions.quote({ code, plan: subscription.plan, market: INFINITEPAY_MARKET, accountId: String(accountId), use: 'existing' })
      if (!quoted.ok || preview) return quoted
      if (subscription.promo_remaining !== 0) return { ok: false, reason: 'already_discounted' }
      const redemption = promotions.reserve({ quote: quoted, accountId: String(accountId), plan: subscription.plan, market: INFINITEPAY_MARKET, provider: 'infinitepay', ref: subscription.id })
      promotions.redeem(redemption, subscription.id)
      q.setPromo.run(quoted.code, quoted.duration === 'forever' ? -1 : quoted.months, now(), subscription.id)
      record(subscription.id, '', 'promo_applied', null, `${quoted.code} ${quoted.duration} ${quoted.months ?? ''}`)
      for (const open of db.prepare("SELECT * FROM ip_charges WHERE subscription_id = ? AND kind = 'renewal' AND status IN ('creating', 'open') AND discount_amount = 0").all(subscription.id)) {
        q.setChargeStatus.run('expired', open.order_nsu, open.status)
        record(subscription.id, open.order_nsu, 'link_expired', null, 'replaced by a discounted renewal')
      }
      publish(subscription.id)
      return { ...quoted, applied: true }
    })
  }

  function resync() {
    return transaction(() => {
      const ids = db.prepare("SELECT id FROM ip_subscriptions WHERE account_id <> ''").all()
      for (const { id } of ids) publish(id)
      return ids.length
    })
  }

  return { router, enabled, createCheckout, verify, refund, clearReview, cancel, reconcile, runRenewals, snapshot, resync, applyRecurringPromo }
}
