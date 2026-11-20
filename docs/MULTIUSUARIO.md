# Multiusuário, etapa 1: contas e vídeos compartilhados (v0.7.0)

*Português; English summary at the end.*

**Objetivo desta etapa:** uma pessoa faz login e só consegue assistir aos vídeos que o administrador da HTS liberou explicitamente para ela. Não há cadastro público, e-mail, SMTP, cobrança nem créditos.

## 1. Auditoria (antes da mudança)

| Ponto | Situação encontrada | Correção |
|---|---|---|
| `api/deps.current_user` | aceitava **qualquer conta ativa**; protegia quase todo o painel | passou a ser o **portão da equipe** (só `admin`). Todo o painel interno fica fechado para contas externas de uma vez. É negação por padrão: uma rota nova que use `current_user` já nasce fechada |
| `viewer` | podia listar cortes, ver detalhes, vídeo, miniatura, **download**, legendas, análises, publicação (contas conectadas), infraestrutura, armazenamento e diagnóstico | agora recebe **403** em todo o painel; só chega a `/api/auth/me` e `/api/library` |
| Papel no JWT | o token carregava `role`, mas o backend já lia o papel do **banco** a cada requisição | `role` saiu do token. O token identifica a conta e leva a **versão de sessão** (`sv`) e o `uid` |
| Sessões abertas | desativar ou rebaixar já valia na hora (leitura do banco), mas **não havia como encerrar sessões** em troca de senha ou revogação explícita | coluna `users.session_version`: desativação, mudança de papel, redefinição de senha e "encerrar sessões" incrementam a versão, e tokens antigos deixam de valer na próxima requisição |
| `User.role` padrão | **`admin`** no modelo: uma conta criada sem papel explícito virava administradora | padrão **`viewer`** |
| Rotas sem autenticação | só `/healthz`, `/readyz` e `/metrics` (este último exige token próprio); o callback OAuth da publicação é autenticado pelo `state` | sem mudança |
| `/internal/*` | HMAC dos workers; bloqueado (404) quando vem pelo domínio público | sem mudança |

**Banco de produção:** a auditoria ao vivo (revisão do Alembic, contas existentes, chaves estrangeiras) **não foi executada**: os servidores ficaram inacessíveis por rede (timeout no SSH e na API) durante esta etapa. Pelo histórico de migrações:
- a produção está na revisão **0010**;
- `users` tem `id`, `email` (único, indexado), `password_hash`, `role`, `active`, `created_at` e `last_login_at`;
- as tabelas que se relacionam são `clips` (corte) e `audit_logs` (que guarda o e-mail, não uma chave estrangeira).

Consultas **somente leitura** para confirmar antes do deploy (o acesso operacional atual, `asc-ctl exec asc-postgres`, basta; nenhuma permissão nova é necessária):

```
sudo -n asc-ctl exec asc-postgres -- psql -U asc -d asc -tA \
  -c "select version_num from alembic_version;" \
  -c "select role, active, count(*) from users group by 1,2;" \
  -c "select column_name, data_type, is_nullable, column_default from information_schema.columns where table_name='users';" \
  -c "select count(*) from clips where video_path<>'' and status in ('ready','approved');"
```

Se existir alguma conta `viewer` em uso interno, ela perde o acesso ao painel com esta versão. Pela política nova, isso é o correto; para manter o acesso, o papel precisa ser `admin`.

## 2. Modelo de permissões

- **`admin` (equipe HTS):** o painel inteiro, inclusive a biblioteca (como prévia).
- **`viewer` (conta externa):** só `/api/auth/me` e `/api/library/*`, e só os cortes com compartilhamento **ativo**.
- **Compartilhamento ativo:**
  - não revogado;
  - não expirado;
  - `can_view`;
  - conta ativa;
  - sessão com a versão atual;
  - corte com vídeo renderizado.

  Isso é conferido **a cada requisição**, inclusive no vídeo, na miniatura e no download. Qualquer outra situação responde **404**, para que uma conta externa não descubra se um corte existe.
