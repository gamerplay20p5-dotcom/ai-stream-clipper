import { useEffect, useState } from 'react'
import type { Language } from './content'
import type { Market, PlanId } from './market'

export interface BillingConfig { enabled: boolean; portalUrl: string }
export const billingOff: BillingConfig = { enabled: false, portalUrl: '' }

/** Server-side on/off switch for Stripe Checkout. Pre-rendered HTML always starts with the waitlist. */
export function useBillingConfig() {
  const [config, setConfig] = useState<BillingConfig>(billingOff)
  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/billing/config', { signal: controller.signal })
      .then(response => (response.ok ? response.json() : billingOff))
      .then((value: BillingConfig) => setConfig({
        enabled: value.enabled === true,
        portalUrl: typeof value.portalUrl === 'string' && value.portalUrl.startsWith('https://billing.stripe.com/') ? value.portalUrl : '',
      }))
      .catch(() => {})
    return () => controller.abort()
  }, [])
  return config
}

export type CheckoutError = 'rate' | 'unavailable'

/** Asks the server for a Checkout Session; only the plan, market and language leave the browser. */
export async function startCheckout(plan: PlanId, market: Market, language: Language): Promise<CheckoutError | null> {
  try {
    const response = await fetch('/api/checkout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(20_000),
      body: JSON.stringify({ plan, market, language }),
    })
    if (response.status === 429) return 'rate'
    if (response.status !== 201) return 'unavailable'
    const { url } = await response.json() as { url?: string }
    if (typeof url !== 'string' || !url.startsWith('https://checkout.stripe.com/')) return 'unavailable'
    window.location.assign(url)
    return null
  } catch {
    return 'unavailable'
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
