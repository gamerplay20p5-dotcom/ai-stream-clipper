// Roda DENTRO do asc-commercial. Nunca imprime valores: só formato, modo, tamanho e respostas dos provedores.
// Chamadas aos provedores são somente leitura (nada é criado, nenhuma cobrança).
import Stripe from 'stripe'
import { infinitePaySettings, missingInfinitePaySettings, INFINITEPAY_API } from './server/infinitepay.js'
import { billingSettings } from './server/billing.js'
import { outboxSettings } from './server/outbox.js'

const env = process.env
const ok = (cond, label, detail = '') => console.log(`${cond ? 'ok  ' : 'ERRO'} ${label}${detail ? ` (${detail})` : ''}`)
const key = env.STRIPE_SECRET_KEY ?? ''
const mode = /_live_/.test(key) ? 'live' : /_test_/.test(key) ? 'test' : 'desconhecido'
const kind = key.startsWith('rk_') ? 'restrita' : key.startsWith('sk_') ? 'secreta' : 'inválida'
ok(/^(sk|rk)_(live|test)_[A-Za-z0-9]{20,}$/.test(key), 'STRIPE_SECRET_KEY: formato', `chave ${kind}, modo ${mode}, ${key.length} caracteres`)
ok(/^whsec_[A-Za-z0-9]{20,}$/.test(env.STRIPE_WEBHOOK_SECRET ?? ''), 'STRIPE_WEBHOOK_SECRET: formato whsec_', `${(env.STRIPE_WEBHOOK_SECRET ?? '').length} caracteres`)
const ip = infinitePaySettings()
const missing = missingInfinitePaySettings(ip)
ok(missing.length === 0, 'InfinitePay: handle, chave inchk_, token do webhook, origem https e ASC_APP_URL válidos', missing.length ? `faltando: ${missing.join(', ')}` : `token ${ip.webhookToken.length} caracteres`)
const b = billingSettings()
ok(b.origin === 'https://getclipper.howto.software' && b.appUrl === 'https://clipper.howto.software', 'origens', `${b.origin} -> ${b.appUrl}`)
const link = outboxSettings()
ok(Boolean(link.secret), 'ASC_BILLING_SECRET (32+ caracteres)', link.secret ? `${link.secret.length} caracteres` : 'ausente')
ok(link.mainUrl === 'https://api:8780', 'ASC_MAIN_API_URL', link.mainUrl)
console.log(`info pagamentos públicos: ${env.COMMERCIAL_PAYMENTS_OPEN === 'true' ? 'ABERTOS' : 'fechados'}`)

const stripe = new Stripe(key, { maxNetworkRetries: 1, timeout: 15_000 })
try {
  const account = await stripe.accounts.retrieve()
  ok(true, 'Stripe: chave aceita pela API', `conta ...${account.id.slice(-4)}, país ${account.country}, moeda ${account.default_currency}, cobranças ${account.charges_enabled ? 'habilitadas' : 'NÃO habilitadas'}, repasses ${account.payouts_enabled ? 'habilitados' : 'não habilitados'}`)
} catch (error) {
  ok(false, 'Stripe: conta', `${error.type ?? error.name} ${error.statusCode ?? ''}`)
}
const HANDLED = ['checkout.session.completed', 'checkout.session.async_payment_succeeded', 'checkout.session.expired',
  'customer.subscription.created', 'customer.subscription.updated', 'customer.subscription.deleted',
  'invoice.paid', 'invoice.payment_failed', 'charge.refunded', 'charge.dispute.created', 'charge.dispute.closed']
try {
  const { data } = await stripe.webhookEndpoints.list({ limit: 20 })
  const ours = data.filter(e => e.url === 'https://getclipper.howto.software/api/stripe/webhook')
  ok(ours.length === 1, 'Stripe: endpoint de webhook cadastrado para getclipper', `${data.length} endpoint(s) na conta, ${ours.length} para getclipper`)
  for (const e of ours) {
    ok(e.status === 'enabled', 'Stripe: endpoint ativo', e.status)
    ok(e.livemode === (mode === 'live'), 'Stripe: endpoint no mesmo modo da chave', e.livemode ? 'live' : 'test')
    const all = e.enabled_events.includes('*')
    const lacking = all ? [] : HANDLED.filter(t => !e.enabled_events.includes(t))
    ok(lacking.length === 0, 'Stripe: eventos necessários assinados', all ? 'todos (*)' : lacking.length ? `faltam: ${lacking.join(', ')}` : `${e.enabled_events.length} eventos`)
    console.log(`info versão da API do endpoint: ${e.api_version ?? 'padrão da conta'}`)
  }
  for (const e of data.filter(e => e.url !== 'https://getclipper.howto.software/api/stripe/webhook')) console.log(`info outro endpoint: ${new URL(e.url).host}${new URL(e.url).pathname} (${e.status})`)
} catch (error) {
  ok(false, 'Stripe: listar webhooks', `${error.type ?? error.name} ${error.statusCode ?? ''} (chave restrita sem permissão de leitura de webhooks?)`)
}
try {
  for (const plan of ['basic', 'creator', 'pro']) {
    const { data } = await stripe.prices.list({ lookup_keys: [`asc_${plan}_usd_monthly`], active: true, limit: 1 })
    console.log(`info preço ${plan} USD no Stripe: ${data[0] ? `${data[0].unit_amount} ${data[0].currency}/${data[0].recurring?.interval}` : 'ainda não existe'}`)
  }
} catch (error) {
  ok(false, 'Stripe: ler preços', `${error.type ?? error.name} ${error.statusCode ?? ''}`)
}

// InfinitePay: payment_check de um pedido que não existe. Não cria nada; mostra se a chave e o handle são aceitos.
try {
  const r = await fetch(`${INFINITEPAY_API}/payment_check`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Api-Key': ip.apiKey },
    body: JSON.stringify({ handle: ip.handle, order_nsu: 'asc-00000000-0000-4000-8000-000000000000', transaction_nsu: 'asc-validacao', slug: 'asc-validacao' }),
    signal: AbortSignal.timeout(10_000),
  })
  const body = await r.json().catch(() => ({}))
  const keys = Object.keys(body).join(',')
  ok(r.status !== 401 && r.status !== 403, 'InfinitePay: API alcançável e chave não recusada', `HTTP ${r.status}, campos: ${keys || '-'}, success=${body.success}, paid=${body.paid}`)
} catch (error) {
  ok(false, 'InfinitePay: payment_check', error.code ?? error.name)
}
