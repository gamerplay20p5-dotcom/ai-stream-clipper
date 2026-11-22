# Homologação com compra real controlada (Stripe e InfinitePay)

**Objetivo:** provar em produção o caminho inteiro com dinheiro de verdade, em valor mínimo e por uma conta da HTS,
**sem abrir os pagamentos ao público**. O caminho:
pagamento → webhook → assinatura no serviço comercial → snapshot assinado → plano, cotas e histórico no painel →
estorno ou cancelamento → acesso encerrado.

A compra é feita por uma pessoa da HTS, com cartão ou Pix próprios. Nada é cobrado automaticamente.

## Antes

| # | O que | Quem |
| --- | --- | --- |
| 1 | Repasses do Stripe habilitados (conta bancária). Sem isso a compra funciona, mas o dinheiro fica retido | Dono da conta Stripe |
| 2 | asc-ctl 1.3.1 instalado na 210 (aceita `COMMERCIAL_PAYMENTS_TEST_ACCOUNTS`) | Administrador da 210 |
| 3 | Conta de cliente de teste: painel → Usuários → novo usuário, papel `customer`, entrega "senha inicial". Anote o **id** da conta | Admin do painel |
| 4 | Opcional, para mover o mínimo: cupom de **uso novo**, válido só para Basic, limite total 1 e por cliente 1, expirando no dia seguinte, deixando o valor no mínimo permitido (**R$ 1,00** e **US$ 0,50**, `MIN_CHARGE`). Sem cupom, o Basic custa R$ 59,90 e US$ 19,99 | Admin do painel |
| 5 | Liberar só essa conta: `COMMERCIAL_PAYMENTS_TEST_ACCOUNTS=<id>` no `/etc/asc/asc.env` e depois `sudo asc-ctl compose up --gpu commercial`. Os pagamentos públicos continuam fechados (`COMMERCIAL_PAYMENTS_OPEN=false`) | Administrador da 210 |

Para conferir a liberação: o log do comercial diz `payments closed, test accounts set`, e
`https://getclipper.howto.software/api/billing/config` continua com `markets: {BR:false, US:false}`.

## Durante: Stripe (USD)

1. Entre no painel com a conta de teste e vá em Conta → Assinatura. Escolha Basic, US$, (cupom) e faça o checkout.
   Pague com cartão real na página do Stripe.
2. Em até 1 minuto, na 210:
   `sudo asc-ctl exec asc-commercial -- node scripts/purchase-check.js <id>`. O esperado:
   - `Stripe sub_…: basic/US status=active` e a fatura `paid` com o valor cobrado;
   - eventos `checkout.session.completed`, `customer.subscription.created` e `invoice.paid` recebidos, com
     assinatura válida;
   - snapshot `entregue`;
   - `API aplicou stripe sub_…: plano basic, acesso active`.
3. No painel da conta de teste:
   - plano Basic ativo, com a data de acesso;
   - limites do Basic (1 canal, 40 h, 1 simultânea, 5 GB, sem publicação automática);
   - pagamento no histórico;
   - o botão "Gerenciar" abre o portal do Stripe.
4. **Cotas:** tente adicionar um 2º canal. Tem que ser recusado pelo limite do plano.
5. **Encerrar:** no Dashboard do Stripe, reembolse o pagamento (total).
   - `purchase-check` mostra a assinatura em revisão ou encerrada, e a API mostra `acesso ended`;
   - o painel tira o acesso;
   - depois, cancele a assinatura no portal ou no Dashboard para não haver renovação.

## Durante: InfinitePay (BRL)

1. No painel, escolha Basic em R$ (com cupom, se houver) e pague por **Pix** no link da InfinitePay.
2. `purchase-check` deve mostrar:
   - a cobrança `first` em `paid`, com o valor pago e `pix`;
   - a assinatura `active`, com "pago até";
   - `API aplicou infinitepay …: plano basic, acesso active`.
3. Painel: as mesmas conferências de plano, limites e histórico.
4. **Encerrar:** a InfinitePay não estorna pela API.
   - Estorne no app da InfinitePay e registre aqui:
     `sudo asc-ctl exec asc-commercial -- node scripts/infinitepay-admin.js refund <order_nsu> --reason "homologação" --cancel`.
   - `purchase-check` passa a mostrar `refunded`, e o painel tira o acesso.

## Depois

1. Remova `COMMERCIAL_PAYMENTS_TEST_ACCOUNTS` do `asc.env` e recrie só o comercial.
2. Desative a conta de teste e o cupom no painel.
3. Guarde a saída do `purchase-check` e prints do painel como registro da homologação.
4. Só então, com a aprovação final, ponha `COMMERCIAL_PAYMENTS_OPEN=true` e recrie só o comercial.

## O que já foi provado sem dinheiro

- Entrega real de eventos do Stripe, com a assinatura válida.
- Chaves do Stripe e da InfinitePay aceitas.
- Vínculo HMAC entre API e serviço comercial nos dois sentidos.
- Cloudflare liberada para os webhooks.
- Ativação dos três planos e recusa de pagamentos negados ou não pagos (`tests/test_payment_activation_e2e.py`).
- E-mails de cadastro, recuperação de senha e renovação por SMTP real (`tests/test_email_flows_smtp.py`).
