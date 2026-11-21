import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import Stripe from 'stripe'
import { createApp } from './app.js'
import { billingSettings } from './billing.js'
import { infinitePayClient, infinitePaySettings, missingInfinitePaySettings } from './infinitepay.js'
import { createMailer, mailSettings } from './mailer.js'

process.umask(0o077)
const root = fileURLToPath(new URL('../', import.meta.url))
const billing = billingSettings()
// Keys come only from the environment; nothing is logged or sent to the browser except the on/off state
const stripe = billing.secretKey ? new Stripe(billing.secretKey, { maxNetworkRetries: 2, timeout: 15_000, appInfo: { name: 'ai-stream-clipper-commercial' } }) : null
const infinitepay = infinitePaySettings()
const { app, markets, runRenewals, close } = createApp({
  databasePath: resolve(root, process.env.COMMERCIAL_DB ?? 'data/waitlist.sqlite'),
  dist: resolve(root, 'dist'),
  trustProxy: process.env.COMMERCIAL_TRUST_PROXY,
  stripe,
  billing,
  infinitepay,
  infinitepayRequest: infinitepay.handle && infinitepay.apiKey ? infinitePayClient(infinitepay) : null,
  mailer: createMailer(mailSettings()),
})

// InfinitePay has no recurring billing: renewal links and expirations are handled here, hourly
const renewals = setInterval(() => runRenewals().catch(error => console.error('Renewal run failed', error.code ?? error.name)), 60 * 60 * 1000)
renewals.unref()
runRenewals().catch(error => console.error('Renewal run failed', error.code ?? error.name))
const port = Number(process.env.COMMERCIAL_PORT ?? 8790)
const host = process.env.COMMERCIAL_HOST ?? '127.0.0.1'
const server = app.listen(port, host, () => {
  console.log(`Commercial site listening on http://${host}:${port} (checkout US ${markets.US ? 'on' : 'off'}, BR ${markets.BR ? 'on' : 'off'})`)
  // Names only, never values: BR checkout stays off until every required InfinitePay setting is present
  if (!markets.BR) console.log(`InfinitePay checkout off, missing: ${missingInfinitePaySettings(infinitepay).join(', ')}`)
})

function shutdown() {
  clearInterval(renewals)
  server.close(() => close())
}

process.once('SIGINT', shutdown)
process.once('SIGTERM', shutdown)
