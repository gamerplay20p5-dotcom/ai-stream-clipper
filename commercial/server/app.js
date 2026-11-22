import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import express from 'express'
import { rateLimit } from 'express-rate-limit'
import catalog from '../catalog.json' with { type: 'json' }
import { billingSettings } from './billing.js'
import { infinitePaySettings } from './infinitepay.js'
import { createPayments } from './payments.js'

import { emailPattern } from './validation.js'

function parseInterest(body) {
  if (!body || typeof body !== 'object') return null
  const { plan, country, platform, language, market, consent } = body
  const email = typeof body.email === 'string' ? body.email.trim().toLowerCase() : ''
  const otherCountry = country === 'OTHER' && typeof body.otherCountry === 'string' ? body.otherCountry.trim() : ''
  if (
    email.length > 254 || !emailPattern.test(email)
    || typeof plan !== 'string' || !Object.hasOwn(catalog.plans, plan)
    || !catalog.countries.includes(country) || !catalog.platforms.includes(platform)
    || !catalog.languages.includes(language) || typeof market !== 'string' || !Object.hasOwn(catalog.markets, market)
    || consent !== true
    || (country === 'OTHER' && (otherCountry.length < 2 || otherCountry.length > 80 || /[\p{C}]/u.test(otherCountry)))
  ) return null
  return { email, plan, country, otherCountry, platform, language, market }
}

export function createApp({ databasePath, dist, trustProxy, waitlistOpen = true, paymentsOpen = true, stripe = null, billing = billingSettings({}), infinitepay = infinitePaySettings({}), infinitepayRequest = null, mailer = null, clock, link = {}, log = console }) {
  mkdirSync(dirname(databasePath), { recursive: true, mode: 0o700 })
  const db = new DatabaseSync(databasePath, { timeout: 1_000 })
  db.exec(`
    CREATE TABLE IF NOT EXISTS waitlist (
      id INTEGER PRIMARY KEY,
      email TEXT NOT NULL UNIQUE,
      plan TEXT NOT NULL,
      country TEXT NOT NULL,
      other_country TEXT NOT NULL,
      platform TEXT NOT NULL,
      language TEXT NOT NULL,
      market TEXT NOT NULL,
      consent_version INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) STRICT
  `)
  const insert = db.prepare(`
    INSERT INTO waitlist (email, plan, country, other_country, platform, language, market, consent_version)
    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
    ON CONFLICT(email) DO NOTHING
  `)

  const app = express()
  app.disable('x-powered-by')
  if (trustProxy) app.set('trust proxy', trustProxy)
  app.use((_req, res, next) => {
    res.set({
      'Content-Security-Policy': "default-src 'self'; style-src-attr 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
      'Referrer-Policy': 'strict-origin-when-cross-origin',
      'X-Content-Type-Options': 'nosniff',
    })
    next()
  })

  const payments = createPayments({ db, stripe, billing, infinitepay, infinitepayRequest, mailer, clock, log, link, waitlistOpen, paymentsOpen })
  app.use(payments.router)

  app.post('/api/waitlist',
    (_req, res, next) => { res.set('Cache-Control', 'no-store'); next() },
    rateLimit({
      windowMs: 10 * 60 * 1000,
      limit: 15,
      standardHeaders: 'draft-8',
      legacyHeaders: false,
      message: { error: 'too_many_requests' },
    }),
    express.json({ limit: '4kb', inflate: false }),
    (req, res) => {
      // Closed until the privacy policy and terms are published: nothing is collected
      if (!waitlistOpen) return res.status(503).json({ error: 'waitlist_closed' })
      if (!req.is('application/json')) return res.status(415).json({ error: 'unsupported_content_type' })
      // Silently accept bots and repeated addresses without disclosing list membership.
      if (typeof req.body?.website === 'string' && req.body.website.trim()) return res.sendStatus(202)
      const interest = parseInterest(req.body)
      if (!interest) return res.status(422).json({ error: 'invalid_interest' })
      const { email, plan, country, otherCountry, platform, language, market } = interest
      insert.run(email, plan, country, otherCountry, platform, language, market)
      return res.sendStatus(202)
    },
  )

  if (dist) {
    app.get('/', (req, res) => {
      const language = catalog.languages.includes(req.query.lang)
        ? req.query.lang
        : req.acceptsLanguages('pt-BR', 'en') || 'pt-BR'
      const market = typeof req.query.market === 'string' && Object.hasOwn(catalog.markets, req.query.market)
        ? req.query.market
        : language === 'en' ? 'US' : 'BR'
      res.vary('Accept-Language').set('Content-Language', language)
      return res.sendFile(join(dist, 'pages', `${language}-${market}.html`))
    })
    app.use('/assets', express.static(join(dist, 'assets'), { immutable: true, maxAge: '1y' }))
    app.use(express.static(dist))
  }
  app.use((_req, res) => res.status(404).json({ error: 'not_found' }))
  app.use((error, _req, res, _next) => {
    if (error.status >= 400 && error.status < 500) {
      return res.status(error.status).json({ error: 'invalid_request' })
    }
    console.error('Commercial request failed', error.code ?? error.name)
    return res.status(500).json({ error: 'request_failed' })
  })

  return { app, markets: payments.markets, ready: payments.ready, runRenewals: payments.runRenewals, dispatch: payments.dispatch, outbox: payments.outbox, promotions: payments.promotions, payments, close: () => db.close() }
}
