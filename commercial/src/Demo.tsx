import { useRef, useState, type KeyboardEvent } from 'react'
import { Check, ChevronDown, Clapperboard, Radio, ScanSearch, Scissors, X } from 'lucide-react'
import type { Language } from './content'
import type { Editorial } from './editorial'
import { Waveform } from './Waveform'

const stepIcons = [Radio, ScanSearch, Scissors, Clapperboard]
const momentImages = { live: '/media/live-wide-small.webp', chase: '/media/moment-chase.webp', palms: '/media/moment-palms.webp' } as const
// Where each detected moment sits on the illustrative timeline (fractions of the width).
const momentRanges: [number, number][] = [[0.52, 0.7], [0.3, 0.44], [0.08, 0.2]]
const cropPositions = [22, 50, 78]
type Review = 'pending' | 'approved' | 'discarded'

function MonitorStep({ t }: { t: Editorial['demo'] }) {
  return (
    <div className="stage-grid">
      <div className="stage-video">
        <div className="stage-bar"><span className="live-badge">{t.monitor.channels[0].state}</span><span>{t.monitor.channels[0].name}</span><span className="timecode">02:14:36</span></div>
        <img src="/media/live-wide.webp" srcSet="/media/live-wide-small.webp 640w, /media/live-wide.webp 1280w" sizes="(max-width: 900px) 92vw, 44vw" width="1280" height="720" alt="" loading="lazy" />
        <div className="audio-meter" aria-hidden="true"><span>{t.monitor.audio}</span><i /><i /><i /><i /><i /><i className="is-dim" /><i className="is-dim" /></div>
      </div>
      <div className="stage-side">
        <h4>{t.monitor.heading}</h4>
        <ul className="channel-list">
          {t.monitor.channels.map(channel => (
            <li key={channel.name}>
              <span className={`channel-dot ${channel.state === t.monitor.channels[0].state ? 'is-live' : ''}`} aria-hidden="true" />
              <span><strong>{channel.name}</strong><small translate="no">{channel.platform}</small></span>
              <em>{channel.state}</em>
            </li>
          ))}
        </ul>
        <p className="stage-note">{t.monitor.note}</p>
      </div>
    </div>
  )
}

