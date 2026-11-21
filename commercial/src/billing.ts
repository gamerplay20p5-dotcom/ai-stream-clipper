import { useEffect, useState } from 'react'
import type { Language } from './content'
import type { Market, PlanId } from './market'

/** Checkout availability per market: US is billed by Stripe (USD), BR by InfinitePay (BRL). */
export interface BillingConfig { markets: Record<Market, boolean>; portalUrl: string }
export const billingOff: BillingConfig = { markets: { BR: false, US: false }, portalUrl: '' }

/** Server-side on/off switch per market. Pre-rendered HTML always starts with the waitlist. */
export function useBillingConfig() {
  const [config, setConfig] = useState<BillingConfig>(billingOff)
  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/billing/config', { signal: controller.signal })
      .then(response => (response.ok ? response.json() : billingOff))
      .then((value: BillingConfig) => setConfig({
        markets: { BR: value.markets?.BR === true, US: value.markets?.US === true },
        portalUrl: typeof value.portalUrl === 'string' && value.portalUrl.startsWith('https://billing.stripe.com/') ? value.portalUrl : '',
      }))
      .catch(() => {})
    return () => controller.abort()
  }, [])
  return config
}

export type CheckoutError = 'rate' | 'unavailable' | 'email'

/** Asks the server for a payment page; only the plan, market, language and (Brazil) email leave the browser. */
export async function startCheckout(plan: PlanId, market: Market, language: Language, email = ''): Promise<CheckoutError | null> {
  try {
    const response = await fetch('/api/checkout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(20_000),
      body: JSON.stringify(market === 'BR' ? { plan, market, language, email } : { plan, market, language }),
    })
    if (response.status === 429) return 'rate'
    if (response.status === 422) return market === 'BR' ? 'email' : 'unavailable'
    if (response.status !== 201) return 'unavailable'
    const { url } = await response.json() as { url?: string }
    if (typeof url !== 'string' || !isProviderUrl(url, market)) return 'unavailable'
    window.location.assign(url)
    return null
  } catch {
    return 'unavailable'
  }
}

function isProviderUrl(value: string, market: Market) {
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:') return false
    return market === 'BR' ? url.hostname === 'infinitepay.io' || url.hostname.endsWith('.infinitepay.io') : url.hostname === 'checkout.stripe.com'
  } catch {
    return false
  }
}

export type CheckoutResult = 'confirmed' | 'processing' | 'incomplete' | 'cancelled' | 'unknown'

export async function checkoutResult(sessionId: string): Promise<{ result: CheckoutResult; plan: PlanId | '' }> {
  try {
    const response = await fetch(`/api/checkout/session?id=${encodeURIComponent(sessionId)}`, { signal: AbortSignal.timeout(15_000) })
    if (!response.ok) return { result: 'unknown', plan: '' }
    const { status, paymentStatus, plan } = await response.json() as { status?: string; paymentStatus?: string; plan?: PlanId | '' }
    const result: CheckoutResult = status === 'complete'
      ? paymentStatus === 'paid' || paymentStatus === 'no_payment_required' ? 'confirmed' : 'processing'
      : 'incomplete'
    return { result, plan: plan ?? '' }
  } catch {
    return { result: 'unknown', plan: '' }
  }
}

/** InfinitePay return: the server re-checks the payment with InfinitePay before reporting it. */
export async function confirmInfinitePay(orderNsu: string, slug: string, transactionNsu: string): Promise<{ result: CheckoutResult; plan: PlanId | '' }> {
  try {
    const response = await fetch('/api/infinitepay/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(15_000),
      body: JSON.stringify({ orderNsu, slug, transactionNsu }),
    })
    if (!response.ok) return { result: 'unknown', plan: '' }
    const { result, plan } = await response.json() as { result?: string; plan?: PlanId | '' }
    return { result: result === 'confirmed' ? 'confirmed' : result === 'processing' ? 'processing' : 'unknown', plan: plan ?? '' }
  } catch {
    return { result: 'unknown', plan: '' }
  }
}
