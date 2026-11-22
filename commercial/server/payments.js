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
import { createPromotions } from './promotions.js'
import { normalizeEmail } from './validation.js'

export function createPayments({ db, stripe, billing, infinitepay, infinitepayRequest, mailer, clock, log, link = {}, waitlistOpen = true, legalPublished = true, paymentsOpen = true, testAccounts = [] }) {
  const outbox = createOutbox({ db, send: link.send ?? null, log, ...(clock ? { now: clock } : {}) })
  const stripeReady = Boolean(stripe && billing.secretKey && billing.webhookSecret && billing.appUrl)
  const promotions = createPromotions({ db, stripe, stripeEnabled: stripeReady, log, ...(clock ? { now: clock } : {}) })
  const stripeBilling = createBilling({ db, stripe, settings: billing, outbox, promotions, log })
  const infinite = createInfinitePay({ db, settings: infinitepay, request: infinitepayRequest, mailer, outbox, promotions, log, ...(clock ? { now: clock } : {}) })
  // Checkout needs the provider AND the signed link to the main backend (otherwise payments could not become access)
  const linked = Boolean(link.secret && link.send)
  // `ready`: provider and link configured. `markets`: ready AND open to the public. With payments closed
  // (COMMERCIAL_PAYMENTS_OPEN unset) no checkout is created, while webhooks, the signed link and reconciliation
  // keep working, so the whole integration can be validated before anyone can pay.
  const ready = Object.fromEntries(Object.keys(catalog.markets).map(market => [market, linked && (
    STRIPE_MARKETS.includes(market) ? stripeBilling.enabled : market === INFINITEPAY_MARKET ? infinite.enabled : false)]))
  const markets = Object.fromEntries(Object.entries(ready).map(([market, on]) => [market, on && Boolean(paymentsOpen)]))
  // Controlled real purchase while payments are closed: only these accounts may check out (the public config,
  // the landing and every other account still see payments closed). Empty by default.
  const testers = new Set(testAccounts.map(String).filter(id => ACCOUNT_ID.test(id)))
  const canBuy = (market, accountId) => Boolean(markets[market] || (ready[market] && testers.has(String(accountId))))

  const router = express.Router()
  router.use(stripeBilling.router)
  router.use(infinite.router)

  // Public: only whether each market is open, where accounts live (the landing links there) and whether the
  // waitlist accepts sign-ups (closed until the privacy policy and terms are published)
  router.get('/api/billing/config', (_req, res) => {
    res.set('Cache-Control', 'no-store').json({ markets, appUrl: billing.appUrl || '', waitlist: Boolean(waitlistOpen), legal: Boolean(legalPublished) })
  })

  const signed = [express.raw({ type: 'application/json', limit: '4kb' }), requireSignature(link.secret, 'main')]

  // Operator check of the link (signed, read-only): what is configured and whether the public can pay
  router.post('/internal/billing/status', ...signed, (_req, res) => {
    res.json({ ready, open: Boolean(paymentsOpen), markets, test_accounts: testers.size, stripe: stripeBilling.enabled, infinitepay: infinite.enabled, outbox: outbox.stats() })
  })

  const validCheckout = ({ account_id: accountId, plan, market, language, promo_code: promoCode }) =>
    ACCOUNT_ID.test(String(accountId ?? '')) && typeof plan === 'string' && Object.hasOwn(catalog.plans, plan)
    && typeof market === 'string' && Object.hasOwn(catalog.markets, market) && catalog.languages.includes(language)
    && (promoCode == null || promoCode === '' || (typeof promoCode === 'string' && promoCode.length <= 32))

  router.post('/internal/billing/checkout', ...signed, async (req, res) => {
    const body = req.signedBody ?? {}
    const { account_id: accountId, plan, market, language } = body
    const promoCode = typeof body.promo_code === 'string' ? body.promo_code.trim() : ''
    const email = normalizeEmail(body.email)
    if (!email || !validCheckout(body)) return res.status(422).json({ error: 'invalid_checkout' })
    if (!canBuy(market, accountId)) return res.status(503).json({ error: 'billing_disabled' })
    let reservation = null
    try {
      if (market === INFINITEPAY_MARKET) {
        const result = await infinite.createCheckout({ plan, language, email, accountId, promoCode })
        // 100% discount or free days: access was granted without any charge
        if (result?.free) return res.status(201).json({ free: true })
        return res.status(201).json({ url: result })
      }
      if (promoCode) {
        const reopened = promotions.openStripeCheckout({ code: promoCode, plan, accountId })
        if (reopened) return res.status(201).json({ url: reopened })
        reservation = promotions.reserveStandalone({ code: promoCode, plan, market, accountId, provider: 'stripe' })
        if (!reservation.ok) return res.status(422).json({ error: 'promo_invalid', reason: reservation.reason })
      }
      const url = await stripeBilling.createCheckout({ plan, market, language, accountId, email, promo: reservation })
      if (reservation) promotions.setRef(reservation.redemptionId, url)
      return res.status(201).json({ url })
    } catch (error) {
      if (reservation?.redemptionId) releaseNow(reservation.redemptionId)
      if (error.code === 'promo_invalid') return res.status(422).json({ error: 'promo_invalid', reason: error.reason ?? 'not_found' })
      log.error('Checkout failed', market, error.code ?? error.type ?? error.name)
      return res.status(503).json({ error: 'checkout_unavailable' })
    }
  })

  function releaseNow(id) {
    db.exec('BEGIN IMMEDIATE')
    try { promotions.release(id, 'checkout not created'); db.exec('COMMIT') } catch (error) { db.exec('ROLLBACK'); log.error('Promotion release failed', error.code ?? error.name) }
  }

  // Price with a code for this account, computed here (the dashboard shows it before the checkout). Reserves nothing.
  router.post('/internal/billing/quote', ...signed, (req, res) => {
    const body = req.signedBody ?? {}
    if (!validCheckout({ ...body, language: 'en' }) || typeof body.promo_code !== 'string' || !body.promo_code.trim()) return res.status(422).json({ error: 'invalid_quote' })
    const quoted = promotions.quote({ code: body.promo_code, plan: body.plan, market: body.market, accountId: String(body.account_id) })
    if (!quoted.ok) return res.json({ ok: false, reason: quoted.reason })
    const { stripePromotion: _hidden, ...visible } = quoted
    return res.json(visible)
  })

  // A RECURRING code on the account's running subscription: discount on its next renewals (never retroactive)
  router.post('/internal/billing/apply-promo', ...signed, async (req, res) => {
    const body = req.signedBody ?? {}
    const accountId = String(body.account_id ?? '')
    const code = typeof body.promo_code === 'string' ? body.promo_code.trim() : ''
    if (!ACCOUNT_ID.test(accountId) || !code || code.length > 32) return res.status(422).json({ error: 'invalid_request' })
    try {
      const stripeRow = stripeBilling.runningFor(accountId)
      const result = stripeRow
        ? await stripeBilling.applyRecurringPromo(stripeRow, code, body.preview === true)
        : infinite.applyRecurringPromo(accountId, code, body.preview === true)
      if (!result.ok) return res.status(result.reason === 'no_subscription' ? 404 : 422).json({ error: 'promo_invalid', reason: result.reason })
      const { stripePromotion: _hidden, ...visible } = result
      return res.json(visible)
    } catch (error) {
      log.error('Recurring promotion failed', error.code ?? error.type ?? error.name)
      return res.status(503).json({ error: 'promotions_unavailable' })
    }
  })

  // ---------------------------------------------------------------- promotion codes (HTS panel, admin only)
  const adminSigned = [express.raw({ type: 'application/json', limit: '8kb' }), requireSignature(link.secret, 'main')]
  const actorOf = body => String(body?.actor ?? '').slice(0, 120)
  const promoError = (res, error) => {
    if (error.code === 'invalid_promotion') return res.status(error.reason === 'not_found' ? 404 : 422).json({ error: 'invalid_promotion', reason: error.reason })
    log.error('Promotion operation failed', error.code ?? error.name)
    return res.status(503).json({ error: 'promotions_unavailable' })
  }
  router.post('/internal/promotions/list', ...adminSigned, (_req, res) => res.json({ codes: promotions.list(), stripe: stripeReady, infinitepay: infinite.enabled }))
  router.post('/internal/promotions/details', ...adminSigned, (req, res) => {
    try { return res.json(promotions.details(req.signedBody?.code)) } catch (error) { return promoError(res, error) }
  })
  router.post('/internal/promotions/create', ...adminSigned, async (req, res) => {
    try { return res.status(201).json(await promotions.create(req.signedBody?.definition, actorOf(req.signedBody))) } catch (error) { return promoError(res, error) }
  })
  router.post('/internal/promotions/update', ...adminSigned, async (req, res) => {
    try { return res.json(await promotions.update(req.signedBody?.changes ?? {}, actorOf(req.signedBody))) } catch (error) { return promoError(res, error) }
  })
  router.post('/internal/promotions/sync', ...adminSigned, async (req, res) => {
    try { return res.json(await promotions.syncStripe(String(req.signedBody?.code ?? '').toUpperCase(), actorOf(req.signedBody))) } catch (error) { return promoError(res, error) }
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

  return { router, markets, ready, runRenewals: infinite.runRenewals, dispatch: outbox.dispatch, outboxStats: outbox.stats, outbox, infinite, stripeBilling, promotions }
}
