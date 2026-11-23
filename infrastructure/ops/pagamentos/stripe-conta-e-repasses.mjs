// Somente leitura. Tipos de evento por endpoint e requisitos de repasse (nomes de campos, nunca valores pessoais).
import Stripe from 'stripe'
const stripe = new Stripe(process.env.STRIPE_SECRET_KEY, { maxNetworkRetries: 1, timeout: 15_000 })
const OURS = 'https://getclipper.howto.software/api/stripe/webhook'
const { data } = await stripe.webhookEndpoints.list({ limit: 20 })
for (const e of data) console.log(`${e.url === OURS ? 'NOSSO' : 'OUTRO'} ${new URL(e.url).host}: ${e.enabled_events.sort().join(' ')}`)
const a = await stripe.accounts.retrieve()
console.log(`conta: tipo=${a.type ?? '-'} business_type=${a.business_type ?? '-'} details_submitted=${a.details_submitted} charges_enabled=${a.charges_enabled} payouts_enabled=${a.payouts_enabled}`)
console.log(`requisitos pendentes agora: ${(a.requirements?.currently_due ?? []).join(', ') || 'nenhum'}`)
console.log(`requisitos vencidos: ${(a.requirements?.past_due ?? []).join(', ') || 'nenhum'} | prazo: ${a.requirements?.current_deadline ? new Date(a.requirements.current_deadline * 1000).toISOString() : '-'} | motivo do bloqueio: ${a.requirements?.disabled_reason ?? '-'}`)
console.log(`requisitos futuros: ${(a.requirements?.eventually_due ?? []).join(', ') || 'nenhum'}`)
console.log(`contas de repasse cadastradas: ${a.external_accounts?.total_count ?? a.external_accounts?.data?.length ?? 'não informado'}`)
console.log(`agenda de repasse: ${JSON.stringify(a.settings?.payouts?.schedule ?? {})}`)
console.log(`capacidades: ${JSON.stringify(a.capabilities ?? {})}`)
try { const b = await stripe.balance.retrieve(); console.log(`saldo disponível: ${JSON.stringify(b.available.map(x => `${x.amount} ${x.currency}`))} pendente: ${JSON.stringify(b.pending.map(x => `${x.amount} ${x.currency}`))}`) } catch (e) { console.log('saldo: ' + (e.type ?? e.name)) }
