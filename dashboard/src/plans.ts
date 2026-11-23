// Plans, prices and allowances for the subscriber area: the SAME commercial/catalog.json the commercial service
// sells from and the API enforces (copied next to the dashboard at build time, see infrastructure/docker/app.Dockerfile).
// Display only: checkout prices, coupons and limits are always decided by the server.
import catalog from "../../commercial/catalog.json";
import { locale } from "./i18n";

export type PlanId = "basic" | "creator" | "pro";
export type Market = "BR" | "US";
export const PLAN_IDS: PlanId[] = ["basic", "creator", "pro"];
export const plans = catalog.plans;

export function money(minor: number, currency: string): string {
  return new Intl.NumberFormat(locale, { style: "currency", currency: currency.toUpperCase() }).format(minor / 100);
}

export function monthly(plan: PlanId, market: Market): { minor: number; currency: string; label: string } {
  const minor = plans[plan].monthlyMinor[market];
  const currency = catalog.markets[market].currency;
  return { minor, currency, label: money(minor, currency) };
}
