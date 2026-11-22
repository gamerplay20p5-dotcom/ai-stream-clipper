import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowUpRight, Check, ChevronDown, LoaderCircle } from 'lucide-react'
import catalog from '../catalog.json'
import type { Copy, Language } from './content'
import { planIds, plans, type Market, type PlanId } from './market'

type Submission = 'idle' | 'sending' | 'success' | 'invalid' | 'rate' | 'unavailable' | 'closed'

interface Props {
  copy: Copy
  language: Language
  market: Market
  interest: PlanId | ''
  onInterest: (plan: PlanId | '') => void
  marker: string
  /** false while the privacy policy and terms are not published: nothing is collected */
  open?: boolean
}

export function Waitlist({ copy, language, market, interest, onInterest, marker, open = true }: Props) {
  const t = copy.waitlist
  const [status, setStatus] = useState<Submission>('idle')
  const [countryChoice, setCountryChoice] = useState<string>()
  const successHeading = useRef<HTMLHeadingElement>(null)
  const country = countryChoice ?? market

  useEffect(() => {
    if (status === 'success') successHeading.current?.focus()
  }, [status])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const fields = new FormData(event.currentTarget)
    setStatus('sending')
    try {
      const response = await fetch('/api/waitlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: AbortSignal.timeout(12_000),
        body: JSON.stringify({
          email: fields.get('email'),
          plan: fields.get('plan'),
          country: fields.get('country'),
          otherCountry: fields.get('otherCountry') ?? '',
          platform: fields.get('platform'),
          consent: fields.get('consent') === 'on',
          website: fields.get('website'),
          language,
          market,
        }),
      })
      if (response.status === 202) setStatus('success')
      else if (response.status === 503) setStatus('closed')
      else if (response.status === 422) setStatus('invalid')
      else if (response.status === 429) setStatus('rate')
      else setStatus('unavailable')
    } catch {
      setStatus('unavailable')
    }
  }

  return (
    <section className="waitlist section-space" id="acesso" aria-labelledby="waitlist-heading" tabIndex={-1}>
      <div className="shell waitlist-inner">
        <div className="waitlist-intro">
          <p className="marker">{marker}</p>
          <h2 id="waitlist-heading">{t.title}</h2>
          <p className="lede">{t.detail}</p>
          <ul className="waitlist-plans" role="list">
            {planIds.map(id => <li key={id} className={interest === id ? 'is-chosen' : undefined}><strong>{plans[id].name}</strong><span>{copy.pricing.hours(plans[id].hours)}</span></li>)}
          </ul>
        </div>
        {!open || status === 'closed' ? (
          <div className="waitlist-confirmation" role="status">
            <h3>{t.closed}</h3>
            <p>{t.closedDetail}</p>
          </div>
        ) : status === 'success' ? (
          <div className="waitlist-confirmation">
            <span className="confirm-icon"><Check size={26} aria-hidden="true" /></span>
            <h3 tabIndex={-1} ref={successHeading}>{t.success}</h3>
            <p>{t.successDetail}</p>
          </div>
        ) : (
          <form action="/api/waitlist" method="post" onSubmit={submit} aria-describedby="privacy-note" className="waitlist-form">
            <fieldset disabled={status === 'sending'}>
              <label className="field field-wide">
                <span>{t.email}</span>
                <input name="email" type="email" autoComplete="email" autoCapitalize="none" spellCheck={false} maxLength={254} placeholder={t.placeholder} required />
              </label>
              <label className="field">
                <span>{t.plan}</span>
                <span className="select-field">
                  <select name="plan" required value={interest} onChange={event => onInterest(event.target.value as PlanId | '')}>
                    <option value="" disabled>{t.choose}</option>
                    {planIds.map(id => <option key={id} value={id}>{plans[id].name}</option>)}
                  </select>
                  <ChevronDown size={15} aria-hidden="true" />
                </span>
              </label>
              <label className="field">
                <span>{t.country}</span>
                <span className="select-field">
                  <select name="country" autoComplete="off" required value={country} onChange={event => setCountryChoice(event.target.value)}>
                    {catalog.countries.map(id => <option key={id} value={id}>{t.countries[id as keyof typeof t.countries]}</option>)}
                  </select>
                  <ChevronDown size={15} aria-hidden="true" />
                </span>
              </label>
              {country === 'OTHER' && (
                <label className="field field-wide">
                  <span>{t.otherCountry}</span>
                  <input name="otherCountry" autoComplete="country-name" minLength={2} maxLength={80} required />
                </label>
              )}
              <label className="field field-wide">
                <span>{t.platform}</span>
                <span className="select-field">
                  <select name="platform" defaultValue="" aria-describedby="platform-note" required>
                    <option value="" disabled>{t.choose}</option>
                    {catalog.platforms.map(id => <option key={id} value={id}>{t.platforms[id as keyof typeof t.platforms]}</option>)}
                  </select>
                  <ChevronDown size={15} aria-hidden="true" />
                </span>
              </label>
              <p id="platform-note" className="form-note field-wide">{t.platformsNote}</p>
              <div hidden aria-hidden="true">
                <label>Website <input name="website" tabIndex={-1} autoComplete="off" /></label>
              </div>
              <label className="consent field-wide">
                <input type="checkbox" name="consent" required />
                <span>{t.consent}</span>
              </label>
              <button className="button primary field-wide" type="submit" aria-describedby="form-status">
                {status === 'sending' ? <>{t.sending}<LoaderCircle className="form-spinner" size={18} aria-hidden="true" /></> : <>{copy.join}<ArrowUpRight size={18} aria-hidden="true" /></>}
              </button>
            </fieldset>
            <div className="form-feedback" role="status" id="form-status">
              {status === 'sending' ? <span className="sr-only">{t.sending}</span> : null}
              {status !== 'idle' && status !== 'sending' && <p className="form-error">{t.errors[status]}</p>}
            </div>
            <p id="privacy-note" className="form-note">{t.privacy}</p>
          </form>
        )}
      </div>
    </section>
  )
}
