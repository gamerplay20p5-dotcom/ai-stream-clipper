#!/usr/bin/env bash
# Run in the dedicated detached checkout of the approved release.
set -euo pipefail
umask 077

if [[ $# != 1 || -z $1 ]] || git symbolic-ref -q HEAD >/dev/null ||
   [[ -n $(git status --porcelain --untracked-files=no) ]]; then
  echo 'Usage from a clean detached release checkout: bash scripts/build-unified-release.sh NEW_OUTPUT_DIRECTORY' >&2
  exit 2
fi

root=$(realpath -- "$(git rev-parse --show-toplevel)")
cd "$root"
revision=$(git rev-parse HEAD)
epoch=$(git show -s --format=%ct "$revision")
tag="unified-${revision:0:12}"
app="ai-stream-clipper:$tag"
vision="ai-stream-clipper-vision:$tag"
commercial="ai-stream-clipper-commercial:$tag"

out=$(realpath -m -- "$1")
case "$out/" in
  "$root/"*) echo 'The output directory must be outside the source checkout.' >&2; exit 2 ;;
esac
mkdir -- "$out"
context=$(mktemp -d)
trap 'rm -rf -- "$context"' EXIT
git archive "$revision" | tar -xf - -C "$context"

{
  printf 'revision=%s\ntag=%s\nplatform=linux/amd64\n' "$revision" "$tag"
  docker buildx version
  docker version --format '{{.Server.Version}}'
} > "$out/build-info.txt"

build() {
  local spec=$1 image=$2
  shift 2
  docker buildx build --platform linux/amd64 --provenance=false \
    --output type=docker,rewrite-timestamp=true --tag "$image" \
    --label "org.opencontainers.image.revision=$revision" \
    --build-arg "SOURCE_DATE_EPOCH=$epoch" --file "$context/infrastructure/docker/$spec" \
    "$@" "$context"
}

build app.Dockerfile "$app" --build-arg ASC_RELEASE_BUILD=1 \
  --build-arg DASHBOARD_IMAGE=node:20.18.0-bookworm-slim@sha256:d03fb171820643298e01a9fa30a5bb51b10abb7620c543e2a9755c69771fddc1 \
  --build-arg PYTHON_IMAGE=python:3.12.7-slim-bookworm@sha256:1c44018d7eb40488f29e7c6ad4991d3200507e14dca71b94fe61011815e98155
build vision-openvino.Dockerfile "$vision" --build-arg ASC_RELEASE_BUILD=1 \
  --build-arg OPENVINO_IMAGE=openvino/ubuntu24_runtime:2026.2.0@sha256:9286c2999e7873b5ded0b76236331964a43c21c157d6b7f7dc7daa4166211bc1
build commercial.Dockerfile "$commercial" \
  --build-arg COMMERCIAL_IMAGE=node:24.13.0-bookworm-slim@sha256:46feb5752989c05b8606e6323fbbc3db667d14ade1c24f5d0d44d9ca9909d607

docker run --rm --network none --entrypoint clipper "$app" --help >/dev/null
docker run --rm --network none --entrypoint /opt/asc/bin/python "$vision" \
  -c 'import cv2, openvino as ov, openvino_genai; assert "CPU" in ov.Core().available_devices'
docker run --rm --network none --entrypoint node "$commercial" --input-type=module \
  -e 'import { createApp } from "./server/app.js"; const app = createApp({ databasePath: ":memory:", paymentsOpen: false }); app.close()'

cd "$out"
for image in "$app" "$vision" "$commercial"; do
  archive="${image/:/_}.tar.gz"
  image_id=$(docker image inspect --format '{{.Id}}' "$image")
  printf '%s %s\n' "$image" "$image_id" >> image-ids.txt
  docker image save "$image" | gzip -n > "$archive"
  sha256sum "$archive" >> SHA256SUMS
done
printf 'Release artifacts verified locally in %s\n' "$out"
