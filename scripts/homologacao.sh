#!/usr/bin/env bash
# Local homologation of PRs #23, #24 and #25 WITHOUT GitHub Actions (Ubuntu/Linux, macOS, Git Bash).
#
#   ./scripts/homologacao.sh               full run: throwaway PostgreSQL in Docker + every suite + report
#   ./scripts/homologacao.sh --keep        keep the PostgreSQL container running at the end (debugging)
#   ./scripts/homologacao.sh --no-browser  skip Playwright (e.g. a server without browser dependencies)
#   ./scripts/homologacao.sh --down        only stop and remove the homologation container
#   HOMOLOG_PYTHON=/path/python ./scripts/homologacao.sh   reuse an existing Python environment
#
# Safety: the database is a disposable container (project "asc-homolog", 127.0.0.1:55432, fake credentials, data in
# memory). Nothing else on the machine is touched: no global Docker cleanup, no other project, no real server, never
# 192.168.1.212, no real keys, no payment, no publication. Without Docker the PostgreSQL steps are NOT RUN and the
# verdict is PENDENTE (never approved).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="asc-homolog"
COMPOSE=(docker compose -p "$PROJECT" -f "$ROOT/infrastructure/compose/test-postgres.yml")
PG_URL="postgresql+psycopg://asc_test:asc_test@127.0.0.1:55432/postgres"
KEEP=0; BROWSER=1
for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=1 ;;
    --no-browser) BROWSER=0 ;;
    --down) "${COMPOSE[@]}" down -v --remove-orphans; exit $? ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "opção desconhecida: $arg"; exit 64 ;;
  esac
done

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN="$ROOT/reports/homologacao/$STAMP"
mkdir -p "$RUN"
: > "$RUN/steps.jsonl"
PY="${PYTHON:-python3}"; command -v "$PY" >/dev/null || PY=python
VENV="$ROOT/.homolog/venv"
[ -x "$VENV/bin/python" ] && VPY="$VENV/bin/python" || VPY="$VENV/Scripts/python.exe"
[ -n "${HOMOLOG_PYTHON:-}" ] && VPY="$HOMOLOG_PYTHON"  # reuse an environment that already has the backend dependencies

record() {  # step status seconds command [reason] [required]
  "$PY" - "$RUN/steps.jsonl" "$1" "$2" "$3" "$4" "${5:-}" "${6:-1}" <<'PY'
import json, sys
path, step, status, secs, cmd, reason, required = sys.argv[1:8]
with open(path, "a", encoding="utf-8") as f:
    f.write(json.dumps({"step": step, "status": status, "seconds": float(secs), "command": cmd,
                        "reason": reason, "required": required == "1"}) + "\n")
PY
}
step() {  # name cwd command...   (stdout+stderr in <name>.log)
  local name="$1" cwd="$2"; shift 2
  echo "==> $name"
  local start=$SECONDS
  (cd "$cwd" && "$@") >"$RUN/$name.log" 2>&1
  local code=$?
  if [ $code -eq 0 ]; then record "$name" passed $((SECONDS-start)) "$*"; echo "    ok"
  else record "$name" failed $((SECONDS-start)) "$*"; echo "    FALHOU (veja $RUN/$name.log)"; fi
  return 0
}
skip() { record "$1" not_run 0 "$2" "$3"; echo "==> $1: NÃO EXECUTADO ($3)"; }

# ---------------------------------------------------------------- prerequisites and environment
HAVE_DOCKER=0
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then HAVE_DOCKER=1; fi
for tool in git node npm; do command -v "$tool" >/dev/null || { echo "pré-requisito ausente: $tool"; exit 69; }; done
"$PY" - "$RUN/environment.json" "$ROOT" "$HAVE_DOCKER" <<'PY'
import json, platform, subprocess, sys, datetime
path, root, docker = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
def out(*cmd):
    try:
        return subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""
