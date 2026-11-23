# Implantação controlada das PRs #29 e #31 / Controlled rollout of PRs #29 and #31

[PT-BR](#pt-br) · [English](#english) · [Comandos / Commands](#comandos--commands)

## PT-BR

Integre a #29 em `main` com merge commit. Em seguida, confirme que o head da #29 está em `main`, redirecione a base da #31 para `main` e revise o diff antes do segundo merge. Publique imagens só a partir da revisão que inclui as duas PRs. A #31 contém as correções de confirmação visual, limites de carga e piloto por canal. As PRs usam o esquema existente, cujo head é `0020`, e os mesmos volumes. A #32 e as mudanças comerciais ficam fora da integração.

**A 210 exige conferência antes do deploy.** Os registros de operação indicam uma API 0.8.1 com o cadastro público protegido e um `asc-commercial` no mesmo projeto Compose. O Compose da `main` não declara esse serviço, e o `asc-ctl compose up` usa `--remove-orphans`. Mantenha o Compose e o `asc-ctl` instalados, com os mesmos perfis. Compare os serviços ativos com `asc-ctl compose config` e pare se algum serviço ativo do projeto `asc` estiver ausente da configuração. Se a API ativa incluir a trava da 0.8.1, aplique a integração multimodal ao **commit exato da API ativa** em um checkout local de build. Preserve o código de cadastro e o dashboard já implantados. A integração com a base documentada `4d36531` foi verificada localmente. Se a base ativa não puder ser identificada ou a sobreposição alterar os arquivos protegidos, bloqueie a criação da nova imagem da API. [Operação da API 0.8.1](https://github.com/gamerplay20p5-dotcom/ai-stream-clipper/blob/4d36531/infrastructure/ops/pagamentos/api-0.8.1-preparar.sh)

Faça a preparação fora dos hosts do Hank. Registre, por serviço, a imagem e a tag anterior, os perfis, o estado, os volumes e as configurações editoriais. Faça backup do PostgreSQL na 212 e confirme um backup ou snapshot recuperável do volume `asc_clips` na 210. Registre IDs e URLs de Shorts já enviados e baixe amostras de MP4 existentes para comparar o SHA-256. Restrinja o acesso aos backups e aos registros. Agende a troca sem lives ativas. Preserve os segmentos, os volumes, os arquivos de configuração e os tags anteriores. Use somente serviços `asc` nomeados; os demais projetos, o comercial, o túnel e os contêineres PostgreSQL ficam fora das operações.

Mantenha `visual_discovery=off`, `min_score=75` e `auto_publish=false` globalmente. Confirme que `multimodal_scoring` e `grounded_metadata` estão desligados para a comparação piloto. Mantenha `publish-pause on` e o `worker-publisher` parado durante a homologação. A pausa bloqueia novos uploads, mas o processo publicador também agenda regras de clientes. Aguarde uploads e processamentos em curso, preserve a fila e as regras e revise os itens pendentes antes de uma eventual retomada autorizada. A pausa também suspende envios manuais. Cortes podem ser gerados e revisados localmente no painel.

Comece com um canal autorizado em `shadow`. Confira as propostas em `scoring.visual_shadow` no diagnóstico da sessão, os controles negativos, `visual_skipped`, a latência de áudio e o uso de CPU da 206. Compare heartbeat, backlog e latência de visão com o registro anterior. Promova **somente o mesmo canal** para `on` após revisão dos resultados, com a Arc atendendo o modelo configurado e carga aceitável. Revise os primeiros Shorts de gameplay real autorizado e as recusas do Quality Gate. O ensaio sintético existente não comprovou aprovação semântica pelo VLM. Recoloque o canal em `off` se houver regressão e mantenha o global em `off`.

## English

Merge #29 into `main` with a merge commit. Confirm that #29's head is an ancestor of `main`, then retarget #31 to `main` and review its diff before merging it. Build deployment images from a revision containing both PRs. #31 supplies visual confirmation, load limits and per-channel pilot controls. Both PRs use the existing schema at head `0020` and the existing volumes. PR #32 and commercial changes stay out of the integration.

**Verify host 210 before deployment.** Operations records identify an API 0.8.1 build with a public sign-up gate and an `asc-commercial` service in the same Compose project. The Compose file on `main` omits that service, and `asc-ctl compose up` uses `--remove-orphans`. Keep the installed Compose file, `asc-ctl` and active profiles. Compare the active services with `asc-ctl compose config` and stop if the configuration omits a running `asc` service. If the active API includes the 0.8.1 sign-up gate, apply the multimodal integration to the **exact deployed API commit** in a local build checkout. Preserve the deployed sign-up code and dashboard. The merge was verified locally against the documented `4d36531` baseline. Block the API image build if the deployed baseline cannot be identified or the overlay changes protected files. [API 0.8.1 operations](https://github.com/gamerplay20p5-dotcom/ai-stream-clipper/blob/4d36531/infrastructure/ops/pagamentos/api-0.8.1-preparar.sh)

Prepare artifacts outside the Hank hosts. Record each service's current image, tag, profiles, state, volumes and editorial settings. Back up PostgreSQL on 212 and confirm a recoverable backup or snapshot of `asc_clips` on 210. Record IDs and URLs of previously published Shorts and compare the SHA-256 of downloaded MP4 samples before and after the rollout. Restrict access to the backups and records. Schedule the cutover with no live sessions. Preserve segments, volumes, configuration and rollback tags. Operate on explicit `asc` service names; leave other projects, the commercial service, the tunnel and the PostgreSQL containers outside the cutover.

Keep global `visual_discovery=off`, `min_score=75` and `auto_publish=false`. Check that `multimodal_scoring` and `grounded_metadata` are off for the pilot comparison. Keep `publish-pause on` and `worker-publisher` stopped during validation. The pause blocks new uploads, while the publisher process can still plan customer publications. Wait for active uploads and processing, preserve the queue and rules, and review pending items before any authorized resumption. The pause also holds manual uploads. Users can still generate and review clips in the dashboard.

Start with one authorized channel in `shadow`. Check `scoring.visual_shadow` in session diagnostics, negative controls, `visual_skipped`, audio latency and CPU use on 206. Compare heartbeats, backlog and vision latency with the baseline. Promote **only that channel** to `on` after reviewing the results and confirming acceptable Arc utilization and model availability. Review the first Shorts from authorized real gameplay and any Quality Gate rejections. The existing synthetic run did not establish VLM semantic approval. Switch the channel back to `off` if it regresses and keep global discovery off.

## Comandos / Commands

### Integração no GitHub / GitHub integration

Execute após aprovar os dois heads. / Run after approving both heads.

```bash
set -euo pipefail
PR29=$(gh pr view 29 --json headRefOid --jq .headRefOid)
PR31=$(gh pr view 31 --json headRefOid --jq .headRefOid)
gh pr merge 29 --merge --match-head-commit "$PR29"
git fetch origin main
git merge-base --is-ancestor "$PR29" origin/main
gh pr edit 31 --base main
gh pr diff 31 --name-only
```

Confira o diff e os checks depois de mudar a base. Se `main` avançar além do conteúdo das duas PRs, repita a revisão antes de gerar imagens. / Review the diff and checks after retargeting. If `main` advances beyond the reviewed PRs, review the changes again before building.

```bash
gh pr merge 31 --merge --match-head-commit "$PR31"
git fetch origin main
git merge-base --is-ancestor "$PR31" origin/main
RELEASE=$(git rev-parse origin/main)
git diff --exit-code "$PR31" "$RELEASE" -- backend dashboard commercial infrastructure
```

### Imagens locais / Local images

Use diretórios novos e `RELEASE` validado acima. Se a 210 usa a API 0.8.1, identifique o commit ativo pelo registro de implantação e valide a sobreposição abaixo. Se usa a API da `main`, use `WORKER_SRC` como `API_SRC` e o mesmo tag da imagem `app` em ambos. / Use new directories and the verified `RELEASE` above. For a 0.8.1 API, identify the active commit from deployment records and verify the overlay below. For a `main` API use `WORKER_SRC` for `API_SRC` and the same `app` image tag for both.

```bash
set -euo pipefail
WORKER_SRC=../asc-multimodal-worker-build
git worktree add --detach "$WORKER_SRC" "$RELEASE"
API_SRC=$WORKER_SRC
if [[ -n ${DEPLOYED_API_COMMIT:-} ]]; then
  API_SRC=../asc-multimodal-api-build
  git worktree add --detach "$API_SRC" "$DEPLOYED_API_COMMIT"
  git -C "$API_SRC" merge --no-commit --no-ff "$RELEASE"
  git -C "$API_SRC" diff --cached --exit-code -- commercial dashboard infrastructure \
    backend/clipper/api/routes_auth.py backend/clipper/migrations
fi
```

```bash
TAG="mm-${RELEASE:0:12}"
APP="ai-stream-clipper:$TAG"
VISION="ai-stream-clipper-vision:$TAG"
API_APP=$APP
images=("$APP" "$VISION")
docker build --platform linux/amd64 -f "$WORKER_SRC/infrastructure/docker/app.Dockerfile" -t "$APP" "$WORKER_SRC"
docker build --platform linux/amd64 -f "$WORKER_SRC/infrastructure/docker/vision-openvino.Dockerfile" -t "$VISION" "$WORKER_SRC"
if [[ $API_SRC != "$WORKER_SRC" ]]; then
  API_APP="ai-stream-clipper:$TAG-api"
  docker build --platform linux/amd64 -f "$API_SRC/infrastructure/docker/app.Dockerfile" -t "$API_APP" "$API_SRC"
  docker run --rm --network none --entrypoint python "$API_APP" -m pip check
  images+=("$API_APP")
fi
docker run --rm --network none --entrypoint python "$APP" -m pip check
docker run --rm --network none --entrypoint /opt/asc/bin/python "$VISION" -m pip check
for image in "${images[@]}"; do
  archive="${image/:/_}.tar.gz"
  docker save "$image" | gzip -c > "$archive" || exit 1
  sha256sum "$archive"
done
printf 'ASC_IMAGE_TAG=%s\nAPI_IMAGE_TAG=%s\n' "$TAG" "${API_APP#*:}"
```

### Pré-verificação na janela / Maintenance-window preflight

Transfira os arquivos validados pelos canais operacionais existentes. Confira o SHA-256 e carregue só as imagens exigidas. Na 206 carregue `app`, na 201 carregue `vision` e na 210 carregue `app`, `vision` quando usado e a API separada quando aplicável. Registre o retorno de `ps`, `images` e `config` antes de iniciar qualquer troca. / Transfer verified archives through existing operational channels. Verify SHA-256 and load only the required images. Load `app` on 206, `vision` on 201 and on 210 if enabled, and both the worker `app` and the separate API image on 210 where applicable. Record `ps`, `images` and `config` before cutover.

```bash
ctl() { sudo -n /usr/local/sbin/asc-ctl "$@"; }
ctl image-load "$ARCHIVE"  # repetir para cada arquivo validado / repeat for each verified archive
ctl images                 # imagem nova e anterior / new and previous images
# Na 210, manter apenas os perfis que já estavam ativos / On 210, keep exactly the active profiles
profiles=(--gpu)  # modelo / example: (--gpu --tunnel) se ambos ativos / if both were active
workers210=(worker-scoring worker-vision)  # sem worker-vision se inativo / omit worker-vision if inactive
ctl compose config "${profiles[@]}"
ctl compose ps "${profiles[@]}"
# 210 [admin], conferir todos os serviços ativos asc / verify all running asc services
sudo docker ps --filter label=com.docker.compose.project=asc \
  --format '{{.Label "com.docker.compose.service"}} {{.ID}} {{.Image}}'
```

Na 210, use uma sessão de administrador já autenticada com TLS válido. Defina `API` com a origem correspondente ao cookie e `COOKIE_JAR` como arquivo privado. Confira as configurações, pause as publicações e use a 212 para verificar a drenagem e o esquema. Faça o backup antes da troca. / On 210 use an existing authenticated admin session with valid TLS. Set `API` to the origin matching the cookie and `COOKIE_JAR` to a private file. Verify settings, pause publishing, then check drain conditions and schema on 212. Take the backup before switching images.

```bash
# 210
api() { curl -fsS --max-time 10 -b "$COOKIE_JAR" -H 'X-Asc-Csrf: 1' -H 'Content-Type: application/json' "$@"; }
api "$API/api/settings" | jq -e '(.visual_discovery // "off") == "off" and .min_score == 75 and .auto_publish == false'
ctl exec asc-api -- clipper publish-pause on --reason 'homologacao multimodal PR 29/31'

# 212
q() { ctl exec asc-postgres -- psql -X -v ON_ERROR_STOP=1 -U asc -d asc -Atc "$1"; }
test "$(q 'select version_num from alembic_version')" = '0020'
q "select count(*) from stream_sessions where status='live'"                    # 0
q "select count(*) from publications where status in ('uploading','processing')" # 0
q "select type, count(*) from jobs where status='running' group by type order by type"
umask 077
STATE_DIR="$HOME/rollout-multimodal-$TAG"
install -d -m 0700 "$STATE_DIR"
q "select value from app_settings where key='ai'" > "$STATE_DIR/editorial-before.json"
q "select id, platform, remote_id, remote_url from publications where status='published' order by id" \
  > "$STATE_DIR/published-before.txt"
ctl backup-db                        # guardar caminho e SHA-256 / record path and SHA-256
gzip -t "$DB_BACKUP"                  # caminho devolvido por backup-db / path printed by backup-db
sha256sum "$DB_BACKUP"
```

### Troca e saúde / Cutover and health

Faça cópia root-only de `/etc/asc/asc.env` em cada host antes de gravar. Na 210, confirme `ASC_DB_HOST=192.168.1.212`, `ASC_DB_PORT=55432` e o seletor de tag esperado pelo Compose instalado. Confirme que as imagens antigas permanecem carregadas. Espere os jobs ativos terminarem antes de parar seus workers. / Take a root-only copy of `/etc/asc/asc.env` on each host before writing it. On 210, confirm the existing database address and port and the tag selector in the installed Compose file. Confirm that prior images remain loaded. Wait for active jobs to drain before stopping their workers.

```bash
# Cada host [admin], antes de trocar tags / On each host [admin], before changing tags
sudo test ! -e "/etc/asc/asc.env.pre-$TAG" &&
  sudo install -o root -g root -m 0600 /etc/asc/asc.env "/etc/asc/asc.env.pre-$TAG"

# 206, sem live ativa / no active live
ctl compose stop worker-capture
# 210, após a drenagem e com os perfis registrados / after drain, with recorded profiles
ctl compose stop "${profiles[@]}" worker-publisher "${workers210[@]}"
# 206
ctl compose stop worker-audio worker-render
# 201
ctl compose stop worker-vision
```

```bash
# 210. Se o Compose separa a API, [admin] use sudoedit /etc/asc/asc.env para definir ASC_API_TAG com API_IMAGE_TAG.
# If Compose uses a separate API tag, [admin] set ASC_API_TAG to the printed API_IMAGE_TAG with sudoedit.
ctl set-tag "$TAG"
ctl compose config "${profiles[@]}"
ctl compose up "${profiles[@]}" api
ctl compose images "${profiles[@]}" api
curl -fsS --max-time 10 --retry 20 --retry-connrefused --retry-max-time 90 \
  --cacert /etc/asc/tls/ca.crt https://192.168.1.210:8780/readyz
curl -fsS --max-time 10 --cacert /etc/asc/tls/ca.crt https://192.168.1.210:8780/healthz
ctl compose logs "${profiles[@]}" api --tail 100
api "$API/api/settings" | jq -e '.visual_discovery == "off" and .min_score == 75 and
  .auto_publish == false and .multimodal_scoring == false and .grounded_metadata == false'
api "$API/api/publish/control" | jq -e '.paused == true'
api "$API/api/streamers" | jq -e \
  'all(.[]; .preferences.visual_discovery != "on" and .preferences.visual_discovery != "shadow")'
```

Prossiga só se a API estiver pronta, as três verificações de configuração passarem e o tag da API for o esperado. / Proceed only after the API is ready, all three configuration checks pass, and the API image tag matches.

```bash
# 210
ctl compose up "${profiles[@]}" "${workers210[@]}"

# 201
ctl set-tag "$TAG"
ctl compose up worker-vision

# 206. Reative captura após validar API e workers / Re-enable capture after API and worker checks
ctl set-tag "$TAG"
ctl compose up worker-audio worker-render
ctl compose ps
ctl exec asc-worker-audio -- clipper healthcheck worker
ctl exec asc-worker-render -- clipper healthcheck worker
ctl compose up worker-capture
```

Confira `compose ps`, `/api/infrastructure`, filas e logs dos serviços ativados. Compare o schema `0020`, os IDs e URLs publicados e os MP4 de amostra com o registro anterior. Confirme que os serviços fora do escopo mantêm seus IDs de contêiner. Se a API anterior era 0.8.1 com o cadastro fechado, `GET /api/auth/config` deve continuar retornando `signup_enabled=false`. / Check `compose ps`, `/api/infrastructure`, queues and logs for the enabled services. Compare schema `0020`, previously published IDs and URLs, and MP4 samples with the baseline. Verify container IDs for services outside the cutover. For a previously closed 0.8.1 API, verify `signup_enabled=false` in `GET /api/auth/config`.

### Piloto por canal / Per-channel pilot

Use a sessão de administrador da etapa anterior. / Use the admin session from the previous step.

```bash
api "$API/api/settings" | jq -e '.visual_discovery == "off" and .min_score == 75 and
  .auto_publish == false and .multimodal_scoring == false and .grounded_metadata == false'
api "$API/api/publish/control" | jq -e '.paused == true'
api "$API/api/streamers/$CHANNEL_ID" | jq -e '.preferences.min_score == null or .preferences.min_score == 75'
api "$API/api/streamers" | jq -e --arg id "$CHANNEL_ID" \
  'all(.[]; (.id | tostring) == $id or (.preferences.visual_discovery != "on" and .preferences.visual_discovery != "shadow"))'
api -X PATCH -d '{"preferences":{"visual_discovery":"shadow"}}' "$API/api/streamers/$CHANNEL_ID"
```

Execute a promoção somente após aprovar a evidência de `shadow`. / Run the promotion only after accepting the shadow evidence.

```bash
api -X PATCH -d '{"preferences":{"visual_discovery":"on"}}' "$API/api/streamers/$CHANNEL_ID"
```

Use `PUT /api/settings` com um JSON parcial apenas se for preciso corrigir uma flag global. Confirme `min_score=75` e os demais critérios após a mudança; investigue discrepâncias com o snapshot antes de sobrescrever valores. / Use a partial JSON payload with `PUT /api/settings` only if a global flag needs correction. Verify `min_score=75` and the other editorial criteria afterward; compare any discrepancy with the snapshot before overwriting values.

### Rollback

Para um problema restrito ao piloto, mude o canal para `off`. Se for necessário reverter imagens, mantenha a publicação pausada, drene os jobs e pare os mesmos serviços ASC. Restaure os seletores de tag anteriores por host. Na 210, [admin] restaure também `ASC_API_TAG` ao valor anterior ou remova a linha se originalmente ausente. Use o backup do ambiente para conferir os valores e mantenha os outros ajustes. Recrie a API e verifique saúde, recrie os workers nomeados com os mesmos perfis e retome a captura por último. Mantenha o publisher parado. Como as PRs conservam `0020`, o rollback de imagem conserva o banco e a mídia atuais. / For a pilot-only problem switch the channel off. For image rollback, keep publishing paused, drain jobs and stop the same ASC services. Restore each host's previous tag selectors. On 210, [admin] also restore `ASC_API_TAG` or remove it if originally absent. Check the environment backup for old values and preserve other settings. Recreate the API, verify health, recreate the named workers using the recorded profiles, and resume capture last. Keep the publisher stopped. Both PRs retain `0020`, so image rollback preserves the current database and media.

```bash
api -X PATCH -d '{"preferences":{"visual_discovery":"off"}}' "$API/api/streamers/$CHANNEL_ID"
```

```bash
# Em cada host, com seu valor capturado antes da troca / On each host, use its recorded original tag
ctl set-tag "$PREVIOUS_TAG"
# 210, após restaurar ASC_API_TAG quando aplicável / after restoring ASC_API_TAG if used
ctl compose up "${profiles[@]}" api
curl -fsS --max-time 10 --cacert /etc/asc/tls/ca.crt https://192.168.1.210:8780/readyz
ctl compose up "${profiles[@]}" "${workers210[@]}"
# 201
ctl compose up worker-vision
# 206
ctl compose up worker-audio worker-render
ctl compose up worker-capture
```

Mantenha a restauração do backup como recuperação de desastre separada. Evite `compose down`, remoção de volumes, `prune` ou downgrade Alembic durante o rollout e o rollback. / Treat backup restore as separate disaster recovery. Avoid `compose down`, volume removal, pruning or Alembic downgrade during rollout and rollback.
