// Operator commands for the entitlement outbox (commercial service -> main API). Every change is audited in
// entitlement_outbox_audit with the operator name and the reason. Resending keeps the event_id and seq, so the
// main API applies each snapshot at most once and never over a newer state.
//
//   npm run outbox -- report [--json]                                  # pending, blocked, rejected, unsynced paid, audit
//   npm run outbox -- resend <seq> --by <name> --reason "texto"        # one rejected or pending event
//   npm run outbox -- resend-rejected --by <name> --reason "texto"     # every rejected event not superseded
//   npm run outbox -- reconcile                                        # compare with the main API (needs the link settings)
//   npm run outbox -- dispatch                                         # one delivery pass now (safe with the server running)
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { DatabaseSync } from 'node:sqlite'
import { createOutbox, httpSender, httpStateReader, outboxSettings } from '../server/outbox.js'

const [command, target] = process.argv.slice(2)
const flag = name => { const index = process.argv.indexOf(`--${name}`); return index > 0 ? process.argv[index + 1] ?? '' : undefined }
const root = fileURLToPath(new URL('../', import.meta.url))
const db = new DatabaseSync(resolve(root, process.env.COMMERCIAL_DB ?? 'data/waitlist.sqlite'))
db.exec('PRAGMA busy_timeout = 5000')
const link = outboxSettings()
const linked = Boolean(link.secret && link.mainUrl)
const outbox = createOutbox({ db, send: linked ? httpSender(link) : null })
const age = seconds => seconds == null ? '' : seconds >= 86400 ? `${Math.floor(seconds / 86400)}d` : seconds >= 3600 ? `${Math.floor(seconds / 3600)}h` : `${Math.floor(seconds / 60)}min`

try {
  if (command === 'report') {
    const report = outbox.report()
    if (process.argv.includes('--json')) {
      console.log(JSON.stringify(report, null, 2))
    } else {
      console.log('Counts'); console.table([{ ...report.counts, oldest_pending_age: age(report.counts.oldest_pending_age_s) }])
      const rows = list => list.map(row => ({ seq: row.seq, provider: row.provider, ref: row.subscription_ref, state: row.state, attempts: row.attempts,
        age: age(row.age_s), failing_for: age(row.failing_for_s), next_try: row.next_attempt_in_s == null ? 'manual' : age(row.next_attempt_in_s), last_error: row.last_error }))
      console.log('\nPaid subscriptions not yet active on the main API'); console.table(rows(report.unsynced_paid))
      console.log('\nPending (retried automatically)'); console.table(rows(report.pending))
      console.log('\nRejected (needs an operator: fix the cause, then resend)'); console.table(rows(report.rejected))
      console.log('\nAudit trail'); console.table(report.audit.map(entry => ({ ...entry, at: new Date(entry.at * 1000).toISOString() })))
      const alerts = outbox.alerts()
      console.log(alerts.length ? `\nALERTS: ${alerts.map(alert => `${alert.code} (${alert.count})`).join(', ')}` : '\nNo alerts')
    }
  } else if (command === 'resend' && target) {
    console.log(outbox.resend(Number(target), { actor: flag('by'), reason: flag('reason') }))
    console.log('The running server delivers it within 15 seconds (or run: npm run outbox -- dispatch)')
  } else if (command === 'resend-rejected') {
    const results = outbox.resendRejected({ actor: flag('by'), reason: flag('reason') })
    console.table(results.length ? results : [{ outcome: 'nothing to resend' }])
  } else if (command === 'reconcile') {
    if (!linked) throw new Error('Set ASC_MAIN_API_URL and ASC_BILLING_SECRET to compare with the main API')
    const result = await outbox.reconcile(httpStateReader(link), { actor: 'reconcile (cli)' })
    console.log(`Checked ${result.checked} subscriptions; queued again: ${result.requeued.join(', ') || 'none'}`)
    if (result.issues.length) console.table(result.issues)
  } else if (command === 'dispatch') {
    if (!linked) throw new Error('Set ASC_MAIN_API_URL and ASC_BILLING_SECRET to deliver')
    console.log(await outbox.dispatch())
  } else {
    console.log('Usage: npm run outbox -- report | resend <seq> --by <name> --reason "..." | resend-rejected --by <name> --reason "..." | reconcile | dispatch')
    process.exitCode = 2
  }
} catch (error) {
  console.error(error.message)
  process.exitCode = 1
} finally {
  db.close()
}
