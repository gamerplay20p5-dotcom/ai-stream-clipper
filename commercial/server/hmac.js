// HMAC signing shared with the main backend (backend/clipper/security.py): same canonical string
// METHOD \n path?query \n timestamp \n sha256(body) \n caller, same X-ASC-* headers. The billing link uses its own
// secret (ASC_BILLING_SECRET), never the workers' internal secret.
import { createHash, createHmac, timingSafeEqual } from 'node:crypto'

export const HEADERS = { caller: 'x-asc-worker', ts: 'x-asc-timestamp', sig: 'x-asc-signature', body: 'x-asc-content-sha256' }
export const MAX_SKEW_S = 60

export const sha256 = data => createHash('sha256').update(data).digest('hex')
const canonical = (method, pathQs, ts, bodySha, caller) => [method.toUpperCase(), pathQs, ts, bodySha, caller].join('\n')

export function signRequest(secret, caller, method, pathQs, body, now = Date.now()) {
  const ts = String(Math.floor(now / 1000))
  const bodySha = sha256(body)
  const sig = createHmac('sha256', secret).update(canonical(method, pathQs, ts, bodySha, caller)).digest('hex')
  return { 'X-ASC-Worker': caller, 'X-ASC-Timestamp': ts, 'X-ASC-Signature': sig, 'X-ASC-Content-SHA256': bodySha }
}

/** Express middleware for routes the main backend calls. Needs the raw body (express.raw). */
export function requireSignature(secret, expectedCaller) {
  return (req, res, next) => {
    if (!secret) return res.status(503).json({ error: 'billing_link_disabled' })
    const caller = req.get(HEADERS.caller) ?? ''
    const ts = Number(req.get(HEADERS.ts))
    const sig = req.get(HEADERS.sig) ?? ''
    const declared = req.get(HEADERS.body) ?? ''
    const body = Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0)
    const bodySha = sha256(body)
    const fresh = Number.isFinite(ts) && Math.abs(Date.now() / 1000 - ts) <= MAX_SKEW_S
    const expected = createHmac('sha256', secret).update(canonical(req.method, req.originalUrl, String(ts), bodySha, caller)).digest('hex')
    const ok = caller === expectedCaller && fresh && declared === bodySha && sig.length === expected.length
      && timingSafeEqual(Buffer.from(sig), Buffer.from(expected))
    if (!ok) return res.status(401).json({ error: 'invalid_signature' })
    try {
      req.signedBody = body.length ? JSON.parse(body.toString('utf8')) : {}
    } catch {
      return res.status(400).json({ error: 'invalid_json' })
    }
    return next()
  }
}
