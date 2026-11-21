// Illustrative audio waveform with an optional highlighted clip range (fractions of the width).
const bars = Array.from({ length: 72 }, (_, index) => {
  const base = Math.abs(Math.sin(index * 1.7) * 0.5 + Math.sin(index * 0.37) * 0.35)
  const burst = Math.exp(-(((index - 40) / 7) ** 2)) * 0.55
  return Math.min(1, 0.16 + base * 0.6 + burst)
})

interface Props {
  selection?: [number, number]
  markers?: number[]
  activeMarker?: number
  className?: string
  label?: string
}

export function Waveform({ selection, markers = [], activeMarker, className = '', label }: Props) {
  const inRange = (index: number) => selection && index / bars.length >= selection[0] && index / bars.length < selection[1]
  return (
    <div className={`waveform ${className}`} role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      {selection ? <span className="wave-range" style={{ left: `${selection[0] * 100}%`, width: `${(selection[1] - selection[0]) * 100}%` }} /> : null}
      <span className="wave-bars">
        {bars.map((height, index) => <i key={index} className={inRange(index) ? 'is-cut' : undefined} style={{ height: `${Math.round(height * 100)}%` }} />)}
      </span>
      {markers.map((position, index) => <span key={position} className={`wave-marker ${index === activeMarker ? 'is-active' : ''}`} style={{ left: `${position * 100}%` }} />)}
    </div>
  )
}
