import { useRef, useState, type KeyboardEvent } from 'react'
import { Activity, ArrowUpRight, Clapperboard, Play, Radio, ScanLine, Scissors } from 'lucide-react'
import type { Editorial } from './editorial'
import type { Language } from './content'

const source = '/media/night-source.webp'
const close = '/media/night-turn.webp'
const portrait = '/media/night-vertical.webp'
const phases = [Radio, Activity, ScanLine, Scissors, Clapperboard]
const wave = [24, 39, 30, 52, 35, 20, 57, 36, 49, 30, 28, 53, 74, 56, 39, 61, 85, 96, 55, 68, 96, 72, 52, 28, 45, 62, 49, 24, 42, 37, 22, 57, 62, 34, 27, 18, 25, 41, 22, 31, 21, 32, 22, 28]

function Timeline({ t, selected }: { t: Editorial; selected: boolean }) {
  return (
    <div className={`console-timeline ${selected ? 'has-clip' : ''}`}>
      <div className="timeline-ruler timecode" aria-hidden="true"><span>01:24:12</span><span>01:24:18</span><span>01:24:24</span></div>
      <div className="timeline-track"><span className="track-name">{t.demo.audioTrack}</span><div className="waveform" role="img" aria-label={t.demo.wave}><i className="playhead" aria-hidden="true" />{wave.map((height, index) => <i key={index} className={index >= 13 && index < 29 ? 'wave-cut' : undefined} style={{ height: `${height}%` }} aria-hidden="true" />)}</div></div>
      <div className="timeline-track"><span className="track-name">{t.demo.clipTrack}</span><div className="range-track"><span>{t.hero.signal}</span></div></div>
    </div>
  )
}

function Caption({ text }: { text: string }) {
  const words = text.split(' ')
  return <b className="frame-caption">{words.slice(0, -2).join(' ')} <mark>{words.slice(-2).join(' ')}</mark></b>
}

