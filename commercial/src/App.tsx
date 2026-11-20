import { useEffect, useState } from 'react'
import { content, type Language } from './content'
import { editorial } from './editorial'
import type { Market, PlanId, Preferences } from './market'
import { Footer, Header } from './SiteChrome'
import { Hero, Workflow } from './Studio'
import { Features, Questions } from './EditorialSections'
import { Pricing } from './Pricing'
import { Waitlist } from './Waitlist'

export function App({ initial }: { initial: Preferences }) {
  const [{ language, market }, setPreferences] = useState(initial)
  const [interest, setInterest] = useState<PlanId | ''>('')
  const copy = content[language]
  const t = editorial[language]

  useEffect(() => {
    document.documentElement.lang = language
    document.title = copy.title
    document.querySelector('meta[name="description"]')?.setAttribute('content', copy.description)
    document.querySelector('meta[property="og:title"]')?.setAttribute('content', copy.title)
    document.querySelector('meta[property="og:description"]')?.setAttribute('content', copy.description)
    document.querySelector('meta[property="og:locale"]')?.setAttribute('content', language === 'en' ? 'en_US' : 'pt_BR')
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
      <Header t={t} language={language} languageLabel={copy.nav.language} onLanguage={next => updatePreferences(next, market)} join={copy.join} />
      <main id="main" tabIndex={-1}>
        <Hero t={t} join={copy.join} language={language} />
        <Workflow t={t} />
        <Features t={t} />
        <Pricing copy={copy} market={market} onMarket={next => updatePreferences(language, next)} onInterest={setInterest} />
        <Questions t={t.faq} />
        <Waitlist copy={copy} language={language} market={market} interest={interest} onInterest={setInterest} />
      </main>
      <Footer t={t} join={copy.join} independent={copy.footer.independent} />
    </>
  )
}
