// Entitlement outbox: failure classes, long outages, audited recovery, supersession, leases and reconciliation.
// Uses an in-memory main backend that behaves like backend/clipper/entitlements.apply_snapshot
// (idempotent by event_id, ignores older seq). No network, no provider keys.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { DatabaseSync } from 'node:sqlite'
import { mkdtempSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { backoff, classify, createOutbox, migrateOutbox, STALLED_AFTER, UNSYNCED_AFTER } from './outbox.js'
import { createOutboxMonitor } from './outbox-monitor.js'

const quiet = { log() {}, error() {}, warn() {} }

function mainBackend() {
  const main = { events: new Set(), subs: new Map(), applied: 0, statuses: [], down: false, calls: 0 }
  main.send = async body => {
    main.calls++
    if (main.down) throw Object.assign(new Error('connect ECONNREFUSED'), { code: 'ECONNREFUSED' })
    if (main.statuses.length) {
      const status = main.statuses.shift()
      if (status !== 200) return status
    }
    const snap = JSON.parse(body)
    if (main.events.has(snap.event_id)) return 200 // duplicate
    main.events.add(snap.event_id)
    const key = `${snap.provider}\n${snap.subscription_ref}`
    const current = main.subs.get(key)
    if (!current || snap.seq > current.last_seq) {
      main.subs.set(key, { last_seq: snap.seq, access_status: snap.access.status, plan: snap.plan })
      main.applied++
    }
    return 200
  }
  main.readState = async refs => refs.flatMap(ref => {
    const row = main.subs.get(`${ref.provider}\n${ref.subscription_ref}`)
    return row ? [{ ...ref, ...row }] : []
  })
  return main
}

function setup(t, { path = ':memory:' } = {}) {
  const clock = { now: 1_800_000_000 }
  const db = new DatabaseSync(path)
  t.after(() => { try { db.close() } catch { /* closed */ } })
  const main = mainBackend()
  const outbox = createOutbox({ db, send: main.send, log: quiet, now: () => clock.now })
  const snap = (ref, status = 'active', plan = 'creator') => ({ provider: 'stripe', subscription_ref: ref, account_id: 7, plan, access: { status } })
  const enqueue = (...args) => {
    db.exec('BEGIN IMMEDIATE')
    const seq = outbox.enqueue(snap(...args))
    db.exec('COMMIT')
    return seq
  }
  const row = seq => db.prepare('SELECT * FROM entitlement_outbox WHERE seq = ?').get(seq)
  return { clock, db, main, outbox, enqueue, row }
}

test('failure classes: transient and blocked retry forever (bounded backoff), invalid snapshots are parked', () => {
  assert.equal(classify(200), 'delivered')
  for (const status of [400, 413, 415, 422, 410]) assert.equal(classify(status), 'permanent', String(status))
  for (const status of [401, 403, 404, 409]) assert.equal(classify(status), 'blocked', String(status))
  for (const status of [0, 408, 429, 500, 502, 503]) assert.equal(classify(status), 'transient', String(status))
  // Capped: never faster than the cap after a long outage, never more than 1 h / 6 h apart
  assert.equal(backoff(1, 'transient'), 10)
  assert.equal(backoff(500, 'transient'), 3600)
  assert.equal(backoff(500, 'blocked'), 6 * 3600)
})

test('main backend down for many cycles: nothing dropped, one probe per cycle, delivered in order afterwards', async t => {
  const { clock, main, outbox, enqueue, row } = setup(t)
  const seqs = [enqueue('sub_a', 'active'), enqueue('sub_b', 'active'), enqueue('sub_a', 'grace')]
  main.down = true
  for (let cycle = 0; cycle < 400; cycle++) {
    await outbox.dispatch()
    clock.now += 3600
  }
  // ~400 hours down: still queued (never rejected), and roughly one request per cycle instead of 3
  assert.ok(main.calls <= 400, `calls ${main.calls}`)
  assert.deepEqual(seqs.map(seq => row(seq).rejected_at), [null, null, null])
  assert.ok(seqs.every(seq => !row(seq).delivered_at))
  assert.ok(row(seqs[0]).next_attempt_at - clock.now <= 3600, 'backoff capped at one hour')
  const alerts = outbox.alerts().map(alert => alert.code).sort()
  assert.deepEqual(alerts, ['paid_unsynced', 'stalled'])

  main.down = false
  clock.now += 3600
  for (let cycle = 0; cycle < 3; cycle++) await outbox.dispatch()
  assert.equal(main.subs.get('stripe\nsub_a').access_status, 'grace', 'latest state wins')
  assert.equal(main.subs.get('stripe\nsub_b').access_status, 'active')
  assert.deepEqual(outbox.alerts(), [])
})

test('older snapshot of the same subscription is superseded by a newer delivery and never resent', async t => {
  const { main, outbox, enqueue, row } = setup(t)
  const first = enqueue('sub_a', 'active')
  const second = enqueue('sub_a', 'ended')
  main.statuses.push(503) // first fails transiently and the cycle stops
  await outbox.dispatch()
  // Next cycle: first is in backoff, second goes out and replaces it
  await outbox.dispatch()
  assert.ok(row(second).delivered_at)
  assert.equal(row(first).superseded_by, second)
  assert.equal(main.subs.get('stripe\nsub_a').access_status, 'ended')
  assert.equal(outbox.stats().pending, 0)
})

test('rejected events stay visible, are not retried automatically, and resend is audited and idempotent', async t => {
  const { clock, main, outbox, enqueue, row } = setup(t)
  const seq = enqueue('sub_a', 'active', 'pro')
  main.statuses.push(422)
  await outbox.dispatch()
  assert.ok(row(seq).rejected_at)
  // Hours later: still parked, zero automatic attempts, alerts raised
  for (let cycle = 0; cycle < 20; cycle++) { clock.now += 3600; await outbox.dispatch() }
  assert.equal(row(seq).attempts, 1)
  assert.equal(main.calls, 1)
  const report = outbox.report()
  assert.equal(report.rejected.length, 1)
  assert.equal(report.rejected[0].last_error, 'http_422')
  assert.equal(report.rejected[0].next_attempt_in_s, null)
  assert.ok(report.rejected[0].age_s >= 20 * 3600)
  assert.equal(report.unsynced_paid.length, 1, 'paid subscription not active on the API')
  assert.deepEqual(outbox.alerts().map(alert => alert.code).sort(), ['paid_unsynced', 'rejected'])

  // Resend requires an operator and a reason
  assert.throws(() => outbox.resend(seq, { actor: '', reason: 'x' }), /operator/)
  assert.throws(() => outbox.resend(seq, { actor: 'ana', reason: '' }), /reason/)
  assert.deepEqual(outbox.resend(seq, { actor: 'ana', reason: 'API corrigida' }), { seq, outcome: 'queued', previous: 'rejected' })
  await outbox.dispatch()
  assert.ok(row(seq).delivered_at)
  assert.equal(main.subs.get('stripe\nsub_a').plan, 'pro')
  // Resending a delivered event is refused unless explicitly allowed; when allowed it is deduplicated
  assert.equal(outbox.resend(seq, { actor: 'ana', reason: 'de novo' }).outcome, 'already_delivered')
  outbox.resend(seq, { actor: 'ana', reason: 'forçar', allowDelivered: true })
  await outbox.dispatch()
  assert.equal(main.applied, 1, 'same event_id applied once')
  const audit = outbox.report().audit
  assert.deepEqual(audit.map(entry => [entry.actor, entry.action, entry.previous_state, entry.previous_error]),
    [['ana', 'resend', 'delivered', ''], ['ana', 'resend', 'rejected', 'http_422']])
})

test('a rejected event replaced by a newer delivered snapshot is resolved automatically (audited)', async t => {
  const { main, outbox, enqueue, row } = setup(t)
  const bad = enqueue('sub_a', 'active')
  main.statuses.push(422)
  await outbox.dispatch()
  const good = enqueue('sub_a', 'active')
  await outbox.dispatch()
  assert.equal(row(bad).superseded_by, good)
  assert.equal(outbox.stats().rejected, 0)
  assert.equal(outbox.report().audit[0].action, 'superseded')
  assert.equal(outbox.resend(bad, { actor: 'ana', reason: 'teste' }).outcome, 'superseded')
  assert.deepEqual(outbox.alerts(), [])
})

test('blocked (401 misconfiguration, 409 account not ready) keeps retrying slowly and heals by itself', async t => {
  const { clock, main, outbox, enqueue, row } = setup(t)
  const seq = enqueue('sub_a', 'active')
  main.statuses.push(401, 409, 409)
  for (let cycle = 0; cycle < 3; cycle++) { await outbox.dispatch(); clock.now += 6 * 3600 }
  assert.equal(row(seq).error_class, 'blocked')
  assert.equal(row(seq).rejected_at, null)
  assert.ok(outbox.alerts().some(alert => alert.code === 'blocked'))
  await outbox.dispatch()
  assert.ok(row(seq).delivered_at)
  assert.deepEqual(outbox.alerts(), [])
})

test('restart and concurrency: two dispatchers on the same database never send the same row twice at once', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'asc-outbox-'))
  const path = join(dir, 'outbox.sqlite')
  const { clock, main, outbox, enqueue, db } = setup(t, { path })
  for (let index = 0; index < 20; index++) enqueue(`sub_${index}`, 'active')
  // Slow API so both dispatchers overlap
  const inFlight = new Set()
  let overlap = 0
  const slow = async body => {
    const { seq } = JSON.parse(body)
    if (inFlight.has(seq)) overlap++
    inFlight.add(seq)
    await new Promise(done => setTimeout(done, 2))
    inFlight.delete(seq)
    return main.send(body)
  }
  const otherDb = new DatabaseSync(path)
  otherDb.exec('PRAGMA busy_timeout = 5000')
  db.exec('PRAGMA busy_timeout = 5000')
  t.after(() => {
    otherDb.close()
    try { db.close() } catch { /* closed by setup */ }
    rmSync(dir, { recursive: true, force: true })
  })
  const a = createOutbox({ db, send: slow, log: quiet, now: () => clock.now })
  const b = createOutbox({ db: otherDb, send: slow, log: quiet, now: () => clock.now })
  const [ra, rb] = await Promise.all([a.dispatch(), b.dispatch()])
  assert.equal(overlap, 0)
  assert.equal(ra.delivered + rb.delivered, 20)
  assert.equal(main.calls, 20, 'each row sent exactly once')
  assert.equal(outbox.stats().delivered, 20)

  // Crash after the API applied but before we recorded delivery: the lease expires and the resend is a no-op
  const seq = enqueue('sub_crash', 'active')
  otherDb.prepare('UPDATE entitlement_outbox SET lease_until = ?, lease_owner = ? WHERE seq = ?').run(clock.now + 60, 'dead-process', seq)
  await main.send(otherDb.prepare('SELECT payload FROM entitlement_outbox WHERE seq = ?').get(seq).payload)
  assert.equal((await a.dispatch()).delivered, 0, 'leased by the crashed process')
  clock.now += 61
  assert.equal((await a.dispatch()).delivered, 1)
  assert.equal(main.applied, 21, 'applied once despite two sends')
})

