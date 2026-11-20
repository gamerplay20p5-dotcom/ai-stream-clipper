import { ArrowUpRight, Clock3, Compass, Flame, Gamepad2, Laugh, Layers, Library, Play, Radio, Scissors, Settings, Siren, Zap } from 'lucide-react'
import type { Editorial } from './editorial'
import { Waveform } from './Waveform'

// Deterministic pseudo-random sequence so the server-rendered and hydrated scene are identical.
function seeded(seed: number) {
  let value = seed
  return () => {
    value = (value * 1664525 + 1013904223) % 4294967296
    return value / 4294967296
  }
}

const skyline = (() => {
  const random = seeded(7)
  const towers: { x: number; w: number; h: number }[] = []
  let x = 560
  while (x < 1640) {
    const w = 26 + random() * 46
    const nearCenter = Math.exp(-(((x - 1180) / 260) ** 2))
    const h = 70 + random() * 120 + nearCenter * 210
    towers.push({ x, w, h })
    x += w + 2 + random() * 6
  }
  const windows: { x: number; y: number }[] = []
  for (const tower of towers) {
    for (let wy = 640 - tower.h + 10; wy < 628; wy += 9) {
      for (let wx = tower.x + 5; wx < tower.x + tower.w - 5; wx += 8) {
        if (random() > 0.83) windows.push({ x: wx, y: wy })
      }
    }
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
  })
  return (
    <g>
      <path d={`M${x - 9 * scale} 900 Q${x + lean * 0.2} ${900 - height * 0.55} ${top.x - 3} ${top.y} L${top.x + 3} ${top.y} Q${x + lean * 0.25 + 6} ${900 - height * 0.55} ${x + 9 * scale} 900 Z`} />
      {fronds.map(d => <path key={d} d={d} fill="none" stroke="currentColor" strokeWidth={9 * scale} strokeLinecap="round" />)}
      {fronds.map(d => <path key={`${d}-leaf`} d={d} fill="none" stroke="currentColor" strokeWidth={22 * scale} strokeLinecap="round" strokeDasharray={`2 ${12 * scale}`} opacity=".9" />)}
    </g>
  )
}

/** Original vector dusk scene: sky, sun, coastal skyline, palms and a helicopter. */
function HeroScene() {
  return (
    <svg className="hero-scene" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#0d0a1d" />
          <stop offset=".22" stopColor="#2a1046" />
          <stop offset=".44" stopColor="#6b1a5c" />
          <stop offset=".58" stopColor="#b7306a" />
          <stop offset=".67" stopColor="#ff5466" />
          <stop offset=".72" stopColor="#ff8a74" />
        </linearGradient>
        <radialGradient id="sun" cx=".5" cy=".5" r=".5">
          <stop offset="0" stopColor="#ffd0a8" />
          <stop offset=".55" stopColor="#ff7a6a" />
          <stop offset="1" stopColor="#ff4f62" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="sea" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#6a1f4c" />
          <stop offset=".35" stopColor="#24102a" />
          <stop offset="1" stopColor="#090b10" />
        </linearGradient>
        <filter id="haze" x="-20%" y="-200%" width="140%" height="500%"><feGaussianBlur stdDeviation="14" /></filter>
      </defs>
      <rect width="1600" height="900" fill="url(#sky)" />
      <g fill="#7640c8" opacity=".55" filter="url(#haze)">
        <ellipse cx="900" cy="180" rx="560" ry="40" />
        <ellipse cx="1400" cy="250" rx="320" ry="30" />
      </g>
      <g fill="#ff7a96" opacity=".45" filter="url(#haze)">
        <ellipse cx="1080" cy="345" rx="440" ry="22" />
        <ellipse cx="1360" cy="415" rx="300" ry="16" />
        <ellipse cx="760" cy="440" rx="300" ry="14" />
        <ellipse cx="1000" cy="500" rx="380" ry="12" />
      </g>
      <circle cx="1210" cy="610" r="150" fill="url(#sun)" opacity=".85" />
      <rect y="640" width="1600" height="260" fill="url(#sea)" />
      <g fill="#140b1d">
        {skyline.towers.map(({ x, w, h }) => <rect key={x} x={x} y={640 - h} width={w} height={h} />)}
      </g>
      <g fill="#ff9fb0" opacity=".55">
        {skyline.windows.map(({ x, y }) => <rect key={`${x}-${y}`} x={x} y={y} width="3" height="4" />)}
      </g>
      <g className="helicopter" fill="#120a1a" transform="translate(470 128) scale(1.15)">
        <ellipse cx="0" cy="0" rx="22" ry="9" />
        <path d="M-18 -2 L-62 -4 L-62 1 L-18 4 Z" />
        <rect x="-66" y="-10" width="4" height="12" />
        <rect x="-44" y="-15" width="88" height="2.5" rx="1" />
        <rect x="-2" y="-14" width="4" height="6" />
        <path d="M-14 9 L-16 14 M12 9 L14 14 M-20 14 H20" stroke="#120a1a" strokeWidth="2" />
      </g>
      <g color="#06050b" fill="#06050b">
        <Palm x={70} height={560} lean={60} scale={1.15} />
        <Palm x={250} height={440} lean={-30} />
        <Palm x={1560} height={380} lean={-50} scale={.8} />
      </g>
    </svg>
  )
}

