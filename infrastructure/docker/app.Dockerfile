# AI Stream Clipper - main image (API + dashboard + CPU workers).
# One image, many roles: the container command selects api / worker roles.
#
#   docker build -f infrastructure/docker/app.Dockerfile -t ai-stream-clipper:0.2.0 .

# ---------- dashboard build ----------
FROM node:20.18.0-bookworm-slim AS dashboard
WORKDIR /src
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

# ---------- runtime ----------
FROM python:3.12.7-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/hf \
    ASC_DASHBOARD_DIR=/app/dashboard \
    ASC_STORAGE_DIR=/data/storage \
    ASC_CAPTURE_DIR=/data/capture \
    ASC_WORK_DIR=/data/work \
    ASC_TEST_MEDIA_DIR=/data/test-media \
    ASC_MODELS_DIR=/models \
    ASC_CAPTION_FONT="Noto Sans"

# ffmpeg (libx264, libass), fonts for burned-in captions, tini as PID 1
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fonts-noto-core fontconfig tini ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f

RUN groupadd --gid 10001 asc && useradd --uid 10001 --gid asc --home /app --shell /usr/sbin/nologin asc

WORKDIR /app
COPY backend/pyproject.toml ./backend/
COPY backend/clipper ./backend/clipper
# plan limits for HTS courtesy plans: same file as the commercial service (single source of truth)
COPY commercial/catalog.json /app/catalog.json
ARG EXTRAS=capture,audio,vision
RUN pip install "./backend[${EXTRAS}]" && rm -rf /root/.cache

COPY --from=dashboard /src/dist /app/dashboard
# volume mount points must exist (owned by the container user) so new named volumes inherit it
RUN mkdir -p /data/work /data/storage /data/capture /data/test-media /models && chown -R asc:asc /data /models

USER asc
EXPOSE 8780 8781
ENTRYPOINT ["/usr/bin/tini", "--", "clipper"]
CMD ["api"]