test('databases from the first outbox version are upgraded in place', t => {
  const db = new DatabaseSync(':memory:')
  t.after(() => db.close())
  db.exec(`CREATE TABLE entitlement_outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, provider TEXT NOT NULL,
    subscription_ref TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL, delivered_at INTEGER, rejected_at INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '') STRICT`)
  db.prepare("INSERT INTO entitlement_outbox (event_id, provider, subscription_ref, payload, created_at, rejected_at, attempts, last_error) VALUES ('e1', 'stripe', 'sub_old', '{\"access\":{\"status\":\"active\"}}', 1, 2, 30, 'ECONNREFUSED')").run()
  migrateOutbox(db)
  migrateOutbox(db) // idempotent
  const outbox = createOutbox({ db, log: quiet, now: () => 10_000 })
  // The old version parked network failures after 30 attempts: they show up as rejected, ready to resend
  assert.equal(outbox.report().rejected[0].last_error, 'ECONNREFUSED')
  assert.equal(outbox.resendRejected({ actor: 'ana', reason: 'migração' })[0].outcome, 'queued')
  assert.equal(outbox.stats().pending, 1)
})

test('reconciliation re-queues what the API lost and reports what it cannot fix', async t => {
  const { main, outbox, enqueue, row } = setup(t)
  const a = enqueue('sub_a', 'active')
  const b = enqueue('sub_b', 'active')
  await outbox.dispatch()
  assert.ok(row(a).delivered_at && row(b).delivered_at)
  // The API lost sub_a (restore from an older backup), and has a sub_c the commercial service never sent
  main.subs.delete('stripe\nsub_a')
  main.events.clear()
  const result = await outbox.reconcile(main.readState)
  assert.deepEqual(result.requeued, [a])
  assert.deepEqual(result.issues.map(issue => [issue.code, issue.subscription_ref]), [['main_behind', 'sub_a']])
  await outbox.dispatch()
  assert.equal(main.subs.get('stripe\nsub_a').access_status, 'active')
  assert.equal(outbox.report().audit[0].actor, 'reconcile')

  main.subs.set('stripe\nsub_b', { last_seq: 99, access_status: 'active', plan: 'creator' })
  const second = await outbox.reconcile(main.readState)
  assert.deepEqual(second.issues.map(issue => issue.code), ['main_ahead'])
  assert.deepEqual(second.requeued, [])
})

test('monitor logs each alert at most every 15 minutes and logs when it clears', async t => {
  const { clock, main, outbox, enqueue } = setup(t)
  const lines = []
  const log = { error: (message, data) => lines.push(['error', data.code]), log: (message, data) => lines.push(['log', data.code]) }
  const monitor = createOutboxMonitor({ outbox, log, now: () => clock.now })
  enqueue('sub_a', 'active')
  main.down = true
  await outbox.dispatch()
  clock.now += UNSYNCED_AFTER
  await monitor.tick()
  clock.now += 60
  await monitor.tick()
  assert.deepEqual(lines, [['error', 'paid_unsynced']])
  clock.now += STALLED_AFTER
  await monitor.tick()
  assert.deepEqual(lines.slice(1).map(line => line[1]).sort(), ['paid_unsynced', 'stalled'])
  main.down = false
  await outbox.dispatch()
  await monitor.tick()
  assert.deepEqual(lines.slice(3).map(line => line.join(':')).sort(), ['log:paid_unsynced', 'log:stalled'])
})
