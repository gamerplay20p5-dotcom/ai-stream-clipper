// Transactional outbox from the commercial service (money) to the main backend (access).
//
// Every change to a subscription's financial state inserts, in the SAME SQLite transaction, a full snapshot
// of that subscription. A dispatcher delivers snapshots in sequence order to POST /internal/billing/events,
// signed with HMAC. Delivery is at-least-once: the main backend deduplicates by event_id and ignores any
// snapshot older than the last one applied (seq), so restarts, network failures, retries and duplicates are safe.
//
// Failure handling (nothing is ever dropped silently):
// - transient (network, timeouts, 5xx, 408, 429): retried forever with exponential backoff capped at 1 hour;
// - blocked (401/403 link misconfiguration or clock skew, 404 endpoint missing, 409 account not ready):
//   retried forever with backoff capped at 6 hours, because they heal once the operator fixes the cause;
// - permanent (400, 413, 415, 422: the API says the snapshot itself is invalid): parked as rejected, never
//   retried automatically, visible in the report and the alerts until an operator resends it (audited) or a
//   newer snapshot of the same subscription is delivered (which supersedes it: snapshots are full state).
// Each row is claimed with a short lease before sending, so two dispatchers (server and CLI, or a restart
// while a request is in flight) never race on the same row; a duplicate send is harmless anyway (event_id).
import { randomUUID } from 'node:crypto'
import { signRequest } from './hmac.js'

export const EVENTS_PATH = '/internal/billing/events'
export const STATE_PATH = '/internal/billing/state'
const LEASE_SECONDS = 60
const TRANSIENT_CAP = 3600
const BLOCKED_CAP = 6 * 3600
/** A paid subscription whose latest snapshot is still undelivered after this long raises an alert. */
export const UNSYNCED_AFTER = 120
/** Undelivered snapshots older than this are reported as stalled. */
export const STALLED_AFTER = 15 * 60
const PERMANENT = new Set([400, 413, 415, 422])
const BLOCKED = new Set([401, 403, 404, 409])
const PAID_ACCESS = ['active', 'grace']

export function outboxSettings(env = process.env) {
  const mainUrl = env.ASC_MAIN_API_URL ? new URL(env.ASC_MAIN_API_URL).origin : ''
  return {
    mainUrl,
    secret: (env.ASC_BILLING_SECRET ?? '').length >= 32 ? env.ASC_BILLING_SECRET : '',
    appUrl: env.ASC_APP_URL ? new URL(env.ASC_APP_URL).origin : '',
  }
}

const COLUMNS = {
  lease_until: 'INTEGER',
  lease_owner: "TEXT NOT NULL DEFAULT ''",
  superseded_by: 'INTEGER',
  error_class: "TEXT NOT NULL DEFAULT ''",
  first_failure_at: 'INTEGER',
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
    CREATE INDEX IF NOT EXISTS entitlement_outbox_ref ON entitlement_outbox(provider, subscription_ref, seq);
    CREATE TABLE IF NOT EXISTS entitlement_outbox_audit (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      at INTEGER NOT NULL,
      actor TEXT NOT NULL,
      action TEXT NOT NULL,
      seq INTEGER NOT NULL,
      reason TEXT NOT NULL,
      previous_state TEXT NOT NULL,
      previous_error TEXT NOT NULL DEFAULT ''
    ) STRICT;
  `)
  // Additive upgrade of databases created by the first version of the outbox
  const existing = new Set(db.prepare('PRAGMA table_info(entitlement_outbox)').all().map(column => column.name))
  for (const [name, type] of Object.entries(COLUMNS)) {
    if (!existing.has(name)) db.exec(`ALTER TABLE entitlement_outbox ADD COLUMN ${name} ${type}`)
  }
}

/** Signed POST to the main backend. Returns { status, body } (body parsed when JSON). */
export function signedPoster(settings, fetchImpl = fetch) {
  return async (path, body) => {
    const headers = signRequest(settings.secret, 'commercial', 'POST', path, body)
    const response = await fetchImpl(`${settings.mainUrl}${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body, signal: AbortSignal.timeout(10_000),
    })
    let parsed = null
    try { parsed = await response.json() } catch { /* not JSON */ }
    return { status: response.status, body: parsed }
  }
}