function AnalyzeStep({ t }: { t: Editorial['demo'] }) {
  const [moment, setMoment] = useState(0)
  const active = t.analyze.moments[moment]
  return (
    <div className="stage-grid">
      <div className="stage-video">
        <div className="stage-bar"><span className="detect-chip">{active.title}</span><span className="timecode">{active.time}</span></div>
        <img src={momentImages[active.image as keyof typeof momentImages]} width="640" height="360" alt="" />
        <Waveform className="stage-wave" selection={momentRanges[moment]} markers={momentRanges.map(([start, end]) => (start + end) / 2)} activeMarker={moment} label={t.analyze.timeline} />
        <ul className="signal-legend" aria-label={t.analyze.signals.join(', ')}>{t.analyze.signals.map(signal => <li key={signal}>{signal}</li>)}</ul>
      </div>
      <div className="stage-side">
        <h4>{t.analyze.heading}</h4>
        <p className="stage-note" id="moment-hint">{t.analyze.hint}</p>
        <div className="moment-picker" role="radiogroup" aria-labelledby="moment-hint">
          {t.analyze.moments.map((item, index) => (
            <button key={item.title} type="button" role="radio" aria-checked={moment === index} tabIndex={moment === index ? 0 : -1}
              onClick={() => setMoment(index)}
              onKeyDown={event => {
                if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return
                event.preventDefault()
                const next = (index + (event.key === 'ArrowDown' || event.key === 'ArrowRight' ? 1 : -1) + t.analyze.moments.length) % t.analyze.moments.length
                setMoment(next)
                ;(event.currentTarget.parentElement?.children[next] as HTMLElement | undefined)?.focus()
              }}>
              <img src={momentImages[item.image as keyof typeof momentImages]} width="640" height="360" alt="" loading="lazy" />
              <span><strong>{item.title}</strong><small className="timecode">{item.time}</small></span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function EditStep({ t, caption }: { t: Editorial['demo']; caption: string }) {
  const [position, setPosition] = useState(1)
  const [captions, setCaptions] = useState(true)
  const offset = cropPositions[position]
  return (
    <div className="stage-grid edit-grid">
      <div className="stage-video">
        <div className="stage-bar"><span>{t.edit.source}</span><span className="timecode">16:9</span></div>
        <div className="crop-frame">
          <img src="/media/live-wide.webp" width="1280" height="720" alt="" loading="lazy" />
          <span className="crop-guide" style={{ left: `calc((100% - var(--crop-w)) * ${offset / 100})` }} aria-hidden="true" />
        </div>
        <fieldset className="crop-controls">
          <legend>{t.edit.framing}</legend>
          {t.edit.positions.map((label, index) => (
            <label key={label}><input type="radio" name="crop-position" checked={position === index} onChange={() => setPosition(index)} />{label}</label>
          ))}
        </fieldset>
        <label className="switch"><input type="checkbox" checked={captions} onChange={event => setCaptions(event.target.checked)} /><span aria-hidden="true" />{t.edit.captions}</label>
      </div>
      <div className="vertical-preview">
        <div className="stage-bar"><span>{t.edit.output}</span><span className="timecode">9:16</span></div>
        <div className="vertical-frame">
          <img src="/media/live-wide.webp" width="1280" height="720" alt="" loading="lazy" style={{ objectPosition: `${offset}% 50%` }} />
          {captions ? <b className="short-caption">{caption}</b> : null}
        </div>
      </div>
    </div>
  )
}

function ReviewStep({ t, language }: { t: Editorial['demo']; language: Language }) {
  const [review, setReview] = useState<Review>('pending')
  return (
    <div className="stage-grid review-grid">
      <div className="vertical-preview">
        <div className="stage-bar"><span>{t.review.heading}</span><span className="timecode">0:05</span></div>
        <video className="vertical-frame" width="450" height="800" controls playsInline preload="metadata" poster="/media/short-vertical.webp" aria-describedby="short-description">
          <source src="/media/short-demo.mp4" type="video/mp4" />
          <track kind="captions" src={language === 'en' ? '/media/short-en.vtt' : '/media/short-pt.vtt'} srcLang={language} label={t.review.captionsTrack} default />
        </video>
      </div>
      <div className="stage-side">
        <h4>{t.review.heading}</h4>
        <p className={`review-state is-${review}`} role="status">{t.review[review]}</p>
        <div className="review-actions">
          <button type="button" className="button primary" aria-pressed={review === 'approved'} onClick={() => setReview('approved')}><Check size={16} aria-hidden="true" />{t.review.approve}</button>
          <button type="button" className="button ghost" aria-pressed={review === 'discarded'} onClick={() => setReview('discarded')}><X size={16} aria-hidden="true" />{t.review.discard}</button>
        </div>
        <details id="short-description"><summary>{t.review.transcript}<ChevronDown size={16} aria-hidden="true" /></summary><p>{t.review.transcriptDetail}</p></details>
      </div>
    </div>
  )
}

export function Demo({ t, language, caption }: { t: Editorial; language: Language; caption: string }) {
  const [step, setStep] = useState(0)
  const tabs = useRef<(HTMLButtonElement | null)[]>([])
  const { demo } = t

  function moveTab(event: KeyboardEvent<HTMLButtonElement>, current: number) {
    const count = demo.steps.length
    const keys: Record<string, number> = { ArrowDown: current + 1, ArrowRight: current + 1, ArrowUp: current - 1, ArrowLeft: current - 1, Home: 0, End: count - 1 }
    if (!(event.key in keys)) return
    event.preventDefault()
    const next = (keys[event.key] + count) % count
    setStep(next)
    tabs.current[next]?.focus()
  }

  return (
    <section className="demo section-space" id="como-funciona" aria-labelledby="demo-heading" tabIndex={-1}>
      <div className="shell demo-layout">
        <div className="demo-intro">
          <p className="marker">{demo.marker}</p>
          <h2 id="demo-heading">{demo.title}</h2>
          <p className="lede">{demo.detail}</p>
          <div className="step-list" role="tablist" aria-label={demo.stepsLabel} aria-orientation="vertical">
            {demo.steps.map((item, index) => {
              const Icon = stepIcons[index]
              return (
                <button key={item.title} type="button" role="tab" id={`step-${index}`} aria-selected={step === index} aria-controls={`stage-${index}`}
                  tabIndex={step === index ? 0 : -1} onClick={() => setStep(index)} onKeyDown={event => moveTab(event, index)}
                  ref={element => { tabs.current[index] = element }}>
                  <span className="step-icon"><Icon size={20} aria-hidden="true" /></span>
                  <span className="step-text"><strong>{index + 1}. {item.title}</strong><span>{item.detail}</span></span>
                </button>
              )
            })}
          </div>
        </div>
        <div className="stage" id="demo-stage">
          <div className="stage-head"><span><img src="/brand.svg" width="18" height="18" alt="" />AI Stream Clipper</span><span>{demo.sample}</span></div>
          {demo.steps.map((item, index) => (
            <div key={item.title} id={`stage-${index}`} role="tabpanel" aria-labelledby={`step-${index}`} hidden={step !== index} className="stage-panel">
              {step === index ? (
                index === 0 ? <MonitorStep t={demo} />
                  : index === 1 ? <AnalyzeStep t={demo} />
                    : index === 2 ? <EditStep t={demo} caption={caption} />
                      : <ReviewStep t={demo} language={language} />
              ) : null}
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
