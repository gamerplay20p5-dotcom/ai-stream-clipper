// Test-only helpers: sign requests the way the main backend does (backend/clipper/security.py).
import { signRequest } from './hmac.js'

export const LINK_SECRET = 'billing-link-secret-0123456789abcdef'

/** POST to a signed /internal/billing/* route as the main backend. */
export function internalPost(base, path, body, { secret = LINK_SECRET, caller = 'main', now = Date.now(), tamper = null } = {}) {
  const raw = JSON.stringify(body)
  const headers = signRequest(secret, caller, 'POST', path, raw, now)
  return fetch(`${base}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: tamper ? tamper(raw) : raw })
}

/** A fake main backend: records snapshots and answers with the statuses the test queues. */
export function fakeMain() {
  const main = { received: [], statuses: [], fail: false }
  main.send = async body => {
    if (main.fail) throw Object.assign(new Error('connect ECONNREFUSED'), { code: 'ECONNREFUSED' })
    main.received.push(JSON.parse(body))
    return main.statuses.length ? main.statuses.shift() : 200
  }
  return main
}
