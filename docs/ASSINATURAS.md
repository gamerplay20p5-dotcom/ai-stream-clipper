# Assinaturas, contas e cotas

Como um pagamento vira um plano ativo, com permissões e cotas aplicadas pelo backend, sem liberação manual da HTS.

*English summary at the end.*

## Arquitetura

```text
 Landing (commercial/)            Painel (dashboard/)                  API principal (backend/, PostgreSQL)
 ─────────────────────            ───────────────────                  ────────────────────────────────────
 "Assinar o Creator" ───────────▶ /conta?plan=creator&market=BR
                                  login ou cadastro com e-mail
                                  verificado (link de uso único)
                                  POST /api/account/checkout ────────▶ sessão autenticada (customer)
                                                                        │ HMAC (ASC_BILLING_SECRET, caller "main")
 Serviço comercial ◀───────────────────────────────────────────────────┘ POST /internal/billing/checkout {account_id,…}
 (Express + SQLite)
   Stripe (USD) / InfinitePay (BRL) ──▶ página do provedor ──▶ pagamento
   webhook assinado / payment_check confirmam
   ledger + estado financeiro + FOTOGRAFIA na outbox (mesma transação)
   despachante ─── HMAC (caller "commercial") ─────────────────────────▶ POST /internal/billing/events
                                                                        apply_snapshot: idempotente (event_id),
                                                                        ordenado (seq), conta verificada
                                                                        account_subscriptions → direito atual
                                                                        pipeline aplica as cotas
```

- **O serviço comercial é dono do dinheiro:** cuida dos provedores, dos webhooks, do ledger e dos estornos. Não sabe nada de canais nem de cotas.
- **A API principal é dona do acesso:** cuida das contas, dos direitos, das cotas e da fiscalização no pipeline. Não sabe nada de Stripe nem de InfinitePay. Ela recebe uma fotografia neutra, com estado de acesso, prazo e limites.
- **Limites dos planos:** vêm só do `commercial/catalog.json`, que é a fonte única. Eles viajam dentro da fotografia, e nenhum número de plano está fixo no Python.
- **Escolha técnica:** a mudança grande foi tornar a plataforma multicliente, com o papel `customer` e `streamers.owner_id`. Sem ela, um cliente não teria canais próprios. **Desde a migração 0018 o canal é compartilhado:** vários clientes acompanham o mesmo streamer com uma única captura e análise-base (veja `docs/LIVES_COMPARTILHADAS.md`). O resto reaproveita o que já existia: contas, links de senha e SMTP do PR #16, a biblioteca com grants (`include_pending`), o HMAC dos workers e os pontos de controle do pipeline.

## Fluxo de ativação

1. **Escolha do plano:** na landing, o cliente clica em "Assinar o X" e vai para `/conta` no painel, com plano e mercado preenchidos.
2. **Conta:** ele entra ou cria uma conta. O cadastro (`POST /api/auth/signup`) envia um link de uso único, válido por 72 h. **A conta só funciona depois que o dono do e-mail cria a senha.** A resposta é sempre igual, para não revelar se o e-mail já tem conta.
3. **Início do checkout:** `POST /api/account/checkout` só aceita sessão de `customer`. O `account_id` vem da sessão, nunca do navegador. Se a conta já tem assinatura ativa, a resposta é 409.
4. **Criação no serviço comercial:** a API chama o serviço comercial com HMAC.
   - **Stripe:** checkout de assinatura com `metadata.account_id`, gravado pelo servidor e devolvido em webhooks assinados.
   - **InfinitePay:** link de uso único vinculado à assinatura da conta.
   - A URL devolvida só é aceita se vier do provedor certo (`checkout.stripe.com` ou `*.infinitepay.io`).
