#!/usr/bin/env bash
# Download AI models for one server (idempotent). Only touches the asc model directory/volume.
#
#   ./infrastructure/scripts/download-models.sh 206          # YAMNet (audio worker volume)
#   ./infrastructure/scripts/download-models.sh 210|201      # VLM + text model into ASC_MODELS_DIR (host dir)
#
# GPU hosts: the host directory must exist and belong to uid 10001 (the container user):
#   sudo mkdir -p /opt/asc/models && sudo chown 10001:10001 /opt/asc/models
set -euo pipefail
SERVER="${1:?usage: download-models.sh 206|210|201 [model ...]}"
shift || true
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$ROOT/.env.$SERVER"
TAG="$(grep -E '^ASC_IMAGE_TAG=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || true)"
TAG="${TAG:-0.2.0}"

case "$SERVER" in
  206)
    MODELS=("${@:-yamnet}")
    docker compose -p asc -f "$ROOT/infrastructure/compose/server-206.yml" --env-file "$ENV_FILE" \
      run --rm --no-deps --entrypoint python worker-audio -m clipper.ai.download "${MODELS[@]}"
    ;;
  210|201)
    DIR="$(grep -E '^ASC_MODELS_DIR=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || true)"
    DIR="${DIR:-/opt/asc/models}"
    [ -d "$DIR" ] || { echo "create $DIR first (owned by uid 10001)"; exit 1; }
    MODELS=("${@:-qwen3-vl-2b-int4 qwen3-1.7b-int4}")
    # shellcheck disable=SC2068
    docker run --rm --security-opt no-new-privileges:true -v "$DIR:/models" -e ASC_MODELS_DIR=/models \
      --entrypoint python3 "ai-stream-clipper-vision:$TAG" -m clipper.ai.download ${MODELS[@]}
    ;;
  *) echo "unknown server $SERVER"; exit 1 ;;
esac
