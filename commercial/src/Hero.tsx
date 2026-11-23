import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { ArrowUpRight, Check, Clock3, Gamepad2, Layers, Pause, Play, Sparkles, Zap } from 'lucide-react'
import type { Editorial } from './editorial'
import { reducedMotion } from './motion'

// Deterministic pseudo-random sequence so the server-rendered and hydrated scene are identical.
function seeded(seed: number) {
  let value = seed
  return () => {
    value = (value * 1664525 + 1013904223) % 4294967296
    return value / 4294967296
  }
}

// The skyline is two <path> elements (towers and lit windows) instead of hundreds of nodes: lighter to hydrate.
const skyline = (() => {
  const random = seeded(7)
  let towers = ''
  let windows = ''
  let x = 560
  while (x < 1640) {
    const w = 26 + random() * 46
    const nearCenter = Math.exp(-(((x - 1180) / 260) ** 2))
    const h = 70 + random() * 120 + nearCenter * 210
    towers += `M${x.toFixed(1)} 640v${(-h).toFixed(1)}h${w.toFixed(1)}v${h.toFixed(1)}z`
    for (let wy = 640 - h + 10; wy < 628; wy += 9) {
      for (let wx = x + 5; wx < x + w - 5; wx += 8) if (random() > 0.83) windows += `M${wx.toFixed(1)} ${wy.toFixed(1)}h3v4h-3z`
    }
    x += w + 2 + random() * 6
  }
  return { towers, windows }
})()

function Palm({ x, height, lean, scale = 1 }: { x: number; height: number; lean: number; scale?: number }) {
  const top = { x: x + lean, y: 900 - height }
  const fronds = [-168, -140, -112, -70, -40, -12, 18].map((angle, index) => {
    const radians = (angle * Math.PI) / 180
    const length = (120 + (index % 3) * 26) * scale
    const end = { x: top.x + Math.cos(radians) * length, y: top.y + Math.sin(radians) * length * 0.55 + length * 0.42 }
    const control = { x: top.x + Math.cos(radians) * length * 0.55, y: top.y - 34 * scale + Math.sin(radians) * 20 }
    return `M${top.x} ${top.y} Q${control.x} ${control.y} ${end.x} ${end.y}`
  }).join(' ')
  return (
    <g>
      <path d={`M${x - 9 * scale} 900 Q${x + lean * 0.2} ${900 - height * 0.55} ${top.x - 3} ${top.y} L${top.x + 3} ${top.y} Q${x + lean * 0.25 + 6} ${900 - height * 0.55} ${x + 9 * scale} 900 Z`} />
      <path d={fronds} fill="none" stroke="currentColor" strokeWidth={9 * scale} strokeLinecap="round" />
      <path d={fronds} fill="none" stroke="currentColor" strokeWidth={22 * scale} strokeLinecap="round" strokeDasharray={`2 ${12 * scale}`} opacity=".9" />
    </g>
  )
}

/** Original vector dusk scene. Layers move a little with the scroll ([data-depth], see motion.ts). */
function HeroScene() {
  return (
    <div className="hero-scene" aria-hidden="true">
      <svg viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice" focusable="false">
        <defs>
          <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#05060b" />
            <stop offset=".24" stopColor="#1b0d36" />
            <stop offset=".46" stopColor="#5a1856" />
            <stop offset=".6" stopColor="#a42a68" />
            <stop offset=".68" stopColor="#ff5466" />
            <stop offset=".72" stopColor="#ff9a72" />
          </linearGradient>
          <radialGradient id="sun" cx=".5" cy=".5" r=".5">
            <stop offset="0" stopColor="#ffd9a8" />
            <stop offset=".55" stopColor="#ff7a6a" />
            <stop offset="1" stopColor="#ff4f62" stopOpacity="0" />
          </radialGradient>
          <linearGradient id="sea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#5a1c46" />
            <stop offset=".35" stopColor="#1d0e24" />
            <stop offset="1" stopColor="#05060b" />
          </linearGradient>
        </defs>
        <rect width="1600" height="900" fill="url(#sky)" />
        <g className="scene-layer" data-depth="0.08">
          <circle cx="1210" cy="610" r="150" fill="url(#sun)" opacity=".85" />
        </g>
        <rect y="640" width="1600" height="260" fill="url(#sea)" />
        <g className="scene-layer" data-depth="0.04">
          <path d={skyline.towers} fill="#120a1b" />
          <path d={skyline.windows} fill="#ff9fb0" opacity=".5" />
        </g>
        <g className="scene-layer" data-depth="-0.05" color="#05060b" fill="#05060b">
          <Palm x={70} height={560} lean={60} scale={1.15} />
          <Palm x={250} height={440} lean={-30} />
          <Palm x={1560} height={380} lean={-50} scale={.8} />
        </g>
      </svg>
    </div>
  )
}

// Signal ticks per lane (fractions of the width); they converge on the detected window
const lanes = [
  [0.07, 0.19, 0.33, 0.55, 0.59, 0.63, 0.82, 0.93],
  [0.13, 0.27, 0.41, 0.54, 0.58, 0.65, 0.88],
  [0.04, 0.22, 0.47, 0.57, 0.61, 0.66, 0.76],
]
const moment: [number, number] = [0.52, 0.69]
// How long each step stays on screen (ms)
const durations = [1900, 2300, 1700, 1700, 2100, 3600]
const LAST = durations.length - 1

