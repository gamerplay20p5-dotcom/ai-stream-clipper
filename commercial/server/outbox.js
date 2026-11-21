// Transactional outbox from the commercial service (money) to the main backend (access).
//
// Every change to a subscription's financial state inserts, in the SAME SQLite transaction, a full snapshot
// of that subscription. A dispatcher delivers snapshots in sequence order to POST /internal/billing/events,
// signed with HMAC. Delivery is at-least-once: the main backend deduplicates by event_id and ignores any
// snapshot older than the last one applied (seq), so restarts, network failures, retries and duplicates are safe.
import { randomUUID } from 'node:crypto'
import { signRequest } from './hmac.js'

export const EVENTS_PATH = '/internal/billing/events'
const MAX_ATTEMPTS = 30

export function outboxSettings(env = process.env) {
  const mainUrl = env.ASC_MAIN_API_URL ? new URL(env.ASC_MAIN_API_URL).origin : ''
  return {
    mainUrl,
    secret: (env.ASC_BILLING_SECRET ?? '').length >= 32 ? env.ASC_BILLING_SECRET : '',
    appUrl: env.ASC_APP_URL ? new URL(env.ASC_APP_URL).origin : '',
  }
}

export function migrateOutbox(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS entitlement_outbox (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,
      event_id TEXT NOT NULL UNIQUE,
      provider TEXT NOT NULL,
      subscription_ref TEXT NOT NULL,
      payload TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      delivered_at INTEGER,
      rejected_at INTEGER,
      attempts INTEGER NOT NULL DEFAULT 0,
      next_attempt_at INTEGER NOT NULL DEFAULT 0,
      last_error TEXT NOT NULL DEFAULT ''
    ) STRICT;
    CREATE INDEX IF NOT EXISTS entitlement_outbox_pending ON entitlement_outbox(delivered_at, rejected_at, next_attempt_at, seq);
  `)
}

/** Default sender: signed POST to the main backend. Returns the HTTP status. */
export function httpSender(settings, fetchImpl = fetch) {
  return async body => {
    const headers = signRequest(settings.secret, 'commercial', 'POST', EVENTS_PATH, body)
    const response = await fetchImpl(`${settings.mainUrl}${EVENTS_PATH}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body, signal: AbortSignal.timeout(10_000),
    })
    return response.status
  }
}

export function createOutbox({ db, send = null, log = console, now = () => Math.floor(Date.now() / 1000) }) {
  migrateOutbox(db)
  const insert = db.prepare('INSERT INTO entitlement_outbox (event_id, provider, subscription_ref, payload, created_at) VALUES (?, ?, ?, ?, ?) RETURNING seq')
  const pending = db.prepare('SELECT * FROM entitlement_outbox WHERE delivered_at IS NULL AND rejected_at IS NULL AND next_attempt_at <= ? ORDER BY seq LIMIT 100')
  const delivered = db.prepare('UPDATE entitlement_outbox SET delivered_at = ?, attempts = attempts + 1, last_error = \'\' WHERE seq = ?')
  const failed = db.prepare('UPDATE entitlement_outbox SET attempts = attempts + 1, next_attempt_at = ?, last_error = ? WHERE seq = ?')
  const rejected = db.prepare('UPDATE entitlement_outbox SET rejected_at = ?, attempts = attempts + 1, last_error = ? WHERE seq = ?')
  let running = false

  /**
   * Queues a snapshot. Must be called inside the transaction that changed the financial state, so the change
   * and its notification are committed together (or not at all).
   */
  function enqueue(snapshot) {
    const eventId = randomUUID()
    const { seq } = insert.get(eventId, snapshot.provider, snapshot.subscription_ref, '{}', now())
    // The sequence is part of the payload so the main backend can discard out-of-order snapshots
    const payload = JSON.stringify({ ...snapshot, event_id: eventId, seq, occurred_at: now() })
    db.prepare('UPDATE entitlement_outbox SET payload = ? WHERE seq = ?').run(payload, seq)
    return seq
  }

  /** Delivers due snapshots in order. Safe to call often; concurrent calls are coalesced. */
  async function dispatch() {
    if (!send || running) return { delivered: 0, failed: 0, skipped: true }
    running = true
    let ok = 0
    let bad = 0
    try {
      for (const row of pending.all(now())) {
        let status = 0
        let error = ''
        try {
          status = await send(row.payload)
        } catch (failure) {
          error = failure.code ?? failure.name ?? 'network_error'
        }
        if (status >= 200 && status < 300) {
          delivered.run(now(), row.seq)
          ok++
        } else if (status >= 400 && status < 500 && status !== 408 && status !== 409 && status !== 429) {
          // Permanently invalid for the main backend: kept for the operator, never retried silently
          rejected.run(now(), `http_${status}`, row.seq)
          log.error('Entitlement snapshot rejected by the main backend', row.provider, status)
          bad++
        } else {
          const attempts = row.attempts + 1
          const reason = error || `http_${status}`
          if (attempts >= MAX_ATTEMPTS) rejected.run(now(), reason, row.seq)
          else failed.run(now() + Math.min(3600, 5 * 2 ** Math.min(attempts, 12)), reason, row.seq)
          bad++
        }
      }
    } finally {
      running = false
    }
    return { delivered: ok, failed: bad }
  }

  function stats() {
    return db.prepare(`SELECT
      SUM(delivered_at IS NULL AND rejected_at IS NULL) AS pending,
      SUM(rejected_at IS NOT NULL) AS rejected,
      MIN(CASE WHEN delivered_at IS NULL AND rejected_at IS NULL THEN created_at END) AS oldest_pending
      FROM entitlement_outbox`).get()
  }

  return { enqueue, dispatch, stats }
}
