// HTS promotion codes (e.g. MALAIO20), owned by the commercial service because they are money.
// The HTS panel (main API, admin only) manages them over the signed billing link; the browser never computes a price.
//
//   * USD (Stripe): each code is mirrored as an official Stripe Coupon + Promotion Code. The checkout applies it
//     explicitly (`discounts: [{ promotion_code }]`) after OUR validation (plans, dates, per-account limit, review),
//     and customers cannot type other Stripe codes (`allow_promotion_codes` is off). Free days use the official
//     subscription trial (`trial_period_days`).
//   * BRL (InfinitePay): HTS's own coupons. The discounted amount is computed here, frozen in the charge (request_json),
//     and payment_check must confirm exactly that amount. A 100% discount or free days grant access without creating
//     a charge. InfinitePay's `options.discounts` (Pix/card method discounts) is a different thing and is not used.
//
// Two uses (`applies_to`), chosen when the code is created:
//   * 'new'      : only at the START of a subscription (checkout); it discounts the first payment only, never the
//                  renewals (duration 'once'). Free days exist only here.
//   * 'existing' : a RECURRING code for subscriptions already running: the customer applies it to the active
//                  subscription and it discounts the next renewals (N months or forever). Never at checkout.
//
// Terms (kind, value, duration, use, plans, markets) are immutable after creation (a trigger refuses changes), so the
// financial history never changes silently; only the switch, the end date and the limits can change. Every use is
// a redemption row (reserved -> redeemed | released | refunded), created inside the same transaction that checks the
// limits, so simultaneous checkouts can never exceed them.
import { randomUUID } from 'node:crypto'
import catalog from '../catalog.json' with { type: 'json' }

export const CODE = /^[A-Z0-9]{3,32}$/
// Smallest charge accepted per market (minor units). Stripe: USD 0.50. InfinitePay: R$ 1,00 (conservative; confirm
// the real minimum with InfinitePay before lowering it). A discount can make a price 0 (free) but never 0 < x < min.
export const MIN_CHARGE = { BR: 100, US: 50 }
export const MAX_FREE_DAYS = 90
const KINDS = ['percent', 'amount', 'free_days']
const DURATIONS = ['once', 'repeating', 'forever']
const COUNTED = "('reserved', 'redeemed', 'refunded')"
// A Stripe checkout session lives 24 h: a reservation that never completed is released after that
const STRIPE_RESERVATION_TTL = 26 * 60 * 60

