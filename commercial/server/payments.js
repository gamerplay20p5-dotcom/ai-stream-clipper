// Routes each market to its payment provider: USD -> Stripe (server/billing.js), BRL -> InfinitePay
// (server/infinitepay.js). The browser sends a plan, a market, a language and, for Brazil, an email
// for the monthly renewal links. Everything else is decided here.
import express from 'express'
import { rateLimit } from 'express-rate-limit'
import catalog from '../catalog.json' with { type: 'json' }
import { createBilling, STRIPE_MARKETS } from './billing.js'
import { createInfinitePay, INFINITEPAY_MARKET } from './infinitepay.js'
import { normalizeEmail } from './validation.js'

export function createPayments({ db, stripe, billing, infinitepay, infinitepayRequest, mailer, clock, log }) {
  const stripeBilling = createBilling({ db, stripe, settings: billing, log })
  const infinite = createInfinitePay({ db, settings: infinitepay, request: infinitepayRequest, mailer, log, ...(clock ? { now: clock } : {}) })
  const markets = Object.fromEntries(Object.keys(catalog.markets).map(market => [market,
    STRIPE_MARKETS.includes(market) ? stripeBilling.enabled : market === INFINITEPAY_MARKET ? infinite.enabled : false]))

  const router = express.Router()
  router.use(stripeBilling.router)
  router.use(infinite.router)

  router.get('/api/billing/config', (_req, res) => {
    res.set('Cache-Control', 'no-store').json({ markets, portalUrl: stripeBilling.portalUrl })
  })

  router.post('/api/checkout',
    (_req, res, next) => { res.set('Cache-Control', 'no-store'); next() },
    rateLimit({ windowMs: 10 * 60 * 1000, limit: 20, standardHeaders: 'draft-8', legacyHeaders: false, message: { error: 'too_many_requests' } }),
    express.json({ limit: '2kb', inflate: false }),
    async (req, res) => {
      if (!req.is('application/json')) return res.status(415).json({ error: 'unsupported_content_type' })
      const { plan, market, language } = req.body ?? {}
      if (typeof plan !== 'string' || !Object.hasOwn(catalog.plans, plan)
        || typeof market !== 'string' || !Object.hasOwn(catalog.markets, market)
        || !catalog.languages.includes(language)) return res.status(422).json({ error: 'invalid_checkout' })
      if (!markets[market]) return res.status(503).json({ error: 'billing_disabled' })
      try {
        if (market === INFINITEPAY_MARKET) {
          const email = normalizeEmail(req.body.email)
          if (!email) return res.status(422).json({ error: 'invalid_email' })
          return res.status(201).json({ url: await infinite.createCheckout({ plan, language, email }) })
        }
        return res.status(201).json({ url: await stripeBilling.createCheckout({ plan, market, language }) })
      } catch (error) {
        log.error('Checkout failed', market, error.code ?? error.type ?? error.name)
        return res.status(503).json({ error: 'checkout_unavailable' })
      }
    })

  return { router, markets, runRenewals: infinite.runRenewals }
}
