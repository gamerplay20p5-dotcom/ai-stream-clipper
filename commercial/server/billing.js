// Stripe billing for the US market (USD only), following the pattern of HowTo-Software/website.blog:
// Checkout Sessions in subscription mode, a signed webhook as the source of truth, and the official Stripe
// customer portal for self-service.
//
// Checkout is only created for an authenticated account of the main platform (the main backend calls
// createCheckout with the account id over the signed billing link). The account id travels in metadata set
// by this server and comes back in signed webhooks, so a subscription is never linked by a typed email.
// Every change is written together with an entitlement snapshot in the outbox (server/outbox.js).
import express from 'express'
import catalog from '../catalog.json' with { type: 'json' }
import { limitsFor, stripeAccess } from './access.js'

const PRICE_CACHE_MS = 10 * 60 * 1000
// Stripe sells only in dollars; Brazilian customers pay through InfinitePay (server/infinitepay.js)
export const STRIPE_MARKETS = ['US']
export const ACCOUNT_ID = /^[1-9][0-9]{0,11}$/

export function lookupKey(plan, market) {
  return `asc_${plan}_${catalog.markets[market].currency.toLowerCase()}_monthly`
}

/** Plan and market encoded in a lookup key we issued, or null for anything else. */
export function parseLookupKey(key) {
  const match = /^asc_([a-z]+)_([a-z]{3})_monthly$/.exec(key ?? '')
  if (!match || !Object.hasOwn(catalog.plans, match[1])) return null
  const market = Object.keys(catalog.markets).find(id => catalog.markets[id].currency.toLowerCase() === match[2])
  return market ? { plan: match[1], market } : null
}

export function billingSettings(env = process.env) {
  const origin = env.COMMERCIAL_ORIGIN ? new URL(env.COMMERCIAL_ORIGIN).origin : ''
  const appUrl = env.ASC_APP_URL ? new URL(env.ASC_APP_URL).origin : ''
  return {
    secretKey: env.STRIPE_SECRET_KEY ?? '',
    webhookSecret: env.STRIPE_WEBHOOK_SECRET ?? '',
    origin,
    // Checkout returns and the customer portal go back to the dashboard, where access is shown from the backend
    appUrl,
    automaticTax: env.STRIPE_AUTOMATIC_TAX === 'true',
  }
}