5. **Retorno ao painel:** depois de pagar, o cliente volta para `/conta/assinatura?checkout=returned`. **O retorno não ativa nada.** A página só mostra o estado que o backend tem e consulta de novo a cada 5 s por até 2 minutos.
6. **Confirmação:**
   - **Stripe:** o webhook é verificado pela assinatura (idempotente, ordenado por `created`).
   - **InfinitePay:** `payment_check` com o valor gravado. O webhook não tem assinatura, então é só um aviso para consultar.
7. **Fotografia:** a mudança financeira e a fotografia são gravadas **na mesma transação SQLite**, na outbox.
8. **Entrega:** o despachante (a cada 15 s e logo após as renovações) envia ao backend.
   - Falhas tentam de novo com recuo exponencial e sobrevivem a reinício.
   - Erro 409 (conta ainda não existe) também é tentado de novo.
   - Erro 422 é separado para o operador.
9. **Aplicação:** `apply_snapshot` grava o `event_id` uma única vez e ignora sequências mais antigas. Uma assinatura nunca muda de conta, e só contas `customer` podem ter assinatura.
10. **Liberação:** `entitlements.current` passa a valer, e o painel mostra o plano, o consumo e os canais.

## Regras de acesso por provedor

| Situação | Stripe (USD) | InfinitePay (BRL) | Acesso |
| --- | --- | --- | --- |
| Pago e em dia | `active` / `trialing` | `active` | Ativo até o fim do período (Stripe: +1 dia de folga para o webhook de renovação) |
| Renovação não paga | `past_due` | `past_due` | **Carência de 3 dias** a partir do vencimento |
| Pausada | `paused` | — | Suspenso |
| Nunca paga | `incomplete` | `pending` / `abandoned` | Sem acesso |
| Cancelada ou expirada | `canceled`, `unpaid`, `incomplete_expired` | `expired`, `canceled` | Encerrado |
| Estorno total | `charge.refunded` total ou contestação perdida | Estorno registrado pelo operador | Encerrado (o mês estornado é removido) |
| Estorno parcial ou contestação aberta | `review` | Sinal de estorno no `payment_check` | **Acesso mantido**, com aviso de revisão financeira |
| Pagamento após cancelamento administrativo | — | `payment_after_cancel` | **Não reativa**. O cliente vê "em revisão financeira" |

O direito só vale com `access_status` em `active` ou `grace` **e** `access_until` no futuro. Assim, mesmo sem nenhum evento novo, o acesso acaba sozinho quando o prazo passa.

## Cotas aplicadas pelo backend

Por cliente, sobre os canais que ele acompanha (`channel_monitors`), mesmo que outros clientes acompanhem o mesmo canal. Cada cliente consome a própria franquia (detalhes em `docs/LIVES_COMPARTILHADAS.md`). O trabalho que a HTS consome (`streamers.hts_monitoring`) continua sem limite.

| Recurso | Onde é verificado | Como |
| --- | --- | --- |
| Assinatura ativa | `report_status` (início da live) | Sem direito, a sessão não é criada |
| Horas do ciclo | `report_status`, `session_state` (a cada consulta do worker), `register_segments` | Cada segmento é medido **uma vez** (`usage_entries` único por `live:{sessão}:{idx}`). Depois da franquia, o segmento não é analisado e a captura recebe `stop` |
| Canais | `POST /api/account/channels` | Bloqueio na linha da assinatura e contagem. Canais acima do plano (rebaixamento ou fim do acesso) são **pausados**, mantendo os mais antigos |
| Lives simultâneas | `report_status` | Contagem de sessões `live` da conta, com bloqueio na linha da assinatura |
| Armazenamento | `report_status` e `enqueue_render` | Soma de `clips.file_size` da conta. Com o armazenamento cheio, o render não começa (`failed` com o motivo) |
| Segunda análise (IA premium) | `_needs_deep` | Só planos com `secondPass` (Pro) |
| Edições alternativas | `enqueue_variant` | Só planos com `variants` (Pro) |
| Prioridade | `enqueue_render` | Planos com `priority` (Pro) entram na fila à frente (60 em vez de 70) |
| Exportações 4K | `POST /api/account/clips/{id}/export-4k` | O plano e a cota são verificados. **O renderer ainda não tem saída 4K**: a rota responde 501 e não consome nada |