/**
 * The hero's demonstration: a live becomes a Short in six coordinated states. Illustrative (original images, no
 * real analysis). It plays only while visible, can be paused and stepped by hand, and with reduced motion it opens on
 * the finished state without autoplay.
 */
function Engine({ t }: { t: Editorial['hero'] }) {
  const e = t.engine
  const [stage, setStage] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [visible, setVisible] = useState(true)
  const figure = useRef<HTMLElement>(null)

  useEffect(() => {
    if (reducedMotion()) {
      setStage(LAST)
      setPlaying(false)
    }
    const node = figure.current
    if (!node || !('IntersectionObserver' in window)) return
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { threshold: 0.25 })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!playing || !visible) return
    const timer = window.setTimeout(() => {
      if (document.visibilityState === 'hidden') return
      setStage(current => (current + 1) % durations.length)
    }, durations[stage])
    return () => window.clearTimeout(timer)
  }, [stage, playing, visible])

  const inMoment = (position: number) => position >= moment[0] && position <= moment[1]
  return (
    <figure ref={figure} className="engine" data-stage={stage} aria-label={e.label}>
      <div className="engine-stage">
      <div className="engine-screen">
        <div className="engine-bar">
          <span className="live-badge"><i aria-hidden="true" />{t.live}</span>
          <span className="engine-title">{t.liveTitle}</span>
          <span className="timecode">02:14:{String(18 + stage * 2).padStart(2, '0')}</span>
        </div>
        <div className="engine-video">
          <img src="/media/live-wide.webp" srcSet="/media/live-wide-small.webp 640w, /media/live-wide.webp 1280w"
            sizes="(max-width: 760px) 92vw, (max-width: 1200px) 60vw, 46vw" width="1280" height="720" alt="" fetchPriority="high" />
          <span className="engine-scan" aria-hidden="true" />
          <span className="engine-crop" aria-hidden="true"><em>{e.crop}</em></span>
        </div>
        <div className="engine-timeline" aria-hidden="true">
          <span className="engine-window" style={{ left: `${moment[0] * 100}%`, width: `${(moment[1] - moment[0]) * 100}%` }}>
            <em><Sparkles size={12} />{e.moment}<span className="engine-score"> · {e.score} 92</span></em>
          </span>
          {e.lanes.map((lane, index) => (
            <div className="engine-lane" key={lane}>
              <span>{lane}</span>
              <div>{lanes[index].map(position => (
                <i key={position} className={inMoment(position) ? 'is-hit' : undefined} style={{ left: `${position * 100}%`, '--at': position } as CSSProperties} />
              ))}</div>
            </div>
          ))}
          <span className="engine-playhead" />
        </div>
      </div>
      <div className="engine-short" aria-hidden="true">
        <img src="/media/short-vertical.webp" width="450" height="800" alt="" />
        <b className="short-caption">{t.caption}</b>
        <span className="engine-ready"><Check size={13} />{e.ready}</span>
      </div>
      </div>
      <div className="engine-controls">
        <ol className="engine-steps" aria-label={e.stepLabel}>
          {e.steps.map((label, index) => (
            <li key={label}>
              <button type="button" aria-current={stage === index ? 'step' : undefined} data-done={index < stage || undefined}
                onClick={() => { setStage(index); setPlaying(false) }}>
                <span className="step-dot" aria-hidden="true">{index + 1}</span><span className="step-label">{label}</span>
              </button>
            </li>
          ))}
        </ol>
        <button type="button" className="engine-toggle" aria-pressed={!playing} aria-label={playing ? e.pause : e.play} onClick={() => setPlaying(value => !value)}>
          {playing ? <Pause size={15} aria-hidden="true" /> : <Play size={15} aria-hidden="true" />}
        </button>
      </div>
      <p className="sr-only" aria-live="polite">{playing ? '' : e.states[stage]}</p>
      <figcaption>{t.sample}</figcaption>
    </figure>
  )
}

const benefitIcons = [Zap, Layers, null, Clock3]

export function Hero({ t, join }: { t: Editorial; join: string }) {
  return (
    <section className="hero" id="produto" aria-labelledby="hero-heading" tabIndex={-1}>
      <HeroScene />
      <div className="hero-grid shell">
        <div className="hero-copy">
          <p className="hero-status"><Gamepad2 size={15} aria-hidden="true" />{t.hero.status}</p>
          <h1 id="hero-heading"><span>{t.hero.title[0]}</span> <span>{t.hero.title[1]}</span></h1>
          <p className="hero-lede">{t.hero.detail}</p>
          <div className="hero-actions">
            <a className="button primary" href="#acesso">{join}<ArrowUpRight size={17} aria-hidden="true" /></a>
            <a className="button ghost" href="#como-funciona"><span className="play-dot"><Play size={12} fill="currentColor" aria-hidden="true" /></span>{t.hero.action}</a>
          </div>
        </div>
        <Engine t={t.hero} />
      </div>
      <ul className="benefits shell" role="list" data-reveal>
        {t.benefits.map(([title, detail], index) => {
          const Icon = benefitIcons[index]
          return (
            <li key={title} data-reveal-item>
              {Icon ? <Icon size={20} aria-hidden="true" /> : <span className="hd-mark" aria-hidden="true">HD</span>}
              <p><strong>{title}</strong><span>{detail}</span></p>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
