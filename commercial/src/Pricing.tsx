import { ArrowUpRight, Check, Layers3, Minus, Plus } from 'lucide-react'
import type { Copy } from './content'
import { formatPrice, planIds, plans, type Market, type PlanId } from './market'

interface Props {
  copy: Copy
  market: Market
  onMarket: (market: Market) => void
  onInterest: (plan: PlanId) => void
}

export function MarketSwitch({ market, onMarket, label }: Pick<Props, 'market' | 'onMarket'> & { label: string }) {
  return (
    <div className="segmented market-switch" role="group" aria-label={label}>
      <button type="button" aria-pressed={market === 'BR'} onClick={() => onMarket('BR')}>BRL</button>
      <button type="button" aria-pressed={market === 'US'} onClick={() => onMarket('US')}>USD</button>
    </div>
  )
}

export function Pricing({ copy, market, onMarket, onInterest }: Props) {
  const t = copy.pricing
  const included = <><Check className="included" size={18} aria-hidden="true" /><span className="sr-only">{t.yes}</span></>
  const excluded = <><Minus size={16} aria-hidden="true" /><span className="sr-only">{t.no}</span></>
  const rows = [
    { label: t.rows.hours, value: (id: PlanId) => `${plans[id].hours} h` },
    { label: t.rows.channels, value: (id: PlanId) => plans[id].channels },
    { label: t.rows.simultaneous, value: (id: PlanId) => plans[id].simultaneous },
    { label: t.rows.ai, value: (id: PlanId) => t.plans[id].ai },
    { label: t.rows.export, value: (id: PlanId) => plans[id].export4k ? '1080p + 4K' : '1080p' },
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
        <div className="section-head pricing-head">
          <div>
            <h2 id="pricing-heading">{t.title}</h2>
            <p>{t.detail}</p>
          </div>
          <MarketSwitch market={market} onMarket={onMarket} label={copy.nav.market} />
        </div>
        <div className="plan-grid">
          {planIds.map(id => {
            const plan = plans[id]
            const price = formatPrice(id, market)
            return (
              <article className={`plan-card plan-${id}`} key={id}>
                <div className="plan-card-heading">
                  <h3>{plan.name}</h3>
                  {id === 'creator' ? <span className="plan-highlight">{t.highlight}</span> : null}
                  {id === 'pro' ? <span className="pro-format">4K</span> : null}
                </div>
                <p className="plan-tagline">{t.plans[id].tagline}</p>
                <p className="price"><span className="price-symbol">{price.symbol}</span>{' '}<strong>{price.amount}</strong>{' '}<span className="price-period">{t.perMonth}</span></p>
                <div className="plan-limits"><strong>{t.hours(plan.hours)}</strong><span>{t.channels(plan.channels)}</span></div>
                <ul className="plan-features">
                  {[t.simul(plan.simultaneous), t.storage(plan.storageGb), t.plans[id].ai, ...(id === 'creator' ? [t.rows.editing] : []), '1080p'].map(item => (
                    <li key={item}><Check size={16} aria-hidden="true" />{item}</li>
                  ))}
                </ul>
                {id === 'pro' ? (
                  <div className="pro-workbench">
                    <h4><Layers3 size={16} aria-hidden="true" />{t.proHighlight}</h4>
                    <ul>{[t.rows.secondPass, t.rows.variants, t.rows.priority, t.exportLimit(plan.export4k)].map(item => <li key={item}>{item}</li>)}</ul>
                  </div>
                ) : null}
                <a className={`button ${id === 'creator' ? 'primary' : 'quiet'}`} href="#acesso" onClick={() => onInterest(id)}>{t.interest(plan.name)}<ArrowUpRight size={17} aria-hidden="true" /></a>
              </article>
            )
          })}
        </div>
        <div className="pricing-note"><p><Check size={15} aria-hidden="true" />{t.shared}</p><p>{t.provisional}</p></div>
        <div className="comparison">
          <h3 id="compare-heading">{t.compare}</h3>
          <table aria-labelledby="compare-heading">
            <thead><tr><th scope="col">{t.feature}</th>{planIds.map(id => <th scope="col" key={id}>{plans[id].name}</th>)}</tr></thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.label}>
                  <th scope="row">{row.label}</th>
                  {planIds.map(id => <td key={id}>{row.value(id)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="comparison-mobile">
            <p>{t.compareMobile}</p>
            {rows.map(row => (
              <details key={row.label}>
                <summary>{row.label}<Plus size={16} aria-hidden="true" /></summary>
                <dl>{planIds.map(id => <div key={id}><dt>{plans[id].name}</dt><dd>{row.value(id)}</dd></div>)}</dl>
              </details>
            ))}
          </div>
          <p>{t.proNote(plans.pro.export4k)}</p>
        </div>
      </div>
    </section>
  )
}