**Concorrência:**
- As decisões de cota bloqueiam a linha da assinatura (`SELECT … FOR UPDATE` no PostgreSQL; no SQLite as escritas já são serializadas).
- O consumo é gravado com chave única, então nova tentativa, segmento reenviado ou reconexão nunca contam duas vezes.

**Tarefas em andamento quando o acesso acaba:**
- a captura para na próxima consulta do worker;
- nenhuma live nova começa e nenhum render novo é enfileirado;
- o que já foi capturado e medido termina de ser analisado;
- os cortes já produzidos continuam na biblioteca do cliente;
- o acompanhamento daquele cliente para (a captura continua para os outros) e volta sozinho quando a assinatura é renovada.

## Segurança

- **Nada vem do frontend:** o navegador nunca envia `account_id`, plano ou limite que o backend aceite. Não existe rota de cliente que escreva assinatura ou cota; `PUT`, `PATCH` e `POST` em `/api/account/subscription` respondem 405.
- **Segredo próprio:** o vínculo entre os serviços usa `ASC_BILLING_SECRET`, com 32 caracteres ou mais e separado do segredo dos workers. A API só aceita a origem `commercial`, e o serviço comercial só aceita `main`. As mensagens têm carimbo de tempo (60 s) e hash do corpo.
- **Isolamento entre contas:**
  - as rotas de conta tiram o usuário da sessão;
  - canais de outra conta respondem 404;
  - um canal já cadastrado responde 409, sem revelar quem é o dono;
  - uma fotografia não consegue mover uma assinatura para outra conta.
- **Papéis:** o `customer` não acessa nenhuma rota da equipe (a regra continua negar por padrão) e o `viewer` não acessa a área do assinante.
- **Links por e-mail (convite, redefinição e cadastro):** a origem vem só de `ASC_PUBLIC_ORIGIN`, configurada pelo operador. Os cabeçalhos `Host`, `Origin`, `X-Forwarded-Host` e `Forwarded` são ignorados, então um link envenenado não consegue levar o token para outro domínio. Em produção a API não sobe sem a variável, que precisa ser `https` (exceção: `localhost`/`127.0.0.1` no compose local). Sem ela, nenhum link é gerado e o cadastro e o "esqueci a senha" ficam desligados. Os tokens não vão para os logs.
- **SMTP:** usuário e senha só são enviados com STARTTLS ou SSL. O modo sem TLS é recusado ao salvar e ao enviar quando há login, e em produção só é aceito para um relay local sem autenticação. Uma configuração antiga insegura conta como "e-mail não configurado".

## Outbox: entrega, falhas e recuperação

Cada mudança financeira grava uma fotografia completa da assinatura na `entitlement_outbox`, na mesma transação. O despachante entrega por ordem de `seq` a cada 15 s.

| Resposta da API | Classe | O que acontece |
| --- | --- | --- |
| 2xx | entregue | Fotografias mais antigas da mesma assinatura que ainda não chegaram são marcadas como substituídas (`superseded_by`), porque a mais nova tem o estado completo |
| Rede, timeout, 5xx, 408, 429 | transitória | Nova tentativa para sempre, com espera exponencial até 1 hora. Com a API fora do ar, é feita **uma** tentativa por ciclo, não uma por evento |
| 401, 403, 404, 409 | bloqueada | Configuração (segredo, relógio, rota ausente) ou conta ainda não pronta. Nova tentativa para sempre, com espera até 6 horas, e alerta `blocked` |
| 400, 413, 415, 422 e outros 4xx | permanente | Fica `rejected`, **sem nova tentativa automática**, visível no relatório e nos alertas até um operador reenviar ou chegar uma fotografia mais nova da mesma assinatura |

