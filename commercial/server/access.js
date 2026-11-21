// Provider-neutral access rules sent to the main backend. The main backend never needs to know Stripe or
// InfinitePay semantics: it grants features while access.status is 'active' or 'grace' and now < access.until.
import catalog from '../catalog.json' with { type: 'json' }

const DAY = 86_400
// Stripe renewal webhooks can arrive a little after the period ends; avoid cutting access in that gap
export const STRIPE_RENEWAL_LEEWAY = DAY
// Days of access kept while Stripe retries a failed renewal (same grace as InfinitePay)
export const STRIPE_GRACE_DAYS = 3

/** Plan limits and features, straight from catalog.json (single source of truth). */
export function limitsFor(plan) {
  const p = catalog.plans[plan]
  return {
    hours: p.hours,
    channels: p.channels,
    simultaneous: p.simultaneous,
    storage_gb: p.storageGb,
    export_4k: p.export4k,
    features: { ...p.features },
  }
}

const none = status => ({ status, until: 0, period_start: null, period_end: null, renews_at: null, cancel_at_period_end: false })

/**
 * Stripe subscription row -> access.
 * active/trialing: until the period end (+1 day leeway); past_due: 3 days from the start of the unpaid period;
 * paused: suspended; incomplete: none (never paid); canceled/unpaid/incomplete_expired or a full refund: ended.
 */
export function stripeAccess(row) {
  if (row.refunded_at) return none('ended')
  const start = row.current_period_start ?? null
  const end = row.current_period_end ?? null
  const base = { period_start: start, period_end: end, cancel_at_period_end: Boolean(row.cancel_at_period_end) }
  switch (row.status) {
    case 'active':
    case 'trialing':
      return { ...base, status: 'active', until: (end ?? 0) + STRIPE_RENEWAL_LEEWAY, renews_at: row.cancel_at_period_end ? null : end }
    case 'past_due':
      return { ...base, status: 'grace', until: (start ?? 0) + STRIPE_GRACE_DAYS * DAY, renews_at: null }
    case 'paused':
      return { ...base, ...none('suspended') }
    case 'incomplete':
      return none('none')
    default:
      return { ...base, ...none('ended') }
  }
}

/**
 * InfinitePay subscription row (+ its latest paid period) -> access.
 * active: until paid_until; past_due: paid_until + grace; pending/abandoned: none; expired/refunded/canceled: ended.
 * There is no automatic renewal in Brazil: renews_at is always null.
 */
export function infinitePayAccess(row, latestPeriodStart, graceDays) {
  const base = { period_start: latestPeriodStart ?? null, period_end: row.paid_until || null, renews_at: null, cancel_at_period_end: false }
  switch (row.status) {
    case 'active': return { ...base, status: 'active', until: row.paid_until }
    case 'past_due': return { ...base, status: 'grace', until: row.paid_until + graceDays * DAY }
    case 'pending':
    case 'abandoned': return none('none')
    default: return { ...base, ...none('ended') }
  }
}
