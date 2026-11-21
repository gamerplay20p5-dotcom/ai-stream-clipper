# Pagamentos: Stripe (EUA) e InfinitePay (Brasil)

A landing cobra as assinaturas Basic, Creator e Pro com dois provedores, escolhidos pelo mercado:

| Mercado | Moeda | Provedor | Como cobra |
| --- | --- | --- | --- |
| US | USD | **Stripe**, exclusivo | Assinatura recorrente automática (Stripe Checkout e Billing) |
| BR | BRL | **InfinitePay** (Checkout Integrado) | Link de pagamento por mês, com Pix ou cartão, renovado pelo nosso sistema |

O navegador envia só **plano, mercado e idioma**, e o e-mail no caso do Brasil. Preço, moeda e provedor são decididos no servidor a partir do `catalog.json`. Cada mercado só oferece checkout quando o seu provedor está configurado; sem isso, os planos daquele mercado continuam levando à lista de espera.

*English summary at the end.*

## Stripe (USD)

Segue o projeto de referência [HowTo-Software/website.blog](https://github.com/HowTo-Software/website.blog):
- Checkout em modo assinatura;
- webhook assinado como fonte da verdade;
- portal do cliente para autoatendimento.

```text
"Subscribe to Creator" ─▶ POST /api/checkout {plan, market: US, language}
                            o servidor busca o preço pela lookup key asc_<plano>_usd_monthly
                            e confere valor, moeda e intervalo com catalog.json
                          ─▶ Stripe Checkout ─▶ volta em /?checkout=success&session_id=…
Stripe ─▶ POST /api/stripe/webhook (assinatura verificada) ─▶ subscriptions + invoices
```

- **Preço divergente do catálogo:** o checkout é recusado em vez de cobrar errado.
- **Webhook:** cada evento é processado uma vez, eventos atrasados não sobrescrevem estado novo, e uma falha responde 500 para o Stripe tentar de novo.
- **Eventos tratados:**
  - `checkout.session.completed` e `checkout.session.async_payment_succeeded`;
  - `customer.subscription.created`, `updated`, `deleted`, `paused` e `resumed`;
  - `invoice.finalized`, `paid`, `payment_succeeded`, `payment_failed`, `voided` e `marked_uncollectible`.
- **Cobranças:** o Stripe emite a fatura mensal, tenta o cartão de novo e envia recibos.
- **Portal:** o cliente troca o cartão e cancela pelo portal (`STRIPE_PORTAL_LOGIN_URL`).
- **`npm run stripe:sync`:** cria e atualiza produtos e preços **só em USD** e recusa chave de produção sem `--live`.

## InfinitePay (BRL)

Documentação oficial: <https://www.infinitepay.io/checkout-documentacao>. O que ela diz e muda o desenho:

| Limitação da API | Como o sistema lida |
| --- | --- |
| **Não há cobrança recorrente.** "O Checkout Integrado faz cobrança recorrente? Não." | Cada mês é um **link de pagamento** novo. O servidor gera o link 5 dias antes de vencer e manda por e-mail |
| **O webhook não tem assinatura** | Nada é gravado só com base no webhook. Toda notificação é conferida em `POST /payment_check`, que precisa responder `paid: true` e o mesmo `amount` da cobrança gravada. A URL do webhook tem um token secreto, como filtro extra |
| **Não há ambiente de teste** | Os testes automatizados simulam a API. O teste real exige uma compra de R$ 1,00 ou mais e o estorno depois, pelo app |
| Sem estorno ou cancelamento pela API | Estornos são feitos no app da InfinitePay |
| O nome do campo da URL na resposta não está documentado | O servidor procura `url`, `link`, `checkout_url` e `payment_url`, e só aceita `https://…infinitepay.io` |

```text
"Assinar o Creator" ─▶ janela pede o e-mail (destino dos links de renovação)
  ─▶ POST /api/checkout {plan, market: BR, language, email}
       grava a assinatura (pending) e a cobrança (open)
       POST api.checkout.infinitepay.io/links  {handle, items:[{price: 13990…}], order_nsu, redirect_url, webhook_url, customer}
  ─▶ página da InfinitePay (Pix ou cartão)
  ─▶ GET /pagamento/infinitepay/<idioma>?order_nsu&slug&transaction_nsu… ─▶ /?checkout=success&provider=infinitepay…
       a página chama POST /api/infinitepay/confirm ─▶ payment_check ─▶ "Pagamento confirmado" ou "em processamento"
InfinitePay ─▶ POST /api/infinitepay/webhook/<token> ─▶ payment_check ─▶ acesso + 1 mês
```

- **Valor:** vem de `catalog.json` (`monthlyMinor.BR`, em centavos). O que o navegador manda é ignorado.
- **Acesso:**
  - Cada pagamento confirmado soma **1 mês-calendário** a partir do vencimento atual, ou a partir de hoje se o acesso já tiver vencido. Exemplo: 31/01 vira 28/02.
  - Confirmações repetidas, pelo webhook ou pelo navegador, não contam duas vezes.
- **Retorno sem parâmetros:** o `redirect_url` é uma rota do próprio servidor, então não importa como a InfinitePay anexa seus parâmetros. A rota repassa só os identificadores validados.
- **Agendador de hora em hora** (no próprio serviço):
  - gera o link do próximo mês 5 dias antes de vencer (`INFINITEPAY_RENEW_DAYS_BEFORE`);
  - envia o link por e-mail com SMTP; sem SMTP, o link aparece em `npm run billing:report` para envio manual;
  - marca `past_due` após o vencimento e `expired` depois de 3 dias de carência (`INFINITEPAY_GRACE_DAYS`);
  - marca como `abandoned` a assinatura nunca paga, depois de 8 dias.
- **Com a chave `inchk_`** (`INFINITEPAY_API_KEY`), os links são de **uso único** (`single_payment`), **expiram em 7 dias** (`valid_until`) e aceitam **Pix e cartão** (`payment_methods_enabled`). Sem a chave, os links não expiram, e por isso ela é recomendada.
- **Cancelar:** basta não pagar o próximo link. O e-mail de renovação explica isso e mostra até quando vai o acesso.

### Tabelas (SQLite)

| Tabela | Conteúdo |
| --- | --- |
| `subscriptions`, `invoices`, `stripe_events` | Estado vindo do Stripe |
| `br_subscriptions` | `id`, `email`, `plan`, `language`, `status` (`pending`, `active`, `past_due`, `expired`, `abandoned`), `paid_until` |
| `br_charges` | Um registro por link: `order_nsu`, tipo (`first` ou `renewal`), valor, `status` (`creating`, `open`, `paid`, `expired`, `failed`), link, validade, quando o e-mail foi enviado e os dados confirmados do pagamento (`slug`, `transaction_nsu`, método, valor pago, parcelas) |

## Configuração

Copie `.env.example` para `.env`, que é ignorado pelo git, e preencha os valores. `COMMERCIAL_ORIGIN` (endereço público) é obrigatório para os dois provedores.

**Stripe (US), em modo de teste:**
1. Preencha `STRIPE_SECRET_KEY=sk_test_…` e rode `npm run stripe:sync -- --dry-run`, depois `npm run stripe:sync`.
2. Configure o webhook. Localmente: `stripe listen --forward-to 127.0.0.1:8790/api/stripe/webhook`. No servidor: crie o endpoint no painel com os eventos acima. Copie o `whsec_…` para `STRIPE_WEBHOOK_SECRET`.
3. Opcional: preencha `STRIPE_PORTAL_LOGIN_URL` com o link de login do portal do cliente.
4. Teste com o cartão `4242 4242 4242 4242`. Para simular falha no pagamento, use `4000 0000 0000 0341`.

**InfinitePay (BR):**
1. No painel da InfinitePay, ative **"Habilitar checkout integrado"** e gere uma chave de API (`inchk_…`). Ela aparece uma única vez.
2. Preencha `INFINITEPAY_HANDLE` com a sua InfiniteTag, sem o `$`, e `INFINITEPAY_API_KEY`.
3. Gere um token aleatório para `INFINITEPAY_WEBHOOK_TOKEN`, por exemplo com `node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"`. Não reutilize nenhum outro segredo.
4. Opcional, mas recomendado: configure SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`) para os e-mails de renovação.
5. **Teste real**, porque não há sandbox. Para isso, crie temporariamente um produto de **R$ 1,00** no catálogo de uma cópia local. Também dá para pagar o plano real e estornar pelo app depois. Confira em `npm run billing:report` se a cobrança ficou `paid` e o acesso `active`.
6. **Atenção:** o endereço do webhook fica gravado em cada link. Se o domínio ou o token mudar, mantenha o endereço antigo funcionando até os links antigos vencerem.

Depois: `npm run build && npm start`. O log mostra `checkout US on/off, BR on/off`.

## Antes de cobrar de verdade

- [ ] Publicar a política de privacidade e os termos, e informar os dados do vendedor exigidos pelo Decreto 7.962/2013 e pelo CDC, como a InfinitePay pede.
- [ ] Definir os impostos com um contador. No Stripe, se usar o Stripe Tax, defina `STRIPE_AUTOMATIC_TAX=true`.
- [ ] Stripe: rodar `npm run stripe:sync -- --live` e criar o webhook de produção.
- [ ] InfinitePay: chave de API de produção, SMTP configurado e um teste real com estorno.
- [ ] Definir como o assinante recebe acesso ao painel. Hoje o sistema registra o pagamento e a HTS envia o acesso manualmente.
- [ ] Decidir as lives simultâneas do Creator (catálogo: 2; estudo comercial: 1).
- [ ] HTTPS no proxy, `COMMERCIAL_TRUST_PROXY` configurado e backup do SQLite.

## Segurança

- Chaves só em variáveis de ambiente. O navegador recebe só se cada mercado está ligado e o link público do portal do Stripe.
- Logs registram só o tipo do erro, nunca chaves, tokens ou corpos de requisição.
- O webhook do Stripe é verificado pela assinatura. O da InfinitePay passa por token secreto na URL (comparação em tempo constante) e, principalmente, pelo `payment_check` com o valor conferido.
- As URLs de retorno usam `COMMERCIAL_ORIGIN`, nunca o cabeçalho Host. A rota de retorno da InfinitePay repassa só identificadores validados.
- Requisições JSON têm tamanho limitado e as rotas de pagamento têm limite de tentativas por IP.
- Os dados de cartão ficam no Stripe ou na InfinitePay e nunca passam pelo servidor da HTS.

## English summary

US/USD is billed exclusively by **Stripe**:
- Subscription Checkout with a signed, idempotent webhook.
- Prices are checked against the catalog.
- Customers manage their subscription in the Stripe portal.

Brazil/BRL is billed by **InfinitePay**, whose API has no recurring billing, no sandbox and an unsigned webhook:
- Each month of access is a single-use payment link (Pix or card) priced from the catalog.
- A notice from the webhook or the browser only counts after `payment_check` confirms `paid: true` for the stored amount.
- The webhook URL carries a secret token.
- An hourly job emails the next link 5 days before access ends, then marks the subscription past due and expires it after a 3-day grace period.