Proteções:
- **Concorrência e reinício:** cada linha é reservada por 60 s (`lease_until`) antes do envio. Dois despachantes (servidor e CLI, ou um reinício no meio de um envio) não enviam a mesma linha ao mesmo tempo. Se o processo cair depois de a API aplicar e antes de marcar a entrega, o reenvio é ignorado pela API (`event_id`).
- **Alertas** (a cada minuto, repetidos no máximo a cada 15 min, só códigos e contagens): `paid_unsynced` (assinatura paga, em `active` ou `grace`, cuja última fotografia não chegou em 2 min), `rejected`, `blocked` e `stalled` (fila parada há 15 min). O log avisa também quando o alerta some.
- **Reconciliação** a cada 30 min por `POST /internal/billing/state` (assinado e só leitura): se a API estiver atrás de uma fotografia já entregue (por exemplo, depois de restaurar um backup), a última entregue volta para a fila; se estiver à frente ou discordar do acesso, aparece como problema no log e no CLI.
- Bancos criados pela primeira versão da outbox são atualizados no lugar. Eventos que ela rejeitou depois de 30 falhas de rede aparecem como `rejected`, prontos para reenvio.

Comandos do operador (cada reenvio fica em `entitlement_outbox_audit` com nome, motivo e estado anterior; o `event_id` e o `seq` não mudam, então o reenvio não duplica efeitos):

```bash
npm run outbox -- report                                   # pendentes, bloqueados, rejeitados, pagos sem sincronizar, idade, último erro, auditoria
npm run outbox -- resend 42 --by ana --reason "API corrigida"
npm run outbox -- resend-rejected --by ana --reason "segredo trocado"
npm run outbox -- reconcile                                # compara com a API agora
npm run outbox -- dispatch                                 # uma passada de entrega agora
```

## Decisões pendentes e limitações

