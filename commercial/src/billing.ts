import { useEffect, useState } from 'react'
import type { Language } from './content'
import type { Market, PlanId } from './market'

/**
 * Whether each market sells now (US -> Stripe, BR -> InfinitePay) and where the account area lives.
 * There is no checkout on this page: subscribing happens in the dashboard, for a signed-in account with a
 * verified e-mail, and access only follows a payment confirmed by the provider.
 */
export interface BillingConfig { markets: Record<Market, boolean>; appUrl: string; waitlist: boolean }
// Before the server answers, the form is shown (no flash for the common case); the server decides
export const billingOff: BillingConfig = { markets: { BR: false, US: false }, appUrl: '', waitlist: true }

export function useBillingConfig() {
  const [config, setConfig] = useState<BillingConfig>(billingOff)
  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/billing/config', { signal: controller.signal })
      .then(response => (response.ok ? response.json() : billingOff))
      .then((value: Partial<BillingConfig>) => {
        let appUrl = ''
        try {
          const url = new URL(String(value.appUrl ?? ''))
          if (url.protocol === 'https:' || url.hostname === '127.0.0.1' || url.hostname === 'localhost') appUrl = url.origin
        } catch { /* not configured */ }
        setConfig({ markets: { BR: value.markets?.BR === true, US: value.markets?.US === true }, appUrl, waitlist: value.waitlist !== false })
      })
      .catch(() => {})
    return () => controller.abort()
  }, [])
  return config
}

/** Account area with the plan and market pre-selected (sign-in or verified sign-up happens there). */
export function subscribeUrl(appUrl: string, plan: PlanId, market: Market, language: Language) {
  const query = new URLSearchParams({ plan, market, lang: language })
  return `${appUrl}/conta?${query}`
}
