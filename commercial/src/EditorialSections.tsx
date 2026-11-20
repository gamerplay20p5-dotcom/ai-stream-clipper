import { Plus } from 'lucide-react'
import type { Editorial } from './editorial'

export function Features({ t }: { t: Editorial['features'] }) {
  return (
    <section id="recursos" className="features shell section-space" aria-labelledby="features-heading" tabIndex={-1}>
      <div className="section-head"><h2 id="features-heading">{t.title}</h2><p>{t.detail}</p></div>
      <ul className="feature-list">{t.items.map(([title, detail]) => <li key={title}><h3>{title}</h3><p>{detail}</p></li>)}</ul>
    </section>
  )
}

export function Questions({ t }: { t: Editorial['faq'] }) {
  return (
    <section id="faq" className="faq shell section-space" aria-labelledby="faq-heading" tabIndex={-1}>
      <h2 id="faq-heading">{t.title}</h2>
      <div className="faq-list">{t.items.map(([question, answer]) => <details key={question}><summary>{question}<Plus size={19} aria-hidden="true" /></summary><p>{answer}</p></details>)}</div>
    </section>
  )
}