- [ ] **Lives simultâneas do Creator:** o catálogo diz 2 e o estudo comercial (PR #17) recomendava 1. O valor aplicado é `commercial/catalog.json` → `plans.creator.simultaneous`, e o mesmo arquivo registra a pendência em `decisions.creatorSimultaneous`.
- [ ] **Renderização 4K:** a cota existe, mas o renderer só gera 1080p.
- [ ] **IA econômica e IA aprimorada:** hoje a única diferença técnica entre os planos é a segunda análise, a edição alternativa e a prioridade. Basic e Creator usam o mesmo modelo, e a diferença entre os dois ainda precisa ser definida no produto.
- [x] **Posse do canal:** não é exigida. Clipadores e editores acompanham canais de terceiros, e vários clientes podem acompanhar o mesmo canal (0018). O respeito às regras das plataformas e aos direitos do criador é responsabilidade comercial e jurídica.
- [ ] **E-mail:** o SMTP precisa estar configurado no painel (PR #16) para o cadastro funcionar.

## Configuração

| Serviço | Variável | Uso |
| --- | --- | --- |
| API | `ASC_BILLING_SECRET` | Segredo do vínculo com o serviço comercial, com 32 caracteres ou mais (mesmo valor nos dois lados) |
| API | `ASC_COMMERCIAL_URL` | Endereço interno do serviço comercial, por exemplo `http://asc-commercial:8790` |
| API | `ASC_PUBLIC_ORIGIN` | Endereço `https` do painel usado nos links por e-mail. Obrigatória em produção (o compose da 210 não sobe sem ela) |
| Comercial | `ASC_BILLING_SECRET`, `ASC_MAIN_API_URL`, `ASC_APP_URL` | Vínculo com a API e endereço do painel. Sem eles, nenhum mercado vende |
| Comercial | `STRIPE_*`, `INFINITEPAY_*`, `SMTP_*` | Provedores. Veja `commercial/docs/PAGAMENTOS.md` |

Migração **0017**. Ela só **adiciona** estruturas (`streamers.owner_id`, `account_subscriptions`, `billing_events` e `usage_entries`) e tem `downgrade`. Faça backup antes de aplicá-la (`asc-ctl backup-db`).

## Homologação, nesta ordem e com autorização

1. Aplicar a 0017 num banco de homologação, nunca em produção sem backup e autorização.
2. Configurar `ASC_BILLING_SECRET` nos dois serviços, o SMTP do painel e `ASC_APP_URL`, `ASC_MAIN_API_URL` e `ASC_COMMERCIAL_URL`.
3. **Stripe, em modo de teste:**
   1. Rodar `npm run stripe:sync` e configurar o webhook.
   2. Criar uma conta pelo cadastro e assinar com o cartão `4242 4242 4242 4242`.
   3. Conferir no painel o plano Pro ativo, os limites e o consumo zerado.
   4. Pelo portal, cancelar e reativar. Simular uma falha de pagamento (`4000 0000 0000 0341`) e um estorno pelo painel do Stripe.
4. **InfinitePay**, com compra real autorizada de R$ 1,00 ou mais e estorno pelo app:
   1. Conferir as quatro lacunas da documentação listadas em `commercial/docs/PAGAMENTOS.md`.
   2. Registrar o estorno com `npm run infinitepay -- refund` e conferir que o acesso acabou.
5. **Cotas:**
   1. Cadastrar canais além do limite, que deve dar 409.
   2. Abrir duas lives num plano Basic: a segunda não deve começar.
   3. Encostar a franquia de horas em homologação: a captura deve parar e os segmentos seguintes não devem ser analisados.
   4. Encher o armazenamento: o render não deve começar.
6. **Falha de sincronização:** derrubar a API, gerar eventos no serviço comercial e subir de novo. As fotografias pendentes devem chegar (`npm run outbox -- report` mostra a fila e os alertas), e um evento repetido não pode ter efeito. Forçar um 422 deve deixar o evento `rejected` sem novas tentativas até `npm run outbox -- resend`.

## English summary

The commercial service owns the money (Stripe for USD, InfinitePay for BRL, webhooks, ledger and refunds). The main API owns access (accounts, entitlements and quotas).

1. **Sign-up and checkout:** customers sign up with a verified e-mail (single-use link) and start checkout from an authenticated session. The account id travels from the server, never from the browser.
2. **Confirmation:** the provider confirms the payment (signed Stripe webhook, or InfinitePay `payment_check` for the stored amount). The financial change and a provider-neutral entitlement snapshot are committed together in an outbox.
3. **Delivery:** snapshots are delivered over HMAC with retries. The API applies them idempotently (by event id), in order (by sequence) and only to verified customer accounts.
4. **Enforcement:** plan limits come only from `catalog.json`. The pipeline enforces them for customer channels: session start, capture polling, per-segment hour metering (counted once), simultaneous streams, storage before rendering, and premium features (second pass, alternate edits, priority). HTS channels are unchanged.
5. **When access ends:** that customer stops following (the shared capture goes on for the others), new work for it is refused, while existing clips stay available.
6. **Outbox failures:** transient errors retry forever (backoff up to 1 h, one probe per cycle while the API is down); configuration errors (401/403/404/409) retry up to every 6 h; invalid snapshots (400/413/415/422) stay `rejected` and visible until an audited `npm run outbox -- resend` or a newer snapshot supersedes them. Leases make concurrent dispatchers and restarts safe, alerts flag paid subscriptions that are not synced, and a signed reconciliation re-queues what the API lost.
7. **E-mailed links** use only the operator's `ASC_PUBLIC_ORIGIN` (https in production), never request headers. SMTP refuses to send credentials without TLS.

**Pending:**
- Creator simultaneous streams (2 in the catalog vs 1 in the study).
- No 4K renderer yet.
- No technical difference between economy and enhanced AI.
- Channel ownership is not required: channels are shared between customers (migration 0018).