env = {"commit": out("git", "rev-parse", "HEAD"), "branch": out("git", "branch", "--show-current"),
       "dirty": bool(out("git", "status", "--porcelain")), "os": f"{platform.system()} {platform.release()} ({platform.platform()})",
       "python": platform.python_version(), "node": out("node", "--version"),
       "started": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
if docker:
    env["docker"], env["compose"] = out("docker", "--version"), out("docker", "compose", "version")
json.dump(env, open(path, "w", encoding="utf-8"), indent=2)
PY

cleanup() {
  if [ $HAVE_DOCKER -eq 1 ] && [ $KEEP -eq 0 ]; then "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

# ---------------------------------------------------------------- 1. secrets
step secrets "$ROOT" bash .github/scripts/check-secrets.sh

# ---------------------------------------------------------------- 2. Python environment (isolated venv)
if [ -z "${HOMOLOG_PYTHON:-}" ] && [ ! -x "$VPY" ]; then "$PY" -m venv "$ROOT/.homolog/venv"; [ -x "$VENV/bin/python" ] && VPY="$VENV/bin/python" || VPY="$VENV/Scripts/python.exe"; fi
step python-deps "$ROOT/backend" "$VPY" -m pip install --quiet --upgrade pip -e ".[dev,audio,vision]"

# ---------------------------------------------------------------- 3. throwaway PostgreSQL
if [ $HAVE_DOCKER -eq 1 ]; then
  step postgres-up "$ROOT" "${COMPOSE[@]}" up -d --wait
  PGV="$(docker exec asc-test-postgres postgres --version 2>/dev/null)"
  "$PY" -c 'import json,sys; p=sys.argv[1]; e=json.load(open(p)); e["postgres"]=sys.argv[2]; json.dump(e,open(p,"w"),indent=2)' "$RUN/environment.json" "$PGV"
fi

# ---------------------------------------------------------------- 4. backend
step backend-sqlite "$ROOT/backend" "$VPY" -m pytest -q -m "not models and not postgres" -p no:cacheprovider \
  --junitxml="$RUN/backend-sqlite.xml"
if [ $HAVE_DOCKER -eq 1 ]; then
  step backend-postgres-suite "$ROOT/backend" env ASC_TEST_SUITE_DB=postgres ASC_TEST_PG_URL="$PG_URL" \
    "$VPY" -m pytest -q -m "not models and not postgres" -p no:cacheprovider --junitxml="$RUN/backend-postgres-suite.xml"
  step postgres-concurrency-migrations "$ROOT/backend" env ASC_REQUIRE_PG=1 ASC_TEST_PG_URL="$PG_URL" \
    "$VPY" -m pytest -q -m postgres -p no:cacheprovider --junitxml="$RUN/postgres-concurrency-migrations.xml" \
    ../tests/test_postgres_shared_lives.py ../tests/test_postgres_customer_publishing.py ../tests/test_postgres_migrations.py
else
  skip backend-postgres-suite "ASC_TEST_SUITE_DB=postgres pytest" "Docker indisponível nesta máquina"
  skip postgres-concurrency-migrations "ASC_REQUIRE_PG=1 pytest -m postgres" "Docker indisponível nesta máquina"
fi

# ---------------------------------------------------------------- 5. commercial service and landing
step commercial-install "$ROOT/commercial" npm ci --no-audit --no-fund
step commercial-build "$ROOT/commercial" npm run build
step commercial-tests "$ROOT/commercial" npm test
step commercial-audit "$ROOT/commercial" npm audit --audit-level=high
if [ $BROWSER -eq 1 ]; then
  step commercial-playwright "$ROOT/commercial" npx playwright test --reporter=line
else
  skip commercial-playwright "npx playwright test" "--no-browser"
fi

# ---------------------------------------------------------------- 6. dashboard
step dashboard-install "$ROOT/dashboard" npm ci --no-audit --no-fund
step dashboard-typecheck "$ROOT/dashboard" npm run typecheck
step dashboard-i18n "$ROOT/dashboard" npm run check:i18n
step dashboard-build "$ROOT/dashboard" npm run build

# ---------------------------------------------------------------- report
"$PY" "$ROOT/scripts/homologacao_report.py" "$RUN"
exit $?
