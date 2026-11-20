import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { ArrowDown, ArrowRight, ArrowUpRight, AudioLines, Captions, Clapperboard, Radio, ScanLine } from 'lucide-react'
import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/instrument-sans'
import { content, type Copy, type Language } from './content'
import { initialPreferences, type Market, type PlanId } from './market'
import { Pricing } from './Pricing'
import { Waitlist } from './Waitlist'
import './styles.css'

const scene = '/media/night-run.webp'
const stepsIcons = [Radio, ScanLine, Captions]
const benefitIcons = [Radio, AudioLines, Clapperboard]
const timeline = [22, 36, 29, 60, 33, 44, 20, 55, 40, 21, 29, 53, 70, 59, 37, 65, 89, 93, 52, 64, 97, 74, 51, 30, 47, 68, 51, 24, 42, 36, 22, 51, 66, 37, 29, 17, 27, 42, 22, 32]

function Brand({ label }: { label: string }) {
  return (
    <a className="brand" href="#top" aria-label={label}>
      <img src="/brand.svg" width="36" height="36" alt="" />
      <span>AI Stream Clipper <small>by HTS</small></span>
    </a>
  )
}

function Hero({ copy }: { copy: Copy }) {
  const t = copy.hero
  return (
    <div className="hero-wrap">
      <section className="hero shell" aria-labelledby="hero-heading">
        <div className="hero-copy">
          <span className="early-pill"><span className="status-dot" />{t.label}</span>
          <h1 id="hero-heading">{t.title[0]} <span>{t.title[1]}</span></h1>
          <p>{t.detail}</p>
          <div className="hero-actions">
            <a className="button primary" href="#acesso">{copy.join}<ArrowUpRight size={19} aria-hidden="true" /></a>
            <a className="demo-link" href="#como-funciona"><ArrowDown size={17} aria-hidden="true" />{t.demo}</a>
          </div>
        </div>
        <figure className="hero-visual">
          <div className="hero-composition">
            <div className="stream-card">
              <div className="stream-picture">
                <img src={scene} width="1200" height="800" alt="" fetchPriority="high" />
                <span className="live-label"><span />{t.live}</span>
                <div className="frame-bracket" aria-hidden="true"><i /><i /><i /><i /></div>
              </div>
              <div className="stream-timeline">
                <span>01:24:18</span>
                <div aria-hidden="true">{timeline.slice(0, 26).map((height, i) => <i key={i} style={{ height: `${height}%` }} />)}</div>
              </div>
            </div>
            <span className="stage-arrow" aria-hidden="true"><ArrowRight size={18} /></span>
            <div className="clip-card">
              <div className="clip-picture">
                <img src={scene} width="1200" height="800" alt="" />
                <span className="ratio">9:16</span>
                <b>{t.caption}</b>
              </div>
              <span className="clip-label"><Captions size={15} aria-hidden="true" />{t.clip}</span>
            </div>
          </div>
          <figcaption><span><ScanLine size={14} aria-hidden="true" />{t.detection}</span><span>{t.note}</span></figcaption>
        </figure>
      </section>
      <ul className="benefits shell">
        {t.benefits.map(([title, detail], index) => {
          const Icon = benefitIcons[index]
          return <li key={index}><span className="benefit-icon"><Icon size={21} aria-hidden="true" /></span><div><strong>{title}</strong><p>{detail}</p></div></li>
        })}
      </ul>
    </div>
  )
}