- **Permissões separadas:** download (`can_download`, desligado por padrão) e variantes (`can_variants`, desligado por padrão).
- **Identidade:** a conta vem da sessão. Parâmetros `user_id`, `email` ou `clip_id` de outra conta na URL ou no corpo não mudam nada (teste de IDOR).
- **Mídia:** sempre transmitida pela API, com Range para o player, sem caminho de arquivo, sem URL permanente e com `Cache-Control: private, no-store`.

### Tabela `clip_shares` (migração 0012)

| Coluna | Tipo | Observação |
|---|---|---|
| `id` | int PK | |
| `clip_id` | FK `clips.id` ON DELETE CASCADE | índice |
| `user_id` | FK `users.id` ON DELETE CASCADE | |
| `can_view` | bool, padrão true | |
| `can_download` | bool, padrão **false** | |
| `can_variants` | bool, padrão **false** | |
| `expires_at` | datetime, opcional | |
| `created_by` | FK `users.id` ON DELETE SET NULL | administrador que compartilhou |
| `created_at` | datetime | |
| `revoked_at` / `revoked_by` | datetime / FK `users.id` | revogar **não apaga**: o histórico fica |

- **Índices:** `uq_clip_shares_active` é único em `(clip_id, user_id)` **quando `revoked_at IS NULL`** (um único compartilhamento ativo, com histórico ilimitado); `ix_clip_shares_user_active` cobre `(user_id, revoked_at)`.
- **Usuários:** `users.session_version` (int, padrão 0).
- **Downgrade:** renomeia para `clip_shares_preserved` (o histórico de acesso nunca é apagado) e remove `session_version`. O upgrade seguinte restaura os dados.
- **Revisão:** a 0012 revisa a **0011** (PR #4). A cadeia é linear, com head única: `0010 → 0011 → 0012` (testado). O branch deste PR já contém o PR #4.
- **Compatibilidade:** tokens emitidos antes desta versão não têm `sv` e valem como versão 0. Ninguém é deslogado no deploy.

### Versão exata e imutável do vídeo compartilhado

- **Snapshot:** ao compartilhar, a conta recebe um **snapshot** da mídia aprovada naquele momento:
  - `video_path`, `video_sha256`, `video_bytes`, `thumb_path` e `clip_version`;
  - as variantes prontas naquela hora (`variants`).
- **Re-edição não troca nada:** uma reedição, um novo render ou uma variante promovida **não mudam** o que a conta assiste. O painel da equipe mostra a versão nova, e o convidado continua com a aprovada. O compartilhamento aparece como `outdated: true` para a equipe.
- **Trocar de versão é explícito:** `POST /api/shares/{id}/reapprove` (ou compartilhar de novo).
- **Acompanhar a mais recente:** é uma escolha explícita, `follow_latest: true`, desligada por padrão.
- **Arquivo preservado enquanto a autorização vale** (compartilhamento não revogado e não expirado):
  - **re-render ou troca de variante:** o arquivo do snapshot entra na lista de arquivos referenciados, que **nunca** são apagados nem sobrescritos (a mesma lista dos snapshots de publicação);
  - **limpeza por nota:** o corte fica **protegido** ("compartilhado com N conta(s)"). A quarentena não o seleciona e o purge, que reconfere a proteção, cancela;
  - **exclusão do corte:** `DELETE /api/clips/{id}` é **recusado (409)** enquanto houver compartilhamento válido; é preciso revogar antes;
  - um compartilhamento **expirado ou revogado** deixa de segurar a mídia.

## 3. Endpoints

### Administração (só equipe)

| Método e rota | O que faz |
|---|---|
| `GET /api/admin/users` | contas: e-mail, papel, ativa, compartilhamentos ativos, último login |
| `POST /api/admin/users` `{email, role="viewer"}` | cria a conta e devolve **uma única vez** `initial_password` (cerca de 120 bits), para entrega pessoal. A senha não é guardada em claro nem registrada na auditoria. E-mail duplicado dá 409 |
| `PATCH /api/admin/users/{id}` `{active?, role?}` | ativa, desativa ou muda o papel; encerra as sessões abertas. Não permite rebaixar ou desativar a si mesmo nem o **último** administrador (409) |
| `POST /api/admin/users/{id}/reset-password` | nova senha única e sessões encerradas |
| `POST /api/admin/users/{id}/end-sessions` | encerra as sessões abertas |
| `GET /api/admin/users/{id}/shares[?include_revoked]` | cortes compartilhados com a conta |
| `POST /api/shares` `{email, clip_ids[], can_download=false, can_variants=false, expires_at?, follow_latest=false}` | compartilha um ou vários cortes **renderizados** com uma conta **existente e ativa**. O e-mail é normalizado; conta inexistente dá **404 "conta não encontrada"**, e nada é criado nem enviado. Repetir atualiza as permissões. Devolve `shared` e `refused` (por corte, com o motivo) |
| `GET /api/shares[?clip_id&user_id&include_revoked]` | lista os compartilhamentos, com estado ativo, expirado ou revogado |
| `PATCH /api/shares/{id}` `{can_download?, can_variants?, expires_at?, clear_expiry?}` | ajusta as permissões |
| `POST /api/shares/{id}/reapprove` | passa o compartilhamento para a versão **atual** do corte (decisão explícita) |
| `DELETE /api/shares/{id}` | **revoga** (fica no histórico); vale na próxima requisição da conta |

Todas as ações ficam em `audit_logs`.

### Conta autenticada

| Método e rota | O que faz |
|---|---|
| `GET /api/auth/me` | `{email, role, staff, areas}`; `areas` é `["library"]` para contas externas |
| `GET /api/library` | `{items: [...]}` com os cortes compartilhados com **esta** conta |
| `GET /api/library/{clip_id}` | um item (404 se não compartilhado) |
| `GET /api/library/{clip_id}/video` | MP4 com Range (206) e `no-store` |
| `GET /api/library/{clip_id}/thumbnail` | miniatura |
| `GET /api/library/{clip_id}/download` | `attachment`, só com `can_download` (sem a permissão: 403); fica na auditoria |
| `GET /api/library/{clip_id}/variants/{name}/video` | variante, só com `can_variants` |

Formato de um item (o que a conta externa vê, e nada além disso):

```json
{"clip_id": 5, "title": "...", "streamer": "Nome exibido", "duration_s": 20.0,
 "shared_at": "...Z", "expires_at": null,
 "permissions": {"view": true, "download": false, "variants": false},
 "video_url": "/api/library/5/video", "thumbnail_url": "/api/library/5/thumbnail",
 "download_url": null, "variants": [{"name": "edit", "label": "...", "video_url": "..."}]}
```

O item não traz caminhos de arquivo, transcrição, notas, análises, fonte, SHA nem nada da publicação.

## 4. Plano de integração com o novo frontend (identidade AI Stream Clipper by HTS)

Esta etapa entrega só o backend e os contratos. Para o redesign:
- Depois do login, usar `GET /api/auth/me`: `staff=false` leva **direto** para a Minha Biblioteca, e o resto do painel nem é exibido. Se uma chamada voltar 401, a sessão terminou (revogação, desativação ou troca de papel): voltar ao login.
- **Administração → Usuários:**
  - tabela com e-mail, papel, estado, compartilhamentos ativos e último login;
  - "Nova conta" mostra a senha **uma vez**, com botão de copiar e o aviso de entregar pessoalmente;
  - ações: ativar ou desativar, mudar o papel (confirmação forte para administrador), redefinir senha e encerrar sessões;
  - detalhe da conta com os cortes compartilhados (revogar, alternar download e variantes, mudar a expiração).
- **Central de Cortes → Compartilhar:**
  - seleção múltipla de cortes prontos;
  - campo de e-mail com busca entre as contas existentes (`GET /api/admin/users`);
  - permissões: visualizar (fixo), "permitir download" e "incluir variantes", ambos desligados por padrão;
  - expiração opcional;
  - mostrar o resultado por corte, porque `refused` explica cada recusa.
- **Minha Biblioteca → Compartilhados comigo:**
  - grade de miniaturas com título, streamer e duração;
  - player usando `video_url` (o navegador faz Range sozinho);
  - botão de download só quando `download_url` existir;
  - variantes como abas;
  - aviso de expiração quando houver.
- Nenhuma tela externa deve oferecer publicação, análise ou edição.

## 5. Preparação para os planos (não implementado)

A estrutura acomoda os planos sem misturá-los com o compartilhamento:
- **planos:** tabelas futuras `plans`, `subscriptions` e `credit_ledger`, ligadas a `users.id`;
- **limites por plano:** duração analisada por live; cortes por live (4, 7 ou 12); créditos renováveis a cada 5 horas;
- **recomendação de horários:** um serviço que lê o calendário editorial existente.

**Decisão fixa:** contas externas **nunca** publicam automaticamente, em nenhum plano. O publicador continua exclusivo da equipe, e as rotas de publicação já são `current_user` (equipe).

## 6. Testes (`tests/test_multiuser.py`, 24, e `tests/test_e2e_sharing.py`, 1)

Todos passam:
- administrador vê tudo;
- conta nova começa sem nenhum acesso;
- a conta vê só o que foi compartilhado;
- o e-mail de outra pessoa não dá acesso;
- a resposta não expõe caminhos, transcrição, nota nem SHA;
- conta inexistente é informada e nunca criada;
- só cortes renderizados podem ser compartilhados;
- o player funciona com Range e os endereços diretos do painel continuam fechados;
- download com permissão própria;
- variantes só quando liberadas;
- revogação imediata na sessão aberta;
- compartilhamento expirado é recusado;
- conta desativada perde o acesso na hora;
- mudança de papel invalida os privilégios antigos;
- encerrar sessões e redefinir senha funcionam;
- contas criadas como `viewer`, com senha única que nunca vai para o log;
- não é possível ficar sem administrador;
- tentativas de IDOR e de escalada de privilégio são bloqueadas;
- **varredura de todas as rotas da API** com uma conta externa: toda rota da equipe responde **exatamente 403** ("acesso restrito à equipe"). Um 404, 405 ou 422 **não** conta como prova de bloqueio. Há também um **controle positivo**: o administrador não recebe 403 em nenhum GET, o que prova que o bloqueio vem da permissão;
- rotas da biblioteca: o mesmo endereço responde 404 sem compartilhamento e 200 com ele (muda só a autorização);
- a versão aprovada continua sendo servida depois de um **re-render real** pelo endpoint do worker; o arquivo antigo não é apagado; a troca só acontece com `reapprove`;
- `follow_latest` é explícito; as variantes também ficam congeladas;
- a mídia compartilhada fica protegida na limpeza e na exclusão enquanto é válida, e deixa de estar quando expira;
- quarentena e purge pulam o corte compartilhado;
- **ponta a ponta com duas contas** (`test_e2e_sharing.py`), contra a **API real** (processo uvicorn, HTTP, cookies):
  1. o administrador cria a conta convidada e compartilha 3 de 5 vídeos;
  2. o convidado vê **exatamente os 3**, assiste com Range (206) e recebe 403 no download;
  3. os outros 2 dão 404 na biblioteca e 403 no painel;
  4. revogar um corta o acesso **na mesma sessão aberta**;
  5. desativar a conta encerra a sessão;
- migração 0012 preserva o histórico.

Suíte completa da versão integrada (#4 + #6): **349 passando**. Typecheck, verificação de privacidade e build do painel OK.

## 7. Ordem de integração (#4 → #5 → #6)

| Passo | PR | Observação |
|---|---|---|
| 1 | **#4** (6C, v0.6.3, migração 0011) | backend e frontend da 6C |
| 2 | **#5** (redesign HTS, Codex) | é baseado no branch do #4. Depois que o #4 entrar, o base do #5 passa a ser `main`. Só toca no frontend (`dashboard/`, `docs/design`) |
| 3 | **#6** (multiusuário, v0.7.0, migração 0012) | já contém o #4 e a cadeia 0011 → 0012. Depois do #5, atualizo o branch com o `main` (merge, sem force push). O único conflito esperado é o número da versão em `dashboard/package.json` |

Para o #5 (Codex), o contrato do backend está nas seções 3 e 4: `GET /api/auth/me` (`staff` e `areas`), `/api/admin/users*`, `/api/shares*` (incluindo `follow_latest` e `reapprove`) e `/api/library*`. A autorização vale sempre no backend: a interface só escolhe o que mostrar.

**Deploy (depois da autorização):**
1. backup validado;
2. `alembic upgrade` aplica a 0011 e a 0012;
3. conferir os três servidores;
4. auditoria do banco com as consultas da seção 1. **Ainda pendente:** os servidores seguem inacessíveis desta máquina; nenhuma mudança foi feita na produção.

---

**EN summary:**
- **Audit:** `current_user` accepted any active account, so a `viewer` could list clips, download videos and see publishing and infrastructure; `User.role` defaulted to `admin`; open sessions could not be ended.
- **Now:** `current_user` is the staff gate (deny by default), the role is read from the database, tokens carry a `session_version` that is bumped on deactivation, role change, password reset or "end sessions", and new accounts are viewers.
- **Sharing:** `clip_shares` holds one active share per clip and user, plus history. View-only by default, with separate download and variant permissions, optional expiry and revocation (kept).
- **Shared library:** checked on every request; anything not shared is a 404. Media is streamed with Range and `no-store`, and no paths or transcripts are exposed.
- **Not included:** e-mail, public sign-up and billing.
- **Tests:** 18 security tests, including a crawl of every API route with an external session; 317 passing overall.
- **Pending:** the live database audit could not run because the servers were unreachable; the exact read-only queries are listed in section 1.

**Update (review of PR #6):** each share freezes the approved media (path, sha256, size, thumbnail, version and variants). Re-edits never change what a guest watches unless the admin re-approves or chose `follow_latest`. Shared files are kept by the renderer and by storage cleanup and quarantine while the share is valid, and deleting a shared clip is refused. The crawl requires exactly 403, with an admin positive control. The two-account end-to-end test runs against a real API process. Migration 0012 follows 0011 with a single head. 349 tests pass.

## 8. Mais acesso para contas já criadas (v0.7.2, migração 0013)

Em **Pessoas e acesso**, cada conta tem uma área **Dar mais acesso**:

- **Todos os cortes de um streamer** (`POST /api/streamer-grants`):
  - a conta passa a ver todos os cortes **aprovados** do streamer, agora e os aprovados depois;
  - se um corte for rejeitado, ele sai da biblioteca da conta;
  - o acesso é feito com compartilhamentos comuns, marcados com `grant_id`. Valem a mesma versão imutável, a mesma proteção de arquivos e a mesma autorização;
  - aprovar um corte (individual ou em lote) já o compartilha. A rotina de manutenção sincroniza a cada ciclo, como rede de segurança;
  - compartilhamentos manuais nunca são duplicados, nem removidos por rejeição ou por revogação do acesso;
  - revogar (`DELETE /api/streamer-grants/{id}`) tira só os cortes que vieram por esse acesso, na próxima ação da conta;
  - mudar download ou prazo atualiza todos os cortes desse acesso.
- **Escolher cortes:** lista de cortes aprovados que a conta ainda não tem, com miniatura e filtro por streamer (`POST /api/shares`).
- **Tipo de conta:** tornar da equipe ou convidada, sempre com confirmação. A proteção do último administrador e da própria conta continua no backend.
- **Conta desativada:** a tela pede para reativar antes de dar acesso (o backend recusa com 422).

A **Minha Biblioteca** agrupa os vídeos por streamer, com um filtro por streamer.

**Migração 0013:**
- cria `streamer_grants` e `clip_shares.grant_id`;
- no downgrade, a tabela vira `streamer_grants_preserved` e os compartilhamentos continuam válidos como manuais, para nenhuma conta perder acesso sem aviso.

**Testes:**
- 9 em `tests/test_streamer_grants.py`;
- 6 cenários de navegador em `dashboard/scripts/ux/scenarios_access.py`;
- os 21 cenários anteriores continuam passando.

**EN:** Accounts can now get every approved clip of a streamer (future approvals included, rejected clips withdrawn), hand-picked clips, or a role change, always with confirmation. Guest libraries are grouped by streamer. Grants are materialised as ordinary shares, so snapshots, file protection and authorization are unchanged.
