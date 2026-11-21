// Routes each market to its payment provider: USD -> Stripe (server/billing.js), BRL -> InfinitePay
// (server/infinitepay.js).
//
// There is no anonymous checkout. The main backend, for an authenticated account, calls the signed
// /internal/billing/* routes; the account id it sends is what links the payment to the account. Financial
// changes reach the main backend as entitlement snapshots through the outbox (server/outbox.js).
import express from 'express'
import catalog from '../catalog.json' with { type: 'json' }
import { ACCOUNT_ID, createBilling, STRIPE_MARKETS } from './billing.js'
import { requireSignature } from './hmac.js'
import { createInfinitePay, INFINITEPAY_MARKET } from './infinitepay.js'
import { createOutbox } from './outbox.js'
import { normalizeEmail } from './validation.js'

export function createPayments({ db, stripe, billing, infinitepay, infinitepayRequest, mailer, clock, log, link = {} }) {
  const outbox = createOutbox({ db, send: link.send ?? null, log, ...(clock ? { now: clock } : {}) })
  const stripeBilling = createBilling({ db, stripe, settings: billing, outbox, log })
  const infinite = createInfinitePay({ db, settings: infinitepay, request: infinitepayRequest, mailer, outbox, log, ...(clock ? { now: clock } : {}) })
  // Checkout needs the provider AND the signed link to the main backend (otherwise payments could not become access)
  const linked = Boolean(link.secret && link.send)
  const markets = Object.fromEntries(Object.keys(catalog.markets).map(market => [market, linked && (
    STRIPE_MARKETS.includes(market) ? stripeBilling.enabled : market === INFINITEPAY_MARKET ? infinite.enabled : false)]))

  const router = express.Router()
  router.use(stripeBilling.router)
  router.use(infinite.router)

  // Public: only whether each market is open and where accounts live (the landing links there)
  router.get('/api/billing/config', (_req, res) => {
    res.set('Cache-Control', 'no-store').json({ markets, appUrl: billing.appUrl || '' })
  })

  const signed = [express.raw({ type: 'application/json', limit: '4kb' }), requireSignature(link.secret, 'main')]

  router.post('/internal/billing/checkout', ...signed, async (req, res) => {
    const { account_id: accountId, plan, market, language } = req.signedBody ?? {}
    const email = normalizeEmail(req.signedBody?.email)
    if (!ACCOUNT_ID.test(String(accountId ?? '')) || !email
      || typeof plan !== 'string' || !Object.hasOwn(catalog.plans, plan)
      || typeof market !== 'string' || !Object.hasOwn(catalog.markets, market)
      || !catalog.languages.includes(language)) return res.status(422).json({ error: 'invalid_checkout' })
    if (!markets[market]) return res.status(503).json({ error: 'billing_disabled' })
    try {
      const url = market === INFINITEPAY_MARKET
        ? await infinite.createCheckout({ plan, language, email, accountId })
        : await stripeBilling.createCheckout({ plan, market, language, accountId, email })
      return res.status(201).json({ url })
    } catch (error) {
      log.error('Checkout failed', market, error.code ?? error.type ?? error.name)
      return res.status(503).json({ error: 'checkout_unavailable' })
    }
  })

  router.post('/internal/billing/portal', ...signed, async (req, res) => {
    const accountId = String(req.signedBody?.account_id ?? '')
    if (!ACCOUNT_ID.test(accountId)) return res.status(422).json({ error: 'invalid_account' })
    if (!stripeBilling.enabled) return res.status(503).json({ error: 'billing_disabled' })
    try {
      return res.json({ url: await stripeBilling.portalSession(accountId) })
    } catch (error) {
      if (error.code === 'not_found') return res.status(404).json({ error: 'no_stripe_customer' })
      log.error('Stripe portal failed', error.code ?? error.type ?? error.name)
      return res.status(503).json({ error: 'portal_unavailable' })
    }
  })

  // Recovery: re-send every subscription's current state (the main backend ignores what it already has)
  router.post('/internal/billing/resync', ...signed, (_req, res) => {
    const count = stripeBilling.resync() + infinite.resync()
    outbox.dispatch().catch(() => {})
    res.json({ queued: count })
  })

  return { router, markets, runRenewals: infinite.runRenewals, dispatch: outbox.dispatch, outboxStats: outbox.stats, infinite, stripeBilling }
}
