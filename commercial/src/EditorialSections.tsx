import { ArrowRight, AudioLines, Captions, Clapperboard, Film, Plus, Radio } from 'lucide-react'
import type { Editorial } from './editorial'

const signalIcons = [Film, AudioLines, Captions]

export function Features({ t }: { t: Editorial }) {
  const { features, demo } = t
  const [monitor, multimodal, context, vertical, library, exportVideo] = features.items

  return (
    <section id="recursos" className="features shell section-space" aria-labelledby="features-heading" tabIndex={-1}>
      <div className="section-head"><h2 id="features-heading">{features.title}</h2><p>{features.detail}</p></div>
      <div className="feature-grid">
        <article className="feature-card signals-feature">
          <h3>{multimodal[0]}</h3><p>{multimodal[1]}</p>
          <figure className="signal-monitor">
            <figcaption><span>{features.signalTitle}</span><strong>{features.selection}</strong></figcaption>
            <ul role="list">
              {demo.sources.map((name, index) => {
                const Icon = signalIcons[index]
                return <li key={name}><span><Icon size={16} aria-hidden="true" />{name}</span><div className={`signal-strip signal-${index}`} aria-hidden="true"><i /><i /><i /><i /><i /></div></li>
              })}
            </ul>
          </figure>
          <div className="feature-context"><h4>{context[0]}</h4><p>{context[1]}</p></div>
        </article>
        <article className="feature-card vertical-feature">
          <div className="format-diagram" role="img" aria-label={features.cropped}><span>16:9</span><ArrowRight size={19} aria-hidden="true" /><span>9:16<small aria-hidden="true">CC</small></span></div>
          <h3>{vertical[0]}</h3><p>{vertical[1]}</p>
        </article>
        <article className="feature-card monitoring-feature">
          <Radio className="feature-icon" size={25} aria-hidden="true" />
          <h3>{monitor[0]}</h3><p>{monitor[1]}</p>
          <div className="platform-tags" translate="no"><span>Twitch</span><span>YouTube</span></div>
        </article>
        <article className="feature-card library-feature">
          <div className="library-copy"><Clapperboard className="feature-icon" size={25} aria-hidden="true" /><h3>{library[0]}</h3><p>{library[1]}</p></div>
          <div className="export-specs">
            <h4>{exportVideo[0]}</h4>
            <dl><div><dt>{features.standard}</dt><dd>1080p</dd></div><div><dt>{features.proOnly}</dt><dd>4K</dd></div></dl>
            <p>{exportVideo[1]}</p>
          </div>
        </article>
      </div>
    </section>
  )
}

export function Questions({ t }: { t: Editorial['faq'] }) {
  return (
    <section id="faq" className="faq shell section-space" aria-labelledby="faq-heading" tabIndex={-1}>
      <div><h2 id="faq-heading">{t.title}</h2></div>
      <div className="faq-list">{t.items.map(([question, answer]) => <details key={question}><summary>{question}<Plus size={19} aria-hidden="true" /></summary><p>{answer}</p></details>)}</div>
    </section>
  )
}
