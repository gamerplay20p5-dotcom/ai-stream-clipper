import { randomUUID } from 'node:crypto'
import { writeFile } from 'node:fs/promises'
import { DatabaseSync } from 'node:sqlite'
import { expect, test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

const sizes = [320, 375, 390, 768, 1024, 1440, 1920]
const form = page => page.locator('form.waitlist-form')

async function checkAccessibility(page) {
  const { violations } = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  expect(violations.map(({ id, nodes }) => ({ id, targets: nodes.map(node => node.target) }))).toEqual([])
}

test('pre-rendered HTML includes localized content and independent market pricing', async ({ request }) => {
  const response = await request.get('/?lang=en&market=BR')
  expect(response.ok()).toBeTruthy()
  expect(response.headers()['content-language']).toBe('en')
  const html = await response.text()
  expect(html).toContain('<html lang="en">')
  expect(html).toContain('Your stream ends')
  expect(html).toContain('139,90')
  expect(html).toContain('property="og:locale" content="en_US"')
  const preferred = await request.get('/', { headers: { 'Accept-Language': 'en-US,en;q=0.9' } })
  expect(preferred.headers()['content-language']).toBe('en')
  expect(preferred.headers().vary).toContain('Accept-Language')
})

test('changes language and market independently and retains URL preferences', async ({ page }) => {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/?lang=pt-BR&market=BR')
  await expect(page.locator('.price strong')).toHaveText(['59,90', '139,90', '279,90'])
  await page.getByRole('button', { name: 'EN', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('lang', 'en')
  await expect(page.locator('.price strong')).toHaveText(['59,90', '139,90', '279,90'])
  await page.getByRole('button', { name: 'USD', exact: true }).click()
  await expect(page.locator('.price strong')).toHaveText(['19.99', '44.99', '89.99'])
  await expect(page.locator('.plan-limits strong')).toHaveText(['40 streaming hours per month', '100 streaming hours per month', '200 streaming hours per month'])
  await expect(page.locator('.plan-limits span')).toHaveText(['1 channel', '2 channels', '4 channels'])
  await expect(page.locator('.plan-pro .pro-workbench')).toContainText('Up to 30 4K exports per month')
  await expect(page).toHaveURL(/lang=en&market=US/)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Plans for the way you stream' })).toBeVisible()
  await expect(page.locator('meta[property="og:locale"]')).toHaveAttribute('content', 'en_US')
  expect(errors).toEqual([])
})

test('mobile menu traps focus, returns focus on Escape and opens comparison', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/?lang=pt-BR&market=BR')
  const trigger = page.getByRole('button', { name: 'Abrir menu' })
  await trigger.focus()
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('button', { name: 'Fechar menu' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(dialog.getByRole('link', { name: 'Produto', exact: true })).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await page.keyboard.press('Shift+Tab')
  await expect(dialog.getByRole('link', { name: 'Entrar na lista de espera' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(trigger).toBeFocused()
  await trigger.click()
  await dialog.getByRole('link', { name: 'Preços' }).click()
  await expect(dialog).toBeHidden()
  await expect(page.locator('#planos')).toBeFocused()
  const comparison = page.locator('.comparison-mobile').getByText('Canais monitorados', { exact: true })
  await comparison.click()
  await expect(comparison.locator('..').locator('dd')).toHaveText(['1', '2', '4'])
  const faq = page.locator('#faq').getByText('Como funciona a exportação 4K?', { exact: true })
  await faq.focus()
  await page.keyboard.press('Enter')
  await expect(faq.locator('..')).toHaveAttribute('open', '')
})

test('editorial demo switches frames and captions and plays the local Short', async ({ page }) => {
  await page.goto('/?lang=en&market=US')
  await page.getByRole('tab', { name: 'Live' }).focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: 'Analyze' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tabpanel').locator('.console-signals li')).toHaveText(['Video', 'Audio', 'Speech'])
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: /Select/ })).toHaveAttribute('aria-selected', 'true')
  await page.getByRole('button', { name: /Last turn/ }).click()
  await expect(page.getByRole('tabpanel').locator('.monitor-scene img')).toHaveAttribute('src', '/media/night-turn.webp')
  await page.getByRole('tab', { name: /Edit/ }).click()
  await expect(page.getByRole('tabpanel').locator('.frame-caption')).toBeVisible()
  await page.getByRole('checkbox', { name: 'Show captions' }).uncheck()
  await expect(page.getByRole('tabpanel').locator('.frame-caption')).toHaveCount(0)
  await page.getByRole('tab', { name: 'Live' }).click()
  await page.getByRole('button', { name: 'Open the sample Short' }).click()
  await expect(page.getByRole('tab', { name: 'Review' })).toBeFocused()
  const video = page.locator('video')
  await expect(video.locator('track')).toHaveAttribute('srclang', 'en')
  await expect.poll(() => video.evaluate(element => element.readyState)).toBeGreaterThan(0)
  await video.evaluate(element => element.play())
  await expect.poll(() => video.evaluate(element => element.currentTime)).toBeGreaterThan(0)
  await video.evaluate(element => element.pause())
})

async function fillInterest(page, email) {
  await page.getByRole('link', { name: 'I’m interested in Pro' }).click()
  const signup = form(page)
  await expect(signup.locator('select[name="plan"]')).toHaveValue('pro')
  await expect(signup.locator('select[name="country"]')).toHaveValue('US')
  await signup.getByRole('textbox', { name: 'Your email' }).fill(email)
  await signup.locator('select[name="country"]').selectOption('OTHER')
  await signup.getByRole('textbox', { name: 'Which country are you in?' }).fill('Canada')
  await signup.getByRole('combobox', { name: 'Streaming platform' }).selectOption('kick')
  await signup.getByRole('checkbox').check()
}

test('waitlist waits for confirmation and persists a real form submission', async ({ page }) => {
  await page.goto('/?lang=en&market=US')
  const email = `${randomUUID()}@example.com`
  await fillInterest(page, email)
  let resume
  const pending = new Promise(resolve => { resume = resolve })
  await page.route('**/api/waitlist', async route => { await pending; await route.continue() })
  await form(page).getByRole('button', { name: 'Join the waitlist' }).click()
  try {
    await expect(form(page).getByRole('button', { name: 'Submitting…' })).toBeDisabled()
    await expect(page.getByRole('heading', { name: 'You’re on the list' })).toHaveCount(0)
  } finally {
    resume()
  }
  await expect(page.getByRole('heading', { name: 'You’re on the list' })).toBeFocused()
  using database = new DatabaseSync(process.env.ASC_BROWSER_DB, { readOnly: true })
  const registered = database.prepare('SELECT plan, other_country, platform, market, language, consent_version FROM waitlist WHERE email = ?').get(email)
  expect({ ...registered }).toEqual({ plan: 'pro', other_country: 'Canada', platform: 'kick', market: 'US', language: 'en', consent_version: 1 })
})

test('waitlist presents validation, throttling and availability errors without clearing fields', async ({ page }) => {
  await page.goto('/?lang=en&market=US')
  await fillInterest(page, 'review@example.com')
  for (const [status, message] of [[422, 'Check your information'], [429, 'Wait a few minutes'], [503, 'Please try again in a moment']]) {
    await page.route('**/api/waitlist', route => route.fulfill({ status }), { times: 1 })
    await form(page).getByRole('button', { name: 'Join the waitlist' }).click()
    await expect(form(page).getByRole('status')).toContainText(message)
    await expect(form(page).getByRole('textbox', { name: 'Your email' })).toHaveValue('review@example.com')
  }
})

test('captures the required widths and checks WCAG rules across languages and UI states', async ({ page, browserName }, testInfo) => {
  test.skip(browserName !== 'chromium', 'One set of browser screenshots and accessibility scans')
  test.setTimeout(90_000)
  for (const width of sizes) {
    await page.setViewportSize({ width, height: width < 700 ? 844 : 1000 })
    await page.goto('/?lang=pt-BR&market=BR')
    await page.evaluate(() => document.fonts.ready)
    await page.evaluate(() => Promise.all([...document.images].filter(img => img.getClientRects().length).map(img => {
      img.loading = 'eager'
      return img.decode()
    })))
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width)
    expect(await page.locator('h1, h2, h3, h4, summary, header nav a, button').allTextContents()).not.toContainEqual(expect.stringMatching(/[.!]\s*$/))
    await page.screenshot({ path: testInfo.outputPath(`landing-${width}.png`), fullPage: true })
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  await checkAccessibility(page)
  await page.goto('/?lang=en&market=US')
  await page.getByRole('tab', { name: /Review/ }).click()
  await checkAccessibility(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: 'Open menu' }).click()
  await checkAccessibility(page)
  await page.keyboard.press('Escape')
  await checkAccessibility(page)
})

test('records initial-load metrics and defers the demo video', async ({ page, browserName }, testInfo) => {
  test.skip(browserName !== 'chromium', 'PerformanceObserver LCP and layout shift entries')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => {
    window.previewMetrics = { lcp: null, layoutShift: 0 }
    new PerformanceObserver(entries => {
      window.previewMetrics.lcp = entries.getEntries().at(-1)?.startTime
    }).observe({ type: 'largest-contentful-paint', buffered: true })
    new PerformanceObserver(entries => {
      for (const entry of entries.getEntries()) {
        if (!entry.hadRecentInput) window.previewMetrics.layoutShift += entry.value
      }
    }).observe({ type: 'layout-shift', buffered: true })
  })
  await page.goto('/?lang=pt-BR&market=BR')
  await expect.poll(() => page.evaluate(() => window.previewMetrics.lcp)).not.toBeNull()
  const measurements = await page.evaluate(async () => {
    await document.fonts.ready
    return {
      ...window.previewMetrics,
      source: document.querySelector('.monitor-scene img').currentSrc.split('/').at(-1),
      requestedDemoVideo: performance.getEntriesByType('resource').some(entry => entry.name.endsWith('.mp4')),
    }
  })
  await writeFile(testInfo.outputPath('local-metrics.json'), JSON.stringify(measurements, null, 2))
  expect(measurements.source).toBe('night-source-small.webp')
  expect(measurements.layoutShift).toBeLessThan(.1)
  expect(measurements.requestedDemoVideo).toBe(false)
})
