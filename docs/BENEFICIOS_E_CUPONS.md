# Benefícios e cupons (painel Clientes, v0.8.0)

Ferramentas comerciais da HTS dentro de **Clientes**, só para administradores. Elas mantêm separadas as duas
responsabilidades:

- **Acesso** (API principal): compensação de dias, upgrade temporário e cortesias. Nunca mexe em dinheiro.
- **Dinheiro** (serviço comercial, `commercial/`): cupons promocionais no Stripe (USD) e na InfinitePay (BRL). A API
  só repassa pedidos de administradores pelo vínculo assinado (HMAC) e nunca calcula preço.

Toda ação exige `require_admin` e o cabeçalho CSRF, valida a entrada no servidor e fica auditada.

## A. Compensar dias de acesso (crédito de acesso)

Serve para compensar indisponibilidade ou dar um benefício. O administrador escolhe o cliente, os dias (1 a 90) e o
motivo. Antes de confirmar, o painel mostra a data de acesso atual e a que vai ficar.

- **Fica fora do pagamento:** não altera pagamentos, faturas nem o snapshot da assinatura. A próxima cobrança no
  Stripe ou na InfinitePay continua na mesma data.
- **Continua valendo na renovação:** os dias se somam ao fim do acesso pago, qualquer que seja ele. Quando o cliente
  renova, o crédito vai junto para a nova data.
- **Fim natural da assinatura:** depois de um cancelamento no Stripe ou de uma expiração na InfinitePay, o crédito
  ainda vale. Ele conta a partir do momento em que o acesso terminou, nunca devolve o resto do período e mantém as
  cotas do mesmo ciclo.
- **Quando não vale:**
  - depois de estorno, chargeback, suspensão ou cancelamento administrativo;
  - enquanto um pagamento estiver em revisão.
- **Clique duplo:** cada pedido tem uma chave (`request_key`). Repetir a mesma chave, até ao mesmo tempo, devolve o
  mesmo crédito.
- **Revogação:** exige motivo, fica no histórico e a data volta na hora.

## B. Upgrade temporário

Exemplo: um cliente Creator recebe o Pro por 7 dias.

- **Durante o benefício:** valem os limites do Pro (canais, horas, simultâneas, publicações, 4K e recursos).
- **Depois:** o cliente volta sozinho ao Creator pago. Os canais além do limite pausam (os mais novos) e nada é
  apagado.
- **Sem impacto na cobrança:** o preço da assinatura não muda e o acesso não é estendido.
- **Sem cotas duplicadas:** o consumo continua no mesmo período (mesma chave de período), então as horas já usadas
  continuam contando.
- **Eventos da assinatura:** renovação, webhook ou mudança de plano não apagam nem estendem o benefício. Se o
  cliente comprar o Pro, vale o plano pago.
- **Quando não vale:** nunca reativa assinatura suspensa, estornada ou expirada, porque exige um plano ativo.
- **Limites:** um upgrade ativo por vez, com revogação mediante motivo.

## C. Cortesias

São as cortesias Basic, Creator e Pro que já existiam (`courtesy.py`) e continuam sem mudança: cada uma tem uma
assinatura própria com o provedor `hts`. Vale o plano mais alto e, no mesmo plano, o pago tem precedência.

## Histórico

"Histórico de alterações" mostra, por cliente, quem fez o quê, quando e por quê:

- cortesias;
- créditos;
- upgrades;
- revogações;
- bloqueios de automação.

## Cupons promocionais

São criados no painel, por exemplo `MALAIO20`. O administrador define:

- **Tipo:** percentual, valor fixo em R$ e/ou US$, ou dias grátis;
- **Planos elegíveis e mercados:** BRL, USD ou os dois;
- **Validade:** início e expiração;
- **Limites:** total e por cliente;
- **Situação:** ativo ou desativado.

O painel mostra os usos, os clientes, o desconto total e o histórico de cada cupom.

### Dois tipos de uso

| Uso | Onde vale | Duração |
| --- | --- | --- |
| **Assinatura nova** | Só no checkout de uma assinatura nova | **Só a primeira cobrança**; as renovações seguem o preço normal. Os dias grátis existem só aqui |
| **Recorrente para assinaturas em andamento** | O cliente aplica na assinatura ativa, em "Renovação e cancelamento"; nunca no checkout | Desconto nas próximas renovações: N meses (1 a 24) ou para sempre. Não muda o que já foi pago |

