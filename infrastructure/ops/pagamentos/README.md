# Operação dos pagamentos (210)

Estes scripts foram usados na implantação e na homologação de pagamentos de 22/11/2026.

- Rodam na 210 como `asc` e só passam pelo `asc-ctl`.
- Nenhum imprime segredos ou dados de clientes.
- Os `.mjs` rodam **dentro** do `asc-commercial` e os `.py` dentro do `asc-api`:

```
sudo -n /usr/local/sbin/asc-ctl exec asc-commercial -- node --input-type=module -e "$(cat validar-credenciais.mjs)"
sudo -n /usr/local/sbin/asc-ctl exec asc-api -- python3 -c "$(cat contas-de-cliente.py)"
```

| Script | O que faz | Escreve algo? |
| --- | --- | --- |
| `implantar-comercial.sh TAG` | Instala o código (`branch-pay.tgz`), compila a imagem comercial e recria só o `asc-commercial`. Prova que os outros contêineres mantiveram os mesmos processos | Código em `~/ai-stream-clipper` (com cópia anterior) e o contêiner comercial |
| `compilar-comercial.sh TAG` | Instala o código e **só compila** a imagem; o contêiner em execução não é recriado | Código em `~/ai-stream-clipper` (com cópia anterior) e a imagem nova |
| `validar-vinculo.sh` | Recria `api` e `commercial` e valida o vínculo HMAC nos dois sentidos, inclusive a recusa de um segredo errado | Recria os dois contêineres |
| `api-para-comercial.py` | Chamada assinada da API para `/internal/billing/status`: o que está pronto ou aberto e as contagens do outbox | Não |
| `validar-credenciais.mjs` | Formato e modo das chaves, conta Stripe, endpoint de webhook e seus eventos, preços, `payment_check` da InfinitePay | Não |
| `infinitepay-chave.mjs` | Compara a resposta do `payment_check` com a chave real e com uma inválida: prova que a chave e o handle são aceitos | Não |
| `caminhos-webhook.mjs` | Os webhooks direto no contêiner e pelo endereço público (Cloudflare), com assinatura e token inválidos | Não |
| `fatos-webhook-stripe.mjs` | Eventos que cada endpoint da conta assina e eventos já recebidos | Não |
| `stripe-conta-e-repasses.mjs` | Requisitos da conta Stripe, repasses, capacidades e saldo | Não |
| `entrega-real-stripe.mjs` | Prova a entrega real e a assinatura de um evento do Stripe sem dinheiro. Acrescenta `customer.created` e `customer.deleted` **só ao nosso endpoint**, cria e apaga um cliente descartável e restaura o endpoint | Não guarda nada nosso. No Stripe, altera o endpoint só durante o teste (é restaurado no fim) e cria e apaga um cliente descartável. **Aborta se o outro projeto assinar esses eventos** |
| `contas-de-cliente.py` | Quantas contas de cliente e assinaturas existem (só contagens) | Não |

A verificação de uma compra real controlada é `commercial/scripts/purchase-check.js`
(veja `docs/HOMOLOGACAO_COMPRA_REAL.md`).
