import catalog from '../catalog.json'
import type { Language } from './content'

export const plans = catalog.plans
export type PlanId = keyof typeof plans
export type Market = keyof typeof catalog.markets
export const planIds = Object.keys(plans) as PlanId[]

export function initialPreferences() {
  const params = new URLSearchParams(window.location.search)
  const requestedLanguage = params.get('lang')
  const requestedMarket = params.get('market')
  const language: Language = requestedLanguage === 'en' || requestedLanguage === 'pt-BR'
    ? requestedLanguage
    : navigator.language.toLowerCase().startsWith('pt') ? 'pt-BR' : 'en'
  const market: Market = requestedMarket === 'BR' || requestedMarket === 'US'
    ? requestedMarket
    : language === 'pt-BR' ? 'BR' : 'US'
  return { language, market }
}

export function formatPrice(plan: PlanId, market: Market) {
  const { priceLocale, currency } = catalog.markets[market]
  const amount = new Intl.NumberFormat(priceLocale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(plans[plan].monthlyMinor[market] / 100)
  return { amount, symbol: currency === 'BRL' ? 'R$' : 'US$' }
}
