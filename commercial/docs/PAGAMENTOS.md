# Pagamentos: Stripe (EUA) e InfinitePay (Brasil)

> **Integração com as contas:** o pagamento confirmado pelo provedor vira acesso automaticamente.
>
> - **O que este serviço faz:** grava o estado financeiro e uma fotografia da assinatura (outbox) na mesma transação.
> - **O que a API principal faz:** recebe a fotografia por HMAC e ativa, estende, suspende ou encerra o plano da conta, aplicando as cotas.
> - **Onde está o detalhe:** arquitetura, regras e homologação em [docs/ASSINATURAS.md](../../docs/ASSINATURAS.md).
>
> Só há checkout para contas autenticadas do painel, com e-mail verificado. A landing não cria mais cobranças.

| Mercado | Moeda | Provedor | Como cobra |
| --- | --- | --- | --- |
| US | USD | **Stripe**, exclusivo | Assinatura recorrente (Stripe Checkout e Billing) |
| BR | BRL | **InfinitePay** (Checkout Integrado) | Um link de pagamento por mês, com Pix ou cartão, renovado pelo nosso sistema |

O checkout é pedido pela API principal, por HMAC, para uma conta autenticada (`/internal/billing/checkout` com `account_id`, plano, mercado e idioma). Preço, moeda e provedor são decididos no servidor a partir do `catalog.json`. Cada mercado só vende quando o seu provedor **e** o vínculo com a API (`ASC_BILLING_SECRET`, `ASC_MAIN_API_URL`, `ASC_APP_URL`) estão configurados.

*English summary at the end.*

## Stripe (USD)

