# Publicação automática para clientes (Creator e Pro) e painel comercial da HTS

PR #25, que depende do #24 (lives compartilhadas), que depende do #23 (assinaturas). Nada foi mesclado, implantado ou conectado a contas reais. Nenhum vídeo foi publicado.

## Recursos por plano

Os preços não mudaram. Os limites vêm de `commercial/catalog.json` → `plans.*.features` (`autoPublish`, `publishDestinations`, `publishDaily`). Chegam à API dentro das fotografias assinadas (ou das cortesias da HTS) e nunca do navegador.

| Plano | Preço | Geração automática | Presets editoriais | Destinos conectados | Publicações por dia (total) | Agendamento, aprovação e histórico | Segunda análise e prioridade |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Basic | R$ 59,90 / US$ 19,99 | Sim | Não | 0 | 0 (sem publicação automática) | Não | Não |
| Creator | R$ 139,90 / US$ 44,99 | Sim | Sim | 2 | **3** | Sim | Não |
| Pro | R$ 279,90 / US$ 89,99 | Sim | Sim | 4 | **4** | Sim, com métricas | Sim |

**Destino** é uma conta (ou uma Página) conectada, não uma plataforma. O Pro pode ter quatro canais do YouTube. Nenhuma rede que ainda não foi homologada é oferecida.

**O limite diário é o TOTAL do cliente, somando todos os destinos.** Exemplos:
- **Creator:** YouTube 2 + Instagram 1 = 3. Limite atingido.
- **Pro:** YouTube A 1 + YouTube B 1 + Instagram 2 = 4. Limite atingido.
- O mesmo Short no YouTube e no Instagram conta 2.

## Plataformas

| Rede | Situação para clientes | Observações |
| --- | --- | --- |
| YouTube Shorts | **Disponível** (`customer_publish_platforms` padrão) | Envio privado já validado pela HTS. OAuth do Google com `youtube.upload`; cotas da YouTube Data API respeitadas pelo publicador |
| Instagram Reels | Pendente de homologação | Exige conta profissional e consentimento para publicação pública. Os Reels são sempre públicos |
| TikTok | **Nunca automática** | O TikTok exige o consentimento do criador a cada postagem. A API recusa a ativação |
| Facebook Reels | Pendente de homologação | Implementado nesta etapa (detalhes abaixo). Só Páginas, nunca perfis pessoais |

A HTS libera uma rede para clientes em **Clientes → Capacidade → Redes liberadas** (`PUT /api/admin/customer-publish-platforms`), só depois de um envio real homologado. `catalog.json` → `publishing.platforms` precisa refletir a mesma decisão: é o que a landing mostra.

### Facebook Reels (Graph API v27.0, documentação lida em 21/11/2026)

- **Login:** `https://www.facebook.com/v27.0/dialog/oauth` com os escopos `pages_show_list`, `pages_read_engagement` e `pages_manage_posts`. O código é trocado no servidor por um token de usuário de longa duração (`grant_type=fb_exchange_token`), e `/me/accounts` lista as Páginas.
  - Só entram Páginas em que a pessoa tem a tarefa `CREATE_CONTENT`. Cada Página vira um destino, com o seu próprio token de Página, que não expira por data.
  - Perfis pessoais não são suportados pela API, nem por nós.
- **Publicação:**
  1. `POST /{page-id}/video_reels` com `upload_phase=start`, que devolve o `video_id`;
  2. envio do arquivo para `rupload.facebook.com/video-upload/v27.0/{video-id}`, com `Authorization: OAuth <token da Página>`, `offset` e `file_size`;
  3. `POST /{page-id}/video_reels` com `upload_phase=finish`, `video_state=PUBLISHED` e a descrição.
  - O passo 3 é **gravado antes** de ser enviado. Se a resposta se perder, a situação é conferida pelo status (`GET /{video-id}?fields=status`), nunca publicando de novo.
- **Limites da Meta:**
  - vídeo 9:16, de 3 a 90 s, 24 a 60 fps, mínimo de 540×960;
  - 30 Reels publicados por API por Página a cada 24 h;
  - público "Público" apenas.
