import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import Stripe from 'stripe'
import { createApp } from './app.js'
import { billingSettings } from './billing.js'
import { infinitePayClient, infinitePaySettings, missingInfinitePaySettings } from './infinitepay.js'
import { createMailer, mailSettings } from './mailer.js'
import { httpSender, httpStateReader, outboxSettings } from './outbox.js'
import { createOutboxMonitor } from './outbox-monitor.js'

process.umask(0o077)
const root = fileURLToPath(new URL('../', import.meta.url))
const billing = billingSettings()
// Keys come only from the environment; nothing is logged or sent to the browser except the on/off state
const stripe = billing.secretKey ? new Stripe(billing.secretKey, { maxNetworkRetries: 2, timeout: 15_000, appInfo: { name: 'ai-stream-clipper-commercial' } }) : null
const infinitepay = infinitePaySettings()
// Signed link to the main backend: checkout requests in, entitlement snapshots out
const link = outboxSettings()
const { app, markets, runRenewals, dispatch, outbox, close } = createApp({
  databasePath: resolve(root, process.env.COMMERCIAL_DB ?? 'data/waitlist.sqlite'),
  dist: resolve(root, 'dist'),
  trustProxy: process.env.COMMERCIAL_TRUST_PROXY,
  stripe,
  billing,
  infinitepay,
  infinitepayRequest: infinitepay.handle && infinitepay.apiKey ? infinitePayClient(infinitepay) : null,
  mailer: createMailer(mailSettings()),
  link: link.secret && link.mainUrl ? { secret: link.secret, send: httpSender(link) } : {},
})

// InfinitePay has no recurring billing: renewal links and expirations are handled here, hourly
const renew = () => runRenewals().then(() => dispatch()).catch(error => console.error('Renewal run failed', error.code ?? error.name))
const renewals = setInterval(renew, 60 * 60 * 1000)
renewals.unref()
renew()
// Entitlement snapshots to the main backend: retried with backoff until delivered (outbox)
const deliveries = setInterval(() => dispatch().catch(error => console.error('Entitlement dispatch failed', error.code ?? error.name)), 15 * 1000)
deliveries.unref()
// Alerts (paid but not synced, rejected, blocked, stalled) and reconciliation with the main backend
const monitor = createOutboxMonitor({ outbox, readState: link.secret && link.mainUrl ? httpStateReader(link) : null })
const watch = setInterval(() => monitor.tick().catch(error => console.error('Outbox monitor failed', error.code ?? error.name)), 60 * 1000)
watch.unref()
const port = Number(process.env.COMMERCIAL_PORT ?? 8790)
const host = process.env.COMMERCIAL_HOST ?? '127.0.0.1'
const server = app.listen(port, host, () => {
  console.log(`Commercial site listening on http://${host}:${port} (checkout US ${markets.US ? 'on' : 'off'}, BR ${markets.BR ? 'on' : 'off'})`)
  // Names only, never values: BR checkout stays off until every required InfinitePay setting is present
  if (!markets.BR) console.log(`InfinitePay checkout off, missing: ${missingInfinitePaySettings(infinitepay).join(', ')}`)
  if (!link.secret || !link.mainUrl) console.log('Checkout off: set ASC_MAIN_API_URL and ASC_BILLING_SECRET (32+ chars) to link payments to accounts')
})

function shutdown() {
  clearInterval(renewals)
  clearInterval(deliveries)
  clearInterval(watch)
  server.close(() => close())
}

process.once('SIGINT', shutdown)
process.once('SIGTERM', shutdown)
