# AI Stream Clipper - vision worker for Intel Arc (OpenVINO).
#
# Built on the official OpenVINO runtime image, which ships the Intel GPU compute
# runtime. Use the SAME base image/tag that passed your GPU test on 201/210
# (the brief reports OpenVINO 2026.2.0 working inside Docker):
#
#   docker build -f infrastructure/docker/vision-openvino.Dockerfile \
#     --build-arg OPENVINO_IMAGE=openvino/ubuntu24_runtime:2026.2.0 \
#     -t ai-stream-clipper-vision:0.2.0 .
#
# NOTE: not built or run in CI (no Intel GPU there). Validate with
#   infrastructure/scripts/validate-gpu.sh
ARG OPENVINO_IMAGE=openvino/ubuntu24_runtime:2026.2.0
FROM ${OPENVINO_IMAGE}

USER root
ARG ASC_RELEASE_BUILD=0
ARG SOURCE_DATE_EPOCH
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    ASC_WORK_DIR=/data/work HF_HOME=/models/hf
RUN set -eu; \
    if [ "$ASC_RELEASE_BUILD" = 1 ]; then \
      printf '%s\n' \
        'deb https://snapshot.ubuntu.com/ubuntu/20261122T000000Z/ noble main universe' \
        'deb https://snapshot.ubuntu.com/ubuntu/20261122T000000Z/ noble-updates main universe' \
        'deb https://snapshot.ubuntu.com/ubuntu/20261122T000000Z/ noble-security main universe' > /tmp/asc-release.list; \
      set -- -o Dir::Etc::sourcelist=/tmp/asc-release.list -o Dir::Etc::sourceparts=- -o Acquire::Check-Valid-Until=false; \
    fi; \
    apt-get "$@" update \
 && apt-get "$@" install -y --no-install-recommends ffmpeg tini python3-venv \
 && rm -rf /var/lib/apt/lists/* /tmp/asc-release.list

# Isolated venv: the base image provides the Intel GPU compute runtime (Level Zero/OpenCL);
# the Python OpenVINO stack is pinned explicitly to the validated 2026.2.0 release.
RUN python3 -m venv /opt/asc
ENV PATH=/opt/asc/bin:$PATH
WORKDIR /app
COPY backend/pyproject.toml ./backend/
COPY backend/clipper ./backend/clipper
COPY infrastructure/docker/vision-linux-amd64.lock /tmp/asc-deps.lock
RUN if [ "$ASC_RELEASE_BUILD" = 1 ]; then \
      pip install --no-compile --only-binary=:all: --require-hashes -r /tmp/asc-deps.lock \
      && pip install --no-compile --no-index --no-deps --no-build-isolation "./backend[openvino]" \
      && pip check; \
    else \
      pip install --upgrade pip && pip install "./backend[openvino]" "huggingface_hub>=0.24"; \
    fi \
 && rm -rf /root/.cache /tmp/asc-deps.lock
ENV ASC_MODELS_DIR=/models

RUN (getent group 10001 || groupadd --gid 10001 asc) \
 && (id -u 10001 >/dev/null 2>&1 || useradd --uid 10001 --gid 10001 --home /app --shell /usr/sbin/nologin asc) \
 && mkdir -p /data/work /models && chown -R 10001:10001 /data /models
USER 10001
ENTRYPOINT ["/usr/bin/tini", "--", "clipper"]
CMD ["worker", "--roles", "vision"]
