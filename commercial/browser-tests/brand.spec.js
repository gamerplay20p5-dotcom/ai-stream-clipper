import { expect, test } from '@playwright/test'

// Composition and brand tokens of the redesigned first screen. Replaces the pixel match against the previous mockup
// (docs/qa/cinematic/reference.webp), which this redesign supersedes.
test('first screen: copy beside the engine, brand tokens and a night palette with coral and violet light', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'One composition check')
  await page.setViewportSize({ width: 1536, height: 1024 })
  await page.goto('/?lang=pt-BR&market=BR')
  await page.evaluate(() => document.fonts.ready)
  const layout = await page.evaluate(() => {
    const box = selector => document.querySelector(selector).getBoundingClientRect().toJSON()
    const style = (selector, property) => getComputedStyle(document.querySelector(selector))[property]
    return {
      copy: box('.hero-copy'), screen: box('.engine-screen'), short: box('.engine-short'), steps: document.querySelectorAll('.engine-steps button').length,
      body: style('body', 'backgroundColor'), text: style('body', 'color'), cta: style('.hero-actions .button.primary', 'backgroundColor'),
      creatorBorder: style('.plan-creator', 'borderTopColor'), display: style('.hero h1', 'fontFamily'),
      nav: [...document.querySelectorAll('.desktop-nav a')].map(link => link.textContent),
      vi: document.querySelectorAll('.neon-vi').length,
    }
  })
  expect(layout.copy.right).toBeLessThan(layout.screen.left)
  expect(layout.screen.right).toBeGreaterThan(1536 * 0.7)
  expect(layout.short.left).toBeGreaterThan(layout.screen.left + layout.screen.width * 0.6)
  expect(layout.steps).toBe(6)
  expect(layout.nav).toEqual(['Produto', 'Como funciona', 'Recursos', 'Planos', 'Perguntas frequentes'])
  expect(layout.body).toBe('rgb(9, 11, 20)')
  expect(layout.text).toBe('rgb(244, 245, 250)')
  expect(layout.cta).toBe('rgb(255, 79, 98)')
  expect(layout.creatorBorder).toBe('rgb(255, 79, 98)')
  expect(layout.display).toContain('Space Grotesk')
  // The game logo outline of the previous design is gone: the identity is the Clipper's own
  expect(layout.vi).toBe(0)

  const shot = await page.screenshot({ clip: { x: 0, y: 0, width: 1536, height: 1024 } })
  const lab = await page.context().newPage()
  const palette = await lab.evaluate(async src => {
    const img = await new Promise((resolve, reject) => { const i = new Image(); i.onload = () => resolve(i); i.onerror = reject; i.src = src })
    const canvas = new OffscreenCanvas(256, 170)
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    ctx.drawImage(img, 0, 0, 256, 170)
    const data = ctx.getImageData(0, 0, 256, 170).data
    const counts = { dark: 0, coral: 0, magentaViolet: 0 }
    for (let i = 0; i < data.length; i += 4) {
      const r = data[i] / 255
      const g = data[i + 1] / 255
      const b = data[i + 2] / 255
      const max = Math.max(r, g, b)
      const min = Math.min(r, g, b)
      const l = (max + min) / 2
      const s = max === min ? 0 : (max - min) / (1 - Math.abs(2 * l - 1))
      let h = 0
      if (max !== min) h = max === r ? 60 * (((g - b) / (max - min)) % 6) : max === g ? 60 * ((b - r) / (max - min) + 2) : 60 * ((r - g) / (max - min) + 4)
      if (h < 0) h += 360
      if (l < 0.14) counts.dark++
      else if (s > 0.45 && (h >= 340 || h < 12)) counts.coral++
      else if (s > 0.25 && h >= 255 && h < 340) counts.magentaViolet++
    }
    const total = data.length / 4
    return Object.fromEntries(Object.entries(counts).map(([k, v]) => [k, v / total]))
  }, `data:image/png;base64,${shot.toString('base64')}`)
  await lab.close()
  expect(palette.dark).toBeGreaterThan(0.35)
  expect(palette.coral).toBeGreaterThan(0.01)
  expect(palette.magentaViolet).toBeGreaterThan(0.025)
})