function migrate(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS stripe_events (
      id TEXT PRIMARY KEY,
      type TEXT NOT NULL,
      received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) STRICT;
    CREATE TABLE IF NOT EXISTS subscriptions (
      subscription_id TEXT PRIMARY KEY,
      customer_id TEXT NOT NULL,
      email TEXT NOT NULL DEFAULT '',
      plan TEXT NOT NULL,
      market TEXT NOT NULL,
      status TEXT NOT NULL,
      current_period_end INTEGER,
      cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
      last_event_at INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) STRICT;
    CREATE TABLE IF NOT EXISTS invoices (
      invoice_id TEXT PRIMARY KEY,
      subscription_id TEXT NOT NULL DEFAULT '',
      customer_id TEXT NOT NULL,
      status TEXT NOT NULL,
      amount_due INTEGER NOT NULL,
      amount_paid INTEGER NOT NULL,
      currency TEXT NOT NULL,
      attempt_count INTEGER NOT NULL DEFAULT 0,
      hosted_invoice_url TEXT NOT NULL DEFAULT '',
      last_event_at INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) STRICT;
  `)
  // Columns added for account binding, refunds and disputes
  const columns = new Set(db.prepare('PRAGMA table_info(subscriptions)').all().map(column => column.name))
  if (!columns.has('account_id')) db.exec("ALTER TABLE subscriptions ADD COLUMN account_id TEXT NOT NULL DEFAULT ''")
  if (!columns.has('current_period_start')) db.exec('ALTER TABLE subscriptions ADD COLUMN current_period_start INTEGER')
  if (!columns.has('refunded_at')) db.exec('ALTER TABLE subscriptions ADD COLUMN refunded_at INTEGER')
  if (!columns.has('review')) db.exec("ALTER TABLE subscriptions ADD COLUMN review TEXT NOT NULL DEFAULT ''")
  db.exec('CREATE INDEX IF NOT EXISTS subscriptions_account ON subscriptions(account_id)')
}

const idOf = value => (typeof value === 'string' ? value : value?.id) ?? ''

export function createBilling({ db, stripe, settings, outbox = null, promotions = null, log = console }) {
  migrate(db)
  const enabled = Boolean(stripe && settings.secretKey && settings.webhookSecret && settings.appUrl)
  const priceCache = new Map()

  const upsertSubscription = db.prepare(`
    INSERT INTO subscriptions (subscription_id, customer_id, account_id, email, plan, market, status, current_period_start, current_period_end, cancel_at_period_end, last_event_at)
    VALUES (:id, :customer, :account, :email, :plan, :market, :status, :periodStart, :periodEnd, :cancelAtEnd, :eventAt)
    ON CONFLICT(subscription_id) DO UPDATE SET
      customer_id = excluded.customer_id,
      account_id = CASE WHEN subscriptions.account_id = '' THEN excluded.account_id ELSE subscriptions.account_id END,
      email = CASE WHEN excluded.email <> '' THEN excluded.email ELSE subscriptions.email END,
      plan = excluded.plan, market = excluded.market, status = excluded.status,
      current_period_start = excluded.current_period_start, current_period_end = excluded.current_period_end,
      cancel_at_period_end = excluded.cancel_at_period_end, last_event_at = excluded.last_event_at, updated_at = CURRENT_TIMESTAMP
    WHERE excluded.last_event_at >= subscriptions.last_event_at
  `)
  const upsertInvoice = db.prepare(`
    INSERT INTO invoices (invoice_id, subscription_id, customer_id, status, amount_due, amount_paid, currency, attempt_count, hosted_invoice_url, last_event_at)
    VALUES (:id, :subscription, :customer, :status, :due, :paid, :currency, :attempts, :url, :eventAt)
    ON CONFLICT(invoice_id) DO UPDATE SET
      status = excluded.status, amount_due = excluded.amount_due, amount_paid = excluded.amount_paid,
      attempt_count = excluded.attempt_count, hosted_invoice_url = excluded.hosted_invoice_url,
      last_event_at = excluded.last_event_at, updated_at = CURRENT_TIMESTAMP
    WHERE excluded.last_event_at >= invoices.last_event_at
  `)
  const seenEvent = db.prepare('SELECT 1 FROM stripe_events WHERE id = ?')
  const recordEvent = db.prepare('INSERT OR IGNORE INTO stripe_events (id, type) VALUES (?, ?)')
  const subscriptionById = db.prepare('SELECT * FROM subscriptions WHERE subscription_id = ?')
  const latestForCustomer = db.prepare('SELECT * FROM subscriptions WHERE customer_id = ? ORDER BY last_event_at DESC LIMIT 1')
  const customerForAccount = db.prepare("SELECT customer_id FROM subscriptions WHERE account_id = ? AND customer_id <> '' ORDER BY last_event_at DESC LIMIT 1")
  const recentInvoices = db.prepare('SELECT * FROM invoices WHERE subscription_id = ? ORDER BY last_event_at DESC LIMIT 12')

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

  /** Full, provider-neutral state of one subscription for the main backend. */
  function snapshot(subscriptionId) {
    const row = subscriptionById.get(subscriptionId)
    if (!row || !row.account_id) return null
    return {
      provider: 'stripe',
      subscription_ref: row.subscription_id,
      account_id: Number(row.account_id),
      plan: row.plan,
      market: row.market,
      financial_status: row.refunded_at ? 'refunded' : row.status,
      review: row.review || '',
      access: stripeAccess(row),
      limits: limitsFor(row.plan),
      renewal: null,
      manage: 'stripe_portal',
      payments: recentInvoices.all(row.subscription_id).map(invoice => ({
        id: invoice.invoice_id, at: invoice.last_event_at, amount: invoice.amount_paid || invoice.amount_due,
        currency: invoice.currency, status: invoice.status,
      })),
    }
  }

  function publish(subscriptionId) {
    const value = outbox && snapshot(subscriptionId)
    if (value) outbox.enqueue(value)
  }

  /** The Stripe price for a plan, verified against the catalog so Stripe and the site can never disagree silently. */
  async function priceFor(plan, market) {
    const key = lookupKey(plan, market)
    const cached = priceCache.get(key)
    if (cached && cached.expires > Date.now()) return cached.id
    const { data } = await stripe.prices.list({ lookup_keys: [key], active: true, limit: 1 })
    const price = data[0]
    const expected = catalog.plans[plan].monthlyMinor[market]
    const currency = catalog.markets[market].currency.toLowerCase()
    if (!price || price.unit_amount !== expected || price.currency !== currency || price.recurring?.interval !== 'month' || price.recurring?.interval_count !== 1) {
      const error = new Error(`Stripe price for ${key} is missing or differs from catalog.json`)
      error.code = 'price_mismatch'
      throw error
    }
    priceCache.set(key, { id: price.id, expires: Date.now() + PRICE_CACHE_MS })
    return price.id
  }

  function subscriptionRow(subscription, eventAt, email = '') {
    const item = subscription.items?.data?.[0]
    const parsed = parseLookupKey(item?.price?.lookup_key) ?? parseLookupKey(lookupKeyFromMetadata(subscription.metadata))
    if (!parsed) return null
    const account = String(subscription.metadata?.account_id ?? '')
    return {
      id: subscription.id,
      customer: idOf(subscription.customer),
      account: ACCOUNT_ID.test(account) ? account : '',
      email,
      plan: parsed.plan,
      market: parsed.market,
      status: subscription.status,
      // Newer API versions report the period on the subscription item
      periodStart: item?.current_period_start ?? subscription.current_period_start ?? null,
      periodEnd: item?.current_period_end ?? subscription.current_period_end ?? null,
      cancelAtEnd: subscription.cancel_at_period_end ? 1 : 0,
      eventAt,
    }
  }

  function lookupKeyFromMetadata(metadata) {
    const { plan, market } = metadata ?? {}
    return Object.hasOwn(catalog.plans, plan ?? '') && Object.hasOwn(catalog.markets, market ?? '') ? lookupKey(plan, market) : ''
  }

  function storeSubscription(row, after = null) {
    transaction(() => {
      upsertSubscription.run(row)
      publish(row.id)
      if (after) after()
    })
  }

  /** Finds our subscription for a charge (refunds and disputes). */
  async function subscriptionForCharge(charge) {
    const invoiceId = idOf(charge.invoice)
    if (invoiceId) {
      const invoice = await stripe.invoices.retrieve(invoiceId)
      const id = idOf(invoice.parent?.subscription_details?.subscription ?? invoice.subscription)
      if (id) return subscriptionById.get(id)
    }
    // Newer API versions do not link charges to invoices: one account has one active subscription
    return charge.customer ? latestForCustomer.get(idOf(charge.customer)) : null
  }

  async function handleEvent(event) {
    const object = event.data.object
    switch (event.type) {
      case 'checkout.session.completed':
      case 'checkout.session.async_payment_succeeded': {
        if (object.mode !== 'subscription' || !object.subscription) return
        // Retrieve the subscription so the stored state comes from Stripe, not from the session payload
        const subscription = await stripe.subscriptions.retrieve(idOf(object.subscription))
        const row = subscriptionRow(subscription, event.created, object.customer_details?.email ?? '')
        if (!row) return log.warn('Stripe subscription with an unknown price ignored', subscription.id)
        // The promotion use reserved at checkout becomes final with the subscription it paid for
        const redemption = object.metadata?.promo_redemption
        return storeSubscription(row, redemption && promotions ? () => promotions.redeem(redemption, row.id) : null)
      }
      case 'checkout.session.expired': {
        const redemption = object.metadata?.promo_redemption
        if (redemption && promotions) transaction(() => promotions.release(redemption, 'stripe checkout expired'))
        return
      }
      case 'customer.subscription.created':
      case 'customer.subscription.updated':
      case 'customer.subscription.deleted':
      case 'customer.subscription.paused':
      case 'customer.subscription.resumed': {
        const row = subscriptionRow(object, event.created)
        if (!row) return log.warn('Stripe subscription with an unknown price ignored', object.id)
        return storeSubscription(row)
      }
      case 'invoice.finalized':
      case 'invoice.paid':
      case 'invoice.payment_succeeded':
      case 'invoice.payment_failed':
      case 'invoice.voided':
      case 'invoice.marked_uncollectible': {
        const subscription = idOf(object.parent?.subscription_details?.subscription ?? object.subscription)
        return transaction(() => {
          upsertInvoice.run({
            id: object.id,
            subscription,
            customer: idOf(object.customer),
            status: event.type === 'invoice.payment_failed' ? 'payment_failed' : object.status ?? 'unknown',
            due: object.amount_due ?? 0,
            paid: object.amount_paid ?? 0,
            currency: object.currency ?? '',
            attempts: object.attempt_count ?? 0,
            url: typeof object.hosted_invoice_url === 'string' && object.hosted_invoice_url.startsWith('https://') ? object.hosted_invoice_url : '',
            eventAt: event.created,
          })
          // Payment history changed; access itself follows customer.subscription.* events
          if (subscription) publish(subscription)
        })
      }
      case 'charge.refunded': {
        const row = await subscriptionForCharge(object)
        if (!row) return log.warn('Stripe refund for an unknown subscription', object.id)
        const full = object.refunded === true || (object.amount_refunded ?? 0) >= (object.amount ?? Infinity)
        return transaction(() => {
          // A full refund ends access; a partial refund keeps it and asks for a financial review
          if (full) db.prepare('UPDATE subscriptions SET refunded_at = ?, updated_at = CURRENT_TIMESTAMP WHERE subscription_id = ? AND refunded_at IS NULL').run(event.created, row.subscription_id)
          if (full && promotions) promotions.refundSubscription(row.subscription_id)
          else db.prepare("UPDATE subscriptions SET review = 'partial_refund', updated_at = CURRENT_TIMESTAMP WHERE subscription_id = ?").run(row.subscription_id)
          publish(row.subscription_id)
        })
      }
      case 'charge.dispute.created':
      case 'charge.dispute.closed': {
        const charge = typeof object.charge === 'string' ? await stripe.charges.retrieve(object.charge) : object.charge
        const row = charge && await subscriptionForCharge(charge)
        if (!row) return log.warn('Stripe dispute for an unknown subscription', object.id)
        return transaction(() => {
          if (event.type === 'charge.dispute.created') {
            // Access is kept while the dispute is open, but the account is flagged
            db.prepare("UPDATE subscriptions SET review = 'dispute', updated_at = CURRENT_TIMESTAMP WHERE subscription_id = ?").run(row.subscription_id)
          } else if (object.status === 'lost') {
            db.prepare("UPDATE subscriptions SET refunded_at = ?, review = 'dispute_lost', updated_at = CURRENT_TIMESTAMP WHERE subscription_id = ?").run(event.created, row.subscription_id)
          } else {
            db.prepare("UPDATE subscriptions SET review = '', updated_at = CURRENT_TIMESTAMP WHERE subscription_id = ? AND review = 'dispute'").run(row.subscription_id)
          }
          publish(row.subscription_id)
        })
      }
      default:
    }
  }

  const router = express.Router()

  // Stripe signs the raw body, so this route must not go through the JSON parser
  router.post('/api/stripe/webhook', express.raw({ type: 'application/json', limit: '1mb' }), async (req, res) => {
    if (!enabled) return res.status(503).json({ error: 'billing_disabled' })
    let event
    try {
      event = stripe.webhooks.constructEvent(req.body, req.get('stripe-signature') ?? '', settings.webhookSecret)
    } catch {
      return res.status(400).json({ error: 'invalid_signature' })
    }
    if (seenEvent.get(event.id)) return res.json({ received: true, duplicate: true })
    try {
      await handleEvent(event)
      recordEvent.run(event.id, event.type)
      return res.json({ received: true })
    } catch (error) {
      // A non-2xx answer makes Stripe retry the delivery later
      log.error('Stripe webhook processing failed', event.type, error.code ?? error.name)
      return res.status(500).json({ error: 'processing_failed' })
    }
  })

  /**
   * Checkout Session for an authenticated account. Returns the URL to redirect to. `promo` is a reservation already
   * validated by server/promotions.js: a discount is applied with the mirrored Stripe promotion code, free days with
   * the official trial. Customers cannot type other codes on Stripe's page (allow_promotion_codes is off).
   */
  async function createCheckout({ plan, market, language, accountId, email, promo = null }) {
    if (!STRIPE_MARKETS.includes(market)) throw Object.assign(new Error('Stripe only sells in USD'), { code: 'unsupported_market' })
    if (!ACCOUNT_ID.test(String(accountId))) throw Object.assign(new Error('Account required'), { code: 'account_required' })
    if (promo && !promo.freeDays && !promo.stripePromotion) throw Object.assign(new Error('Promotion not synced with Stripe'), { code: 'promo_not_synced' })
    const price = await priceFor(plan, market)
    const metadata = { plan, market, language, account_id: String(accountId), ...(promo ? { promo_code: promo.code, promo_redemption: promo.redemptionId } : {}) }
    const customer = customerForAccount.get(String(accountId))?.customer_id
    const session = await stripe.checkout.sessions.create({
      mode: 'subscription',
      line_items: [{ price, quantity: 1 }],
      client_reference_id: `account-${accountId}`,
      ...(customer ? { customer } : { customer_email: email }),
      // The dashboard shows the state the webhook delivered; the return itself grants nothing
      success_url: `${settings.appUrl}/conta/assinatura?checkout=returned`,
      cancel_url: `${settings.appUrl}/conta/assinatura?checkout=cancelled`,
      locale: language === 'en' ? 'en' : 'pt-BR',
      // Only HTS codes validated by the server (plans, dates, limits per account); never a code typed on Stripe
      ...(promo && !promo.freeDays ? { discounts: [{ promotion_code: promo.stripePromotion }] } : {}),
      billing_address_collection: 'auto',
      metadata,
      subscription_data: { metadata, ...(promo?.freeDays ? { trial_period_days: promo.freeDays } : {}) },
      ...(settings.automaticTax ? { automatic_tax: { enabled: true } } : {}),
    })
    return session.url
  }

  /** Official Stripe customer portal for the account's own customer record. */
  async function portalSession(accountId) {
    const customer = customerForAccount.get(String(accountId))?.customer_id
    if (!customer) throw Object.assign(new Error('No Stripe customer for this account'), { code: 'not_found' })
    const session = await stripe.billingPortal.sessions.create({ customer, return_url: `${settings.appUrl}/conta/assinatura` })
    return session.url
  }

  /** The account's running Stripe subscription (active, trialing or past due), if any. */
  function runningFor(accountId) {
    return db.prepare(`SELECT * FROM subscriptions WHERE account_id = ? AND status IN ('active', 'trialing', 'past_due')
      AND refunded_at IS NULL ORDER BY last_event_at DESC LIMIT 1`).get(String(accountId)) ?? null
  }

  /**
   * A RECURRING HTS code on a running subscription: the mirrored Stripe promotion code is attached to the
   * subscription, so Stripe discounts its next invoices (never the one already paid). `preview` changes nothing.
   */
  async function applyRecurringPromo(row, code, preview = false) {
    if (!promotions) return { ok: false, reason: 'not_found' }
    if (!enabled) return { ok: false, reason: 'not_synced' }
    if (row.review) return { ok: false, reason: 'account_review' }
    const quoted = promotions.quote({ code, plan: row.plan, market: row.market, accountId: row.account_id, use: 'existing' })
    if (!quoted.ok || preview) return quoted
    if (promotions.hasRecurring(row.subscription_id)) return { ok: false, reason: 'already_discounted' }
    const reserved = promotions.reserveStandalone({ code, plan: row.plan, market: row.market, accountId: row.account_id, provider: 'stripe', use: 'existing' })
    if (!reserved.ok) return reserved
    try {
      await stripe.subscriptions.update(row.subscription_id, { discounts: [{ promotion_code: reserved.stripePromotion }] },
        { idempotencyKey: `asc-apply-${reserved.redemptionId}` })
    } catch (error) {
      transaction(() => promotions.release(reserved.redemptionId, 'stripe update failed'))
      throw error
    }
    transaction(() => promotions.redeem(reserved.redemptionId, row.subscription_id))
    return { ...reserved, applied: true }
  }

  function resync() {
    return transaction(() => {
      const ids = db.prepare("SELECT subscription_id FROM subscriptions WHERE account_id <> ''").all()
      for (const { subscription_id: id } of ids) publish(id)
      return ids.length
    })
  }

  return { router, enabled, createCheckout, portalSession, snapshot, resync, runningFor, applyRecurringPromo }
}