function BroadcastConsole({ t, language }: { t: Editorial; language: Language }) {
  const [step, setStep] = useState(0)
  const [shot, setShot] = useState(0)
  const [captions, setCaptions] = useState(true)
  const tabs = useRef<(HTMLButtonElement | null)[]>([])
  const { demo, hero } = t

  function activate(index: number) {
    setStep(index)
    tabs.current[index]?.focus()
  }

  function moveTab(event: KeyboardEvent<HTMLButtonElement>, current: number) {
    const count = demo.steps.length
    let next: number
    switch (event.key) {
      case 'ArrowRight': next = (current + 1) % count; break
      case 'ArrowLeft': next = (current + count - 1) % count; break
      case 'Home': next = 0; break
      case 'End': next = count - 1; break
      default: return
    }
    event.preventDefault()
    activate(next)
  }

  const isClose = step > 2 || (step === 2 && shot === 1)
  return (
    <section className="broadcast-console" id="estudio" aria-labelledby="studio-heading" tabIndex={-1}>
      <header className="console-head">
        <h2 id="studio-heading"><img src="/brand.svg" alt="" width="22" height="22" />{demo.studio}</h2>
        <span>{hero.sample}</span>
      </header>
      <div className="console-tabs" role="tablist" aria-label={demo.stepsLabel}>
        {demo.steps.map((item, index) => {
          const Icon = phases[index]
          return <button key={index} type="button" role="tab" id={`step-${index}`} aria-selected={step === index} aria-controls={`panel-${index}`}
            tabIndex={step === index ? 0 : -1} onClick={() => setStep(index)} onKeyDown={event => moveTab(event, index)}
            ref={element => { tabs.current[index] = element }}><Icon size={15} aria-hidden="true" />{item.title}</button>
        })}
      </div>
      {demo.steps.map((item, index) => (
        <div key={index} id={`panel-${index}`} className="console-panel" role="tabpanel" aria-labelledby={`step-${index}`} hidden={step !== index} tabIndex={0}>
          {step === index ? <>
            <div className={`console-monitors ${step === 4 ? 'review-monitors' : ''}`}>
              <div className="input-monitor">
                <div className="monitor-title"><span><i />{hero.source}</span><span className="timecode">16:9</span></div>
                <div className="monitor-scene">
                  {isClose ? <img src={close} width="960" height="540" alt={hero.closeAlt} /> :
                    <img src={source} srcSet={`/media/night-source-small.webp 640w, ${source} 1280w`}
                      sizes="(max-width: 379px) 52vw, (max-width: 820px) 62vw, 42vw"
                      width="1280" height="720" fetchPriority={step === 0 ? 'high' : 'auto'} alt={hero.wideAlt} />}
                  {step === 1 || step === 3 ? <span className={`monitor-guide ${step === 3 ? 'vertical-guide' : ''}`} aria-hidden="true" /> : null}
                </div>
                <Timeline t={t} selected={step !== 1} />
              </div>
              <div className="output-monitor">
                <div className="monitor-title"><span>{hero.output}</span><span className="timecode">9:16</span></div>
                {step === 4 ? (
                  <video width="450" height="800" controls playsInline preload="metadata" poster={portrait} aria-label={hero.output} aria-describedby="short-description">
                    <source src="/media/night-short.mp4" type="video/mp4" />
                    <track kind="captions" src={language === 'en' ? '/media/night-en.vtt' : '/media/night-pt.vtt'} srcLang={language} label={demo.captionsTrack} default />
                  </video>
                ) : (
                  <div className="output-image">
                    <img src={portrait} width="450" height="800" alt={hero.closeAlt} />
                    {step !== 3 || captions ? <Caption text={hero.caption} /> : null}
                    {step === 0 ? <button className="poster-play" type="button" aria-label={demo.openShort} onClick={() => activate(4)}><Play size={17} aria-hidden="true" /></button> : null}
                  </div>
                )}
              </div>
            </div>
            <div className="console-detail">
              <p>{item.detail}</p>
              {step === 1 ? <ul className="console-signals">{demo.sources.map(label => <li key={label}>{label}</li>)}</ul> : null}
              {step === 2 ? <div className="shot-buttons">{demo.shots.map((label, shotIndex) => <button className="shot-button" type="button" key={shotIndex} aria-pressed={shot === shotIndex} onClick={() => setShot(shotIndex)}>{label}</button>)}</div> : null}
              {step === 3 ? <label className="caption-toggle"><input type="checkbox" checked={captions} onChange={event => setCaptions(event.target.checked)} />{demo.captions}</label> : null}
              {step === 4 ? <details id="short-description"><summary>{demo.transcript}</summary><p>{demo.transcriptDetail}</p></details> : null}
            </div>
          </> : null}
        </div>
      ))}
    </section>
  )
}

export function Hero({ t, join, language }: { t: Editorial; join: string; language: Language }) {
  return (
    <section className="hero" id="produto" aria-labelledby="hero-heading" tabIndex={-1}>
      <div className="hero-grid shell">
        <div className="hero-copy">
          <p className="edition-label"><Radio size={15} aria-hidden="true" />{t.hero.status}</p>
          <h1 id="hero-heading">{t.hero.title[0]} <span>{t.hero.title[1]}</span></h1>
          <p className="hero-description">{t.hero.detail}</p>
          <div className="hero-actions">
            <a className="button primary" href="#acesso">{join}<ArrowUpRight size={17} aria-hidden="true" /></a>
            <a className="button quiet" href="#estudio"><Play size={16} aria-hidden="true" />{t.hero.action}</a>
          </div>
        </div>
        <BroadcastConsole t={t} language={language} />
      </div>
    </section>
  )
}

export function Workflow({ t }: { t: Editorial }) {
  return (
    <section className="workflow section-space" id="como-funciona" aria-labelledby="workflow-heading" tabIndex={-1}>
      <div className="shell workflow-layout">
        <div className="section-head">
          <h2 id="workflow-heading">{t.demo.title}</h2><p>{t.demo.detail}</p>
          <a className="text-link" href="#estudio">{t.demo.explore}<ArrowUpRight size={16} aria-hidden="true" /></a>
        </div>
        <ol className="workflow-sequence" role="list">
          {t.demo.steps.map((item, index) => {
            const Icon = phases[index]
            return <li key={index}><span className="sequence-icon"><Icon size={19} aria-hidden="true" /></span><div><h3>{item.title}</h3><p>{item.detail}</p></div></li>
          })}
        </ol>
      </div>
    </section>
  )
}
