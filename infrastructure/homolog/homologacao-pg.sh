#!/usr/bin/env bash
# PostgreSQL homologation of PRs #23, #24 and #25 on a Linux server, run ONCE by the server administrator.
#
#   sudo ./infrastructure/homolog/homologacao-pg.sh             preflight + run + report + cleanup
#   sudo ./infrastructure/homolog/homologacao-pg.sh --check     preflight only (changes nothing)
#   sudo ./infrastructure/homolog/homologacao-pg.sh --keep-images   keep the downloaded base images afterwards
#
# Isolation (nothing else on the server is touched):
# * everything is created with a unique run id (asc-homolog-<UTC time>) and the label asc.homolog.run=<id>:
#   a PostgreSQL 16.4 container and a Python 3.12 runner container, plus one INTERNAL Docker network between them;
# * the database is NOT published on any host port; data lives in a tmpfs (memory) and dies with the container;
# * fake credentials; the tests refuse any database host other than the run's own ("postgres"), and this script
#   refuses to run on 192.168.1.212;
# * limits: PostgreSQL 2 CPUs / 2 GB RAM; runner 4 CPUs / 6 GB RAM / 4096 pids, low CPU priority;
# * the source checkout is mounted READ-ONLY; only the report directory is writable;
# * cleanup removes only the run's containers and network, and the two base images only if this run pulled them;
#   never `docker system prune`, never other containers, volumes, networks or images; existing services untouched;
# * no production database, no secrets, no deploy, no migration outside the throwaway database.
# The heavy audio/AI dependencies are NOT installed (only backend[dev,vision]); the end-to-end media tests
# (Whisper) are out of scope here and run on the developer machine.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_ID="asc-homolog-$(date -u +%Y%m%dt%H%M%Sz)"
LABEL="asc.homolog.run=$RUN_ID"
PG_IMAGE="postgres:16.4-bookworm"
PY_IMAGE="python:3.12-slim-bookworm"
NET="$RUN_ID-net"
OUT="$ROOT/reports/homologacao/$RUN_ID"
CHECK_ONLY=0; KEEP_IMAGES=0
MIN_DISK_GB=10; MIN_RAM_GB=12; MIN_CPUS=6
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    --keep-images) KEEP_IMAGES=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "opção desconhecida: $arg"; exit 64 ;;
  esac
done

say() { printf '%s\n' "$*"; }
fail() { say "ERRO: $*"; exit 1; }

# ---------------------------------------------------------------- preflight (read-only)
say "== Verificação prévia ($RUN_ID)"
[ "$(id -u)" -eq 0 ] || fail "rode com sudo (o Docker exige o administrador; a conta asc não ganha acesso ao Docker)"
command -v docker >/dev/null || fail "docker não encontrado"
docker info >/dev/null 2>&1 || fail "o daemon do Docker não responde"
if hostname -I 2>/dev/null | tr ' ' '\n' | grep -qx "192.168.1.212"; then fail "recusado: esta é a máquina do banco de produção (.212)"; fi
[ -f "$ROOT/backend/pyproject.toml" ] && [ -f "$ROOT/tests/test_postgres_migrations.py" ] || fail "rode a partir de um checkout do PR #25"
DOCKER_ROOT="$(docker info -f '{{.DockerRootDir}}')"
DISK_GB=$(( $(df -Pk "$DOCKER_ROOT" | awk 'NR==2{print $4}') / 1024 / 1024 ))
OUT_DISK_GB=$(( $(df -Pk "$ROOT" | awk 'NR==2{print $4}') / 1024 / 1024 ))
RAM_GB=$(( $(awk '/MemAvailable/{print $2}' /proc/meminfo) / 1024 / 1024 ))
CPUS=$(nproc)
LOAD=$(cut -d' ' -f1 /proc/loadavg)
say "disco livre (Docker em $DOCKER_ROOT): ${DISK_GB} GB  | disco livre (relatório): ${OUT_DISK_GB} GB"
say "RAM disponível: ${RAM_GB} GB | CPUs: ${CPUS} | carga (1 min): ${LOAD}"
say "containers em execução (não serão tocados): $(docker ps -q | wc -l)"
[ "$DISK_GB" -ge "$MIN_DISK_GB" ] || fail "menos de ${MIN_DISK_GB} GB livres para o Docker"
[ "$RAM_GB" -ge "$MIN_RAM_GB" ] || fail "menos de ${MIN_RAM_GB} GB de RAM disponível"
[ "$CPUS" -ge "$MIN_CPUS" ] || fail "menos de ${MIN_CPUS} CPUs"
awk -v l="$LOAD" -v c="$CPUS" 'BEGIN{exit !(l < c*0.7)}' || fail "carga alta (${LOAD} em ${CPUS} CPUs): tente mais tarde"
HAD_PG=0; docker image inspect "$PG_IMAGE" >/dev/null 2>&1 && HAD_PG=1
HAD_PY=0; docker image inspect "$PY_IMAGE" >/dev/null 2>&1 && HAD_PY=1
say "imagens base já presentes: postgres=$HAD_PG python=$HAD_PY (serão removidas no fim só se esta execução as baixar)"
say "espaço previsto: imagens ~0,6 GB + ambiente de teste ~2 GB (na camada do container, removida no fim) + relatório < 20 MB"
if [ $CHECK_ONLY -eq 1 ]; then say "Verificação concluída. Nada foi criado."; exit 0; fi

