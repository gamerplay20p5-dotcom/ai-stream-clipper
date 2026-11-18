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
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    ASC_WORK_DIR=/data/work HF_HOME=/models/hf
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tini python3-venv \
 && rm -rf /var/lib/apt/lists/*

# Isolated venv: the base image provides the Intel GPU compute runtime (Level Zero/OpenCL);
# the Python OpenVINO stack is pinned explicitly to the validated 2026.2.0 release.
RUN python3 -m venv /opt/asc
ENV PATH=/opt/asc/bin:$PATH
WORKDIR /app
COPY backend/pyproject.toml ./backend/
COPY backend/clipper ./backend/clipper
RUN pip install --upgrade pip \
 && pip install "./backend[vision]" "openvino==2026.2.0" "openvino-genai==2026.2.0.0" \
        "openvino-tokenizers==2026.2.0.0" "transformers>=4.57,<5" "huggingface_hub>=0.24" "pillow>=10" \
 && rm -rf /root/.cache
ENV ASC_MODELS_DIR=/models

RUN (getent group 10001 || groupadd --gid 10001 asc) \
 && (id -u 10001 >/dev/null 2>&1 || useradd --uid 10001 --gid 10001 --home /app --shell /usr/sbin/nologin asc) \
 && mkdir -p /data/work /models && chown -R 10001:10001 /data /models
USER 10001
ENTRYPOINT ["/usr/bin/tini", "--", "clipper"]
CMD ["worker", "--roles", "vision"]
