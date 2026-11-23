import { expect, test } from '@playwright/test'

// Motion system of the landing: the hero engine, reduced motion, no-JavaScript rendering, scroll reveals, header state,
// current section, plan selection and keyboard access.
const stage = page => page.locator('.engine').getAttribute('data-stage')
// playwright.config.mjs runs every spec with reduced motion; these tests need the animations on (the reduced-motion
// test below opens its own context)
test.use({ reducedMotion: 'no-preference' })

test('hero engine plays its six states, pauses, and can be stepped by hand', async ({ page }) => {
  await page.goto('/?lang=en')
  const engine = page.locator('.engine')
  await expect(engine).toHaveAttribute('aria-label', 'Animated demo: a livestream becomes a Short in six steps')
  await expect.poll(() => stage(page), { timeout: 8000 }).not.toBe('0')
  await page.getByRole('button', { name: 'Pause the demo' }).click()
  await expect(page.getByRole('button', { name: 'Resume the demo' })).toHaveAttribute('aria-pressed', 'true')
  const paused = await stage(page)
  await page.waitForTimeout(2600)
  expect(await stage(page)).toBe(paused)
  // Each step is a real button with the current one marked; the live region describes it
  await page.getByRole('button', { name: /Vertical/ }).click()
  await expect(engine).toHaveAttribute('data-stage', '4')
  await expect(page.getByRole('button', { name: /Vertical/ })).toHaveAttribute('aria-current', 'step')
  await expect(page.locator('.engine p[aria-live]')).toHaveText('The framing switches to vertical 9:16')
  await page.getByRole('button', { name: /Short ready/ }).click()
  await expect(page.locator('.engine-short')).toHaveCSS('opacity', '1')
  await expect(page.locator('.engine-ready')).toContainText('Ready to review')
})

test('reduced motion: the engine opens on the finished Short, does not autoplay and nothing waits for the scroll', async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: 'reduce' })
  const page = await context.newPage()
  await page.goto('/?lang=pt-BR')
  await expect(page.locator('.engine')).toHaveAttribute('data-stage', '5')
  await page.waitForTimeout(2500)
  await expect(page.locator('.engine')).toHaveAttribute('data-stage', '5')
  await expect(page.getByRole('button', { name: 'Continuar a demonstração' })).toBeVisible()
  expect(await page.locator('.is-waiting').count()).toBe(0)
  await page.locator('#planos').scrollIntoViewIfNeeded()
  await expect(page.locator('.plan-pro')).toHaveCSS('opacity', '1')
  await context.close()
})

test('without JavaScript every section, price and plan is visible', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false })
  const page = await context.newPage()
  await page.goto('/?lang=pt-BR')
  for (const selector of ['.feature-signals', '.feature-framing', '.plan-grid', '.faq-list', '.waitlist-inner']) {
    await expect(page.locator(selector)).toHaveCSS('opacity', '1')
  }
  await expect(page.locator('.price strong')).toHaveText(['59,90', '139,90', '279,90'])
  await context.close()
})

test('scroll reveals sections once, the header turns solid and the current section is marked', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/?lang=pt-BR')
  await expect(page.locator('.site-header')).not.toHaveClass(/is-scrolled/)
  await expect(page.locator('.feature-framing')).toHaveClass(/is-waiting/)
  await page.locator('.feature-framing').scrollIntoViewIfNeeded()
  await expect(page.locator('.feature-framing')).toHaveClass(/is-revealed/)
  await expect(page.locator('.site-header')).toHaveClass(/is-scrolled/)
  await page.evaluate(() => document.getElementById('planos').scrollIntoView())
  await expect(page.locator('.desktop-nav a[href="#planos"]')).toHaveAttribute('aria-current', 'location')
  await expect(page.locator('.desktop-nav a[aria-current]')).toHaveCount(1)
})

test('choosing a plan marks its card and carries it to the waitlist', async ({ page }) => {
  await page.goto('/?lang=en&market=US')
  await page.getByRole('link', { name: 'Choose Pro' }).click()
  await expect(page.locator('.plan-pro')).toHaveClass(/is-selected/)
  await expect(page.locator('.plan-card.is-selected')).toHaveCount(1)
  await expect(page.locator('form.waitlist-form select[name="plan"]')).toHaveValue('pro')
})

test('keyboard reaches the engine controls with a visible focus ring', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/?lang=pt-BR')
  await page.locator('.engine-steps button').first().focus()
  await page.keyboard.press('Tab')
  const second = page.locator('.engine-steps button').nth(1)
  await expect(second).toBeFocused()
  expect(await second.evaluate(element => getComputedStyle(element).outlineStyle)).not.toBe('none')
  await page.keyboard.press('Enter')
  await expect(page.locator('.engine')).toHaveAttribute('data-stage', '1')
})