const momentIcons = [Flame, Siren, Laugh, Compass]
const sideIcons = [Radio, Scissors, Library, Settings]
const momentScores = [92, 88, 81, 76]

function ProductPreview({ t }: { t: Editorial }) {
  const { hero } = t
  return (
    <figure className="hero-product" aria-label={hero.productLabel}>
      <div className="device" aria-hidden="true">
        <aside className="device-side">
          <span className="device-brand"><img src="/brand.svg" width="20" height="20" alt="" />AI Stream Clipper</span>
          <ul>{hero.sidebar.map((label, index) => {
            const Icon = sideIcons[index]
            return <li key={label} className={index === 0 ? 'is-active' : undefined}><Icon size={14} />{label}</li>
          })}</ul>
        </aside>
        <div className="device-main">
          <div className="device-bar">
            <span className="live-badge">{hero.live}</span>
            <span className="device-title">{hero.liveTitle}</span>
            <span className="timecode">2:14:36</span>
          </div>
          <div className="device-video">
            <img src="/media/live-wide.webp" srcSet="/media/live-wide-small.webp 640w, /media/live-wide.webp 1280w"
              sizes="(max-width: 700px) 92vw, (max-width: 1100px) 70vw, 34vw" width="1280" height="720" alt="" fetchPriority="high" />
          </div>
          <Waveform className="device-wave" selection={[0.46, 0.66]} />
          <div className="device-strip">
            <img src="/media/moment-ramp.webp" width="640" height="360" alt="" loading="lazy" />
            <img src="/media/live-wide-small.webp" width="640" height="360" alt="" className="is-current" />
            <img src="/media/moment-turn.webp" width="640" height="360" alt="" loading="lazy" />
          </div>
        </div>
        <div className="device-detect">
          <p className="detect-title">{hero.detection}</p>
          <ul>{hero.moments.map((label, index) => {
            const Icon = momentIcons[index]
            return <li key={label} className={index === 0 ? 'is-active' : undefined}><Icon size={14} /><span>{label}</span><b>{momentScores[index]}</b></li>
          })}</ul>
          <span className="fake-button"><Scissors size={14} />{hero.generate}</span>
        </div>
      </div>
      <div className="short-card" aria-hidden="true">
        <span className="short-label">{hero.short} · 0:05</span>
        <img src="/media/short-vertical.webp" width="450" height="800" alt="" />
        <b className="short-caption">{hero.caption}</b>
        <span className="short-progress"><Play size={11} fill="currentColor" /><i /><span className="timecode">0:00 / 0:05</span></span>
      </div>
      <figcaption>{hero.sample}</figcaption>
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
          <h1 id="hero-heading">{t.hero.title[0]} <span>{t.hero.title[1]}</span></h1>
          <p className="hero-lede">{t.hero.detail}</p>
          <div className="hero-actions">
            <a className="button primary" href="#acesso">{join}<ArrowUpRight size={17} aria-hidden="true" /></a>
            <a className="button ghost" href="#como-funciona"><span className="play-dot"><Play size={12} fill="currentColor" aria-hidden="true" /></span>{t.hero.action}</a>
          </div>
        </div>
        <ProductPreview t={t} />
      </div>
      <ul className="benefits shell" role="list">
        {t.benefits.map(([title, detail], index) => {
          const Icon = benefitIcons[index]
          return (
            <li key={title}>
              {Icon ? <Icon size={24} aria-hidden="true" /> : <span className="hd-mark" aria-hidden="true">HD</span>}
              <p><strong>{title}</strong><span>{detail}</span></p>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
