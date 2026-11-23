#!/usr/bin/env bash
# Recria SÓ o asc-commercial e prova que os demais contêineres mantiveram os mesmos processos.
set -euo pipefail
C="sudo -n /usr/local/sbin/asc-ctl"; D="$HOME/deploy-pagamentos"
ids() { $C compose ps --gpu | awk 'NR>1{print $1}' | sort | while read -r n; do printf '%s ' "$n"; $C exec "$n" -- sh -c 'cut -d" " -f22 /proc/1/stat' 2>/dev/null || echo "?"; done; }
ids | grep -v '^asc-commercial ' > "$D/smtp-antes.txt"
$C compose up --gpu commercial 2>&1 | grep -v '^$'
for i in $(seq 1 40); do $C compose ps --gpu | grep -w asc-commercial | grep -q "(healthy)" && break; sleep 3; done
$C compose ps --gpu | awk 'NR>1{print "  "$1, $(NF-1), $NF}'
$C compose images --gpu 2>/dev/null | grep -i commercial | awk '{print "  imagem: "$2":"$3}'
$C compose logs commercial --tail 4 | sed 's/^/  log: /'
ids | grep -v '^asc-commercial ' > "$D/smtp-depois.txt"
diff -q "$D/smtp-antes.txt" "$D/smtp-depois.txt" >/dev/null && echo "API, WORKERS E BANCOS INTACTOS (mesmos processos)" || { echo "ATENÇÃO"; diff "$D/smtp-antes.txt" "$D/smtp-depois.txt"; }
curl -s -w '\n' -A 'Mozilla/5.0 Chrome/131' https://getclipper.howto.software/api/billing/config