### Regras

- **Valores:** o servidor calcula e valida tudo a partir do `catalog.json`. O navegador só mostra o resultado.
- **Termos fixos:** depois de criado o cupom, os termos (tipo, valor, uso, duração, planos e mercados) não mudam.
  Um gatilho no banco garante isso. Dá para mudar só a situação, a expiração e os limites. Cupons nunca são
  apagados: são desativados.
- **Uso duplicado:** cada uso é uma reserva, criada na mesma transação que confere os limites. Checkouts
  simultâneos não passam do limite, e o mesmo cliente clicando duas vezes reaproveita o link ou a sessão.
- **Contas bloqueadas para cupons:** quem tem estorno, chargeback, disputa, revisão ou cancelamento administrativo
  não usa cupons.
- **Valor mínimo:** um desconto nunca deixa o preço negativo nem entre zero e o mínimo de cobrança (US$ 0,50 no
  Stripe; R$ 1,00 na InfinitePay, valor conservador a confirmar com ela). Preço zero conta como gratuidade.
- **Estorno:** uma compra com cupom estornada continua contando o uso. Estornar nunca libera o cupom para nova
  tentativa.

### Stripe (USD)

- **Espelhamento:** cada cupom em dólar vira um **Coupon** e um **Promotion Code** oficiais, criados com chave de
  idempotência. Se o Stripe falhar, o painel mostra o erro e o botão "Sincronizar com o Stripe".
- **Checkout:** o desconto é aplicado explicitamente (`discounts: [{ promotion_code }]`) depois da nossa validação.
  O campo de cupom do próprio Stripe fica desligado (`allow_promotion_codes`), então ninguém digita um código fora
  das nossas regras.
- **Dias grátis:** usam o período de teste oficial (`trial_period_days`).
- **Ciclo da reserva:** o checkout concluído confirma o uso; a sessão expirada ou abandonada (depois de 26 h)
  libera a reserva.
- **Cupom recorrente:** é anexado à assinatura em andamento (`subscriptions.update` com `discounts`) e vale para as
  próximas faturas.

### InfinitePay (BRL)

Os cupons são da própria HTS. As `options.discounts` da InfinitePay (descontos por Pix ou cartão) são outra coisa e
não são usadas.

- **Valor:** o cupom é validado antes de gerar o link. O valor com desconto vai para os itens da cobrança e fica
  **congelado** no `request_json` (novas tentativas mandam exatamente o mesmo pedido). O `payment_check` precisa
  confirmar **esse** valor.
- **Registro:** o preço original, o desconto e o preço final ficam guardados na cobrança e no uso do cupom.
- **Gratuidade:** com 100% de desconto ou dias grátis, o acesso é liberado sem gerar cobrança nenhuma (fica um
  registro `promo_free` no livro-caixa).
- **Troca de cupom:** trocar, tirar ou pôr um cupom aposenta o link aberto e gera outro com o preço certo.
- **Link não pago:** quando ele expira, o uso volta.
- **Renovações com cupom recorrente:**
  - os próximos links já saem com desconto;
  - um link de renovação aberto com o preço antigo é substituído;
  - um mês 100% gratuito é concedido sem cobrança.

## Produção: o que funciona já e o que depende de pagamentos

| Recurso | Na v0.8.0 sem o serviço comercial |
| --- | --- |
| Cortesias | Funcionam |
| Compensação e upgrade temporário | Funcionam, mas só fazem efeito em clientes com assinatura **paga**. Sem venda ativa, não há a quem aplicar |
| Histórico | Funciona |
| Cupons | O painel mostra "serviço comercial não conectado" e nada é apresentado como funcionando |

Para os cupons funcionarem em produção, falta a implantação do serviço comercial, que precisa de autorização
própria:

- um serviço no Compose da 210;
- `ASC_BILLING_SECRET` e `ASC_COMMERCIAL_URL`, que exigem uma nova versão do `asc-ctl` com essas variáveis;
- as chaves de Stripe e InfinitePay;
- os webhooks públicos.

## Migração 0020

