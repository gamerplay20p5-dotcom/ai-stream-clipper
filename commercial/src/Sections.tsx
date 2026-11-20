import { ArrowRight, Plus, Trash2 } from 'lucide-react'
import type { Editorial } from './editorial'

// Event ticks per signal lane (fractions of the width); they converge on the selected window.
const lanes = [
  [0.08, 0.21, 0.36, 0.55, 0.6, 0.64, 0.83],
  [0.14, 0.29, 0.53, 0.57, 0.62, 0.66, 0.9],
  [0.05, 0.42, 0.56, 0.61, 0.75],
]
const selected = [0.51, 0.69]

export function Features({ t }: { t: Editorial }) {
  const { signals, framing, library, export: exporting } = t.features
  return (
    <section id="recursos" className="features section-space" aria-labelledby="features-heading" tabIndex={-1}>
      <div className="shell">
        <div className="section-head">
          <p className="marker">{t.features.marker}</p>
          <h2 id="features-heading">{t.features.title}</h2>
          <p className="lede">{t.features.detail}</p>
        </div>
        <div className="feature-grid">
          <article className="feature feature-signals">
            <div className="feature-copy"><h3>{signals.title}</h3><p>{signals.detail}</p></div>
            <figure className="signal-board" aria-label={`${signals.lanes.join(', ')}: ${signals.selected}`}>
              <div className="signal-tracks">
                <span className="signal-window" style={{ left: `${selected[0] * 100}%`, width: `${(selected[1] - selected[0]) * 100}%` }}><em>{signals.selected}</em></span>
                {signals.lanes.map((lane, index) => (
                  <div className="signal-lane" key={lane}>
                    <span>{lane}</span>
                    <div aria-hidden="true">{lanes[index].map(position => <i key={position} className={position >= selected[0] && position <= selected[1] ? 'is-hit' : undefined} style={{ left: `${position * 100}%` }} />)}</div>
                  </div>
                ))}
              </div>
            </figure>
          </article>

          <article className="feature feature-framing">
            <figure className="framing-pair" role="img" aria-label={framing.alt}>
              <span className="framing-source"><img src="/media/moment-turn.webp" width="640" height="360" alt="" loading="lazy" /><i /></span>
              <ArrowRight size={18} aria-hidden="true" />
              <span className="framing-output"><img src="/media/short-turn.webp" width="450" height="800" alt="" loading="lazy" /><b>{framing.caption}</b></span>
            </figure>
            <div className="feature-copy"><h3>{framing.title}</h3><p>{framing.detail}</p></div>
          </article>

          <article className="feature feature-library">
            <div className="feature-copy"><h3>{library.title}</h3><p>{library.detail}</p></div>
            <div className="library-panel">
              <ul role="list">
                {library.rows.map(([title, status, score]) => (
                  <li key={title}><span className="library-score">{score}</span><strong>{title}</strong><em>{status}</em></li>
                ))}
              </ul>
              <div className="storage">
                <span>{library.storage}</span><span className="storage-value">{library.storageValue}</span>
                <span className="storage-bar" aria-hidden="true"><i style={{ width: '62%' }} /></span>
              </div>
              <p className="cleanup-rule"><Trash2 size={15} aria-hidden="true" />{library.rule}</p>
              <p className="sample-note">{library.example}</p>
            </div>
          </article>

          <article className="feature feature-export">
            <div className="feature-copy"><h3>{exporting.title}</h3><p>{exporting.detail}</p></div>
            <dl className="format-list">
              {exporting.formats.map(([format, plan]) => <div key={format} className={format === '4K' ? 'is-pro' : undefined}><dt>{format}</dt><dd>{plan}</dd></div>)}
            </dl>
            <div className="platforms">
              <span>{exporting.platforms}</span>
              <p translate="no"><b>Twitch</b><b>YouTube</b></p>
              <small>{exporting.platformsSoon}</small>
            </div>
          </article>
        </div>
      </div>
    </section>
  )
}

export function Questions({ t }: { t: Editorial['faq'] }) {
  return (
    <section id="faq" className="faq section-space" aria-labelledby="faq-heading" tabIndex={-1}>
      <div className="shell faq-layout">
        <div className="section-head">
          <p className="marker">{t.marker}</p>
          <h2 id="faq-heading">{t.title}</h2>
          <p className="lede">{t.detail}</p>
        </div>
        <div className="faq-list">
          {t.items.map(([question, answer]) => (
            <details key={question}><summary>{question}<Plus size={18} aria-hidden="true" /></summary><p>{answer}</p></details>
          ))}
        </div>
      </div>
    </section>
  )
}