/** Default sender: signed POST of one snapshot. Returns the HTTP status. */
export function httpSender(settings, fetchImpl = fetch) {
  const post = signedPoster(settings, fetchImpl)
  return async body => (await post(EVENTS_PATH, body)).status
}

/** Reads what the main backend has applied for a list of subscriptions (reconciliation). */
export function httpStateReader(settings, fetchImpl = fetch) {
  const post = signedPoster(settings, fetchImpl)
  return async refs => {
    const { status, body } = await post(STATE_PATH, JSON.stringify({ refs }))
    if (status !== 200 || !Array.isArray(body?.items)) throw Object.assign(new Error('state unavailable'), { code: `http_${status}` })
    return body.items
  }
}

export function classify(status) {
  if (status >= 200 && status < 300) return 'delivered'
  if (PERMANENT.has(status)) return 'permanent'
  if (BLOCKED.has(status)) return 'blocked'
  if (status >= 400 && status < 500 && status !== 408 && status !== 429) return 'permanent'
  return 'transient'
}

export function backoff(attempts, kind) {
  if (kind === 'blocked') return Math.min(BLOCKED_CAP, 60 * 2 ** Math.min(attempts, 12))
  return Math.min(TRANSIENT_CAP, 5 * 2 ** Math.min(attempts, 12))
}

function stateOf(row) {
  if (row.delivered_at) return 'delivered'
  if (row.superseded_by) return 'superseded'
  if (row.rejected_at) return 'rejected'
  return row.error_class === 'blocked' ? 'blocked' : 'pending'
}

