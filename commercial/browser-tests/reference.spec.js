import { readFile, writeFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'

// Reproducible fidelity check against the approved mockup (docs/qa/cinematic/reference.webp, 1536x1024).
// 1. Structure and tokens at the mockup's own viewport.
// 2. Pixel comparison of the first screen (header, hero and benefits strip), normalized to the mockup's
//    hero region, against the rejected editorial design as a negative control.

const reference = new URL('../docs/qa/cinematic/reference.webp', import.meta.url)
const rejected = new URL('../docs/qa/desktop-after.webp', import.meta.url)
const REFERENCE_HERO_HEIGHT = 520 // header, hero and benefits strip in the mockup

const dataUrl = async (source, type) => `data:${type};base64,${(await readFile(source)).toString('base64')}`

/** Mean-color grid similarity and palette coverage, computed in a canvas so any machine gets the same numbers. */
async function measure(page, images) {
  return page.evaluate(async ({ images, height }) => {
    const load = src => new Promise((resolve, reject) => { const img = new Image(); img.onload = () => resolve(img); img.onerror = reject; img.src = src })
    function pixels(img, crop) {
      const canvas = new OffscreenCanvas(192, 64)
      const context = canvas.getContext('2d', { willReadFrequently: true })
      context.drawImage(img, 0, 0, img.width, crop * img.width, 0, 0, 192, 64)
      return context.getImageData(0, 0, 192, 64).data
    }
    function grid(data) {
      const cells = []
      for (let gy = 0; gy < 4; gy++) for (let gx = 0; gx < 12; gx++) {
        const sum = [0, 0, 0]
        for (let y = gy * 16; y < gy * 16 + 16; y++) for (let x = gx * 16; x < gx * 16 + 16; x++) {
          const i = (y * 192 + x) * 4
          sum[0] += data[i]; sum[1] += data[i + 1]; sum[2] += data[i + 2]
        }
        cells.push(sum.map(v => v / 256))
      }
      return cells
    }
    function palette(data) {
      const counts = { dark: 0, coral: 0, magentaViolet: 0, warm: 0, other: 0 }
      for (let i = 0; i < data.length; i += 4) {
        const [r, g, b] = [data[i] / 255, data[i + 1] / 255, data[i + 2] / 255]
        const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2
        const s = max === min ? 0 : (max - min) / (1 - Math.abs(2 * l - 1))
        let h = 0
        if (max !== min) h = max === r ? 60 * (((g - b) / (max - min)) % 6) : max === g ? 60 * ((b - r) / (max - min) + 2) : 60 * ((r - g) / (max - min) + 4)
        if (h < 0) h += 360
        if (l < .14) counts.dark++
        else if (s > .45 && (h >= 340 || h < 12)) counts.coral++
        else if (s > .25 && h >= 255 && h < 340) counts.magentaViolet++
        else if (s > .3 && h >= 30 && h < 70) counts.warm++
        else counts.other++
      }
      const total = data.length / 4
      return Object.fromEntries(Object.entries(counts).map(([key, value]) => [key, value / total]))
    }
    const [ref, ...others] = await Promise.all(images.map(({ src }) => load(src)))
    const refData = pixels(ref, height / ref.width)
    const refGrid = grid(refData)
    const refPalette = palette(refData)
    return others.map((img, index) => {
      const data = pixels(img, images[index + 1].crop)
      const cells = grid(data)
      const distance = cells.reduce((total, cell, i) => total + Math.hypot(...cell.map((v, c) => v - refGrid[i][c])), 0) / cells.length
      const pal = palette(data)
      const paletteDistance = Object.keys(pal).reduce((total, key) => total + Math.abs(pal[key] - refPalette[key]), 0) / 2
      return { name: images[index + 1].name, gridSimilarity: 1 - distance / 441.67, paletteOverlap: 1 - paletteDistance, palette: pal, reference: refPalette }
    })
  }, { images, height: REFERENCE_HERO_HEIGHT })
}

test('matches the approved reference in structure, tokens and first-screen composition', async ({ page, browserName }, testInfo) => {
  test.skip(browserName !== 'chromium', 'One reference comparison')
  await page.setViewportSize({ width: 1536, height: 1024 })
  await page.goto('/?lang=pt-BR&market=BR')
  await page.evaluate(() => document.fonts.ready)
  await page.evaluate(() => Promise.all([...document.images].map(img => img.decode().catch(() => {}))))

  const layout = await page.evaluate(() => {
    const box = selector => document.querySelector(selector).getBoundingClientRect().toJSON()
    const style = (selector, property) => getComputedStyle(document.querySelector(selector))[property]
    return {
      width: innerWidth, viewport: innerHeight,
      copy: box('.hero-copy'), device: box('.device'), short: box('.short-card'), benefits: box('.benefits'),
      headlineLines: document.querySelector('.hero h1').getClientRects().length && Math.round(box('.hero h1').height / parseFloat(style('.hero h1', 'lineHeight'))),
      body: style('body', 'backgroundColor'), accent: style('.hero h1 span', 'color'), cta: style('.hero-actions .button.primary', 'backgroundColor'),
      ctaText: style('.hero-actions .button.primary', 'color'), surface: style('.plan-card', 'backgroundImage'), creatorBorder: style('.plan-creator', 'borderTopColor'),
      text: style('body', 'color'), muted: style('.hero-product figcaption', 'color'),
      plans: [...document.querySelectorAll('.plan-card')].map(card => card.getBoundingClientRect().top),
      nav: [...document.querySelectorAll('.desktop-nav a')].map(link => link.textContent),
      benefitCount: document.querySelectorAll('.benefits li').length,
      actionTops: [...document.querySelectorAll('.hero-actions .button')].map(button => Math.round(button.getBoundingClientRect().top)),
    }
  })
  // Composition: copy on the left, product window and vertical Short on the right, benefits in the first screen
  expect(layout.copy.right).toBeLessThan(layout.device.left)
  expect(layout.copy.left).toBeLessThan(layout.width * .1)
  expect(layout.device.right).toBeGreaterThan(layout.width * .7)
  expect(layout.short.left).toBeGreaterThan(layout.device.left + layout.device.width * .7)
  expect(layout.short.top).toBeLessThan(layout.device.top)
  expect(layout.short.bottom).toBeGreaterThan(layout.device.top)
  expect(layout.benefits.bottom).toBeLessThanOrEqual(layout.viewport)
  expect(layout.benefitCount).toBe(4)
  expect(new Set(layout.actionTops).size).toBe(1)
  expect(layout.headlineLines).toBe(2)
  expect(layout.nav).toEqual(['Produto', 'Como funciona', 'Recursos', 'Planos', 'Perguntas frequentes'])
  // Tokens from the brief
  expect(layout.body).toBe('rgb(9, 11, 16)')
  expect(layout.text).toBe('rgb(245, 246, 250)')
  expect(layout.muted).toBe('rgb(173, 177, 192)')
  expect(layout.accent).toBe('rgb(255, 79, 98)')
  expect(layout.cta).toBe('rgb(255, 79, 98)')
  expect(layout.ctaText).toBe('rgb(255, 255, 255)')
  expect(layout.creatorBorder).toBe('rgb(255, 79, 98)')
  expect(layout.surface).toContain('rgb(21, 23, 34)')
  expect(new Set(layout.plans.map(Math.round)).size).toBe(1)

  // Pixel comparison of the first screen
  const shot = await page.screenshot({ clip: { x: 0, y: 0, width: 1536, height: Math.ceil(layout.benefits.bottom) } })
  const ours = `data:image/png;base64,${shot.toString('base64')}`
  const crop = Math.ceil(layout.benefits.bottom) / 1536
  // The site's CSP blocks data: images, so the comparison runs in a separate blank page.
  const lab = await page.context().newPage()
  const results = await measure(lab, [
    { name: 'reference', src: await dataUrl(reference, 'image/webp') },
    { name: 'cinematic', src: ours, crop: 1 / (1536 / Math.ceil(layout.benefits.bottom)) },
    { name: 'rejected editorial', src: await dataUrl(rejected, 'image/webp'), crop },
  ])
  await lab.close()
  await writeFile(testInfo.outputPath('reference-fidelity.json'), JSON.stringify(results, null, 2))
  console.log(JSON.stringify(results.map(({ name, gridSimilarity, paletteOverlap }) => ({ name, gridSimilarity: +gridSimilarity.toFixed(3), paletteOverlap: +paletteOverlap.toFixed(3) }))))
  const [cinematic, editorial] = results
  expect(cinematic.paletteOverlap).toBeGreaterThan(editorial.paletteOverlap + .25)
  expect(cinematic.gridSimilarity).toBeGreaterThan(editorial.gridSimilarity + .1)
  expect(cinematic.paletteOverlap).toBeGreaterThan(.85)
  expect(cinematic.gridSimilarity).toBeGreaterThan(.9)
})
