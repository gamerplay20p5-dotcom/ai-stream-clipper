#!/usr/bin/env bash
# Print fresh random secrets for .env files. Use the SAME ASC_INTERNAL_SECRET on 201, 206 and 210.
set -euo pipefail
rand() { openssl rand -hex "${1:-32}"; }
cat <<EOF
ASC_INTERNAL_SECRET=$(rand 32)
ASC_JWT_SECRET=$(rand 32)
ASC_DB_PASSWORD=$(rand 24)
ASC_METRICS_TOKEN=$(rand 16)
EOF
