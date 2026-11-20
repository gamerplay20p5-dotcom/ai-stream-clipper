import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createApp } from './app.js'

process.umask(0o077)
const root = fileURLToPath(new URL('../', import.meta.url))
const { app, close } = createApp({
  databasePath: resolve(root, process.env.COMMERCIAL_DB ?? 'data/waitlist.sqlite'),
  dist: resolve(root, 'dist'),
  trustProxy: process.env.COMMERCIAL_TRUST_PROXY,
})
const port = Number(process.env.COMMERCIAL_PORT ?? 8790)
const host = process.env.COMMERCIAL_HOST ?? '127.0.0.1'
const server = app.listen(port, host, () => console.log(`Commercial site listening on http://${host}:${port}`))

function shutdown() {
  server.close(() => close())
}

process.once('SIGINT', shutdown)
process.once('SIGTERM', shutdown)
