#!/usr/bin/env bash
# Read-only preflight for a target server. Changes NOTHING.
#   ./infrastructure/scripts/preflight.sh 210|206|201
set -euo pipefail
SERVER="${1:?usage: preflight.sh 210|206|201}"
ok()   { printf '  [ok]   %s\n' "$*"; }
warn() { printf '  [warn] %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; FAILED=1; }
FAILED=0

echo "== AI Stream Clipper preflight for server $SERVER ($(hostname)) =="

echo "-- docker"
docker version --format '  engine {{.Server.Version}}' || fail "docker not reachable"
docker compose version >/dev/null 2>&1 && ok "docker compose plugin present" || fail "docker compose plugin missing"

echo "-- existing containers (left untouched)"
docker ps --format '  {{.Names}}\t{{.Status}}' | head -50
if docker ps -a --format '{{.Names}}' | grep -q '^asc-'; then warn "asc-* containers already exist (re-deploy/upgrade)"; fi

echo "-- ports"
check_port() {
  if ss -ltnH "( sport = :$1 )" | grep -q .; then
    if docker ps --format '{{.Names}} {{.Ports}}' | grep -q "asc-.*:$1->"; then ok "port $1 used by asc (upgrade)"; else fail "port $1 already in use"; fi
  else ok "port $1 free"; fi
}
case "$SERVER" in
  210) check_port 8780 ;;
  206) check_port 8781 ;;
esac
for p in 80 443 8088 9000; do ss -ltnH "( sport = :$p )" | grep -q . && ok "port $p in use by existing services (not touched)"; done || true

echo "-- disk"
df -h / /var/lib/docker 2>/dev/null | sed 's/^/  /'
avail=$(df --output=avail -BG /var/lib/docker 2>/dev/null | tail -1 | tr -dc 0-9)
[ "${avail:-0}" -ge 100 ] && ok "≥100 GiB free for docker" || warn "less than 100 GiB free for docker"

echo "-- resources"
echo "  cpus: $(nproc)  mem: $(free -g | awk '/Mem:/{print $2" GiB total, "$7" GiB available"}')"
uptime | sed 's/^/  /'

if [ "$SERVER" = 201 ] || [ "$SERVER" = 210 ]; then
  echo "-- gpu"
  if [ -e /dev/dri/renderD128 ]; then ok "/dev/dri/renderD128 present"; else fail "/dev/dri/renderD128 missing"; fi
  gid=$(getent group render | cut -d: -f3 || true)
  [ -n "$gid" ] && ok "render group gid=$gid (set ASC_RENDER_GID=$gid)" || warn "render group not found"
  lsmod 2>/dev/null | grep -E '^(xe|i915) ' | sed 's/^/  driver: /' || true
fi

echo "-- connectivity"
if [ "$SERVER" != 210 ]; then
  api="${ASC_API_URL:-http://192.168.1.210:8780}"
  if curl -fsS -m 5 "$api/healthz" >/dev/null 2>&1; then ok "API reachable at $api"; else warn "API not reachable at $api (deploy 210 first)"; fi
fi
if [ "$SERVER" = 210 ]; then
  curl -fsS -m 5 http://192.168.1.206:8781/healthz >/dev/null 2>&1 && ok "segment server on 206 reachable" || warn "segment server on 206 not reachable yet"
fi

[ "$FAILED" = 0 ] && echo "== preflight passed ==" || { echo "== preflight FAILED =="; exit 1; }
