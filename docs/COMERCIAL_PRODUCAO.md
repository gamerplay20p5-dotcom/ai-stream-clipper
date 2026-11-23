# Site comercial em produção (`asc-commercial`, 210)

## Como roda

| Item | Valor |
| --- | --- |
| Contêiner | `asc-commercial`, projeto Docker `asc` da 210. Imagem `ai-stream-clipper-commercial:<tag>`, compilada na própria 210 com `sudo asc-ctl build commercial <tag>` (`infrastructure/docker/commercial.Dockerfile`, Node.js 24.13) |
| Porta | `192.168.1.210:8790` (rede local). O proxy do servidor publica em `https://getclipper.howto.software` |
| Persistência | Volume `asc_commercial`, montado em `/data`, com o arquivo `commercial.sqlite`: lista de espera, assinaturas, livro-caixa, outbox e cupons |
| Execução | `restart: unless-stopped`, sistema de arquivos somente leitura, `cap_drop: ALL`, `no-new-privileges`, 1 CPU e 512 MB, usuário `node` (sem root) |
| Saúde | Healthcheck do Compose em `/api/billing/config` a cada 20 s |
| Logs | `sudo asc-ctl compose logs commercial` |

O painel `clipper.howto.software`, a API, os workers e o banco não são tocados por este serviço.

## Configuração de produção

**Arquivo:** `/etc/asc/asc.env` na 210. O dono é o root, com permissão 600. Ele é instalado com
`sudo asc-ctl env-install <arquivo>`, a partir de um arquivo temporário com permissão 600 que é apagado em seguida.
Os segredos aparecem como `***` em `env-show` e nunca vão para o repositório.

O que já vale sem configurar nada:
- `COMMERCIAL_ORIGIN`: o padrão é `https://getclipper.howto.software`.
- `ASC_APP_URL`: segue o `ASC_PUBLIC_ORIGIN` (`https://clipper.howto.software`).
- `ASC_COMMERCIAL_TAG`: o padrão é `1.0.0`.

### Variáveis que você ainda precisa fornecer

Enquanto faltarem, a parte correspondente fica **desligada**.

| Variável | Para quê | Sem ela |
| --- | --- | --- |
| `ASC_BILLING_SECRET` | Segredo HMAC entre a API e o serviço comercial, com 32 caracteres ou mais. É o mesmo valor nos dois serviços; o administrador pode gerar | Nenhum checkout liga, em nenhuma moeda |
| `INFINITEPAY_HANDLE`, `INFINITEPAY_API_KEY` (`inchk_…`) | Checkout em BRL (Pix e cartão) | BRL desligado |
| `INFINITEPAY_WEBHOOK_TOKEN` | Texto aleatório de 32 a 128 caracteres na URL do webhook (`https://getclipper.howto.software/api/infinitepay/webhook/<token>`) | BRL desligado |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Checkout em USD. O webhook é `https://getclipper.howto.software/api/stripe/webhook` | USD desligado |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURE`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` | E-mails de renovação da InfinitePay | Os links de renovação aparecem no painel do cliente, mas não são enviados por e-mail |
| `COMMERCIAL_PAYMENTS_OPEN=true` | Abre os pagamentos ao público. Exige asc-ctl 1.3.1 para passar pelo `env-install` | Fechados: mesmo com todas as credenciais, nenhum checkout é criado. Webhooks, vínculo assinado e reconciliação funcionam normalmente |
| `COMMERCIAL_WAITLIST_OPEN=true` | Abre a lista de espera | Fechada: a página explica e **não coleta dados**. Ligue só depois de publicar a política de privacidade e os termos |

Para os checkouts passarem a valer, a API também precisa do mesmo `ASC_BILLING_SECRET`. O valor é lido quando o
contêiner `asc-api` é recriado (`sudo asc-ctl compose up --gpu api`).

### Acrescentar o `ASC_BILLING_SECRET` sem expor os outros segredos

Como administrador na 210 (gera 64 caracteres aleatórios e não mostra o valor):

```
sudo sh -c 'f=/etc/asc/asc.env; grep -q "^ASC_BILLING_SECRET=" "$f" && echo "já existe" || { [ -n "$(tail -c1 "$f")" ] && echo >> "$f"; printf "ASC_BILLING_SECRET=%s\n" "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d "[:space:]")" >> "$f"; echo "adicionado"; }; stat -c "%U %a" "$f"'
```

