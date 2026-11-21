import { useEffect, useRef, useState } from 'react'
import { CircleAlert, CircleCheck, Clock3, X } from 'lucide-react'
import { checkoutResult, confirmInfinitePay, type BillingConfig, type CheckoutResult } from './billing'
import type { Copy } from './content'
import { plans, type PlanId } from './market'

type Notice = { result: CheckoutResult | 'checking'; plan: PlanId | ''; provider: 'stripe' | 'infinitepay' }
const SESSION_ID = /^cs_(test|live)_[A-Za-z0-9]{10,200}$/

/** Result of a Stripe Checkout return (?checkout=success|cancelled). The webhook, not this notice, records the subscription. */
export function CheckoutNotice({ copy, billing }: { copy: Copy; billing: BillingConfig }) {
  const [notice, setNotice] = useState<Notice | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const t = copy.billing

  useEffect(() => {
    const url = new URL(window.location.href)
    const outcome = url.searchParams.get('checkout')
    const sessionId = url.searchParams.get('session_id') ?? ''
    const provider = url.searchParams.get('provider') === 'infinitepay' ? 'infinitepay' : 'stripe'
    const [orderNsu, slug, transactionNsu] = ['order_nsu', 'slug', 'transaction_nsu'].map(name => url.searchParams.get(name) ?? '')
    if (outcome !== 'success' && outcome !== 'cancelled') return
    // Remove the checkout parameters so a refresh or a shared link does not repeat the notice
    for (const name of ['checkout', 'session_id', 'provider', 'order_nsu', 'slug', 'transaction_nsu']) url.searchParams.delete(name)
    window.history.replaceState(null, '', url)
    if (outcome === 'cancelled') { setNotice({ result: 'cancelled', plan: '', provider }); return }
    if (provider === 'infinitepay') {
      if (!orderNsu || !slug || !transactionNsu) { setNotice({ result: 'unknown', plan: '', provider }); return }
      setNotice({ result: 'checking', plan: '', provider })
      confirmInfinitePay(orderNsu, slug, transactionNsu).then(value => setNotice({ ...value, provider }))
      return
    }
    if (!SESSION_ID.test(sessionId)) { setNotice({ result: 'unknown', plan: '', provider }); return }
    setNotice({ result: 'checking', plan: '', provider })
    checkoutResult(sessionId).then(value => setNotice({ ...value, provider }))
  }, [])

  useEffect(() => {
    if (notice && notice.result !== 'checking') heading.current?.focus()
  }, [notice])

  if (!notice) return null
  const Icon = notice.result === 'confirmed' ? CircleCheck : notice.result === 'checking' || notice.result === 'processing' ? Clock3 : CircleAlert
  // 'review' uses the alert icon and its own text: a payment under review is never presented as an active subscription
  const text = notice.result === 'checking' ? { title: t.notice.checking.title, detail: t.notice.checking.detail }
    : notice.result === 'confirmed' && notice.provider === 'infinitepay'
      ? { title: t.notice.confirmedBr.title, detail: t.notice.confirmedBr.detail(notice.plan ? plans[notice.plan].name : '') }
      : { title: t.notice[notice.result].title, detail: t.notice[notice.result].detail(notice.plan ? plans[notice.plan].name : '') }

  return (
    <section className={`checkout-notice is-${notice.result}`} aria-labelledby="checkout-notice-title">
      <div className="shell checkout-notice-inner">
        <Icon size={24} aria-hidden="true" />
        <div role="status">
          <h2 id="checkout-notice-title" tabIndex={-1} ref={heading}>{text.title}</h2>
          <p>{text.detail}</p>
          {notice.result === 'confirmed' && notice.provider === 'stripe' && billing.portalUrl ? <a className="text-link" href={billing.portalUrl} rel="noopener">{t.manage}</a> : null}
        </div>
        {notice.result !== 'checking' ? <button type="button" className="notice-close" aria-label={t.dismiss} onClick={() => setNotice(null)}><X size={18} aria-hidden="true" /></button> : null}
      </div>
    </section>
  )
}
