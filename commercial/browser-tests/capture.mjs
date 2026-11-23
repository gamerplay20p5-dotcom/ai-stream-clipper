// Before/after evidence for the landing: screenshots (first viewport and full page) at 390, 768, 1440 and 1920 px in
// PT and EN, plus performance numbers measured in Chromium (median of 3 cold loads per profile).
//   node browser-tests/capture.mjs <base url> <output dir>
// Mobile profile: 390x844, 4x CPU slowdown and a fast-4G-like network. Desktop profile: 1440x900, no throttling.
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from '@playwright/test'

const base = (process.argv[2] ?? 'http://127.0.0.1:8793').replace(/\/$/, '')
const out = process.argv[3] ?? 'test-results/capture'
mkdirSync(out, { recursive: true })
const browser = await chromium.launch()

// ------------------------------------------------------------------ screenshots
for (const lang of ['pt-BR', 'en']) {
  for (const [width, height] of [[390, 844], [768, 1024], [1440, 900], [1920, 1080]]) {
    const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 })
    await page.goto(`${base}/?lang=${lang}`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(2600) // let the entrance and the first states of the hero sequence play
    await page.screenshot({ path: join(out, `${lang}-${width}-viewport.png`) })
    // Scroll through so scroll-triggered reveals run, then capture the whole page
    await page.evaluate(async () => {
      for (let y = 0; y < document.documentElement.scrollHeight; y += Math.round(innerHeight * 0.7)) {
        window.scrollTo(0, y)
        await new Promise(r => setTimeout(r, 120))
      }
      window.scrollTo(0, 0)
    })
    await page.waitForTimeout(400)
    await page.screenshot({ path: join(out, `${lang}-${width}-full.png`), fullPage: true })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)
    if (overflow > 1) console.log(`OVERFLOW ${lang} ${width}px: ${overflow}px`)
    await page.close()
  }
}

// ------------------------------------------------------------------ performance
async function measure({ width, height, cpu, network }) {
  const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 })
  const page = await context.newPage()
  const cdp = await context.newCDPSession(page)
  await cdp.send('Network.enable')
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })
  if (network) await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 60, downloadThroughput: 1_600_000, uploadThroughput: 750_000 })
  if (cpu > 1) await cdp.send('Emulation.setCPUThrottlingRate', { rate: cpu })
  const bytes = { script: 0, stylesheet: 0, image: 0, font: 0, document: 0, media: 0, other: 0 }
  const types = new Map()
  cdp.on('Network.responseReceived', e => types.set(e.requestId, e.type))
  cdp.on('Network.loadingFinished', e => {
    const type = (types.get(e.requestId) ?? 'other').toLowerCase()
    const key = type in bytes ? type : 'other'
    bytes[key] += e.encodedDataLength
  })
  await page.addInitScript(() => {
    window.__perf = { lcp: 0, cls: 0, tbt: 0 }
    new PerformanceObserver(list => { for (const e of list.getEntries()) window.__perf.lcp = e.startTime }).observe({ type: 'largest-contentful-paint', buffered: true })
    new PerformanceObserver(list => { for (const e of list.getEntries()) if (!e.hadRecentInput) window.__perf.cls += e.value }).observe({ type: 'layout-shift', buffered: true })
    new PerformanceObserver(list => { for (const e of list.getEntries()) window.__perf.tbt += Math.max(0, e.duration - 50) }).observe({ type: 'longtask', buffered: true })
  })
  await page.goto(`${base}/?lang=pt-BR`, { waitUntil: 'load' })
  await page.waitForTimeout(3500)
  const timing = await page.evaluate(() => {
    const nav = performance.getEntriesByType('navigation')[0]
    const fcp = performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? 0
    return { fcp, dcl: nav.domContentLoadedEventEnd, load: nav.loadEventEnd, ...window.__perf }
  })
  await context.close()
  return { ...timing, kb: Object.fromEntries(Object.entries(bytes).map(([k, v]) => [k, Math.round(v / 1024)])), totalKb: Math.round(Object.values(bytes).reduce((a, b) => a + b, 0) / 1024) }
}

const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
const report = {}
for (const [name, profile] of [['mobile', { width: 390, height: 844, cpu: 4, network: true }], ['desktop', { width: 1440, height: 900, cpu: 1, network: false }]]) {
  const runs = []
  for (let i = 0; i < 3; i++) runs.push(await measure(profile))
  report[name] = {
    fcp: Math.round(median(runs.map(r => r.fcp))), lcp: Math.round(median(runs.map(r => r.lcp))),
    cls: Number(median(runs.map(r => r.cls)).toFixed(3)), tbt: Math.round(median(runs.map(r => r.tbt))),
    load: Math.round(median(runs.map(r => r.load))), totalKb: median(runs.map(r => r.totalKb)), kb: runs[0].kb,
  }
}
writeFileSync(join(out, 'performance.json'), JSON.stringify(report, null, 1))
console.log(JSON.stringify(report, null, 1))
await browser.close()
