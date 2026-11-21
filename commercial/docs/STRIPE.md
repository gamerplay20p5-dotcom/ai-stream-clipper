# Pagamentos com Stripe

Cobrança das assinaturas Basic, Creator e Pro pela landing comercial. O desenho segue o projeto de referência [HowTo-Software/website.blog](https://github.com/HowTo-Software/website.blog):
- Stripe Checkout em modo assinatura;
- webhook assinado como fonte da verdade;
- portal do cliente para autoatendimento.

*English summary at the end.*

## Como funciona

```text
Visitante ──"Assinar o Creator"──▶ POST /api/checkout {plan, market, language}
                                      │  o servidor busca o preço pela lookup key
                                      │  e confere valor, moeda e intervalo com catalog.json
                                      ▼
                             Stripe Checkout (página do Stripe, cartão etc.)
                                      │
             ┌────────────────────────┴───────────────────────┐
             ▼                                                ▼
   Volta para /?checkout=success&session_id=…      Webhook POST /api/stripe/webhook
   aviso consulta GET /api/checkout/session        assinatura verificada, evento registrado uma vez
   (sem dados pessoais)                            subscriptions + invoices no SQLite
```

- **O navegador envia só plano, mercado e idioma.** Preço, moeda e produto vêm do Stripe pela lookup key `asc_<plano>_<moeda>_monthly`, por exemplo `asc_creator_brl_monthly`. Se o preço no Stripe não bater com o `catalog.json`, o checkout é recusado (503) em vez de cobrar o valor errado.
- **A assinatura só vale depois do webhook.** A página de retorno mostra o resultado, mas quem grava assinaturas e faturas é o webhook, com a assinatura `Stripe-Signature` verificada pela biblioteca oficial.
  - Cada evento é processado uma única vez (tabela `stripe_events`).
  - Eventos atrasados não sobrescrevem um estado mais novo.
  - Se o processamento falhar, a resposta é 500 e o Stripe tenta de novo.
- **Eventos tratados:**
  - `checkout.session.completed` e `checkout.session.async_payment_succeeded`;
  - `customer.subscription.created`, `updated`, `deleted`, `paused` e `resumed`;
  - `invoice.finalized`, `paid`, `payment_succeeded`, `payment_failed`, `voided` e `marked_uncollectible`.
- **Cobranças recorrentes:** o Stripe emite a fatura mensal, tenta o cartão de novo quando o pagamento falha e envia recibos. O estado de cada fatura fica na tabela `invoices`.
- **Autoatendimento:** o link de login do portal do cliente (`STRIPE_PORTAL_LOGIN_URL`) aparece no aviso de confirmação e no rodapé. Pelo portal o cliente troca o cartão, baixa faturas e cancela.
- **Desligado por padrão:** sem `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` e `COMMERCIAL_ORIGIN`, os cards continuam levando à lista de espera e as rotas de pagamento respondem 503.

## Ativar em modo de teste

1. Copie `.env.example` para `.env`. O arquivo é ignorado pelo git.
2. Preencha `STRIPE_SECRET_KEY` com a chave **de teste** (`sk_test_…`) e `COMMERCIAL_ORIGIN` com o endereço público. Para testar localmente, use `http://127.0.0.1:8790`.
3. Crie os produtos e preços a partir do catálogo:
   ```sh
   npm run stripe:sync -- --dry-run   # mostra o que vai mudar
   npm run stripe:sync
   ```
   O script é idempotente. Se um preço mudar no `catalog.json`, ele cria um preço novo que assume a lookup key e arquiva o antigo. Ele recusa chaves de produção sem `--live`.
4. Webhook:
   - **Local:** `stripe listen --forward-to 127.0.0.1:8790/api/stripe/webhook`, com o Stripe CLI. Copie o `whsec_…` mostrado para `STRIPE_WEBHOOK_SECRET`.
   - **Servidor:** no painel do Stripe, crie um endpoint em `https://SEU_DOMINIO/api/stripe/webhook` com os eventos acima e copie o segredo de assinatura.
5. Opcional: ative o portal do cliente no painel (Settings → Billing → Customer portal) e copie o link de login para `STRIPE_PORTAL_LOGIN_URL`.
6. `npm run build && npm start`. O log mostra `checkout enabled`.
7. Pague com o cartão de teste `4242 4242 4242 4242`, com qualquer data futura e qualquer CVC. Para testar falha de pagamento, use `4000 0000 0000 0341`.
8. `npm run billing:report` mostra assinaturas e faturas. Os e-mails aparecem mascarados; para vê-los completos, use `--emails`.

## Antes de cobrar de verdade

- [ ] Publicar a política de privacidade e os termos, e configurar os links deles no Stripe (Settings → Public details).
- [ ] Decidir o enquadramento tributário com um contador. Se for usar o Stripe Tax, configure-o e defina `STRIPE_AUTOMATIC_TAX=true`.
- [ ] Rodar `npm run stripe:sync -- --live` com a chave de produção e criar o webhook de produção.
- [ ] Definir como o assinante recebe acesso ao painel. Hoje o webhook registra a assinatura e a HTS envia o acesso manualmente, e o aviso de confirmação diz isso ao cliente.
- [ ] Decidir as lives simultâneas do Creator (catálogo: 2; estudo comercial: 1).
- [ ] HTTPS no proxy, `COMMERCIAL_TRUST_PROXY` configurado e backup do arquivo SQLite.

## Segurança

- As chaves ficam só nas variáveis de ambiente. Nada é enviado ao navegador além de `enabled` e do link público do portal.
- Os erros registram só o tipo (por exemplo `StripeAuthenticationError`), nunca chaves ou payloads.
- `POST /api/checkout` aceita só JSON de até 2 kB e tem limite de 20 tentativas por IP a cada 10 minutos. A consulta da sessão valida o id e devolve só status e plano.
- As URLs de retorno usam `COMMERCIAL_ORIGIN`, nunca o cabeçalho Host.
- Os dados do cartão nunca passam pelo servidor da HTS. Ficam no Stripe Checkout.

## English summary

Stripe billing for the commercial site, following HowTo-Software/website.blog:
- **Checkout:** subscription-mode Checkout Sessions, with the price resolved server-side from lookup keys and checked against `catalog.json`.
- **Webhook:** a signed webhook is the source of truth. Each event is processed once, stale events are ignored and failed deliveries are retried. Subscriptions and invoices are stored in SQLite.
- **Self-service:** the customer portal link is shown on confirmation and in the footer.
- **Off by default:** checkout stays disabled until the secret key, webhook secret and public origin are configured. Card data never touches HTS servers.
- **Getting started:** see the steps above for test-mode setup, `npm run stripe:sync`, and the launch checklist.