# ---------------------------------------------------------------- cleanup (only this run's resources)
cleanup() {
  set +e
  say "== Limpeza ($RUN_ID)"
  for c in $(docker ps -aq --filter "label=$LABEL"); do docker rm -f "$c" >/dev/null; done
  docker network inspect "$NET" >/dev/null 2>&1 && docker network rm "$NET" >/dev/null
  if [ $KEEP_IMAGES -eq 0 ]; then
    [ $HAD_PG -eq 0 ] && docker image rm "$PG_IMAGE" >/dev/null 2>&1
    [ $HAD_PY -eq 0 ] && docker image rm "$PY_IMAGE" >/dev/null 2>&1
  fi
  left=$(docker ps -aq --filter "label=$LABEL" | wc -l)
  say "recursos restantes desta execução: $left (esperado: 0)"
}
trap cleanup EXIT

# ---------------------------------------------------------------- run
mkdir -p "$OUT"
COMMIT="$(git -C "$ROOT" -c safe.directory="$ROOT" rev-parse HEAD 2>/dev/null || echo desconhecido)"
BRANCH="$(git -C "$ROOT" -c safe.directory="$ROOT" branch --show-current 2>/dev/null || echo desconhecido)"
DIRTY="$(git -C "$ROOT" -c safe.directory="$ROOT" status --porcelain 2>/dev/null | head -1)"
HOST_OS="$(. /etc/os-release && echo "$PRETTY_NAME") / kernel $(uname -r) / $CPUS CPUs / ${RAM_GB} GB livres"
DOCKER_V="$(docker --version)"

say "== Rede interna e PostgreSQL de teste"
docker network create --internal --label "$LABEL" "$NET" >/dev/null
docker run -d --rm --name "$RUN_ID-pg" --label "$LABEL" --network "$NET" --network-alias postgres \
  --tmpfs /var/lib/postgresql/data:rw,size=2g --cpus 2 --memory 2g --pids-limit 512 \
  --security-opt no-new-privileges \
  -e POSTGRES_USER=asc_test -e POSTGRES_PASSWORD=asc_test -e POSTGRES_DB=postgres \
  "$PG_IMAGE" postgres -c max_connections=200 -c fsync=off >/dev/null
for _ in $(seq 1 60); do docker exec "$RUN_ID-pg" pg_isready -U asc_test -q && break; sleep 1; done
docker exec "$RUN_ID-pg" pg_isready -U asc_test -q || fail "o PostgreSQL de teste não ficou pronto"
PG_V="$(docker exec "$RUN_ID-pg" postgres --version)"
say "$PG_V pronto (sem porta publicada no servidor)"

say "== Testes (container descartável, código somente leitura)"
docker create --name "$RUN_ID-runner" --label "$LABEL" \
  --cpus 4 --memory 6g --pids-limit 4096 --cpu-shares 256 --security-opt no-new-privileges \
  -v "$ROOT:/src:ro" -v "$OUT:/out" \
  -e HOMOLOG_COMMIT="$COMMIT" -e HOMOLOG_BRANCH="$BRANCH" -e HOMOLOG_DIRTY="$DIRTY" -e HOMOLOG_OS="$HOST_OS" \
  -e HOMOLOG_DOCKER="$DOCKER_V" -e HOMOLOG_POSTGRES="$PG_V" -e HOMOLOG_RUN_ID="$RUN_ID" \
  "$PY_IMAGE" python /src/infrastructure/homolog/run_in_container.py >/dev/null
docker network connect "$NET" "$RUN_ID-runner"   # the default bridge stays for pip/apt downloads only
set +e
docker start -a "$RUN_ID-runner"
CODE=$?
set -e
say ""
say "Relatório: $OUT/RELATORIO.md"
say "Código de saída: $CODE (0 = APROVADO, 1 = REPROVADO, 2 = PENDENTE)"
exit $CODE