Depois, recrie só `api` e `commercial` (`sudo asc-ctl compose up --gpu api commercial`).

### Conta Stripe compartilhada

A conta Stripe de produção também atende outro projeto (`host.howto.software`), e os eventos dele chegam ao nosso
webhook. O serviço só grava assinaturas e faturas nossas: pelo lookup key `asc_*` ou pelo metadata do nosso checkout
com `account_id`. Os produtos e preços são criados com `metadata.asc_plan`, sem tocar nos do outro projeto:
`sudo asc-ctl exec asc-commercial -- node scripts/stripe-sync.js --live`.

## Publicação em HTTPS (fora do projeto `asc`)

1. **DNS:** `getclipper.howto.software` está na Cloudflare, com proxy ligado.
2. **Proxy do servidor (`caddy-proxy` do Hank):** o upstream precisa ser exatamente `http://192.168.1.210:8790`.
   - É **HTTP simples**, diferente do painel, que usa HTTPS na 8780.
   - Não serve `localhost`, `127.0.0.1` nem `asc-commercial`, porque o Caddy roda em outro contêiner e outra rede.

   ```
   getclipper.howto.software {
       reverse_proxy http://192.168.1.210:8790
   }
   ```
   O webhook da InfinitePay leva o token na URL: tire `/api/infinitepay/webhook/*` dos logs de acesso.
3. **Cloudflare com proxy:** a Cloudflare bloqueia chamadas de servidores com 403 (a verificação de navegador e de
   bots). Os webhooks da Stripe e da InfinitePay seriam bloqueados. Antes de ligar os pagamentos, faça uma destas
   duas coisas:
   - crie uma regra de exceção (WAF, "Skip") para `/api/stripe/webhook` e `/api/infinitepay/webhook/*`; ou
   - deixe o registro em **DNS only**, como o painel.

   O SSL/TLS da zona deve estar em **Full (strict)**.

**Diagnóstico de 22/11/2026:**
- O site responde 200 em `192.168.1.210:8790`.
- O Caddy devolve 502 para `getclipper`, e **nenhuma conexão do Caddy chega ao contêiner**: os testes de dentro dele
  só mostram o healthcheck e as requisições de controle. Ou seja, o upstream configurado no Caddy não é
  `192.168.1.210:8790`.

**Homologação de 22/11/2026 (pagamentos ainda fechados ao público):**
- Cloudflare: os webhooks públicos chegam ao site. A assinatura inválida do Stripe recebe 400 e o token certo da
  InfinitePay é aceito.
- Vínculo assinado entre API e serviço comercial: funciona nos dois sentidos. Um segredo errado é recusado (401).
- Stripe: entrega real confirmada com assinatura válida. Nosso endpoint ganhou `customer.created` e
  `customer.deleted` só durante o teste, com um cliente descartável sem e-mail; o outro projeto não assina esses
  eventos, e o endpoint voltou aos 17 eventos originais.
- Ativação, testada entre processos reais (`tests/test_payment_activation_e2e.py`):
  - Basic, Creator e Pro no Stripe ativam cada um os seus limites;
  - o pagamento confirmado na InfinitePay ativa o Creator;
  - pagamento recusado ou não pago não libera acesso;
  - com a trava fechada, o checkout é recusado.
- Pendências:
  - repasses do Stripe desligados (`payouts_enabled=false`, nenhum requisito pendente informado pela API);
  - SMTP;
  - primeiro pagamento real.

## English

- **Service:** the commercial site runs as `asc-commercial` (Node.js 24) in the 210 `asc` project, on
  `192.168.1.210:8790`.
  - SQLite lives in the `asc_commercial` volume.
  - Hardening: read-only, no root, CPU and memory limits, healthcheck.
- **Public address:** the server's proxy publishes it as `https://getclipper.howto.software`. That needs a DNS
  record plus one proxy site block, both outside the `asc` project.
- **Settings:** `/etc/asc/asc.env` on 210 (root, 600), installed with `asc-ctl env-install`.
- **Payments and waitlist:** payments stay off until the billing secret and the provider keys are set, and stay closed to the public until `COMMERCIAL_PAYMENTS_OPEN=true` is set. The waitlist
  stays closed, collecting nothing, until `COMMERCIAL_WAITLIST_OPEN=true` is set after the privacy policy and terms
  are published.