export function migratePromotions(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS promo_codes (
      code TEXT PRIMARY KEY,
      kind TEXT NOT NULL CHECK (kind IN ('percent', 'amount', 'free_days')),
      percent INTEGER CHECK (percent IS NULL OR (percent BETWEEN 1 AND 100)),
      amount_brl INTEGER CHECK (amount_brl IS NULL OR amount_brl > 0),
      amount_usd INTEGER CHECK (amount_usd IS NULL OR amount_usd > 0),
      free_days INTEGER CHECK (free_days IS NULL OR (free_days BETWEEN 1 AND ${MAX_FREE_DAYS})),
      applies_to TEXT NOT NULL DEFAULT 'new' CHECK (applies_to IN ('new', 'existing')),
      duration TEXT NOT NULL CHECK (duration IN ('once', 'repeating', 'forever')),
      duration_months INTEGER CHECK (duration_months IS NULL OR (duration_months BETWEEN 1 AND 24)),
      plans TEXT NOT NULL,
      markets TEXT NOT NULL,
      starts_at INTEGER NOT NULL,
      ends_at INTEGER,
      max_redemptions INTEGER CHECK (max_redemptions IS NULL OR max_redemptions > 0),
      max_per_account INTEGER NOT NULL DEFAULT 1 CHECK (max_per_account > 0),
      active INTEGER NOT NULL DEFAULT 1,
      stripe_coupon TEXT NOT NULL DEFAULT '',
      stripe_promotion TEXT NOT NULL DEFAULT '',
      stripe_state TEXT NOT NULL DEFAULT '',
      stripe_error TEXT NOT NULL DEFAULT '',
      note TEXT NOT NULL DEFAULT '',
      created_by TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    ) STRICT;
    CREATE TRIGGER IF NOT EXISTS promo_codes_terms_frozen BEFORE UPDATE OF code, kind, percent, amount_brl, amount_usd,
      free_days, applies_to, duration, duration_months, plans, markets ON promo_codes
      BEGIN SELECT RAISE(ABORT, 'promotion terms are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS promo_codes_no_delete BEFORE DELETE ON promo_codes
      BEGIN SELECT RAISE(ABORT, 'promotion codes are never deleted (deactivate them)'); END;
    CREATE TABLE IF NOT EXISTS promo_redemptions (
      id TEXT PRIMARY KEY,
      code TEXT NOT NULL REFERENCES promo_codes(code),
      account_id TEXT NOT NULL,
      market TEXT NOT NULL,
      plan TEXT NOT NULL,
      provider TEXT NOT NULL CHECK (provider IN ('stripe', 'infinitepay')),
      ref TEXT NOT NULL DEFAULT '',
      subscription_ref TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL CHECK (status IN ('reserved', 'redeemed', 'released', 'refunded')),
      list_amount INTEGER NOT NULL CHECK (list_amount > 0),
      discount_amount INTEGER NOT NULL CHECK (discount_amount >= 0 AND discount_amount <= list_amount),
      final_amount INTEGER NOT NULL CHECK (final_amount >= 0 AND final_amount = list_amount - discount_amount),
      currency TEXT NOT NULL,
      free_days INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    ) STRICT;
    CREATE INDEX IF NOT EXISTS promo_redemptions_code ON promo_redemptions(code, status);
    CREATE INDEX IF NOT EXISTS promo_redemptions_account ON promo_redemptions(account_id, code, status);
    -- Append-only history of every administrative change and use
    CREATE TABLE IF NOT EXISTS promo_events (
      id INTEGER PRIMARY KEY,
      code TEXT NOT NULL,
      type TEXT NOT NULL,
      detail TEXT NOT NULL DEFAULT '',
      actor TEXT NOT NULL DEFAULT '',
      created_at INTEGER NOT NULL
    ) STRICT;
    CREATE TRIGGER IF NOT EXISTS promo_events_no_update BEFORE UPDATE ON promo_events BEGIN SELECT RAISE(ABORT, 'promo history is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS promo_events_no_delete BEFORE DELETE ON promo_events BEGIN SELECT RAISE(ABORT, 'promo history is append-only'); END;
  `)
}

const invalid = reason => Object.assign(new Error(reason), { code: 'invalid_promotion', reason })
const int = value => (Number.isInteger(value) ? value : null)

/** Validates an administrative definition. Throws invalid_promotion with a reason code. */
export function parseDefinition(input, now) {
  const body = input && typeof input === 'object' ? input : {}
  const code = String(body.code ?? '').trim().toUpperCase()
  if (!CODE.test(code)) throw invalid('code')
  if (!KINDS.includes(body.kind)) throw invalid('kind')
  const plans = Array.isArray(body.plans) ? [...new Set(body.plans)] : []
  if (!plans.length || !plans.every(plan => Object.hasOwn(catalog.plans, plan))) throw invalid('plans')
  const markets = Array.isArray(body.markets) ? [...new Set(body.markets)] : []
  if (!markets.length || !markets.every(market => Object.hasOwn(catalog.markets, market))) throw invalid('markets')
  const def = { code, kind: body.kind, percent: null, amount_brl: null, amount_usd: null, free_days: null, plans, markets }
  if (body.kind === 'percent') {
    def.percent = int(body.percent)
    if (def.percent === null || def.percent < 1 || def.percent > 100) throw invalid('percent')
  } else if (body.kind === 'amount') {
    def.amount_brl = markets.includes('BR') ? int(body.amount_brl) : null
    def.amount_usd = markets.includes('US') ? int(body.amount_usd) : null
    for (const [market, value] of [['BR', def.amount_brl], ['US', def.amount_usd]]) {
      if (!markets.includes(market)) continue
      // never more than the cheapest eligible plan: a fixed amount can make a price 0, never negative
      const cheapest = Math.min(...plans.map(plan => catalog.plans[plan].monthlyMinor[market]))
      if (value === null || value <= 0 || value > cheapest) throw invalid(`amount_${market.toLowerCase()}`)
    }
  } else {
    def.free_days = int(body.free_days)
    if (def.free_days === null || def.free_days < 1 || def.free_days > MAX_FREE_DAYS) throw invalid('free_days')
  }
  def.applies_to = body.applies_to ?? 'new'
  if (!['new', 'existing'].includes(def.applies_to)) throw invalid('applies_to')
  if (def.applies_to === 'new') {
    // start of a subscription: the first payment only, never the renewals
    def.duration = 'once'
  } else {
    // running subscriptions: recurring discounts on the next renewals (free days only exist for new subscriptions)
    if (body.kind === 'free_days') throw invalid('free_days_new_only')
    def.duration = body.duration
    if (!['repeating', 'forever'].includes(def.duration)) throw invalid('duration')
  }
  if (!DURATIONS.includes(def.duration)) throw invalid('duration')
  def.duration_months = def.duration === 'repeating' ? int(body.duration_months) : null
  if (def.duration === 'repeating' && (def.duration_months === null || def.duration_months < 1 || def.duration_months > 24)) throw invalid('duration_months')
  def.starts_at = body.starts_at == null ? now : int(body.starts_at)
  def.ends_at = body.ends_at == null ? null : int(body.ends_at)
  if (def.starts_at === null || def.starts_at < 0) throw invalid('starts_at')
  if (def.ends_at !== null && (def.ends_at <= def.starts_at || def.ends_at <= now)) throw invalid('ends_at')
  def.max_redemptions = body.max_redemptions == null ? null : int(body.max_redemptions)
  if (def.max_redemptions !== null && (def.max_redemptions < 1 || def.max_redemptions > 1_000_000)) throw invalid('max_redemptions')
  def.max_per_account = body.max_per_account == null ? 1 : int(body.max_per_account)
  if (def.max_per_account === null || def.max_per_account < 1 || def.max_per_account > 100) throw invalid('max_per_account')
  def.note = typeof body.note === 'string' ? body.note.trim().slice(0, 200) : ''
  return def
}

/** Price of one month of `plan` in `market` with the code's terms (pure; minor units). */
export function priceWith(row, plan, market) {
  const list = catalog.plans[plan].monthlyMinor[market]
  let discount = 0
  if (row.kind === 'percent') discount = Math.round(list * row.percent / 100)
  if (row.kind === 'amount') discount = Math.min(list, market === 'BR' ? row.amount_brl ?? 0 : row.amount_usd ?? 0)
  discount = Math.max(0, Math.min(list, discount))
  return { list, discount, final: list - discount, currency: catalog.markets[market].currency.toLowerCase(), freeDays: row.kind === 'free_days' ? row.free_days : 0 }
}

export function createPromotions({ db, stripe = null, stripeEnabled = false, log = console, now = () => Math.floor(Date.now() / 1000) }) {
  migratePromotions(db)
  const q = {
    get: db.prepare('SELECT * FROM promo_codes WHERE code = ?'),
    all: db.prepare('SELECT * FROM promo_codes ORDER BY created_at DESC LIMIT 500'),
    insert: db.prepare(`INSERT INTO promo_codes (code, kind, percent, amount_brl, amount_usd, free_days, applies_to, duration, duration_months,
      plans, markets, starts_at, ends_at, max_redemptions, max_per_account, active, stripe_state, note, created_by, created_at, updated_at)
      VALUES (:code, :kind, :percent, :amount_brl, :amount_usd, :free_days, :applies_to, :duration, :duration_months, :plans, :markets,
      :starts_at, :ends_at, :max_redemptions, :max_per_account, 1, :stripe_state, :note, :created_by, :at, :at)`),
    used: db.prepare(`SELECT COUNT(*) AS n FROM promo_redemptions WHERE code = ? AND status IN ${COUNTED}`),
    usedBy: db.prepare(`SELECT COUNT(*) AS n FROM promo_redemptions WHERE code = ? AND account_id = ? AND status IN ${COUNTED}`),
    insertRedemption: db.prepare(`INSERT INTO promo_redemptions (id, code, account_id, market, plan, provider, ref, status,
      list_amount, discount_amount, final_amount, currency, free_days, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?, ?, ?, ?, ?, ?)`),
    redemption: db.prepare('SELECT * FROM promo_redemptions WHERE id = ?'),
    setRedemption: db.prepare('UPDATE promo_redemptions SET status = ?, updated_at = ? WHERE id = ? AND status = ?'),
    redeem: db.prepare("UPDATE promo_redemptions SET status = 'redeemed', subscription_ref = ?, updated_at = ? WHERE id = ? AND status = 'reserved'"),
    refundBySubscription: db.prepare("UPDATE promo_redemptions SET status = 'refunded', updated_at = ? WHERE subscription_ref = ? AND status = 'redeemed' RETURNING id, code"),
    openStripe: db.prepare(`SELECT * FROM promo_redemptions WHERE provider = 'stripe' AND status = 'reserved' AND account_id = ? AND code = ?
      AND plan = ? AND ref LIKE 'https://checkout.stripe.com/%' AND created_at > ? ORDER BY created_at DESC LIMIT 1`),
    setRef: db.prepare('UPDATE promo_redemptions SET ref = ?, updated_at = ? WHERE id = ?'),
    staleStripe: db.prepare("SELECT id, code FROM promo_redemptions WHERE provider = 'stripe' AND status = 'reserved' AND created_at <= ?"),
    event: db.prepare('INSERT INTO promo_events (code, type, detail, actor, created_at) VALUES (?, ?, ?, ?, ?)'),
    events: db.prepare('SELECT type, detail, actor, created_at FROM promo_events WHERE code = ? ORDER BY id DESC LIMIT 200'),
    redemptions: db.prepare('SELECT * FROM promo_redemptions WHERE code = ? ORDER BY created_at DESC LIMIT 500'),
    stats: db.prepare(`SELECT status, COUNT(*) AS n, COUNT(DISTINCT account_id) AS accounts, COALESCE(SUM(discount_amount), 0) AS discount,
      currency FROM promo_redemptions WHERE code = ? GROUP BY status, currency`),
  }
  // Accounts with a refund, chargeback, dispute, review or administrative cancellation never use codes. Prepared on
  // first use: the Stripe and InfinitePay tables are created by their modules after this one.
  let flags = null
  const flagQueries = () => (flags ??= {
    stripe: db.prepare("SELECT 1 FROM subscriptions WHERE account_id = ? AND (review <> '' OR refunded_at IS NOT NULL) LIMIT 1"),
    ip: db.prepare(`SELECT 1 FROM ip_subscriptions s WHERE s.account_id = ? AND (s.status IN ('refunded', 'canceled')
      OR EXISTS (SELECT 1 FROM ip_charges c WHERE c.subscription_id = s.id AND (c.status IN ('reversal_review', 'refunded') OR c.review <> ''))) LIMIT 1`),
  })

  function event(code, type, detail = '', actor = '') {
    q.event.run(code, type, String(detail).slice(0, 300), String(actor).slice(0, 120), now())
  }

  function transaction(work) {
    db.exec('BEGIN IMMEDIATE')
    try {
      const result = work()
      db.exec('COMMIT')
      return result
    } catch (error) {
      db.exec('ROLLBACK')
      throw error
    }
  }

  const flagged = accountId => Boolean(flagQueries().stripe.get(accountId) || flagQueries().ip.get(accountId))

  /**
   * Server-side quote for one account (no reservation). Every amount comes from catalog.json and the stored terms.
   * Returns { ok: false, reason } or { ok: true, code, list, discount, final, currency, freeDays, duration, months }.
   */
  function quote({ code, plan, market, accountId, at = now(), use = 'new' }) {
    const normalized = String(code ?? '').trim().toUpperCase()
    if (!CODE.test(normalized)) return { ok: false, reason: 'not_found' }
    const row = q.get.get(normalized)
    if (!row) return { ok: false, reason: 'not_found' }
    if (!row.active) return { ok: false, reason: 'inactive' }
    // a start-of-subscription code never discounts a running one, and a recurring code never applies at checkout
    if (row.applies_to !== use) return { ok: false, reason: use === 'new' ? 'existing_only' : 'new_only' }
    if (at < row.starts_at) return { ok: false, reason: 'not_started' }
    if (row.ends_at !== null && at >= row.ends_at) return { ok: false, reason: 'expired' }
    if (!JSON.parse(row.plans).includes(plan)) return { ok: false, reason: 'plan_not_eligible' }
    if (!JSON.parse(row.markets).includes(market)) return { ok: false, reason: 'market_not_eligible' }
    if (row.max_redemptions !== null && q.used.get(row.code).n >= row.max_redemptions) return { ok: false, reason: 'exhausted' }
    if (q.usedBy.get(row.code, String(accountId)).n >= row.max_per_account) return { ok: false, reason: 'account_limit' }
    if (flagged(String(accountId))) return { ok: false, reason: 'account_review' }
    const price = priceWith(row, plan, market)
    if (price.final > 0 && price.final < MIN_CHARGE[market]) return { ok: false, reason: 'below_minimum' }
    if (market === 'US' && row.kind !== 'free_days' && row.stripe_state !== 'synced') return { ok: false, reason: 'not_synced' }
    return { ok: true, code: row.code, ...price, appliesTo: row.applies_to, duration: row.duration, months: row.duration_months, stripePromotion: row.stripe_promotion }
  }

  /** Reserves one use. MUST run inside the caller's write transaction (the same one that checked the quote). */
  function reserve({ quote: quoted, accountId, plan, market, provider, ref = '' }) {
    const id = randomUUID()
    const at = now()
    q.insertRedemption.run(id, quoted.code, String(accountId), market, plan, provider, ref, quoted.list, quoted.discount,
      quoted.final, quoted.currency, quoted.freeDays ?? 0, at, at)
    event(quoted.code, 'reserved', `${provider} account ${accountId} ${plan} ${quoted.final}/${quoted.list}`)
    return id
  }

  /** A repeated click: the Stripe checkout already opened with this code for this account (its use is reserved). */
  function openStripeCheckout({ code, plan, accountId }) {
    const row = q.openStripe.get(String(accountId), String(code ?? '').trim().toUpperCase(), plan, now() - 23 * 60 * 60)
    return row ? row.ref : ''
  }

  function setRef(id, ref) {
    transaction(() => q.setRef.run(String(ref).slice(0, 300), now(), id))
  }

  /** Quote + reservation in one transaction (Stripe checkout). */
  function reserveStandalone({ code, plan, market, accountId, provider, use = 'new' }) {
    return transaction(() => {
      const quoted = quote({ code, plan, market, accountId, use })
      if (!quoted.ok) return quoted
      return { ...quoted, redemptionId: reserve({ quote: quoted, accountId, plan, market, provider }) }
    })
  }

  // The functions below run inside the caller's transaction (webhook, charge confirmation, expiry, refund)
  function redeem(id, subscriptionRef = '') {
    const row = q.redemption.get(id ?? '')
    if (!row) return false
    let changed = q.redeem.run(String(subscriptionRef), now(), row.id).changes > 0
    if (!changed && row.status === 'released' && row.provider === 'stripe') {
      // a Stripe checkout completed after its reservation was released (timeout): it was paid with the discount, so
      // the use counts
      changed = db.prepare("UPDATE promo_redemptions SET status = 'redeemed', subscription_ref = ?, updated_at = ? WHERE id = ? AND status = 'released'")
        .run(String(subscriptionRef), now(), row.id).changes > 0
    }
    if (changed) event(row.code, 'redeemed', `${row.provider} account ${row.account_id}`)
    return changed
  }

  function release(id, reason = '') {
    const row = q.redemption.get(id ?? '')
    if (!row) return false
    const changed = q.setRedemption.run('released', now(), row.id, 'reserved').changes > 0
    if (changed) event(row.code, 'released', reason)
    return changed
  }

  /** A refunded subscription keeps its use counted (no reuse by refunding), marked for the history. */
  function refundSubscription(subscriptionRef) {
    for (const row of q.refundBySubscription.all(now(), String(subscriptionRef))) event(row.code, 'refunded', `subscription ${String(subscriptionRef).slice(0, 40)}`)
  }

  /** Whether a running subscription already has a recurring code (one at a time). */
  function hasRecurring(subscriptionRef) {
    return Boolean(db.prepare(`SELECT 1 FROM promo_redemptions r JOIN promo_codes c ON c.code = r.code
      WHERE r.subscription_ref = ? AND r.status = 'redeemed' AND c.applies_to = 'existing' LIMIT 1`).get(String(subscriptionRef)))
  }

  /** One redemption refunded (InfinitePay: the promotional charge itself was refunded). Still counted. */
  function markRefunded(id) {
    const row = q.redemption.get(id ?? '')
    if (row && q.setRedemption.run('refunded', now(), row.id, 'redeemed').changes) event(row.code, 'refunded', `${row.provider} account ${row.account_id}`)
  }

  function terms(code) {
    return q.get.get(code) ?? null
  }

  /** Stripe reservations whose checkout never completed (the session expired). */
  function sweep() {
    return transaction(() => {
      let released = 0
      for (const row of q.staleStripe.all(now() - STRIPE_RESERVATION_TTL)) if (release(row.id, 'checkout expired')) released++
      return released
    })
  }

  // ---------------------------------------------------------------- administration (HTS panel, admin only)
  async function syncStripe(code, actor = '') {
    const row = q.get.get(code)
    if (!row) throw invalid('not_found')
    const markets = JSON.parse(row.markets)
    if (!markets.includes('US') || row.kind === 'free_days') {
      db.prepare("UPDATE promo_codes SET stripe_state = 'not_needed', updated_at = ? WHERE code = ?").run(now(), code)
      return view(q.get.get(code))
    }
    if (!stripe || !stripeEnabled) {
      db.prepare("UPDATE promo_codes SET stripe_state = 'pending', stripe_error = 'stripe_disabled', updated_at = ? WHERE code = ?").run(now(), code)
      return view(q.get.get(code))
    }
    try {
      // Idempotency keys: a retry after a timeout returns the same Stripe objects instead of creating new ones
      let coupon = row.stripe_coupon
      if (!coupon) {
        const created = await stripe.coupons.create({
          name: row.code,
          ...(row.kind === 'percent' ? { percent_off: row.percent } : { amount_off: row.amount_usd, currency: 'usd' }),
          duration: row.duration,
          ...(row.duration === 'repeating' ? { duration_in_months: row.duration_months } : {}),
          metadata: { asc_code: row.code },
        }, { idempotencyKey: `asc-coupon-${row.code}` })
        coupon = created.id
        db.prepare('UPDATE promo_codes SET stripe_coupon = ?, updated_at = ? WHERE code = ?').run(coupon, now(), code)
      }
      let promotion = row.stripe_promotion
      if (!promotion) {
        const created = await stripe.promotionCodes.create({
          promotion: { type: 'coupon', coupon },
          code: row.code,
          active: Boolean(row.active),
          // backstops only: our own validation (per account, plans, review) runs before every checkout
          ...(row.max_redemptions ? { max_redemptions: row.max_redemptions } : {}),
          ...(row.ends_at ? { expires_at: row.ends_at } : {}),
          metadata: { asc_code: row.code },
        }, { idempotencyKey: `asc-promotion-${row.code}` })
        promotion = created.id
      }
      db.prepare("UPDATE promo_codes SET stripe_promotion = ?, stripe_state = 'synced', stripe_error = '', updated_at = ? WHERE code = ?").run(promotion, now(), code)
      event(code, 'stripe_synced', `${coupon} ${promotion}`, actor)
    } catch (error) {
      const reason = String(error.code ?? error.type ?? error.name).slice(0, 80)
      db.prepare("UPDATE promo_codes SET stripe_state = 'error', stripe_error = ?, updated_at = ? WHERE code = ?").run(reason, now(), code)
      event(code, 'stripe_error', reason, actor)
      log.error('Stripe promotion sync failed', reason)
    }
    return view(q.get.get(code))
  }

  async function create(input, actor) {
    const at = now()
    const def = parseDefinition(input, at)
    transaction(() => {
      if (q.get.get(def.code)) throw invalid('code_taken')
      q.insert.run({ ...def, plans: JSON.stringify(def.plans), markets: JSON.stringify(def.markets), stripe_state: '', created_by: String(actor).slice(0, 120), at })
      event(def.code, 'created', JSON.stringify({ ...def, note: undefined }), actor)
    })
    return syncStripe(def.code, actor)
  }

  async function update(input, actor) {
    const code = String(input?.code ?? '').trim().toUpperCase()
    const row = q.get.get(code)
    if (!row) throw invalid('not_found')
    const changes = {}
    if (typeof input.active === 'boolean') changes.active = input.active ? 1 : 0
    if ('ends_at' in input) {
      const value = input.ends_at == null ? null : int(input.ends_at)
      if (input.ends_at != null && (value === null || value <= row.starts_at)) throw invalid('ends_at')
      changes.ends_at = value
    }
    if ('max_redemptions' in input) {
      const value = input.max_redemptions == null ? null : int(input.max_redemptions)
      if (input.max_redemptions != null && (value === null || value < 1)) throw invalid('max_redemptions')
      changes.max_redemptions = value
    }
    if ('max_per_account' in input) {
      const value = int(input.max_per_account)
      if (value === null || value < 1 || value > 100) throw invalid('max_per_account')
      changes.max_per_account = value
    }
    if (!Object.keys(changes).length) throw invalid('nothing_to_change')
    transaction(() => {
      const sets = Object.keys(changes).map(key => `${key} = :${key}`).join(', ')
      db.prepare(`UPDATE promo_codes SET ${sets}, updated_at = :at WHERE code = :code`).run({ ...changes, at: now(), code })
      event(code, 'updated', JSON.stringify(changes), actor)
    })
    // Stripe promotion codes can only be switched on/off after creation; our validation enforces the rest
    if ('active' in changes && row.stripe_promotion && stripe && stripeEnabled) {
      try {
        await stripe.promotionCodes.update(row.stripe_promotion, { active: Boolean(changes.active) })
      } catch (error) {
        const reason = String(error.code ?? error.type ?? error.name).slice(0, 80)
        db.prepare("UPDATE promo_codes SET stripe_error = ?, updated_at = ? WHERE code = ?").run(`update: ${reason}`, now(), code)
        event(code, 'stripe_error', `update ${reason}`, actor)
      }
    }
    return view(q.get.get(code))
  }

  function view(row) {
    const stats = { reserved: 0, redeemed: 0, released: 0, refunded: 0, accounts: 0, discount: {} }
    for (const s of q.stats.all(row.code)) {
      stats[s.status] = s.n
      if (s.status !== 'released') {
        stats.accounts += s.accounts
        stats.discount[s.currency] = (stats.discount[s.currency] ?? 0) + s.discount
      }
    }
    return {
      code: row.code, kind: row.kind, percent: row.percent, amount_brl: row.amount_brl, amount_usd: row.amount_usd,
      free_days: row.free_days, applies_to: row.applies_to, duration: row.duration, duration_months: row.duration_months,
      plans: JSON.parse(row.plans), markets: JSON.parse(row.markets), starts_at: row.starts_at, ends_at: row.ends_at,
      max_redemptions: row.max_redemptions, max_per_account: row.max_per_account, active: Boolean(row.active),
      stripe: { state: row.stripe_state, error: row.stripe_error, synced: row.stripe_state === 'synced' },
      note: row.note, created_by: row.created_by, created_at: row.created_at, updated_at: row.updated_at, stats,
    }
  }

  function list() {
    return q.all.all().map(view)
  }

  function details(code) {
    const row = q.get.get(String(code ?? '').toUpperCase())
    if (!row) throw invalid('not_found')
    return {
      ...view(row),
      redemptions: q.redemptions.all(row.code).map(r => ({
        id: r.id, account_id: Number(r.account_id), market: r.market, plan: r.plan, provider: r.provider, status: r.status,
        list_amount: r.list_amount, discount_amount: r.discount_amount, final_amount: r.final_amount, currency: r.currency,
        free_days: r.free_days, created_at: r.created_at, updated_at: r.updated_at,
      })),
      events: q.events.all(row.code),
    }
  }

  return { quote, reserve, reserveStandalone, openStripeCheckout, setRef, hasRecurring, markRefunded, redeem, release, refundSubscription, terms, sweep, create, update, syncStripe, list, details }
}

/** Discount of one InfinitePay renewal for a subscription that redeemed `row` (null when none applies). */
export function renewalPrice(row, plan, remaining) {
  if (!row || row.kind === 'free_days' || remaining === 0) return null
  return priceWith(row, plan, 'BR')
}

