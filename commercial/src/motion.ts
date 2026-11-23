// Motion system for the landing. Everything here only adds classes or CSS variables; the markup rendered on the server is
// complete and visible without JavaScript. Animations use transform and opacity, and `prefers-reduced-motion` turns
// the scroll effects off (CSS does the rest).

export const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** Reveal on scroll: elements with [data-reveal] fade and rise once, children with [data-reveal-item] in a short stagger. */
function revealOnScroll(): () => void {
  const root = document.documentElement
  const items = Array.from(document.querySelectorAll<HTMLElement>('[data-reveal]'))
  if (reducedMotion() || !('IntersectionObserver' in window)) return () => {}
  // Only what is below the fold waits for the scroll; what is already visible never flickers
  const fold = window.innerHeight * 0.92
  for (const element of items) {
    if (element.getBoundingClientRect().top > fold) element.classList.add('is-waiting')
    element.querySelectorAll<HTMLElement>('[data-reveal-item]').forEach((child, index) => child.style.setProperty('--i', String(index)))
  }
  root.classList.add('motion-ready')
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue
      entry.target.classList.remove('is-waiting')
      entry.target.classList.add('is-revealed')
      observer.unobserve(entry.target)
    }
  }, { rootMargin: '0px 0px -12% 0px', threshold: 0.08 })
  items.forEach(element => observer.observe(element))
  return () => observer.disconnect()
}

/** Header: solid after the first scroll; the nav link of the section in view is marked as current. */
function headerState(): () => void {
  const header = document.querySelector<HTMLElement>('.site-header')
  const links = Array.from(document.querySelectorAll<HTMLAnchorElement>('.desktop-nav a[href^="#"]'))
  const sections = links.map(link => document.getElementById(link.hash.slice(1))).filter((s): s is HTMLElement => Boolean(s))
  let frame = 0
  const onScroll = () => {
    if (frame) return
    frame = requestAnimationFrame(() => {
      frame = 0
      header?.classList.toggle('is-scrolled', window.scrollY > 12)
    })
  }
  onScroll()
  window.addEventListener('scroll', onScroll, { passive: true })
  let observer: IntersectionObserver | null = null
  if ('IntersectionObserver' in window) {
    const visible = new Map<string, number>()
    observer = new IntersectionObserver(entries => {
      for (const entry of entries) visible.set(entry.target.id, entry.isIntersecting ? entry.intersectionRatio : 0)
      let best = ''
      let ratio = 0
      for (const [id, value] of visible) if (value > ratio) { best = id; ratio = value }
      for (const link of links) {
        if (link.hash.slice(1) === best) link.setAttribute('aria-current', 'location')
        else link.removeAttribute('aria-current')
      }
    }, { rootMargin: '-30% 0px -55% 0px', threshold: [0, 0.25, 0.5, 1] })
    sections.forEach(section => observer?.observe(section))
  }
  return () => {
    window.removeEventListener('scroll', onScroll)
    cancelAnimationFrame(frame)
    observer?.disconnect()
  }
}

/** Discreet parallax on decorative layers ([data-depth]); off on reduced motion, coarse pointers and small screens. */
function parallax(): () => void {
  const layers = Array.from(document.querySelectorAll<HTMLElement>('[data-depth]'))
  if (!layers.length || reducedMotion() || window.matchMedia('(pointer: coarse), (max-width: 760px)').matches) return () => {}
  let frame = 0
  const update = () => {
    frame = 0
    const y = Math.min(window.scrollY, window.innerHeight * 1.2)
    for (const layer of layers) layer.style.setProperty('--parallax', `${(y * Number(layer.dataset.depth)).toFixed(1)}px`)
  }
  const onScroll = () => { if (!frame) frame = requestAnimationFrame(update) }
  window.addEventListener('scroll', onScroll, { passive: true })
  update()
  return () => { window.removeEventListener('scroll', onScroll); cancelAnimationFrame(frame) }
}

export function startMotion(): () => void {
  const stops = [revealOnScroll(), headerState(), parallax()]
  return () => stops.forEach(stop => stop())
}