- **O que cria:** a tabela `access_benefits` (chave de pedido única; tipo e dias validados por `CHECK`) e as
  colunas `account_subscriptions.last_entitled_until` e `last_period_anchor`, preenchidas nas assinaturas ativas.
- **Natureza:** só acrescenta, sem alterar o que existe.
- **Downgrade:** remove só o que a 0020 criou. Os cupons ficam no banco do serviço comercial, não aqui.

## Testes

| Conjunto | O que cobre |
| --- | --- |
| `tests/test_access_benefits.py` (14) | Prévia, idempotência, cliques simultâneos, renovação, fim natural sem zerar cotas, estorno, suspensão, revisão, precedência com plano pago e cortesia, upgrade no mesmo período, fim do upgrade pausando canais, revogação, permissões e histórico |
| `tests/test_admin_promotions.py` (6) | Rotas só para administradores com CSRF, validação, auditoria, erros explicados, serviço comercial ausente, checkout com cupom (o navegador nunca envia preço), cotação e cupom recorrente |
| `commercial/server/promotions.test.js` (12) | Definições e valores; Stripe simulado (espelhamento idempotente, falha e nova tentativa, checkout, expiração, dias grátis, cupom recorrente); InfinitePay simulada (valor congelado e conferido, gratuidade sem cobrança, troca de cupom, renovações N meses, link expirado, estorno); limites sob concorrência; conta em revisão |
| `tests/test_postgres_benefits.py` (3) e `tests/test_postgres_migrations.py` (cenário 0020) | **PostgreSQL real**: mesma chave em 10 conexões, upgrades simultâneos, crédito junto com renovação, preenchimento e unicidade da 0020 |

Os testes de PostgreSQL só contam como homologados depois de rodarem de verdade na .206.

## English

Admin-only commercial tools in **Customers**. Access and money stay separate: the main API owns access, the
commercial service owns money and every promotion code amount.

- **Access credit:** adds days after the paid access ends. Payments, invoices and the next charge date never
  change, and the credit follows every renewal.
  - It also runs after a natural end (Stripe canceled, InfinitePay expired). It counts from the moment access
    ended, keeps the same quota period and never gives back the rest of that period.
  - It never applies after a refund, a chargeback or a suspension, or during a review.
  - It is idempotent by request key and can be revoked with a reason.
- **Temporary upgrade:** a higher plan's limits until an end date, then the paid plan again automatically.
  - Same price, same period: usage is never reset, and access is never extended.
  - Renewals and webhooks neither remove nor extend it, and it never revives an account without access.
  - One active upgrade at a time.
- **Courtesy plans:** unchanged.
- **History:** every change, with who, when and why.
- **Promotion codes:** percent, fixed amount or free days; eligible plans and markets; dates; total and
  per-customer limits; on/off switch; uses, customers and statistics.
  - Two uses:
    - **new subscription:** first charge only; renewals at the normal price;
    - **recurring for current subscriptions:** the customer applies the code to the running subscription; it
      discounts the next N renewals (or all of them) and never what was already paid.
  - **Rules:**
    - terms are immutable (database trigger) and codes are never deleted;
    - limits are checked and the use is reserved in the same transaction;
    - accounts with refunds, disputes or reviews cannot use codes;
    - prices never go negative or below the minimum charge;
    - a refunded purchase still counts its use.
  - **Stripe:**
    - each code is mirrored as an official Coupon and Promotion Code (idempotently, with a visible retry);
    - checkout applies it explicitly after our validation, and Stripe's own code box is off;
    - free days use the official trial;
    - recurring codes are attached to the running subscription.
  - **InfinitePay:**
    - HTS's own codes; the discounted amount is frozen in the link payload and must be confirmed by
      `payment_check`;
    - 100% discounts and free days grant access with no charge;
    - unpaid links release the use;
    - recurring codes discount the next renewal links.
- **Production:** credits and upgrades work with paid subscriptions. Promotion codes need the commercial service
  deployment (Compose service, `ASC_BILLING_SECRET` / `ASC_COMMERCIAL_URL` in a new `asc-ctl`, provider keys,
  webhooks). That is not part of v0.8.0, and the panel says so.
- **Migration 0020** is additive (`access_benefits` and two columns), and its downgrade removes only what it added.
