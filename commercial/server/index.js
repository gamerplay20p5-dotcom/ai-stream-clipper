import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import Stripe from 'stripe'
import { createApp } from './app.js'
import { billingSettings } from './billing.js'

process.umask(0o077)
const root = fileURLToPath(new URL('../', import.meta.url))
const billing = billingSettings()
// Keys come only from the environment; nothing is logged or sent to the browser except the on/off state
const stripe = billing.secretKey ? new Stripe(billing.secretKey, { maxNetworkRetries: 2, timeout: 15_000, appInfo: { name: 'ai-stream-clipper-commercial' } }) : null
const { app, billingEnabled, close } = createApp({
  databasePath: resolve(root, process.env.COMMERCIAL_DB ?? 'data/waitlist.sqlite'),
  dist: resolve(root, 'dist'),
  trustProxy: process.env.COMMERCIAL_TRUST_PROXY,
  stripe,
  billing,
})
const port = Number(process.env.COMMERCIAL_PORT ?? 8790)
const host = process.env.COMMERCIAL_HOST ?? '127.0.0.1'
const server = app.listen(port, host, () => console.log(`Commercial site listening on http://${host}:${port} (checkout ${billingEnabled ? 'enabled' : 'disabled'})`))

function shutdown() {
  server.close(() => close())
}

process.once('SIGINT', shutdown)
process.once('SIGTERM', shutdown)
