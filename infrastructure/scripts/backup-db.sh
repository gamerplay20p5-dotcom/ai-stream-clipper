#!/usr/bin/env bash
# Logical backup of the asc PostgreSQL (server 210). Output: ./backups/asc-YYYYmmdd-HHMM.sql.gz
set -euo pipefail
OUT="${1:-./backups}"
mkdir -p "$OUT"
FILE="$OUT/asc-$(date +%Y%m%d-%H%M).sql.gz"
docker exec asc-postgres pg_dump -U asc -d asc --no-owner | gzip > "$FILE"
echo "backup written to $FILE"
# restore: gunzip -c FILE | docker exec -i asc-postgres psql -U asc -d asc
