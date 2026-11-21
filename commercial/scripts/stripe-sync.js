// Creates or updates the Stripe products and monthly USD prices for every plan in catalog.json.
// Idempotent: prices are found by lookup key; a changed amount creates a new price that takes over the
// lookup key and archives the old one (existing subscribers keep their current price until migrated).
//
//   STRIPE_SECRET_KEY=sk_test_... node scripts/stripe-sync.js            # test mode
//   STRIPE_SECRET_KEY=sk_live_... node scripts/stripe-sync.js --live     # production, explicit opt-in
import Stripe from 'stripe'
import catalog from '../catalog.json' with { type: 'json' }
import { lookupKey, STRIPE_MARKETS } from '../server/billing.js'

const key = process.env.STRIPE_SECRET_KEY ?? ''
if (!/^(sk|rk)_(test|live)_/.test(key)) {
  console.error('Set STRIPE_SECRET_KEY to a Stripe secret or restricted key')
  process.exit(1)
}
if (key.includes('_live_') && !process.argv.includes('--live')) {
  console.error('Refusing to change live-mode prices without --live')
  process.exit(1)
}
const dryRun = process.argv.includes('--dry-run')
const stripe = new Stripe(key, { maxNetworkRetries: 2 })

async function findProduct(plan) {
  for await (const product of stripe.products.list({ limit: 100 })) {
    if (product.metadata?.asc_plan === plan) return product
  }
  return null
}

for (const [plan, details] of Object.entries(catalog.plans)) {
  let product = await findProduct(plan)
  const name = `AI Stream Clipper ${details.name}`
  if (!product) {
    console.log(`create product ${name}`)
    if (!dryRun) product = await stripe.products.create({ name, metadata: { asc_plan: plan } })
  } else if (product.name !== name || !product.active) {
    console.log(`update product ${name}`)
    if (!dryRun) product = await stripe.products.update(product.id, { name, active: true })
  }
  // Only USD prices: Brazil is billed through InfinitePay
  for (const market of STRIPE_MARKETS) {
    const lookup = lookupKey(plan, market)
    const currency = catalog.markets[market].currency.toLowerCase()
    const amount = details.monthlyMinor[market]
    const { data } = await stripe.prices.list({ lookup_keys: [lookup], limit: 1 })
    const current = data[0]
    if (current && current.active && current.unit_amount === amount && current.currency === currency
      && current.recurring?.interval === 'month' && current.recurring?.interval_count === 1) {
      console.log(`ok     ${lookup} ${amount} ${currency}`)
      continue
    }
    console.log(`price  ${lookup} ${amount} ${currency}${current ? ' (replaces previous price)' : ''}`)
    if (dryRun) continue
    await stripe.prices.create({
      product: product.id,
      currency,
      unit_amount: amount,
      recurring: { interval: 'month', interval_count: 1 },
      lookup_key: lookup,
      transfer_lookup_key: true,
      metadata: { asc_plan: plan, asc_market: market },
    })
    if (current?.active) await stripe.prices.update(current.id, { active: false })
  }
}
console.log(dryRun ? 'Dry run finished, nothing changed' : 'Stripe catalog in sync')