- **Para liberar a clientes (App Review):**
  1. App da Meta do tipo Business, com Facebook Login e a URL de redirecionamento `https://<ASC_PUBLIC_ORIGIN>/api/publish/oauth/facebook/callback`.
  2. **Business Verification** da empresa (exigida para Advanced Access).
  3. **Advanced Access** a `pages_show_list`, `pages_read_engagement` e `pages_manage_posts`, com App Review. Material a preparar:
     - descrição de uso: "o cliente conecta as Páginas que administra e autoriza a publicação dos Shorts da própria biblioteca";
     - screencast do fluxo completo: login, escolha da Página, regra, publicação e a tela de histórico;
     - instruções de teste com uma conta de teste;
     - política de privacidade publicada (hoje "em preparação" na landing: **bloqueia a revisão**).
  4. Data Use Checkup anual.
  5. Homologação real numa Página de teste da HTS e só então a liberação em "Redes liberadas".
- **Não confirmado na documentação:**
  - tamanho máximo do arquivo;
  - se a Página precisa estar publicada;
  - se `business_management` é necessário;
  - se os Reels têm App Review próprio.
  - Conferir na homologação.
- **Métricas dos Reels:** ainda não ligadas, porque os campos não foram validados numa Página real. Nada é inventado.

## Fluxo

1. O cliente assina Creator ou Pro.
2. Em **Publicação automática** (`/conta/publicacao`), aceita a autorização de publicação e ativa a automação. Ela começa desativada.
3. **Conectar rede** inicia o OAuth com um estado de uso único, de 15 minutos, ligado ao `owner_id` do cliente. O dono da conta de destino autoriza na própria rede.
   - O retorno volta para `ASC_PUBLIC_ORIGIN/conta/publicacao`. A origem nunca vem dos cabeçalhos.
   - **Se a conta já pertence à HTS ou a outro cliente, é recusada**, sem dizer de quem é.
   - Se a rede compartilhar mais contas do que o plano permite, só as vagas livres são aceitas.
4. Cria uma regra por canal acompanhado e destino. Ela define:
   - horários preferidos, nota mínima, privacidade permitida pela rede e aprovação manual ou automática;
   - **a declaração de direito de republicação daquele canal** (obrigatória, com observação opcional).