Segue o projeto de referência [HowTo-Software/website.blog](https://github.com/HowTo-Software/website.blog): checkout em modo assinatura, webhook assinado como fonte da verdade e portal do cliente.
- **Preço:** o servidor busca o preço no Stripe pela lookup key `asc_<plano>_usd_monthly` e confere valor, moeda e intervalo com o catálogo. Se divergir, o checkout é recusado.
- **Webhook:** cada evento é processado uma vez e eventos atrasados não sobrescrevem estado novo. Se o processamento falhar, a resposta é 500 e o Stripe tenta de novo.
- **Faturas e recibos:** quem emite é o Stripe, que também tenta o cartão de novo quando o pagamento falha.
- **`npm run stripe:sync`:** cria só preços em USD e recusa chave de produção sem `--live`.

## InfinitePay (BRL)

### Contrato conferido com a documentação oficial

Fonte: <https://www.infinitepay.io/checkout-documentacao>. O teste `server/infinitepay.test.js` verifica que o sistema usa só o que está documentado:

| Item | Documentação | Uso no sistema |
| --- | --- | --- |
| Base | `https://api.checkout.infinitepay.io` | Igual |
| Criar link | `POST /links`: `handle`, `items[{quantity, price, description}]` com `price` em centavos, `order_nsu`, `redirect_url`, `webhook` com `url` e `events`, `customer`, `options` | Só esses campos |
| Opções (exigem `X-Api-Key`) | `single_payment`, `valid_until` (ISO, futuro, até 1 ano), `payment_methods_enabled` (`credit_card`, `pix`) | **Sempre enviadas** |
| Confirmar | `POST /payment_check`: `handle`, `order_nsu`, `transaction_nsu`, `slug` → `success`, `paid`, `amount`, `paid_amount`, `installments`, `capture_method` | Única fonte de verdade de pagamento |
| Webhook | Campos `invoice_slug`, `transaction_nsu`, `order_nsu`, `amount`, `paid_amount`, `receipt_url`…; eventos `created`, `paid`, `settled`, `canceled`, `refunded`, `payment_failed`; **sem assinatura**; não-2xx causa nova entrega | Pede `paid`, `refunded` e `canceled`, e trata cada aviso só como sinal para consultar o `payment_check` |
| Estorno e cancelamento | **Não existem pela API**, só pelo app | Registrados pelo operador (veja abaixo) |
| Ambiente de teste | **Não existe** | A API é simulada nos testes |

**Lacunas da documentação e como o sistema lida com cada uma:**
- **Nome do campo da URL na resposta de `/links`:** não documentado. O sistema aceita `url`, `link`, `checkout_url` e `payment_url`, e só se o valor for `https://…infinitepay.io`.
- **Corpo do webhook:** não traz o tipo do evento. Por isso nenhum aviso é aplicado sem consultar o `payment_check`.
- **Resposta do `payment_check` depois de um estorno:** não documentada. Se uma cobrança paga passar a constar como não paga, ela vai para **revisão** e não perde acesso sozinha.
- **Formato do webhook:** a documentação mostra tanto `webhook_url` quanto o objeto `webhook` com `url` e `events`. O sistema usa o objeto, porque é ele que permite pedir `refunded` e `canceled`. Isso precisa ser conferido no primeiro teste real.

### Chave de API obrigatória

O checkout BR **só liga** quando estiverem presentes:
- `INFINITEPAY_HANDLE`;
- `INFINITEPAY_API_KEY` (`inchk_…`);
- `INFINITEPAY_WEBHOOK_TOKEN` (32 caracteres ou mais);
- `COMMERCIAL_ORIGIN` em **https** (http só em `localhost` ou `127.0.0.1`).

Todo link é criado com `single_payment: true`, `valid_until` e `payment_methods_enabled: ["credit_card", "pix"]`. **Nunca são criados links reutilizáveis ou sem vencimento.** O log de inicialização lista os nomes das variáveis que faltam, nunca os valores.

### Uma cobrança por assinatura e ciclo (idempotência)

- **Ciclo:** a primeira compra é o ciclo `first`. Cada renovação é o ciclo `due:<paid_until>`, ou seja, o vencimento que ela cobre.
- **Índice único:** só pode existir uma cobrança ativa (`creating`, `open`, `paid` ou `reversal_review`) por assinatura e ciclo. Isso vale mesmo com reinicializações e vários processos ou agendadores rodando ao mesmo tempo.
- **Reserva com o corpo congelado:** a cobrança é gravada como `creating` antes da chamada à InfinitePay, **junto com o corpo exato da requisição** (`request_json`). Toda nova tentativa reenvia esse corpo **byte a byte**, com o mesmo `order_nsu`, mesmo que a configuração tenha mudado no meio, por exemplo numa troca do token do webhook. A documentação diz que uma requisição idêntica pode devolver o mesmo link.
- **Recuperação, inclusive da primeira compra:** uma falha de rede ou de resposta não encerra a cobrança, que continua em `creating` com o mesmo `order_nsu`. A recuperação acontece de duas formas:
  - **Pelo cliente:** o próximo clique do mesmo cliente reenvia o corpo gravado.
  - **Pelo agendador:** a cada hora, ele recupera as cobranças em `creating` com mais de 10 minutos (primeira compra e renovação), para não duplicar uma chamada que ainda esteja em andamento. Mesmo que o cliente não volte, o link fica pronto e um pagamento eventual é confirmado normalmente.
  - **Limite:** depois de 5 tentativas, a cobrança é encerrada (`link_gave_up`) e um novo clique abre outra.
- **Checkouts simultâneos do mesmo cliente:** um índice único permite **uma assinatura pendente por e-mail, plano e idioma**. A reserva acontece numa transação `BEGIN IMMEDIATE`, que serializa a escrita mesmo entre processos. Cliques duplos, abas simultâneas ou dois servidores no mesmo banco compartilham a mesma assinatura e a mesma cobrança. E-mails ou planos diferentes nunca compartilham.
- **Confirmações repetidas:** webhook duplicado, navegador e webhook ao mesmo tempo, ou reconciliação, geram **um** pagamento e **um** mês de acesso. A garantia vem de uma transação `BEGIN IMMEDIATE`, da troca de status condicional e de um registro único `payment_confirmed` no histórico.

### Estados e renovações

| Estado | Recebe link de renovação? | Transição |
| --- | --- | --- |
| `pending` (aguardando a 1ª compra) | Não | Passa a `abandoned` quando o primeiro link (válido por 7 dias) expira sem pagamento |
| `active` | Sim, a partir de 5 dias antes de `paid_until` | Passa a `past_due` ao chegar em `paid_until` |
| `past_due` (carência de 3 dias, com acesso) | Sim, o mesmo link do ciclo, se ainda não houver um | Passa a `expired` ao fim da carência |
| `expired`, `abandoned`, `refunded`, `canceled` | **Não**. O cliente assina de novo pelo site | — |

- **Validade do link de renovação:** até o **fim da carência** (`paid_until + 3 dias`). O link e a assinatura vencem juntos.
- **Pagamento em `active` ou `past_due`:** o novo mês começa no **vencimento**, sem dias de graça. Em `expired` ou `pending`, começa **hoje**.
- **Pagamento que chega depois do prazo:** por exemplo, uma corrida bem no vencimento. O dinheiro foi recebido, então o pagamento é **aceito** e o mês começa hoje.
- **Pagamento de assinatura cancelada administrativamente:**
  - **Registro:** o pagamento é gravado (`payment_confirmed` e `payment_after_cancel`), mas **não reativa** a assinatura e **não cobre nenhum período** (`period_start` igual a `period_end`). Um estorno posterior, portanto, não reduz nada.
  - **O que o cliente vê:** **"Pagamento em revisão financeira"**, nunca uma confirmação de assinatura ativa. A resposta do webhook também é `review`.
  - **No relatório:** o caso aparece como `DO NOT GRANT` nas mudanças de acesso e em **Precisa de revisão**. A fotografia enviada à API mantém o acesso encerrado.
  - **Ação do operador:** decide entre o estorno (`refund`) e a conclusão da revisão (`clear-review`). Mesmo com a revisão concluída, o cliente continua vendo "em revisão", e não "ativo".
- **E-mail:** é enviado uma vez por link. Se o SMTP falhar, o envio é repetido a cada execução até 5 vezes. Depois disso, o caso aparece como `email_gave_up` no relatório para envio manual. Falha de SMTP nunca trava o agendador.

### Estornos e histórico financeiro

- **Histórico (`ip_ledger`):** guarda todos os eventos: `link_created`, `payment_confirmed`, `status_*`, `link_expired`, `email_sent`, `email_failed`, `reversal_reported`, `refund_recorded`, `review_cleared`, `cancel_recorded`, `amount_mismatch` e `payment_after_cancel`. Ele só aceita inserções: gatilhos no banco bloqueiam UPDATE e DELETE.
- **Sinal de estorno:** quando um aviso chega para uma cobrança já paga, o sistema consulta o `payment_check` de novo. Se a resposta for `paid: false`, a cobrança vai para `reversal_review` e o caso aparece no relatório em **Precisa de revisão**. **O acesso não muda automaticamente.**
- **Registrar o estorno,** depois de feito no app da InfinitePay:
  ```sh
  npm run infinitepay -- refund <order_nsu> --reason "Estorno no app em 21/11" [--cancel]
  ```
  O comando remove exatamente o mês estornado: os meses pagos depois dele recuam o mesmo tanto. Ele recalcula o estado (`active`, `expired` ou `refunded`) e registra `refund_recorded` com o valor negativo. Pode ser repetido sem efeito duplicado.
- **Outros comandos:**
  - `npm run infinitepay -- clear-review <order_nsu> --reason …`: a revisão concluiu que não houve estorno.
  - `npm run infinitepay -- cancel <subscription_id> --reason …`: para as renovações.
  - `npm run infinitepay -- history <subscription_id>`: mostra o histórico de uma assinatura.
  - `npm run infinitepay -- reconcile [--days 120]`: consulta de novo, no `payment_check`, as cobranças pagas recentes e marca para revisão as que constarem como não pagas. Usa a API real e as chaves.

### Segurança

- **Integridade dos valores:**
  - o valor sai do catálogo e fica gravado na cobrança;
  - o pagamento só conta se `amount` for igual ao valor gravado e `paid_amount` for maior ou igual;
  - divergências são registradas (`amount_mismatch`) e não liberam nada;
  - o `price` enviado pelo navegador é ignorado.
- **Isolamento entre clientes:**
  - cada cobrança pertence a uma assinatura, e a confirmação só afeta a assinatura dona do `order_nsu`;
  - `order_nsu` é um UUID aleatório;
  - o endpoint de confirmação devolve só `result` e `plan`, nunca e-mail, valores ou ids.
- **Webhook:**
  - o token secreto na URL é comparado em tempo constante;
  - aceita o token anterior durante uma troca (`INFINITEPAY_WEBHOOK_TOKEN_PREVIOUS`), porque links antigos guardam a URL antiga;
  - tem limite de 300 avisos a cada 10 minutos por IP.
  - Ainda assim, **o token é só um filtro**: a proteção real é o `payment_check`.
- **Logs do proxy:** o token aparece no caminho da URL e, por isso, **nos logs de acesso do proxy**. Desative o log dessa rota:
  ```nginx
  location /api/infinitepay/webhook/ { access_log off; proxy_pass http://127.0.0.1:8790; }
  ```
  ```caddyfile
  log_skip /api/infinitepay/webhook/*
  ```
  Se o token vazar, gere um novo, mova o antigo para `INFINITEPAY_WEBHOOK_TOKEN_PREVIOUS` até os links antigos vencerem (no máximo 8 dias) e depois remova-o. Cada link guarda no corpo congelado a URL com o token da época em que foi criado, e as novas tentativas mantêm essa URL. Por isso o token anterior precisa continuar aceito durante a troca. O aplicativo nunca registra URLs, tokens, chaves ou e-mails nos próprios logs, e os testes verificam isso.
- **Outras proteções:** a rota de retorno repassa só identificadores validados. Os corpos JSON têm tamanho limitado e as rotas têm limite de tentativas. As chaves ficam só no ambiente.

### Tabelas

| Tabela | Conteúdo |
| --- | --- |
| `ip_subscriptions` | Assinatura BR: e-mail, plano, idioma, estado e `paid_until` |
| `ip_charges` | Uma linha por link: ciclo, valor, estado (`creating`, `open`, `paid`, `expired`, `failed`, `refunded`, `reversal_review`), corpo congelado da requisição (`request_json`), marca de revisão (`review = after_cancel`), validade, tentativas de link e de e-mail, dados confirmados do pagamento e o período coberto (`period_start` a `period_end`) |
| `ip_ledger` | Histórico financeiro, só com inserções |

## Configuração

Copie `.env.example` para `.env`, que é ignorado pelo git.

**Stripe (US), em modo de teste:**
1. Preencha `STRIPE_SECRET_KEY=sk_test_…` e rode `npm run stripe:sync`.
2. Configure o webhook (`stripe listen --forward-to 127.0.0.1:8790/api/stripe/webhook`, ou pelo painel) e copie o segredo para `STRIPE_WEBHOOK_SECRET`.
3. Teste com o cartão `4242 4242 4242 4242`.

**InfinitePay (BR):**
1. No painel, ative **"Habilitar checkout integrado"** e gere a chave `inchk_…`. Ela é mostrada uma única vez.
2. Preencha `INFINITEPAY_HANDLE` (InfiniteTag sem o `$`), `INFINITEPAY_API_KEY` e `INFINITEPAY_WEBHOOK_TOKEN`. Para gerar o token: `node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"`.
3. Configure o SMTP (`SMTP_*`). Sem ele, os links de renovação ficam no relatório para envio manual.
4. Desative o log de acesso da rota do webhook no proxy (veja Segurança).
5. **Teste real, com autorização da HTS:** não há sandbox. É preciso fazer uma compra real pequena e estornar pelo app. Depois, confira:
   - se o webhook chega com o formato `webhook` com `events`;
   - qual campo traz a URL na resposta;
   - como o `payment_check` responde depois do estorno;
   - se o `npm run infinitepay -- refund` deixa o histórico correto.

## Antes de cobrar de verdade

- [ ] **Integração com o painel:** liberar e retirar o acesso automaticamente. Até lá, as ações manuais estão no relatório.
- [ ] Teste real autorizado da InfinitePay (compra e estorno), conferindo as lacunas da documentação listadas acima.
- [ ] Publicar a privacidade e os termos, com os dados do vendedor exigidos pelo Decreto 7.962/2013 e pelo CDC.
- [ ] Definir os impostos com um contador.
- [ ] Stripe: rodar `npm run stripe:sync -- --live` e criar o webhook de produção.
- [ ] Configurar HTTPS, o log do proxy sem o token, `COMMERCIAL_TRUST_PROXY` e o backup do SQLite.
- [ ] Decidir as lives simultâneas do Creator (catálogo: 2; estudo comercial: 1).

## English summary

> Provider-confirmed payments become access automatically. This service commits the financial change and an entitlement snapshot together (outbox), and the main API applies it to the account and its quotas. See `docs/ASSINATURAS.md`. Checkout exists only for authenticated dashboard accounts with a verified e-mail.

**USD uses Stripe only.** It works as before: subscription Checkout, a signed webhook and the customer portal.

**BRL uses InfinitePay**, checked against the official documentation:
- **API key:** `inchk_` is required. Every link is single-use and expires (`single_payment` and `valid_until`).
- **One charge per subscription and cycle:** a unique index enforces it, and the exact `/links` payload is frozen at reservation (`request_json`). Retries resend it byte for byte with the same `order_nsu`, even after a webhook token rotation.
- **Crash recovery:** interrupted first checkouts and renewals are recovered by the buyer's next click or by the hourly job.
- **Simultaneous checkouts:** one pending subscription per email, plan and language. Reservation runs in `BEGIN IMMEDIATE`, so simultaneous checkouts from one customer, even across processes, share one charge.
- **Confirmation:** it is transactional and records exactly one `payment_confirmed`.
- **Payments after an administrative cancellation:** they are recorded with no period and never reactivate. The buyer sees "payment under financial review" instead of a confirmation.
- **Payments:** they only count after `payment_check` reports `paid: true` for the stored amount.
- **State machine:** pending and abandoned, active, past due with a 3-day grace period, expired, refunded and canceled. Only active and past-due subscriptions get renewal links, and each link is valid until the end of grace.
- **Refunds:** a reversal signal puts the charge under review without removing access. The operator records the refund with `npm run infinitepay -- refund`, which removes exactly that month.
- **Ledger:** an append-only table keeps the financial history.
- **Logs:** the webhook token must be excluded from proxy access logs, and token rotation is supported. The app never logs tokens, keys or emails.
