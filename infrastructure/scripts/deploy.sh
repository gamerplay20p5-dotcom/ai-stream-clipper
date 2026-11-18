#!/usr/bin/env bash
# Deploy (or upgrade) AI Stream Clipper on one server. Only touches the "asc" compose
# project: no prune, no changes to other containers, networks, volumes or daemon config.
#
#   ./infrastructure/scripts/deploy.sh 210 [--gpu]
#   ./infrastructure/scripts/deploy.sh 206
#   ./infrastructure/scripts/deploy.sh 201
#
# Requires .env.<server> next to the repository root (see .env.example).
set -euo pipefail
SERVER="${1:?usage: deploy.sh 210|206|201 [--gpu]}"
shift || true
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$ROOT/.env.$SERVER"
COMPOSE="$ROOT/infrastructure/compose/server-$SERVER.yml"
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE (copy .env.example)"; exit 1; }
[ -f "$COMPOSE" ] || { echo "unknown server $SERVER"; exit 1; }

PROFILE=()
if [ "${1:-}" = "--gpu" ] || [ "$SERVER" = 201 ]; then PROFILE=(--profile gpu); fi

"$ROOT/infrastructure/scripts/preflight.sh" "$SERVER"

# shellcheck disable=SC1090
TAG="$(grep -E '^ASC_IMAGE_TAG=' "$ENV_FILE" | cut -d= -f2 || true)"
TAG="${TAG:-0.2.0}"
echo "== deploying asc $TAG on $SERVER =="

# remember the running tag for rollback
mkdir -p "$ROOT/.deploy"
docker inspect --format '{{.Config.Image}}' $(docker ps -q --filter label=com.docker.compose.project=asc) 2>/dev/null \
  | sort -u > "$ROOT/.deploy/previous-images-$SERVER.txt" || true

docker compose -p asc -f "$COMPOSE" --env-file "$ENV_FILE" "${PROFILE[@]}" build
docker compose -p asc -f "$COMPOSE" --env-file "$ENV_FILE" "${PROFILE[@]}" up -d --remove-orphans
docker compose -p asc -f "$COMPOSE" --env-file "$ENV_FILE" "${PROFILE[@]}" ps

echo "== waiting for health =="
for i in $(seq 1 30); do
  unhealthy=$(docker ps --filter label=com.docker.compose.project=asc --filter health=unhealthy -q | wc -l)
  starting=$(docker ps --filter label=com.docker.compose.project=asc --filter health=starting -q | wc -l)
  [ "$unhealthy" = 0 ] && [ "$starting" = 0 ] && { echo "all asc containers healthy"; exit 0; }
  sleep 5
done
echo "some containers are not healthy yet:"
docker ps --filter label=com.docker.compose.project=asc --format '{{.Names}}\t{{.Status}}'
exit 1
