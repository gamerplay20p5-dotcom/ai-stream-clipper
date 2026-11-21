import { expect, test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

// The test server runs without payment providers; their availability is simulated here.
// There is no checkout on the landing page: "Subscribe" opens the account area (sign in or verified sign-up),
// and access is granted only from provider-confirmed state (server/billing.test.js, infinitepay.test.js, and
// tests/test_billing_entitlements.py in the main backend).
const appUrl = 'https://app.clipper.example'

async function enableBilling(page, markets = { BR: true, US: true }) {
  await page.route('**/api/billing/config', route => route.fulfill({ json: { markets, appUrl } }))
}

async function noViolations(page) {
  const { violations } = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  expect(violations.map(({ id, nodes }) => ({ id, targets: nodes.map(node => node.target) }))).toEqual([])
}

test('without payments configured the plans keep pointing to the waitlist', async ({ page }) => {
  await page.goto('/?lang=pt-BR&market=BR')
  await expect(page.getByRole('link', { name: 'Quero o Creator' })).toBeVisible()
  await expect(page.getByRole('link', { name: /Assinar/ })).toHaveCount(0)
})

test('subscribe opens the account area with plan, market and language, never a checkout from the landing', async ({ page }) => {
  await enableBilling(page)
  let checkoutCalls = 0
  await page.route('**/api/checkout', route => { checkoutCalls++; return route.fulfill({ status: 404 }) })
  await page.goto('/?lang=pt-BR&market=BR')
  const creator = page.getByRole('link', { name: 'Assinar o Creator' })
  await expect(creator).toHaveAttribute('href', `${appUrl}/conta?plan=creator&market=BR&lang=pt-BR`)
  await expect(page.locator('.secure-note')).toContainText('InfinitePay')
  await expect(page.getByRole('link', { name: 'Ou entre na lista de espera' })).toHaveCount(3)

  await page.getByRole('button', { name: 'USD', exact: true }).click()
  await expect(page.getByRole('link', { name: 'Assinar o Pro' })).toHaveAttribute('href', `${appUrl}/conta?plan=pro&market=US&lang=pt-BR`)
  await expect(page.locator('.secure-note')).toContainText('Stripe')
  await page.getByRole('button', { name: 'EN', exact: true }).click()
  await expect(page.getByRole('link', { name: 'Subscribe to Basic' })).toHaveAttribute('href', `${appUrl}/conta?plan=basic&market=US&lang=en`)

  // Choosing the waitlist from a plan still preselects that plan
  await page.locator('.plan-pro').getByRole('link', { name: 'Or join the waitlist' }).click()
  await expect(page.locator('select[name="plan"]')).toHaveValue('pro')
  expect(checkoutCalls).toBe(0)
})

test('each market only sells when its own provider is configured', async ({ page }) => {
  await enableBilling(page, { BR: false, US: true })
  await page.goto('/?lang=pt-BR&market=BR')
  await expect(page.getByRole('link', { name: 'Quero o Creator' })).toBeVisible()
  await page.getByRole('button', { name: 'USD', exact: true }).click()
  await expect(page.getByRole('link', { name: 'Assinar o Creator' })).toBeVisible()
})

test('an untrusted account URL is ignored', async ({ page }) => {
  await page.route('**/api/billing/config', route => route.fulfill({ json: { markets: { BR: true, US: true }, appUrl: 'javascript:alert(1)' } }))
  await page.goto('/?lang=pt-BR&market=BR')
  await expect(page.getByRole('link', { name: /Assinar/ })).toHaveCount(0)
})

test('billing interface passes WCAG checks and keeps buttons without a final period', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'One accessibility scan')
  await enableBilling(page)
  for (const language of ['pt-BR', 'en']) {
    await page.goto(`/?lang=${language}&market=BR`)
    const texts = await page.locator('.plan-actions a, .secure-note').allTextContents()
    expect(texts.length).toBeGreaterThan(3)
    expect(texts.filter(text => /[.!]\s*$/.test(text.trim()))).toEqual([])
  }
  await noViolations(page)
})