export function createOutbox({ db, send = null, log = console, now = () => Math.floor(Date.now() / 1000) }) {
  migrateOutbox(db)
  const owner = randomUUID()
  const insert = db.prepare('INSERT INTO entitlement_outbox (event_id, provider, subscription_ref, payload, created_at) VALUES (?, ?, ?, ?, ?) RETURNING seq')
  const setPayload = db.prepare('UPDATE entitlement_outbox SET payload = ? WHERE seq = ?')
  const due = db.prepare(`SELECT seq FROM entitlement_outbox
    WHERE delivered_at IS NULL AND rejected_at IS NULL AND superseded_by IS NULL AND next_attempt_at <= ?
      AND (lease_until IS NULL OR lease_until < ?)
    ORDER BY seq LIMIT 100`)
  const claim = db.prepare(`UPDATE entitlement_outbox SET lease_until = ?, lease_owner = ?
    WHERE seq = ? AND delivered_at IS NULL AND rejected_at IS NULL AND superseded_by IS NULL
      AND (lease_until IS NULL OR lease_until < ?)`)
  const byId = db.prepare('SELECT * FROM entitlement_outbox WHERE seq = ?')
  const newerDelivered = db.prepare(`SELECT MAX(seq) AS seq FROM entitlement_outbox
    WHERE provider = ? AND subscription_ref = ? AND seq > ? AND delivered_at IS NOT NULL`)
  const supersede = db.prepare(`UPDATE entitlement_outbox SET superseded_by = ?, lease_until = NULL, lease_owner = ''
    WHERE provider = ? AND subscription_ref = ? AND seq < ? AND delivered_at IS NULL AND superseded_by IS NULL
    RETURNING seq, rejected_at, last_error`)
  const delivered = db.prepare(`UPDATE entitlement_outbox SET delivered_at = ?, attempts = attempts + 1, last_error = '',
    error_class = '', lease_until = NULL, lease_owner = '' WHERE seq = ?`)
  const failed = db.prepare(`UPDATE entitlement_outbox SET attempts = attempts + 1, next_attempt_at = ?, last_error = ?, error_class = ?,
    first_failure_at = COALESCE(first_failure_at, ?), lease_until = NULL, lease_owner = '' WHERE seq = ?`)
  const rejected = db.prepare(`UPDATE entitlement_outbox SET rejected_at = ?, attempts = attempts + 1, last_error = ?, error_class = 'permanent',
    first_failure_at = COALESCE(first_failure_at, ?), lease_until = NULL, lease_owner = '' WHERE seq = ?`)
  const audit = db.prepare(`INSERT INTO entitlement_outbox_audit (at, actor, action, seq, reason, previous_state, previous_error)
    VALUES (?, ?, ?, ?, ?, ?, ?)`)
  let running = false

  function transaction(fn) {
    if (db.isTransaction) return fn()
    db.exec('BEGIN IMMEDIATE')
    try {
      const result = fn()
      db.exec('COMMIT')
      return result
    } catch (error) {
      db.exec('ROLLBACK')
      throw error
    }
  }

  /**
   * Queues a snapshot. Must be called inside the transaction that changed the financial state, so the change
   * and its notification are committed together (or not at all).
   */
  function enqueue(snapshot) {
    const eventId = randomUUID()
    const { seq } = insert.get(eventId, snapshot.provider, snapshot.subscription_ref, '{}', now())
    // The sequence is part of the payload so the main backend can discard out-of-order snapshots
    const payload = JSON.stringify({ ...snapshot, event_id: eventId, seq, occurred_at: now() })
    setPayload.run(payload, seq)
    return seq
  }

  /** Marks older undelivered snapshots of the same subscription as replaced by `seq` (full state wins). */
  function supersedeOlder(row, seq) {
    for (const old of supersede.all(seq, row.provider, row.subscription_ref, seq)) {
      // A rejected snapshot that a newer delivery replaced is resolved: keep the trail of why
      if (old.rejected_at) audit.run(now(), 'system', 'superseded', old.seq, `replaced by seq ${seq}`, 'rejected', old.last_error)
    }
  }

  /** Delivers due snapshots in order. Safe to call often; concurrent calls are coalesced or leased. */
  async function dispatch() {
    if (!send || running) return { delivered: 0, failed: 0, skipped: true }
    running = true
    let ok = 0
    let bad = 0
    try {
      for (const { seq } of due.all(now(), now())) {
        if (claim.run(now() + LEASE_SECONDS, owner, seq, now()).changes !== 1) continue // another dispatcher has it
        const row = byId.get(seq)
        const newer = newerDelivered.get(row.provider, row.subscription_ref, seq).seq
        if (newer) {
          transaction(() => supersedeOlder(row, newer))
          continue
        }
        let status = 0
        let error = ''
        try {
          status = await send(row.payload)
        } catch (failure) {
          error = failure.code ?? failure.name ?? 'network_error'
        }
        const kind = error ? 'transient' : classify(status)
        const reason = error || `http_${status}`
        if (kind === 'delivered') {
          transaction(() => {
            delivered.run(now(), seq)
            supersedeOlder(row, seq)
          })
          ok++
        } else if (kind === 'permanent') {
          // The API says this snapshot is invalid: parked for the operator, never retried in a loop
          rejected.run(now(), reason, now(), seq)
          log.error('Entitlement snapshot rejected by the main backend', { seq, provider: row.provider, status })
          bad++
        } else {
          failed.run(now() + backoff(row.attempts + 1, kind), reason, kind, now(), seq)
          bad++
          // The main backend is unreachable: one probe per cycle while it is down, not one per queued event
          if (kind === 'transient') break
        }
      }
    } finally {
      running = false
    }
    return { delivered: ok, failed: bad }
  }

  function requireAudit(actor, reason) {
    if (typeof actor !== 'string' || !actor.trim()) throw new Error('An operator name is required (--by)')
    if (typeof reason !== 'string' || reason.trim().length < 3) throw new Error('A reason is required (--reason)')
  }

  /**
   * Puts one snapshot back in the queue (audited). The event keeps its event_id and seq, so the main backend
   * applies it at most once and never over a newer state: resending cannot duplicate any effect.
   */
  function resend(seq, { actor, reason, allowDelivered = false }) {
    requireAudit(actor, reason)
    return transaction(() => {
      const row = byId.get(seq)
      if (!row) throw new Error(`No outbox event with seq ${seq}`)
      const state = stateOf(row)
      if (state === 'superseded') return { seq, outcome: 'superseded', by: row.superseded_by }
      if (state === 'delivered' && !allowDelivered) return { seq, outcome: 'already_delivered' }
      const newer = newerDelivered.get(row.provider, row.subscription_ref, seq).seq
      if (newer) {
        supersedeOlder(row, newer)
        audit.run(now(), actor.trim(), 'superseded', seq, reason.trim(), state, row.last_error)
        return { seq, outcome: 'superseded', by: newer }
      }
      db.prepare(`UPDATE entitlement_outbox SET delivered_at = NULL, rejected_at = NULL, next_attempt_at = 0, attempts = 0,
        error_class = '', lease_until = NULL, lease_owner = '' WHERE seq = ?`).run(seq)
      audit.run(now(), actor.trim(), 'resend', seq, reason.trim(), state, row.last_error)
      return { seq, outcome: 'queued', previous: state }
    })
  }

  /** Resends every rejected snapshot that no newer delivery has replaced (audited, one entry per event). */
  function resendRejected({ actor, reason }) {
    requireAudit(actor, reason)
    const rows = db.prepare('SELECT seq FROM entitlement_outbox WHERE rejected_at IS NOT NULL AND superseded_by IS NULL AND delivered_at IS NULL ORDER BY seq').all()
    return rows.map(({ seq }) => resend(seq, { actor, reason }))
  }

  /** Latest snapshot of every subscription that is paid (active/grace) but not delivered yet. */
  function unsyncedPaid(minAge = UNSYNCED_AFTER) {
    return db.prepare(`SELECT o.seq, o.provider, o.subscription_ref, o.created_at, o.attempts, o.last_error, o.error_class,
        o.rejected_at, json_extract(o.payload, '$.access.status') AS access, json_extract(o.payload, '$.plan') AS plan
      FROM entitlement_outbox o
      WHERE o.seq = (SELECT MAX(seq) FROM entitlement_outbox i WHERE i.provider = o.provider AND i.subscription_ref = o.subscription_ref)
        AND o.delivered_at IS NULL AND o.superseded_by IS NULL
        AND json_extract(o.payload, '$.access.status') IN ('active', 'grace') AND o.created_at <= ?
      ORDER BY o.seq`).all(now() - minAge)
  }

  function stats() {
    return db.prepare(`SELECT
      COALESCE(SUM(delivered_at IS NULL AND rejected_at IS NULL AND superseded_by IS NULL), 0) AS pending,
      COALESCE(SUM(delivered_at IS NULL AND rejected_at IS NULL AND superseded_by IS NULL AND error_class = 'blocked'), 0) AS blocked,
      COALESCE(SUM(rejected_at IS NOT NULL AND superseded_by IS NULL AND delivered_at IS NULL), 0) AS rejected,
      COALESCE(SUM(superseded_by IS NOT NULL), 0) AS superseded,
      COALESCE(SUM(delivered_at IS NOT NULL), 0) AS delivered,
      MIN(CASE WHEN delivered_at IS NULL AND rejected_at IS NULL AND superseded_by IS NULL THEN created_at END) AS oldest_pending
      FROM entitlement_outbox`).get()
  }

  /** Operator report: counts, every undelivered event with age and last failure, and the audit trail. */
  function report({ limit = 50 } = {}) {
    const t = now()
    const view = row => ({
      seq: row.seq, provider: row.provider, subscription_ref: row.subscription_ref, state: stateOf(row),
      attempts: row.attempts, age_s: t - row.created_at, last_error: row.last_error, error_class: row.error_class,
      failing_for_s: row.first_failure_at ? t - row.first_failure_at : 0,
      next_attempt_in_s: row.rejected_at ? null : Math.max(0, row.next_attempt_at - t),
    })
    const counts = stats()
    return {
      counts: { ...counts, oldest_pending_age_s: counts.oldest_pending ? t - counts.oldest_pending : 0 },
      pending: db.prepare(`SELECT * FROM entitlement_outbox WHERE delivered_at IS NULL AND rejected_at IS NULL AND superseded_by IS NULL
        ORDER BY seq LIMIT ?`).all(limit).map(view),
      rejected: db.prepare(`SELECT * FROM entitlement_outbox WHERE rejected_at IS NOT NULL AND superseded_by IS NULL AND delivered_at IS NULL
        ORDER BY seq LIMIT ?`).all(limit).map(view),
      unsynced_paid: unsyncedPaid().map(row => ({ ...view(row), plan: row.plan, access: row.access })),
      audit: db.prepare('SELECT * FROM entitlement_outbox_audit ORDER BY id DESC LIMIT ?').all(limit),
    }
  }

  /** Operational alerts (codes and counts only, no personal data): empty when everything is in sync. */
  function alerts() {
    const t = now()
    const counts = stats()
    const out = []
    const unsynced = unsyncedPaid()
    if (unsynced.length) out.push({ code: 'paid_unsynced', count: unsynced.length, oldest_age_s: t - Math.min(...unsynced.map(row => row.created_at)) })
    if (counts.rejected) out.push({ code: 'rejected', count: counts.rejected })
    if (counts.blocked) out.push({ code: 'blocked', count: counts.blocked })
    if (counts.oldest_pending && t - counts.oldest_pending >= STALLED_AFTER) out.push({ code: 'stalled', count: counts.pending, oldest_age_s: t - counts.oldest_pending })
    return out
  }

  /**
   * Compares the commercial view of each subscription with what the main backend applied.
   * - The API is behind a snapshot we delivered (lost on its side, e.g. database restore): the latest delivered
   *   snapshot is queued again (audited as `reconcile`); idempotent by event_id and seq.
   * - The API is ahead of every snapshot we have, or disagrees on paid access at the same seq: reported only.
   */
  async function reconcile(readState, { actor = 'reconcile' } = {}) {
    const latest = db.prepare(`SELECT provider, subscription_ref, MAX(seq) AS latest_seq,
        MAX(CASE WHEN delivered_at IS NOT NULL THEN seq END) AS delivered_seq
      FROM entitlement_outbox GROUP BY provider, subscription_ref`).all()
    const issues = []
    const requeued = []
    for (let index = 0; index < latest.length; index += 500) {
      const batch = latest.slice(index, index + 500)
      const items = await readState(batch.map(row => ({ provider: row.provider, subscription_ref: row.subscription_ref })))
      const remote = new Map(items.map(item => [`${item.provider}\n${item.subscription_ref}`, item]))
      for (const row of batch) {
        const item = remote.get(`${row.provider}\n${row.subscription_ref}`)
        const mainSeq = item?.last_seq ?? 0
        const key = { provider: row.provider, subscription_ref: row.subscription_ref, main_seq: mainSeq, latest_seq: row.latest_seq }
        if (mainSeq > row.latest_seq) {
          issues.push({ code: 'main_ahead', ...key })
        } else if (row.delivered_seq && mainSeq < row.delivered_seq) {
          const result = resend(row.delivered_seq, { actor, reason: `main backend at seq ${mainSeq}`, allowDelivered: true })
          if (result.outcome === 'queued') requeued.push(row.delivered_seq)
          issues.push({ code: 'main_behind', ...key })
        } else if (mainSeq === row.latest_seq) {
          const access = JSON.parse(byId.get(row.latest_seq).payload).access?.status
          if (PAID_ACCESS.includes(access) !== PAID_ACCESS.includes(item?.access_status)) issues.push({ code: 'access_mismatch', ...key, commercial: access, main: item?.access_status ?? null })
        }
      }
    }
    if (issues.length) log.error('Entitlement reconciliation found differences', { issues: issues.length, requeued: requeued.length })
    return { checked: latest.length, requeued, issues }
  }

  return { enqueue, dispatch, stats, report, alerts, resend, resendRejected, reconcile, unsyncedPaid }
}
