# AI Stream Clipper - commercial site (landing + billing service), Node.js 24.
# Built on the server with `sudo asc-ctl build commercial TAG` (context: repository root).
#
#   docker build -f infrastructure/docker/commercial.Dockerfile -t ai-stream-clipper-commercial:1.0.0 .

# ---------- build: prerendered landing (dist/) ----------
FROM node:24.13.0-bookworm-slim AS build
WORKDIR /src
COPY commercial/package.json commercial/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY commercial/ ./
RUN npm run build && npm prune --omit=dev --no-audit --no-fund

# ---------- runtime ----------
FROM node:24.13.0-bookworm-slim AS runtime
ENV NODE_ENV=production \
    COMMERCIAL_HOST=0.0.0.0 \
    COMMERCIAL_PORT=8790 \
    COMMERCIAL_DB=/data/commercial.sqlite \
    NODE_NO_WARNINGS=1
WORKDIR /app
COPY --from=build /src/package.json /src/catalog.json ./
COPY --from=build /src/node_modules ./node_modules
COPY --from=build /src/server ./server
COPY --from=build /src/scripts ./scripts
COPY --from=build /src/dist ./dist
# the SQLite database lives in a named volume mounted at /data (owned by the unprivileged node user)
RUN mkdir -p /data && chown node:node /data && rm -f server/*.test.js server/test-helpers.js
USER node
EXPOSE 8790
CMD ["node", "server/index.js"]
