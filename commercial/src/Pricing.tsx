import { useState } from 'react'
import { ArrowUpRight, Check, Crown, LoaderCircle, Lock, Minus, Plus, Sparkles } from 'lucide-react'
import { startCheckout, type BillingConfig, type CheckoutError } from './billing'
import type { Copy, Language } from './content'
import { formatPrice, planIds, plans, type Market, type PlanId } from './market'

interface Props {
  copy: Copy
  market: Market
  onMarket: (market: Market) => void
  onInterest: (plan: PlanId) => void
  billing: BillingConfig
  language: Language
}

export function MarketSwitch({ market, onMarket, label }: Pick<Props, 'market' | 'onMarket'> & { label: string }) {
  return (
    <div className="segmented market-switch" role="group" aria-label={label}>
      <button type="button" aria-pressed={market === 'BR'} onClick={() => onMarket('BR')}>BRL</button>
      <button type="button" aria-pressed={market === 'US'} onClick={() => onMarket('US')}>USD</button>
    </div>
  )
}

// Display only: prices and allowances come from catalog.json, which the server also uses to validate plan ids.
function planFeatures(id: PlanId, t: Copy['pricing']) {
  const plan = plans[id]
  const common = [t.hours(plan.hours), t.channels(plan.channels), t.simul(plan.simultaneous), t.storage(plan.storageGb)]
  if (id === 'basic') return [...common, t.plans.basic.ai, t.export1080]
  if (id === 'creator') return [...common, t.plans.creator.ai, t.rows.editing, t.export1080]
  return [...common, t.rows.secondPass, t.rows.variants, t.exportLimit(plan.export4k), t.rows.priority]
}

export function Pricing({ copy, market, onMarket, onInterest, billing, language }: Props) {
  const t = copy.pricing
  const b = copy.billing
  const [pending, setPending] = useState<PlanId | null>(null)
  const [error, setError] = useState<CheckoutError | null>(null)

  async function subscribe(plan: PlanId) {
    setPending(plan)
    setError(null)
    const problem = await startCheckout(plan, market, language)
    // On success the browser is already leaving for Stripe Checkout
    if (problem) { setError(problem); setPending(null) }
  }
  const included = <><Check className="included" size={17} aria-hidden="true" /><span className="sr-only">{t.yes}</span></>
  const excluded = <><Minus size={16} aria-hidden="true" /><span className="sr-only">{t.no}</span></>
  const rows = [
    { label: t.rows.hours, value: (id: PlanId) => `${plans[id].hours} h` },
    { label: t.rows.channels, value: (id: PlanId) => plans[id].channels },
    { label: t.rows.simultaneous, value: (id: PlanId) => plans[id].simultaneous },
    { label: t.rows.ai, value: (id: PlanId) => t.plans[id].ai },
    { label: t.rows.export, value: (id: PlanId) => plans[id].export4k ? `1080p + 4K (${plans[id].export4k})` : '1080p' },
    { label: t.rows.storage, value: (id: PlanId) => `${plans[id].storageGb} GB` },
    { label: t.rows.captions, value: () => included },
    { label: t.rows.watermark, value: () => included },
    { label: t.rows.editing, value: (id: PlanId) => id !== 'basic' ? included : excluded },
    { label: t.rows.secondPass, value: (id: PlanId) => id === 'pro' ? included : excluded },
    { label: t.rows.variants, value: (id: PlanId) => id === 'pro' ? included : excluded },
    { label: t.rows.priority, value: (id: PlanId) => id === 'pro' ? included : excluded },
  ]

  return (
    <section className="pricing section-space" id="planos" aria-labelledby="pricing-heading" tabIndex={-1}>
      <div className="shell">
        <div className="pricing-head">
          <div className="section-head">
            <h2 id="pricing-heading" className="slashed">{t.title}</h2>
            <p className="lede">{t.detail}</p>
          </div>
          <MarketSwitch market={market} onMarket={onMarket} label={copy.nav.market} />
        </div>
        <div className="plan-grid">
          {planIds.map(id => {
            const plan = plans[id]
            const price = formatPrice(id, market)
            return (
              <article className={`plan-card plan-${id}`} key={id} aria-labelledby={`plan-${id}-name`}>
                {id === 'creator' ? <span className="plan-badge"><Sparkles size={12} aria-hidden="true" />{t.highlight}</span> : null}
                <h3 id={`plan-${id}-name`}>{id === 'pro' ? <Crown size={18} aria-hidden="true" /> : null}{plan.name}</h3>
                <p className="plan-tagline">{t.plans[id].tagline}</p>
                <p className="price"><span className="price-symbol">{price.symbol}</span><strong>{price.amount}</strong><span className="price-period">{t.perMonth}</span></p>
                <ul className="plan-features" role="list">
                  {planFeatures(id, t).map(item => <li key={item}><Check size={16} aria-hidden="true" />{item}</li>)}
                </ul>
                {billing.enabled ? (
                  <div className="plan-actions">
                    <button type="button" className={`button ${id === 'creator' ? 'primary' : 'outline'}`} disabled={pending !== null} aria-busy={pending === id} onClick={() => subscribe(id)}>
                      {pending === id ? <>{b.redirecting}<LoaderCircle className="form-spinner" size={16} aria-hidden="true" /></> : <>{b.subscribe(plan.name)}<ArrowUpRight size={16} aria-hidden="true" /></>}
                    </button>
                    <a className="plan-waitlist" href="#acesso" onClick={() => onInterest(id)}>{b.orWaitlist}</a>
                  </div>
                ) : (
                  <a className={`button ${id === 'creator' ? 'primary' : 'outline'}`} href="#acesso" onClick={() => onInterest(id)}>{t.interest(plan.name)}<ArrowUpRight size={16} aria-hidden="true" /></a>
                )}
              </article>
            )
          })}
        </div>
        <div className="checkout-feedback" role="status">{error ? <p className="form-error">{b.errors[error]}</p> : null}</div>
        <div className="pricing-notes">
          <p><Check size={15} aria-hidden="true" />{t.shared}</p>
          {billing.enabled ? <p className="secure-note"><Lock size={14} aria-hidden="true" />{b.secure}</p> : null}
          <p>{t.provisional}</p>
        </div>
        <div className="comparison">
          <h3 id="compare-heading">{t.compare}</h3>
          <div className="table-wrap">
            <table aria-labelledby="compare-heading">
              <thead><tr><th scope="col">{t.feature}</th>{planIds.map(id => <th scope="col" key={id} className={`col-${id}`}>{plans[id].name}</th>)}</tr></thead>
              <tbody>
                {rows.map(row => (
                  <tr key={row.label}>
                    <th scope="row">{row.label}</th>
                    {planIds.map(id => <td key={id} className={`col-${id}`}>{row.value(id)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="comparison-mobile">
            <p>{t.compareMobile}</p>
            {rows.map(row => (
              <details key={row.label}>
                <summary>{row.label}<Plus size={16} aria-hidden="true" /></summary>
                <dl>{planIds.map(id => <div key={id}><dt>{plans[id].name}</dt><dd>{row.value(id)}</dd></div>)}</dl>
              </details>
            ))}
          </div>
          <p className="pro-note">{t.proNote(plans.pro.export4k)}</p>
        </div>
      </div>
    </section>
  )
}
