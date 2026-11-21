import { expect, test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

// The test server runs without Stripe keys, so the billing API answers are simulated here.
// Server-side behaviour (prices, signatures, webhooks) is covered by server/billing.test.js.
const portalUrl = 'https://billing.stripe.com/p/login/test_portal123'
const checkoutUrl = 'https://checkout.stripe.com/c/pay/cs_test_browser123'

async function enableBilling(page, markets = { BR: true, US: true }) {
  await page.route('**/api/billing/config', route => route.fulfill({ json: { markets, portalUrl } }))
}

async function noViolations(page) {
  const { violations } = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  expect(violations.map(({ id, nodes }) => ({ id, targets: nodes.map(node => node.target) }))).toEqual([])
}

test('without Stripe configured the plans keep pointing to the waitlist', async ({ page }) => {
  await page.goto('/?lang=pt-BR&market=BR')
  await expect(page.getByRole('link', { name: 'Quero o Creator' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Assinar/ })).toHaveCount(0)
})

test('dollar plans send only plan, market and language and leave for Stripe Checkout', async ({ page }) => {
  await enableBilling(page)
  let body
  await page.route('**/api/checkout', route => { body = route.request().postDataJSON(); return route.fulfill({ status: 201, json: { url: checkoutUrl } }) })
  await page.route('https://checkout.stripe.com/**', route => route.fulfill({ contentType: 'text/html', body: '<title>Stripe Checkout</title>' }))
  await page.goto('/?lang=en&market=US')
  const subscribe = page.getByRole('button', { name: 'Subscribe to Creator' })
  await expect(subscribe).toBeVisible()
  await expect(page.getByRole('link', { name: 'Or join the waitlist' })).toHaveCount(3)
  await expect(page.locator('.secure-note')).toContainText('Stripe')
  await subscribe.click()
  await expect(page).toHaveURL(checkoutUrl)
  expect(body).toEqual({ plan: 'creator', market: 'US', language: 'en' })
})

test('real plans ask for the renewal email and leave for InfinitePay', async ({ page }) => {
  await enableBilling(page)
  const infiniteUrl = 'https://checkout.infinitepay.io/hts/asc-test'
  const bodies = []
  await page.route('**/api/checkout', route => {
    const body = route.request().postDataJSON()
    bodies.push(body)
    return body.email === 'creator@example.com'
      ? route.fulfill({ status: 201, json: { url: infiniteUrl } })
      : route.fulfill({ status: 422, json: { error: 'invalid_email' } })
  })
  await page.route('https://checkout.infinitepay.io/**', route => route.fulfill({ contentType: 'text/html', body: '<title>InfinitePay</title>' }))
  await page.goto('/?lang=pt-BR&market=BR')
  await expect(page.locator('.secure-note').first()).toContainText('InfinitePay')
  const subscribe = page.getByRole('button', { name: 'Assinar o Creator' })
  await subscribe.click()
  const dialog = page.getByRole('dialog', { name: 'Assinar o Creator' })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('139,90')
  await expect(dialog.getByRole('textbox', { name: 'Seu e-mail' })).toBeFocused()
  // Escape closes the dialog and returns focus to the plan button
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(subscribe).toBeFocused()

  await subscribe.click()
  await dialog.getByRole('textbox', { name: 'Seu e-mail' }).fill('server-rejects@example.com')
  await dialog.getByRole('button', { name: 'Continuar para o pagamento' }).click()
  await expect(dialog.getByRole('status')).toContainText('Confira o e-mail')
  await dialog.getByRole('textbox', { name: 'Seu e-mail' }).fill('creator@example.com')
  await dialog.getByRole('button', { name: 'Continuar para o pagamento' }).click()
  await expect(page).toHaveURL(infiniteUrl)
  expect(bodies.at(-1)).toEqual({ plan: 'creator', market: 'BR', language: 'pt-BR', email: 'creator@example.com' })
})

test('each market only offers checkout when its own provider is configured', async ({ page }) => {
  await enableBilling(page, { BR: false, US: true })
  await page.goto('/?lang=pt-BR&market=BR')
  await expect(page.getByRole('link', { name: 'Quero o Creator' })).toBeVisible()
  await page.getByRole('button', { name: 'USD', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Assinar o Creator' })).toBeVisible()
})

test('an InfinitePay return is confirmed by the server and offers no Stripe portal', async ({ page }) => {
  await enableBilling(page)
  let sent
  await page.route('**/api/infinitepay/confirm', route => { sent = route.request().postDataJSON(); return route.fulfill({ json: { result: 'confirmed', plan: 'creator' } }) })
  await page.goto('/?lang=pt-BR&market=BR&checkout=success&provider=infinitepay&order_nsu=asc-11111111-2222-3333-4444-555555555555&slug=s1&transaction_nsu=t1')
  await expect(page.getByRole('heading', { name: 'Pagamento confirmado' })).toBeFocused()
  await expect(page.locator('.checkout-notice')).toContainText('link de renovação')
  await expect(page.locator('.checkout-notice').getByRole('link', { name: 'Gerenciar assinatura' })).toHaveCount(0)
  expect(sent).toEqual({ orderNsu: 'asc-11111111-2222-3333-4444-555555555555', slug: 's1', transactionNsu: 't1' })
  await expect(page).toHaveURL(/\?lang=pt-BR&market=BR$/)

  await page.route('**/api/infinitepay/confirm', route => route.fulfill({ json: { result: 'processing', plan: 'creator' } }))
  await page.goto('/?lang=pt-BR&market=BR&checkout=success&provider=infinitepay&order_nsu=asc-11111111-2222-3333-4444-555555555555&slug=s1&transaction_nsu=t1')
  await expect(page.getByRole('heading', { name: 'Pagamento em processamento' })).toBeVisible()
})

test('checkout errors are announced and the buttons recover', async ({ page }) => {
  await enableBilling(page)
  // The most recently registered route wins: first 503 once, then 429
  await page.route('**/api/checkout', route => route.fulfill({ status: 429, json: { error: 'too_many_requests' } }))
  await page.route('**/api/checkout', route => route.fulfill({ status: 503, json: { error: 'checkout_unavailable' } }), { times: 1 })
  await page.goto('/?lang=en&market=US')
  await page.getByRole('button', { name: 'Subscribe to Pro' }).click()
  await expect(page.locator('.checkout-feedback')).toContainText('We couldn’t open checkout right now')
  await expect(page.getByRole('button', { name: 'Subscribe to Pro' })).toBeEnabled()
  await page.getByRole('button', { name: 'Subscribe to Basic' }).click()
  await expect(page.locator('.checkout-feedback')).toContainText('Wait a few minutes')
  // Switching to the waitlist from a plan still preselects that plan
  await page.locator('.plan-pro').getByRole('link', { name: 'Or join the waitlist' }).click()
  await expect(page.locator('select[name="plan"]')).toHaveValue('pro')
})

test('a successful return shows the confirmation, cleans the URL and links to the customer portal', async ({ page, browserName }) => {
  await enableBilling(page)
  await page.route('**/api/checkout/session?*', route => {
    expect(new URL(route.request().url()).searchParams.get('id')).toBe('cs_test_returned123')
    return route.fulfill({ json: { status: 'complete', paymentStatus: 'paid', plan: 'pro' } })
  })
  await page.goto('/?lang=en&market=US&checkout=success&session_id=cs_test_returned123')
  const heading = page.getByRole('heading', { name: 'Subscription confirmed' })
  await expect(heading).toBeFocused()
  await expect(page.locator('.checkout-notice')).toContainText('Your Pro payment was approved')
  await expect(page.locator('.checkout-notice').getByRole('link', { name: 'Manage subscription' })).toHaveAttribute('href', portalUrl)
  await expect(page.locator('footer').getByRole('link', { name: 'Manage subscription' })).toHaveAttribute('href', portalUrl)
  await expect(page).toHaveURL(/\?lang=en&market=US$/)
  // Like the rest of the suite, the axe scan runs in Chromium (it crashes Playwright's Firefox page here)
  if (browserName === 'chromium') await noViolations(page)
  await page.getByRole('button', { name: 'Dismiss notice' }).click()
  await expect(page.locator('.checkout-notice')).toHaveCount(0)
})

test('processing, cancelled and unverifiable returns never claim a confirmed payment', async ({ page }) => {
  await enableBilling(page)
  await page.route('**/api/checkout/session?*', route => route.fulfill({ json: { status: 'complete', paymentStatus: 'unpaid', plan: 'basic' } }))
  await page.goto('/?lang=pt-BR&market=BR&checkout=success&session_id=cs_test_processing123')
  await expect(page.getByRole('heading', { name: 'Pagamento em processamento' })).toBeVisible()

  await page.goto('/?lang=pt-BR&market=BR&checkout=cancelled')
  await expect(page.getByRole('heading', { name: 'Pagamento cancelado' })).toBeFocused()
  await expect(page.locator('.checkout-notice')).toContainText('Nenhuma cobrança foi feita')

  await page.goto('/?lang=pt-BR&market=BR&checkout=success&session_id=<script>')
  await expect(page.getByRole('heading', { name: 'Não foi possível confirmar agora' })).toBeVisible()
})

test('billing interface passes WCAG checks and keeps headings and buttons without a final period', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'One accessibility scan')
  await enableBilling(page)
  await page.goto('/?lang=pt-BR&market=BR')
  await page.getByRole('button', { name: 'Assinar o Pro' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await noViolations(page)
  expect((await page.locator('.checkout-dialog h3, .checkout-dialog button, .checkout-dialog label > span').allTextContents()).filter(text => /[.!]\s*$/.test(text.trim()))).toEqual([])
  await page.keyboard.press('Escape')
  for (const language of ['pt-BR', 'en']) {
    await page.goto(`/?lang=${language}&market=BR&checkout=cancelled`)
    await expect(page.locator('.checkout-notice h2')).toBeVisible()
    const texts = await page.locator('.checkout-notice h2, .plan-actions button, .plan-waitlist, .secure-note').allTextContents()
    expect(texts.filter(text => /[.!]\s*$/.test(text.trim()))).toEqual([])
  }
  await noViolations(page)
})