function Workflow({ copy }: { copy: Copy }) {
  const t = copy.flow
  const [step, setStep] = useState(0)
  const tabs = useRef<(HTMLButtonElement | null)[]>([])
  const current = t.steps[step]
  const StageIcon = stepsIcons[step]

  function navigateTabs(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = t.steps.length - 1
    switch (event.key) {
      case 'ArrowDown': index = index === last ? 0 : index + 1; break
      case 'ArrowUp': index = index === 0 ? last : index - 1; break
      case 'Home': index = 0; break
      case 'End': index = last; break
      default: return
    }
    event.preventDefault()
    setStep(index)
    tabs.current[index]?.focus()
  }

  return (
    <section className="workflow shell" id="como-funciona" aria-labelledby="workflow-heading">
      <div className="section-head">
        <span className="eyebrow">{t.eyebrow}</span>
        <h2 id="workflow-heading">{t.title}</h2>
      </div>
      <div className="workflow-grid">
        <div className="workflow-tabs" role="tablist" aria-labelledby="workflow-heading" aria-orientation="vertical">
          {t.steps.map((item, index) => {
            const Icon = stepsIcons[index]
            return (
              <button key={index} type="button" role="tab" id={`step-${index}`} aria-selected={step === index}
                aria-controls="workflow-preview" tabIndex={step === index ? 0 : -1}
                ref={element => { tabs.current[index] = element }} onClick={() => setStep(index)}
                onKeyDown={event => navigateTabs(event, index)}>
                <span className="step-icon"><Icon size={19} aria-hidden="true" /></span>
                <span><strong>{item.title}</strong><span className="step-detail">{item.detail}</span></span>
                <ArrowRight className="step-arrow" size={17} aria-hidden="true" />
              </button>
            )
          })}
        </div>
        <div className={`workflow-preview phase-${step}`} role="tabpanel" id="workflow-preview" aria-labelledby={`step-${step}`} tabIndex={0}>
          <div className="preview-top"><span><StageIcon size={16} aria-hidden="true" />{current.panel}</span><small>{t.sample}</small></div>
          <div className="preview-frame">
            <div className="preview-art">
              <img src={scene} width="1200" height="800" alt="" loading="lazy" />
              {step === 1 && <div className="frame-bracket" aria-hidden="true"><i /><i /><i /><i /></div>}
              {step === 2 && <><span className="ratio">9:16</span><b>{copy.hero.caption}</b></>}
            </div>
            <span className="preview-time">{current.time}</span>
          </div>
          <div className="preview-timeline">
            <div className="waveform" aria-label={t.timeline} role="img">
              {timeline.map((height, index) => <i key={index} className={step > 0 && index >= 11 && index <= 27 ? 'active' : ''} style={{ height: `${height}%` }} />)}
              {step > 0 && <span aria-hidden="true" className="selected-range"><i /><i /></span>}
            </div>
            <div className="preview-status">
              <span><span className="status-dot" />{current.status}</span>
              {step > 0 && <small>{t.start} / {t.end}</small>}
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

function App() {
  const [preferences, setPreferences] = useState(initialPreferences)
  const [interest, setInterest] = useState<PlanId | ''>('')
  const { language, market } = preferences
  const copy = content[language]

  useEffect(() => {
    document.documentElement.lang = language
    document.title = copy.title
    document.querySelector('meta[name="description"]')?.setAttribute('content', copy.description)
  }, [language, copy])

  function updatePreferences(nextLanguage: Language, nextMarket: Market) {
    const url = new URL(window.location.href)
    url.searchParams.set('lang', nextLanguage)
    url.searchParams.set('market', nextMarket)
    window.history.replaceState(null, '', url)
    setPreferences({ language: nextLanguage, market: nextMarket })
  }

  return (
    <>
      <a className="skip-link" href="#main">{copy.skip}</a>
      <header className="site-header" id="top">
        <div className="header-inner shell">
          <Brand label="AI Stream Clipper by HTS" />
          <nav aria-label={language === 'en' ? 'Main navigation' : 'Navegação principal'}>
            <a href="#como-funciona">{copy.nav.flow}</a>
            <a href="#planos">{copy.nav.plans}</a>
          </nav>
          <div className="header-actions">
            <div className="segmented language-switch" role="group" aria-label={copy.nav.language}>
              <button lang="pt-BR" type="button" aria-pressed={language === 'pt-BR'} onClick={() => updatePreferences('pt-BR', market)}>PT-BR</button>
              <button lang="en" type="button" aria-pressed={language === 'en'} onClick={() => updatePreferences('en', market)}>EN</button>
            </div>
            <a className="button quiet header-cta" href="#acesso">{copy.nav.waitlist}<ArrowUpRight size={15} aria-hidden="true" /></a>
          </div>
        </div>
      </header>
      <main id="main" tabIndex={-1}>
        <Hero copy={copy} />
        <Workflow copy={copy} />
        <Pricing copy={copy} market={market} onMarket={next => updatePreferences(language, next)} onInterest={setInterest} />
        <Waitlist copy={copy} language={language} market={market} interest={interest} onInterest={setInterest} />
      </main>
      <footer className="site-footer shell">
        <Brand label="AI Stream Clipper by HTS" />
        <p>{copy.footer.tagline}</p>
        <small>{copy.footer.independent}</small>
      </footer>
    </>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
