// Stripe billing for the commercial site, following the pattern of HowTo-Software/website.blog:
// Checkout Sessions in subscription mode, a signed webhook as the source of truth, and the Stripe
// customer portal for self-service. The browser only ever sends a plan id and a market; prices are
// resolved on the server from Stripe lookup keys and checked against catalog.json.
import express from 'express'
import { rateLimit } from 'express-rate-limit'
import catalog from '../catalog.json' with { type: 'json' }

const SESSION_ID = /^cs_(test|live)_[A-Za-z0-9]{10,200}$/
const PRICE_CACHE_MS = 10 * 60 * 1000
// Stripe subscription states that grant access to the product
export const ACTIVE_STATUSES = new Set(['active', 'trialing'])

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
  const portal = env.STRIPE_PORTAL_LOGIN_URL ?? ''
  const origin = env.COMMERCIAL_ORIGIN ? new URL(env.COMMERCIAL_ORIGIN).origin : ''
  return {
    secretKey: env.STRIPE_SECRET_KEY ?? '',
    webhookSecret: env.STRIPE_WEBHOOK_SECRET ?? '',
    origin,
    portalUrl: /^https:\/\/billing\.stripe\.com\/p\/login\/[A-Za-z0-9_-]+$/.test(portal) ? portal : '',
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
}

const idOf = value => (typeof value === 'string' ? value : value?.id) ?? ''

