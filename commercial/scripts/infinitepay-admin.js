// Operator commands for InfinitePay (BRL). The API cannot refund or cancel, so refunds and chargebacks done
// in the InfinitePay app are recorded here, adjusting access and the append-only ledger.
//
//   npm run infinitepay -- refund <order_nsu> --reason "texto" [--cancel]
//   npm run infinitepay -- clear-review <order_nsu> --reason "texto"     # reviewed: not refunded after all
//   npm run infinitepay -- cancel <subscription_id> --reason "texto"     # stop renewals
//   npm run infinitepay -- reconcile [--days 120]                        # calls payment_check (real API, needs keys)
//   npm run infinitepay -- history <subscription_id>
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { DatabaseSync } from 'node:sqlite'
import { createInfinitePay, infinitePayClient, infinitePaySettings } from '../server/infinitepay.js'

const [command, target] = process.argv.slice(2)
const flag = name => { const index = process.argv.indexOf(`--${name}`); return index > 0 ? process.argv[index + 1] ?? '' : undefined }
const root = fileURLToPath(new URL('../', import.meta.url))
const db = new DatabaseSync(resolve(root, process.env.COMMERCIAL_DB ?? 'data/waitlist.sqlite'))
const settings = infinitePaySettings()
const request = command === 'reconcile' && settings.apiKey ? infinitePayClient(settings) : null
const infinite = createInfinitePay({ db, settings, request })

try {
  if (command === 'refund' && target) {
    const reason = flag('reason')
    if (!reason) throw new Error('Pass --reason "…" so the ledger explains the refund')
    const { changed, subscription } = infinite.refund(target, { reason, cancel: process.argv.includes('--cancel') })
    console.log(changed ? 'Refund recorded' : 'Already recorded', { subscription: subscription.id, status: subscription.status, paid_until: new Date(subscription.paid_until * 1000).toISOString() })
    console.log('Remember: update the customer\'s access in the AI Stream Clipper dashboard')
  } else if (command === 'clear-review' && target) {
    console.log(infinite.clearReview(target, flag('reason') ?? '') ? 'Review cleared' : 'Charge is not under review')
  } else if (command === 'cancel' && target) {
    const subscription = infinite.cancel(target, flag('reason') ?? '')
    console.log('Canceled: no more renewal links', { subscription: subscription.id })
  } else if (command === 'reconcile') {
    if (!request) throw new Error('reconcile calls payment_check: set INFINITEPAY_HANDLE and INFINITEPAY_API_KEY first')
    const flagged = await infinite.reconcile({ sinceDays: Number(flag('days') ?? 120) })
    console.log(flagged.length ? `Charges reported unpaid, now under review: ${flagged.join(', ')}` : 'All recent paid charges still confirmed')
  } else if (command === 'history' && target) {
    console.table(db.prepare("SELECT datetime(created_at, 'unixepoch') AS at, type, order_nsu, amount, detail FROM ip_ledger WHERE subscription_id = ? ORDER BY id").all(target).map(row => ({ ...row })))
  } else {
    console.log('Commands: refund <order_nsu> --reason … [--cancel] | clear-review <order_nsu> | cancel <subscription_id> | reconcile [--days N] | history <subscription_id>')
    process.exitCode = 1
  }
} catch (error) {
  console.error(error.message)
  process.exitCode = 1
} finally {
  db.close()
}
