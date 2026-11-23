# Release unificada 0.8.2 / Unified release 0.8.2

[PT-BR](#pt-br) · [English](#english) · [Comandos / Commands](#comandos--commands)

## PT-BR

A release integra a API 0.8.1-r2 (`4d36531`) e a IA multimodal da `main` (`44ee892`, PRs #29 e #31). Conserva o esquema PostgreSQL `0020`, os dados editoriais, os volumes de mídia e a integração assinada com o comercial. O código do dashboard preserva a revisão em produção e a versão acompanha a API. A PR #32 fica fora desta release.

A troca usa o Compose instalado, o projeto `asc` e os perfis ativos de cada host. Na 210, confirme `asc-commercial` e a seleção independente via `ASC_API_TAG`. Um administrador autorizado executa Docker Compose diretamente, com serviços explícitos e `--no-deps`; o subcomando `asc-ctl compose up` acrescenta `--remove-orphans`. Preserve os serviços fora da tabela, o `asc-ctl`, as redes, os volumes, o proxy e os demais projetos.

Antes da janela, confirme `ASC_SIGNUP_OPEN=false`, preserve a allowlist de cadastro já autorizada e confirme pagamentos fechados em todos os mercados e sem contas autorizadas para compra. O segredo compartilhado e o vínculo de billing seguem ativos para consultas assinadas e entregas pendentes. Mantenha `visual_discovery=off`, `min_score=75`, `auto_publish=false` e a pausa persistida de publicação. Drene uploads e jobs. Deixe o publicador parado durante o piloto, inclusive para envios manuais. Habilite `shadow` só no canal autorizado. Confira evidências, controles negativos, latência, backlog e GPU antes de promover o mesmo canal para `on`. Reverta o piloto para `off` em caso de regressão.

## English

This release integrates API 0.8.1-r2 (`4d36531`) with multimodal AI from `main` (`44ee892`, PRs #29 and #31). It retains PostgreSQL schema `0020`, editorial settings, media volumes and signed commercial billing. The dashboard ships its deployed implementation with a version matching the API. PR #32 stays outside this release.

Use the installed Compose files, project `asc` and each host's active profiles. On 210, verify `asc-commercial` and the independent `ASC_API_TAG` selector. An authorized administrator runs Docker Compose directly with explicit services and `--no-deps`; `asc-ctl compose up` adds `--remove-orphans`. Preserve services outside the table, `asc-ctl`, networks, volumes, the proxy and other projects.

Before the rollout, verify `ASC_SIGNUP_OPEN=false`, retain the authorized sign-up allowlist and confirm that payments are closed in all markets with zero checkout test accounts. Retain the shared billing link for signed reads and outstanding deliveries. Keep `visual_discovery=off`, `min_score=75`, `auto_publish=false` and the persistent publication pause. Drain uploads and jobs. Keep the publisher stopped during the pilot, including for manual uploads. Enable `shadow` for the authorized channel only. Check evidence, negative controls, latency, backlog and GPU before promoting that channel to `on`. Set the pilot channel back to `off` if it regresses.

## Serviços e imagens / Services and images

| Host | Troca na janela / Cutover | Imagem / Image |
| --- | --- | --- |
| 210 | `api` (API + dashboard), `worker-scoring` | `ai-stream-clipper:$TAG` |
| 210 | `worker-vision` somente se já ativo / only if already active | `ai-stream-clipper-vision:$TAG` |
| 201 | `worker-vision` | `ai-stream-clipper-vision:$TAG` |
| 206 | `worker-audio`, `worker-render`, `worker-capture` (por último / last) | `ai-stream-clipper:$TAG` |
| 210 | `worker-publisher` para retomada autorizada posterior / for later authorized resumption | `ai-stream-clipper:$TAG` |

O build também gera `ai-stream-clipper-commercial:$TAG` para validação e arquivamento. A 210 mantém a imagem comercial ativa, o `ASC_COMMERCIAL_TAG` atual e o mesmo contêiner. O serviço `tunnel`, o PostgreSQL da 212 e qualquer PostgreSQL local da 210 ficam fora da troca. / The build also produces `ai-stream-clipper-commercial:$TAG` for validation and archiving. Host 210 retains its current commercial image, `ASC_COMMERCIAL_TAG` and container. The tunnel, PostgreSQL on 212 and any local PostgreSQL on 210 stay outside the cutover.

## Comandos / Commands

### Build

Parta do SHA aprovado da `main` após o merge e faça o build em um checkout separado. Se o administrador usar a 206, verifique CPU, RAM, I/O e espaço para contexto, cache e arquivos, em uma janela sem captura ou renderização. Use um builder Linux amd64 conhecido, com BuildKit compatível com `rewrite-timestamp`. Use o mesmo builder e compare `image-ids.txt` em uma reconstrução. As bases têm digest fixo, APT usa snapshots assinados e Python e npm usam locks. O contexto vem de `git archive`, sem configurações ou dados locais. / Build the approved post-merge `main` SHA in a separate checkout. On 206, an authorized administrator checks CPU, RAM, I/O and free space for context, cache and archives during a capture- and render-free window. Use a known Linux amd64 BuildKit builder supporting `rewrite-timestamp`. Reuse the same builder and compare `image-ids.txt` on rebuild. Base digests, signed APT snapshots and dependency locks pin build inputs. The context comes from `git archive`, without local settings or data. [Docker Build](https://docs.docker.com/build/ci/github-actions/reproducible-builds/)

```bash
set -euo pipefail
: "${RELEASE:?set the approved full main SHA}"
git fetch origin main
git merge-base --is-ancestor "$RELEASE" origin/main
TAG="unified-${RELEASE:0:12}"
git worktree add --detach "../asc-$TAG" "$RELEASE"
cd "../asc-$TAG"
bash scripts/build-unified-release.sh "../artifacts-$TAG"
```

Confirme `SHA256SUMS` e o ID de cada imagem carregada. Copie apenas os arquivos necessários em cada host conforme a tabela. Preserve as imagens antigas, seus IDs e tags. / Verify `SHA256SUMS` and each loaded image ID. Transfer only the archives listed for each host. Retain the old images, IDs and tags.

### Pré-verificação e backup / Preflight and backup

Prepare uma janela sem lives. Em cada host, identifique o arquivo Compose instalado e os perfis atuais. Use um caminho absoluto sob controle administrativo. Confirme o projeto `asc`, compare os serviços declarados com os contêineres da janela e salve a relação de contêiner, imagem e estado antes da troca. Use o diretório de transferência aprovado e carregue um arquivo por vez. / Schedule a window without live streams. Identify the installed Compose file and current profiles on each host. Use an absolute administrator-controlled path. Confirm the `asc` project, compare declared services with the existing containers and record each container, image and state. Load one archive at a time from the approved transfer directory.

```bash
set -euo pipefail
umask 077
: "${TAG:?set the tag from build-info.txt}" "${COMPOSE_FILE:?set the installed absolute path}"
profiles=()                           # set (--profile gpu --profile tunnel) only for profiles already in use
dc() { sudo env COMPOSE_REMOVE_ORPHANS=false docker compose -p asc --env-file /etc/asc/asc.env -f "$COMPOSE_FILE" "${profiles[@]}" "$@"; }
ctl() { sudo -n /usr/local/sbin/asc-ctl "$@"; }
recreate() { dc up -d --wait --wait-timeout 120 --no-deps --no-build --pull never "$@"; }
dc config --quiet
dc config --services
sudo docker ps -a --filter label=com.docker.compose.project=asc --format '{{.ID}} {{.Names}} {{.Image}} {{.Status}}'
sudo test ! -e "/etc/asc/asc.env.pre-$TAG"
sudo install -o root -g root -m 0600 /etc/asc/asc.env "/etc/asc/asc.env.pre-$TAG"

# Repeat per archive from the verified transfer directory. Set ARCHIVE to the basename from SHA256SUMS.
awk -v archive="$ARCHIVE" '$2 == archive { print; found++ } END { if (found != 1) exit 1 }' SHA256SUMS |
  sha256sum -c -
ctl image-load "$ARCHIVE"              # archive in the operator's approved home
sudo docker image inspect --format '{{.Id}}' "$IMAGE"  # compare with image-ids.txt
```

Na 210, use o cookie privado de uma sessão administrativa e a origem TLS correspondente. Confirme o billing com as consultas assinadas abaixo, antes e depois da troca. Verifique que o Compose instalado seleciona a API por `ASC_API_TAG`, que PostgreSQL é a 212 na porta 55432 e que os volumes e as montagens permanecem iguais. Salve apenas as informações necessárias em arquivos privados. / On 210, use a private cookie jar from an authenticated admin session and its matching TLS origin. Run the signed billing reads below before and after cutover. Verify the installed Compose selects the API via `ASC_API_TAG`, that PostgreSQL is on 212 port 55432, and that volumes and mounts match. Store only necessary information in private files.

```bash
# 210
api() { curl -fsS --max-time 10 -b "$COOKIE_JAR" -H 'X-Asc-Csrf: 1' -H 'Content-Type: application/json' "$@"; }
dc config --format json | jq -e '.services.commercial and
  (.services.api.environment.ASC_DATABASE_URL | contains("@192.168.1.212:55432/asc"))' >/dev/null
api "$API/api/settings" | jq -e '(.visual_discovery // "off") == "off" and .min_score == 75 and
  .auto_publish == false and ((.multimodal_scoring // false) == false) and ((.grounded_metadata // false) == false)'
api "$API/api/auth/config" | jq -e '.signup_enabled == false'
ctl exec asc-api -- clipper publish-pause on --reason 'piloto multimodal release unificada'
# 206, pause capture to prevent new work while the queue drains.
dc stop worker-capture
# 212. Wait for 0 live sessions, 0 uploads/processing, and 0 running jobs.
q() { ctl exec asc-postgres -- psql -X -v ON_ERROR_STOP=1 -U asc -d asc -Atc "$1"; }
test "$(q 'select version_num from alembic_version')" = '0020'
test "$(q "select count(*) from stream_sessions where status='live'")" = 0
test "$(q "select count(*) from publications where status in ('uploading','processing')")" = 0
test "$(q "select count(*) from jobs where status='running'")" = 0
q "select value from app_settings where key='ai'" > editorial-before.json
q "select id, platform, remote_id, remote_url from publications where status='published' order by id" > published-before.txt
ctl backup-db                          # set DB_BACKUP to the printed path
gzip -t "$DB_BACKUP"
sha256sum "$DB_BACKUP"
```

Confirme um backup recuperável de `asc_clips` e do armazenamento de segmentos, e registre hash de MP4s já gerados. O comercial permanece ativo. Se for preciso fazer backup dele, use o backup online do SQLite ou snapshot consistente, incluindo o WAL, de acordo com o procedimento já autorizado. Preserve filas, publicações e backups. / Confirm recoverable backups of `asc_clips` and segment storage, and record hashes of existing MP4s. The commercial service stays active. If a fresh commercial backup is required, use SQLite online backup or a consistent snapshot covering WAL under the existing operating procedure. Retain queues, publications and backups.

### Troca e saúde / Cutover and health

Na 210, altere apenas `ASC_API_TAG` e `ASC_IMAGE_TAG` para `$TAG` com `sudoedit /etc/asc/asc.env`. Na 201 e 206, altere apenas `ASC_IMAGE_TAG`. Confira as imagens resolvidas e verifique que os IDs antigos seguem disponíveis. Na 210, mantenha `worker-vision` na lista apenas se ativo antes da janela. / On 210, update only `ASC_API_TAG` and `ASC_IMAGE_TAG` to `$TAG` with `sudoedit /etc/asc/asc.env`. On 201 and 206 update only `ASC_IMAGE_TAG`. Verify resolved images and retained old IDs. On 210 include `worker-vision` only if active before maintenance.

```bash
# 210
workers210=(worker-scoring)            # add worker-vision only if already active
dc stop worker-publisher "${workers210[@]}"
dc config --images
commercial_before=$(dc ps -q commercial)
test -n "$commercial_before"
recreate api
curl -fsS --retry 20 --retry-connrefused --retry-max-time 90 --max-time 10 \
  --cacert /etc/asc/tls/ca.crt https://192.168.1.210:8780/readyz
curl -fsS --max-time 10 --cacert /etc/asc/tls/ca.crt https://192.168.1.210:8780/healthz | jq -e '.version == "0.8.2"'
api "$API/api/auth/config" | jq -e '.signup_enabled == false'
api "$API/api/settings" | jq -e '.visual_discovery == "off" and .min_score == 75 and
  .auto_publish == false and .multimodal_scoring == false and .grounded_metadata == false'
api "$API/api/publish/control" | jq -e '.paused == true'
api "$API/api/streamers" | jq -e 'all(.[]; .preferences.visual_discovery != "on" and .preferences.visual_discovery != "shadow")'
test "$(dc ps -q commercial)" = "$commercial_before"
```

Verifique os dois sentidos do vínculo na 210. As consultas usam os segredos dos contêineres e só leem estado. / Verify both directions of the billing link on 210. These queries use the containers' own credentials and only read state.

```bash
ctl exec asc-api -- python -c '
from clipper import billing_link
status, data = billing_link.call("/internal/billing/status", {})
assert status == 200 and data["open"] is False and data["test_accounts"] == 0
assert data["markets"] and not any(data["markets"].values())
print("API -> commercial OK")
'
ctl exec asc-commercial -- node --input-type=module -e '
import { httpStateReader, outboxSettings } from "./server/outbox.js";
const result = await httpStateReader(outboxSettings())([]);
if (!Array.isArray(result)) process.exit(1);
console.log("commercial -> API OK");
'
```

```bash
# 210, after API, settings and billing checks
recreate "${workers210[@]}"
# 201
recreate worker-vision
ctl exec asc-worker-vision -- clipper healthcheck worker
# 206
recreate worker-audio worker-render
ctl exec asc-worker-audio -- clipper healthcheck worker
ctl exec asc-worker-render -- clipper healthcheck worker
# 206, after all checks and heartbeats are healthy
recreate worker-capture
```

Em cada host, confira `dc ps`, os IDs das imagens e os logs. Na 210, verifique o dashboard, `GET /api/infrastructure`, heartbeats e filas. Compare os hashes de MP4, os IDs e URLs de publicações e o esquema `0020` com o baseline. Confirme os IDs anteriores dos contêineres fora do escopo. Rode apenas a seção de piloto do roteiro multimodal anterior após validar os serviços acima. / Check `dc ps`, image IDs and logs on each host. On 210, check the dashboard, `GET /api/infrastructure`, heartbeats and queues. Compare MP4 hashes, published IDs and URLs and schema `0020` against the baseline. Verify any containers outside scope retain their original IDs. Use only the pilot section of the previous multimedia runbook after validating the services above. [Piloto por canal / Per-channel pilot](MULTIMODAL_PR29_PR31.md#piloto-por-canal--per-channel-pilot)

### Rollback

Para regressão só no piloto, volte o canal para `off`. Para regressão de imagem, mantenha a pausa e o publicador parado, pare primeiro a captura na 206, espere os jobs drenarem e pare os workers afetados. Restaure os seletores anteriores pelo backup root-only, com o valor ou a ausência original de `ASC_API_TAG` e `ASC_IMAGE_TAG` em cada host. Preserve os demais valores atuais. Recrie só os serviços da tabela e valide saúde, billing e heartbeats antes de voltar a capturar. Restauração do banco ou da mídia é recuperação de desastre separada. / For a pilot-only regression set the channel to `off`. For an image regression keep the pause and publisher stop in place, stop capture on 206, drain jobs and stop affected workers. Restore the original tag values or absence of `ASC_API_TAG` and `ASC_IMAGE_TAG` on each host using the root-only backup. Preserve the other current values. Recreate only the listed services and verify health, billing and heartbeats before resuming capture. Database or media restoration is a separate disaster recovery procedure.

```bash
# 210, if the pilot was enabled and the API is reachable
api -X PATCH -d '{"preferences":{"visual_discovery":"off"}}' "$API/api/streamers/$CHANNEL_ID"
# After restoring tags on each affected host, use the same dc/recreate functions.
# 210
recreate api
curl -fsS --max-time 10 --cacert /etc/asc/tls/ca.crt https://192.168.1.210:8780/readyz
api "$API/api/auth/config" | jq -e '.signup_enabled == false'
recreate "${workers210[@]}"
# 201
recreate worker-vision
# 206
recreate worker-audio worker-render
recreate worker-capture
```