export function createBilling({ db, stripe, settings, log = console }) {
  migrate(db)
  const enabled = Boolean(stripe && settings.secretKey && settings.webhookSecret && settings.origin)
  const priceCache = new Map()

  const upsertSubscription = db.prepare(`
    INSERT INTO subscriptions (subscription_id, customer_id, email, plan, market, status, current_period_end, cancel_at_period_end, last_event_at)
    VALUES (:id, :customer, :email, :plan, :market, :status, :periodEnd, :cancelAtEnd, :eventAt)
    ON CONFLICT(subscription_id) DO UPDATE SET
      customer_id = excluded.customer_id,
      email = CASE WHEN excluded.email <> '' THEN excluded.email ELSE subscriptions.email END,
      plan = excluded.plan, market = excluded.market, status = excluded.status,
      current_period_end = excluded.current_period_end, cancel_at_period_end = excluded.cancel_at_period_end,
      last_event_at = excluded.last_event_at, updated_at = CURRENT_TIMESTAMP
    WHERE excluded.last_event_at >= subscriptions.last_event_at
  `)
  const setEmail = db.prepare(`UPDATE subscriptions SET email = ?, updated_at = CURRENT_TIMESTAMP WHERE subscription_id = ? AND email = ''`)
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
    return {
      id: subscription.id,
      customer: idOf(subscription.customer),
      email,
      plan: parsed.plan,
      market: parsed.market,
      status: subscription.status,
      // Newer API versions report the period on the subscription item
      periodEnd: item?.current_period_end ?? subscription.current_period_end ?? null,
      cancelAtEnd: subscription.cancel_at_period_end ? 1 : 0,
      eventAt,
    }
  }

  function lookupKeyFromMetadata(metadata) {
    const { plan, market } = metadata ?? {}
    return Object.hasOwn(catalog.plans, plan ?? '') && Object.hasOwn(catalog.markets, market ?? '') ? lookupKey(plan, market) : ''
  }

  async function handleEvent(event) {
    const object = event.data.object
    switch (event.type) {
      case 'checkout.session.completed':
      case 'checkout.session.async_payment_succeeded': {
        if (object.mode !== 'subscription' || !object.subscription) return
        // Retrieve the subscription so the stored state comes from Stripe, not from the session payload
        const subscription = await stripe.subscriptions.retrieve(idOf(object.subscription))
        const email = object.customer_details?.email ?? ''
        const row = subscriptionRow(subscription, event.created, email)
        if (!row) return log.warn('Stripe subscription with an unknown price ignored', subscription.id)
        upsertSubscription.run(row)
        if (email) setEmail.run(email, subscription.id)
        return
      }
      case 'customer.subscription.created':
      case 'customer.subscription.updated':
      case 'customer.subscription.deleted':
      case 'customer.subscription.paused':
      case 'customer.subscription.resumed': {
        const row = subscriptionRow(object, event.created)
        if (!row) return log.warn('Stripe subscription with an unknown price ignored', object.id)
        upsertSubscription.run(row)
        return
      }
      case 'invoice.finalized':
      case 'invoice.paid':
      case 'invoice.payment_succeeded':
      case 'invoice.payment_failed':
      case 'invoice.voided':
      case 'invoice.marked_uncollectible': {
        upsertInvoice.run({
          id: object.id,
          subscription: idOf(object.parent?.subscription_details?.subscription ?? object.subscription),
          customer: idOf(object.customer),
          status: event.type === 'invoice.payment_failed' ? 'payment_failed' : object.status ?? 'unknown',
          due: object.amount_due ?? 0,
          paid: object.amount_paid ?? 0,
          currency: object.currency ?? '',
          attempts: object.attempt_count ?? 0,
          url: typeof object.hosted_invoice_url === 'string' && object.hosted_invoice_url.startsWith('https://') ? object.hosted_invoice_url : '',
          eventAt: event.created,
        })
        return
      }
      default:
    }
  }

  const router = express.Router()
  const noStore = (_req, res, next) => { res.set('Cache-Control', 'no-store'); next() }
  const limited = (limit) => rateLimit({ windowMs: 10 * 60 * 1000, limit, standardHeaders: 'draft-8', legacyHeaders: false, message: { error: 'too_many_requests' } })

  router.get('/api/billing/config', noStore, (_req, res) => {
    res.json({ enabled, portalUrl: enabled ? settings.portalUrl : '' })
  })

  router.post('/api/checkout', noStore, limited(20), express.json({ limit: '2kb', inflate: false }), async (req, res) => {
    if (!enabled) return res.status(503).json({ error: 'billing_disabled' })
    if (!req.is('application/json')) return res.status(415).json({ error: 'unsupported_content_type' })
    const { plan, market, language } = req.body ?? {}
    if (typeof plan !== 'string' || !Object.hasOwn(catalog.plans, plan)
      || typeof market !== 'string' || !Object.hasOwn(catalog.markets, market)
      || !catalog.languages.includes(language)) return res.status(422).json({ error: 'invalid_checkout' })
    try {
      const price = await priceFor(plan, market)
      const query = new URLSearchParams({ lang: language, market })
      const metadata = { plan, market, language }
      const session = await stripe.checkout.sessions.create({
        mode: 'subscription',
        line_items: [{ price, quantity: 1 }],
        // Stripe substitutes the real id; the braces must stay unencoded
        success_url: `${settings.origin}/?${query}&checkout=success&session_id={CHECKOUT_SESSION_ID}`,
        cancel_url: `${settings.origin}/?${query}&checkout=cancelled#planos`,
        locale: language === 'en' ? 'en' : 'pt-BR',
        allow_promotion_codes: true,
        billing_address_collection: 'auto',
        metadata,
        subscription_data: { metadata },
        ...(settings.automaticTax ? { automatic_tax: { enabled: true } } : {}),
      })
      return res.status(201).json({ url: session.url })
    } catch (error) {
      log.error('Checkout session failed', error.code ?? error.type ?? error.name)
      return res.status(503).json({ error: 'checkout_unavailable' })
    }
  })

  router.get('/api/checkout/session', noStore, limited(60), async (req, res) => {
    if (!enabled) return res.status(503).json({ error: 'billing_disabled' })
    const id = typeof req.query.id === 'string' ? req.query.id : ''
    if (!SESSION_ID.test(id)) return res.status(422).json({ error: 'invalid_session' })
    try {
      const session = await stripe.checkout.sessions.retrieve(id)
      // No personal data is returned: only what the confirmation notice needs
      const plan = Object.hasOwn(catalog.plans, session.metadata?.plan ?? '') ? session.metadata.plan : ''
      return res.json({ status: session.status, paymentStatus: session.payment_status, plan })
    } catch (error) {
      if (error.statusCode === 404 || error.code === 'resource_missing') return res.status(404).json({ error: 'not_found' })
      log.error('Checkout session lookup failed', error.code ?? error.type ?? error.name)
      return res.status(503).json({ error: 'checkout_unavailable' })
    }
  })

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

  return { router, enabled }
}
