import { useRef, useState, type KeyboardEvent } from 'react'
import { ArrowDown, ArrowRight, ArrowUpRight } from 'lucide-react'
import type { Editorial } from './editorial'
import type { Language } from './content'

const wide = '/media/harbor-wide.webp'
const turn = '/media/harbor-turn.webp'
const vertical = '/media/harbor-vertical.webp'
const bars = [24, 44, 29, 46, 31, 20, 54, 31, 50, 21, 29, 53, 70, 59, 37, 65, 89, 93, 52, 64, 97, 74, 51, 30, 47, 68, 51, 24, 42, 36, 22, 51, 66, 37, 29, 17, 27, 42, 22, 32]

function Wave({ label }: { label: string }) {
  return <div className="waveform" role="img" aria-label={label}>{bars.map((height, i) => <i key={i} className={i >= 13 && i < 27 ? 'selected' : undefined} style={{ height: `${height}%` }} />)}<span aria-hidden="true" /></div>
}

function Caption({ text }: { text: string }) {
  const words = text.split(' ')
  return <b className="frame-caption">{words.slice(0, -2).join(' ')} <mark>{words.slice(-2).join(' ')}</mark></b>
}

export function Hero({ t, join }: { t: Editorial; join: string }) {
  return (
    <section className="hero shell" id="produto" aria-labelledby="hero-heading" tabIndex={-1}>
      <p className="edition-label"><span />{t.hero.status}</p>
      <div className="hero-copy">
        <h1 id="hero-heading">{t.hero.title[0]} <span>{t.hero.title[1]}</span></h1>
        <div><p>{t.hero.detail}</p><div className="hero-actions"><a className="button primary" href="#acesso">{join}<ArrowUpRight size={17} aria-hidden="true" /></a><a className="text-link" href="#como-funciona">{t.hero.action}<ArrowDown size={16} aria-hidden="true" /></a></div></div>
      </div>
      <figure className="hero-stage">
        <div className="stage-label"><span>{t.hero.sample}</span><span>16:9 <ArrowRight size={16} aria-hidden="true" /> 9:16</span></div>
        <div className="stage-composition">
          <div className="stage-source">
            <img src={wide} srcSet={`/media/harbor-wide-small.webp 720w, ${wide} 1440w`}
              sizes="(max-width: 540px) 65vw, (max-width: 1450px) 73vw, 1030px" width="1440" height="810" fetchPriority="high" alt={t.hero.wideAlt} />
            <span className="frame-label">{t.hero.source}</span>
            <span aria-hidden="true" className="crop-guide" />
          </div>
          <div className="stage-output">
            <img src={vertical} width="450" height="800" alt={t.hero.closeAlt} />
            <span className="frame-label">{t.hero.output}</span>
            <Caption text={t.hero.caption} />
          </div>
        </div>
        <figcaption className="stage-timeline"><span className="timecode">01:24:18</span><Wave label={t.demo.wave} /><strong>{t.hero.signal}</strong></figcaption>
      </figure>
    </section>
  )
}

export function Workflow({ t, language }: { t: Editorial; language: Language }) {
  const [step, setStep] = useState(0)
  const [shot, setShot] = useState(0)
  const [captions, setCaptions] = useState(true)
  const tabs = useRef<(HTMLButtonElement | null)[]>([])
  const { demo, hero } = t

  function moveTab(event: KeyboardEvent<HTMLButtonElement>, current: number) {
    const count = demo.steps.length
    let next: number
    switch (event.key) {
      case 'ArrowDown': next = (current + 1) % count; break
      case 'ArrowUp': next = (current + count - 1) % count; break
      case 'Home': next = 0; break
      case 'End': next = count - 1; break
      default: return
    }
    event.preventDefault()
    setStep(next)
    tabs.current[next]?.focus()
  }

  return (
    <section className="workflow band-dark" id="como-funciona" aria-labelledby="workflow-heading" tabIndex={-1}>
      <div className="shell">
        <div className="section-head"><h2 id="workflow-heading">{demo.title}</h2><p>{demo.detail}</p></div>
        <div className="workflow-grid">
          <div className="workflow-tabs" role="tablist" aria-labelledby="workflow-heading" aria-orientation="vertical">
            {demo.steps.map((item, i) => (
              <button key={item.title} type="button" role="tab" id={`step-${i}`} aria-selected={step === i} aria-controls={`panel-${i}`}
                tabIndex={step === i ? 0 : -1} onClick={() => setStep(i)} onKeyDown={event => moveTab(event, i)} ref={element => { tabs.current[i] = element }}>
                <span className="timecode">0{i + 1}</span><strong>{item.title}</strong><ArrowRight size={18} aria-hidden="true" />
              </button>
            ))}
          </div>
          {demo.steps.map((item, i) => (
            <div key={i} hidden={step !== i} className="workflow-panel" id={`panel-${i}`} role="tabpanel" aria-labelledby={`step-${i}`} tabIndex={0}>
              {step === i ? <>
                <p className="workflow-detail">{item.detail}</p>
                {i === 0 ? (
                  <div className="monitor"><img src={wide} width="1440" height="810" loading="lazy" alt={hero.wideAlt} /><span className="frame-label">{hero.source}</span><div className="monitor-wave"><Wave label={demo.wave} /></div></div>
                ) : i === 1 ? (
                  <div className="shot-selection">
                    <img src={shot === 0 ? wide : turn} width="960" height="540" loading="lazy" alt={shot === 0 ? hero.wideAlt : hero.closeAlt} />
                    <div className="shot-buttons">{demo.shots.map((label, index) => <button className="shot-button" key={index} aria-pressed={shot === index} onClick={() => setShot(index)}><span className="timecode">{index === 0 ? '01:24:16' : '01:24:18'}</span>{label}</button>)}</div>
                  </div>
                ) : i === 2 ? (
                  <div className="vertical-editor">
                    <div className="portrait-preview"><img src={vertical} width="450" height="800" alt={hero.closeAlt} loading="lazy" />{captions ? <Caption text={hero.caption} /> : null}</div>
                    <div className="edit-options"><p>{demo.format} <strong>9:16</strong></p><label><input type="checkbox" checked={captions} onChange={event => setCaptions(event.target.checked)} />{demo.captions}</label></div>
                  </div>
                ) : (
                  <div className="vertical-editor">
                    <video className="portrait-preview" width="450" height="800" controls playsInline preload="metadata" poster={vertical} aria-label={hero.output} aria-describedby="short-description">
                      <source src="/media/harbor-short.mp4" type="video/mp4" />
                      <track kind="captions" src={language === 'en' ? '/media/harbor-en.vtt' : '/media/harbor-pt.vtt'} srcLang={language} label={demo.captionsTrack} default />
                    </video>
                    <div className="edit-options" id="short-description"><strong>{demo.transcript}</strong><p>{demo.transcriptDetail}</p></div>
                  </div>
                )}
              </> : null}
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
