#!/usr/bin/env bash
# Roll back or remove AI Stream Clipper on one server. Only the "asc" project is affected.
#
#   ./infrastructure/scripts/rollback.sh 210 --to 0.0.9   # redeploy a previous image tag
#   ./infrastructure/scripts/rollback.sh 206 --stop       # stop asc containers (data kept)
#   ./infrastructure/scripts/rollback.sh 210 --db 0001    # alembic downgrade (run BEFORE --to)
#
# Volumes (database, clips, capture buffer) are never deleted by this script.
set -euo pipefail
SERVER="${1:?usage: rollback.sh <server> --to TAG | --stop | --db REVISION}"
ACTION="${2:?missing action}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$ROOT/.env.$SERVER"
COMPOSE="$ROOT/infrastructure/compose/server-$SERVER.yml"
DC=(docker compose -p asc -f "$COMPOSE" --env-file "$ENV_FILE")

case "$ACTION" in
  --to)
    TAG="${3:?missing tag}"
    docker image inspect "ai-stream-clipper:$TAG" >/dev/null || { echo "image ai-stream-clipper:$TAG not present"; exit 1; }
    ASC_IMAGE_TAG="$TAG" "${DC[@]}" up -d --no-build
    "${DC[@]}" ps
    ;;
  --stop)
    "${DC[@]}" --profile gpu stop
    ;;
  --db)
    REV="${3:?missing alembic revision}"
    docker exec asc-api clipper migrate --downgrade "$REV"
    ;;
  *) echo "unknown action $ACTION"; exit 1 ;;
esac