5. O pipeline compartilhado gera os Shorts. Os que entram na biblioteca do cliente (PR #24) e passam a nota da regra viram publicações:
   - "aguarda aprovação", se a regra exige aprovação;
   - "na fila", se não exige.
6. O planejador distribui os Shorts da fila nos horários preferidos sem passar do total diário. O resto espera os próximos dias e expira depois de **7 dias** na fila.
7. O worker do publicador (o mesmo da HTS) envia cada publicação no horário, verificando de novo a assinatura, a pausa, a autorização, a biblioteca, os direitos, o destino e a vaga do dia.
8. O cliente acompanha publicações de hoje, limite, próximas, histórico e falhas que pedem ação (reconectar, aprovar). Não vê filas internas, workers, diagnóstico, modelos nem agenda da HTS.

## Segurança multi-tenant

- **Dono:** `owner_id` em `publish_accounts`, `publish_rules`, `publications` e `oauth_states`. Quando é nulo, o recurso é da HTS e o comportamento é o de antes.
- **Rotas do cliente** (`/api/account/publishing/*`):
  - nenhuma recebe id de dono;
  - todo objeto é buscado com `owner_id == usuário`, e o de outro cliente responde 404;
  - tokens, escopos e segredos nunca saem da API.
- **Painel de publicação da HTS** (`/api/publish/*`): só para a equipe. Ele **não lista nem altera** recursos de clientes (404). A varredura automática, o calendário, o relatório, as propostas e o reagendamento da HTS ignoram as publicações de clientes.
- **Mídia:** um cliente só publica Shorts com compartilhamento ativo na **própria** biblioteca e envia exatamente a mídia entregue (cópia congelada do compartilhamento).
- **Bloqueios da HTS:**
  - por cliente, a automação inteira (com motivo e autor);
  - por canal, a republicação para todos os clientes (cancela o que ainda não começou).
- **Auditoria:** todas as ações do cliente e da HTS ficam no `audit_log`.

## Cotas de publicação (`publish_units`)

- **Uma unidade por publicação**, com restrição única. Repetição de tentativa, reinício do worker e job duplicado nunca contam duas vezes.
- **Reserva:** feita logo antes do envio, com as linhas de assinatura do cliente travadas e recontagem depois da inserção. Dois workers nunca levam a última vaga. Testado com threads e preparado para PostgreSQL real.
- **Dia:** o dia de calendário no fuso do cliente, que é validado (nome IANA) e salvo. Ele é calculado a partir do UTC pelo `zoneinfo`, então funciona certo na troca de horário de verão (testado).

| Situação | Unidade |
| --- | --- |
| Envio começou, publicado, processando ou incerto | **Consumida** (nunca devolvida: pode estar no ar) |
| Falha permanente antes de publicar, cancelamento, destino desconectado, expiração na fila | **Liberada** |
| Nova tentativa agendada | Mantida (a mesma unidade) |
| Reserva sem envio por 6 h (worker morreu antes de enviar) | Liberada automaticamente |
| Publicação confirmada | Nunca reenviada (o worker ignora `published`) |

- **Sem vaga no dia:** a publicação volta para "na fila", sem erro.
- **Pausa:** o cliente pausa e retoma quando quiser. Pausado, nada novo entra na fila e nada é enviado.

## Assinatura, revisão e mudanças de plano

| Situação | Efeito |
| --- | --- |
| Expirada, cancelada ou suspensa, ou plano sem `autoPublish` | Nenhum envio novo; as publicações voltam para a fila e expiram no prazo |
| Revisão financeira ou contestação | Em espera até a resolução (política do #24) |
| Downgrade Pro → Creator | Destinos além de 2 ficam pausados ("fora do limite de destinos do plano"), e as publicações neles são recusadas |
| Upgrade | Os destinos pausados voltam |
| Biblioteca | Os Shorts entregues continuam lá depois do fim da assinatura (#24) |

## Painel interno da HTS (`/clientes`, só administradores)

Mostra:
- clientes, plano (pago ou cortesia), consumo, destinos sem segredos, publicações e falhas das últimas 24 h;
- canais com o número de clientes que os acompanham, e se a HTS também monitora (`hts_monitoring`).

Permite:
- bloquear ou desbloquear a automação de um cliente, com motivo;
- **dar ou revogar cortesias** Basic, Creator ou Pro, com validade e motivo;
- bloquear a republicação de um canal;
- ver a capacidade (lives capturadas, clientes acompanhando, bibliotecas, filas de jobs, publicações na fila);
- liberar redes para clientes.

**Cortesias** (`courtesy_grants`):
- cada uma vira uma linha própria em `account_subscriptions`, com `provider="hts"`, `financial_status="courtesy"` e os limites do catálogo (a imagem passa a levar `catalog.json`);
- **nunca alteram pagamentos, o ledger comercial nem fotografias pagas**;
- **precedência:** vale o plano mais alto; no mesmo plano, o pago vence;
- uma cortesia não impede o cliente de comprar um plano.

## Configuração

| Onde | O quê |
| --- | --- |
| API | `ASC_PUBLIC_ORIGIN` (retorno do OAuth do cliente). `ASC_CATALOG_PATH` é opcional (padrão `/app/catalog.json` na imagem) |
| Painel da HTS → Publicação → Apps | Client ID e segredo de cada rede, como antes, e a URL de redirecionamento `https://<origem>/api/publish/oauth/<rede>/callback`. O mesmo app atende a HTS e os clientes |
| Clientes → Redes liberadas | Só `youtube` no início |

## Migração 0019 (aditiva)

- Acrescenta:
  - `owner_id` em `oauth_states`, `publish_accounts`, `publish_rules` e `publications` (nulo = HTS);
  - `publish_accounts.paused_reason`;
  - `publish_rules.rights_confirmed_at` e `rights_note`;
  - as tabelas `customer_publish_settings`, `publish_units` e `courtesy_grants`.
- Tem `downgrade`.
- Exige backup antes de aplicar, junto com a 0017 e a 0018.

## Testes

| Suíte | Onde rodou | Resultado |
| --- | --- | --- |
| Backend completo (SQLite) com e2e | **Local** | **506 aprovados**, 6 ignorados (PostgreSQL e 1 que já existia) |
| `test_customer_publishing.py` (20 cenários) | Local | 20/20 |
| `test_facebook_reels.py` (Graph API simulada) | Local | 6/6 |
| Serviço comercial | Local | 37/37 |
| Playwright da landing (Chromium, Firefox e WebKit, com axe) | Local | 34 aprovados, 8 ignorados |
| Dashboard: typecheck, i18n (0 faltando) e build | Local | Ok |
| Capturas da página do cliente (390 e 1440, Creator e Basic) | Local | `docs/qa/publicacao/` |
| `test_postgres_customer_publishing.py` (10 workers disputando as vagas) | **Pendente** | Não executado: sem PostgreSQL nesta máquina |
| `test_postgres_shared_lives.py` (#24) | **Pendente** | Não executado |
| GitHub Actions | **Não rodou** | `startup_failure` por bloqueio da conta (ver `docs/INTEGRACAO_PRS.md`) |

Cenários cobertos com APIs externas simuladas (nenhuma conta, token ou post real):
- Basic recusado;
- terceiro destino do Creator e quinto do Pro recusados;
- Creator passando de 3 e Pro passando de 4 por dia;
- dois workers na última vaga;
- nova tentativa e reinício sem contagem dupla nem repostagem;
- o mesmo Short em dois destinos;
- dois clientes da mesma live publicando nas próprias contas;
- isolamento total entre clientes e em relação ao painel da HTS;
- revisão, downgrade, upgrade, suspensão, cancelamento e expiração;
- conta expirada ou desconectada;
- automação pausada e autorização;
- direito de republicação e bloqueio pela HTS;
- privacidade incompatível;
- fila depois do limite diário;
- aprovação manual e fuso inválido;
- troca de horário de verão;
- cortesias e precedência;
- painel da HTS;
- redes não homologadas;
- Facebook: login só com permissões de Página, Páginas sem permissão de criar conteúdo ignoradas, perfis pessoais recusados, fluxo em 3 etapas com o `finish` uma vez só, reconciliação de resposta perdida e token inválido.

**Conferência dos testes:** remover de propósito a checagem de dono, ou o limite diário, faz os testes correspondentes falharem.

## English

**Plans** (prices unchanged):

| Plan | Automatic posting | Connected destinations | Posts per day (TOTAL across destinations) |
| --- | --- | --- | --- |
| Basic | No (upgrade offer only) | 0 | 0 |
| Creator | Yes, with presets, scheduling, optional approval and history | 2 | 3 |
| Pro | Everything in Creator, plus metrics | 4 | 4 |

- Pro also keeps the second AI pass and priority it already had.
- A destination is a connected account or Page, not a platform.
- Limits come from `catalog.json` through signed snapshots or HTS courtesy plans, never from the browser.

**Platforms:**
- **YouTube Shorts:** the only one enabled for customers (private upload already validated).
- **Instagram and Facebook Reels:** wait for real homologation.
- **TikTok:** never automatic.
- **Facebook Reels on Pages** (never personal profiles) is implemented from Meta's official Reels Publishing API (v27.0): Page permissions only, a 3-step upload, a `finish` call recorded before sending and never repeated. The App Review checklist is above: Business Verification, Advanced Access, screencast and a published privacy policy.

**Multi-tenancy:**
- `owner_id` on accounts, rules, publications and OAuth states (NULL = HTS, unchanged).
- Customer routes scope every object to the signed-in account (404 otherwise) and never return tokens.
- The HTS publishing panel cannot see or change customer resources.
- Customers publish only Shorts in their own library, using the delivered media snapshot. Republication rights are declared per channel.
- HTS can block a customer or a channel, and everything is audited.

**Daily allowance:**
- one unit per publication, reserved under the account lock with a recount;
- never counted twice;
- consumed once something may be online, released when nothing was published;
- abandoned reservations are freed after 6 h;
- the day is the customer's validated IANA timezone and is DST-safe;
- over-limit Shorts wait in the queue and expire after 7 days.

**HTS panel (`/clientes`):**
- customers, plans, usage, destinations without secrets, publications and failures, followers per channel, `hts_monitoring`, capacity and platform enablement;
- blocks and courtesy plans, kept separate from payments (own `hts` subscription rows: the highest plan wins, and the paid one wins on the same plan).

**Migration 0019** is additive.

**Tests:**
- **Local:** 506 backend, 20 customer publishing, 6 Facebook (simulated Graph API), 37 commercial, 34 Playwright; dashboard typecheck, i18n and build pass.
- **PostgreSQL tests: pending (not executed).**
- **GitHub Actions:** did not run (account-level block).
