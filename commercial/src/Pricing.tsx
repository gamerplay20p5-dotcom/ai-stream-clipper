import { ArrowUpRight, Check, Minus, Clapperboard, Radio, Zap } from 'lucide-react'
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

const planIcons = { basic: Radio, creator: Clapperboard, pro: Zap }

export function Pricing({ copy, market, onMarket, onInterest }: Props) {
  const t = copy.pricing
  const included = <><Check className="included" size={18} aria-hidden="true" /><span className="sr-only">{t.yes}</span></>
  const excluded = <><Minus size={16} aria-hidden="true" /><span className="sr-only">{t.no}</span></>
  const rows = [
    { label: t.rows.hours, value: (id: PlanId) => `${plans[id].hours} h` },
    { label: t.rows.simultaneous, value: (id: PlanId) => plans[id].simultaneous },
    { label: t.rows.ai, value: (id: PlanId) => t.plans[id].ai },
    { label: t.rows.export, value: (id: PlanId) => plans[id].export4k ? '1080p + 4K' : '1080p' },
    { label: t.rows.storage, value: (id: PlanId) => `${plans[id].storageGb} GB` },
    { label: t.rows.captions, value: () => included },
    { label: t.rows.watermark, value: () => included },
    { label: t.rows.priority, value: (id: PlanId) => id === 'pro' ? included : excluded },
  ]

  return (
    <section className="pricing shell" id="planos" aria-labelledby="pricing-heading">
      <div className="section-head pricing-head">
        <div>
          <span className="eyebrow">{t.eyebrow}</span>
          <h2 id="pricing-heading">{t.title}</h2>
          <p>{t.detail}</p>
        </div>
        <MarketSwitch market={market} onMarket={onMarket} label={copy.nav.market} />
      </div>
      <div className="plan-grid">
        {planIds.map(id => {
          const plan = plans[id]
          const price = formatPrice(id, market)
          const Icon = planIcons[id]
          return (
            <article className={`plan-card plan-${id}`} key={id}>
              <div className="plan-name"><span className="plan-glyph"><Icon size={19} aria-hidden="true" /></span><h3>{plan.name}</h3></div>
              <p className="plan-tagline">{t.plans[id].tagline}</p>
              <p className="price"><span className="price-symbol">{price.symbol}</span>{' '}<strong>{price.amount}</strong>{' '}<span className="price-period">{t.perMonth}</span></p>
              <ul className="plan-features">
                {[t.hours(plan.hours), t.simul(plan.simultaneous), plan.export4k ? `1080p + ${t.proExport}` : '1080p', t.watermark].map(item => (
                  <li key={item}><Check size={16} aria-hidden="true" />{item}</li>
                ))}
                {id === 'pro' && <li><Check size={16} aria-hidden="true" />{t.rows.priority}</li>}
              </ul>
              <a className={`button ${id === 'creator' ? 'primary' : 'quiet'}`} href="#acesso" onClick={() => onInterest(id)}>{t.interest(plan.name)}<ArrowUpRight size={17} aria-hidden="true" /></a>
            </article>
          )
        })}
      </div>
      <p className="pricing-note">{t.provisional}</p>
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
        <p>{t.proNote(plans.pro.export4k)}</p>
      </div>
    </section>
  )
}
