#!/usr/bin/env bash
# Recria SÓ asc-api (0.8.1) e asc-commercial; prova que workers e bancos mantiveram os mesmos processos.
set -euo pipefail
C="sudo -n /usr/local/sbin/asc-ctl"; D="$HOME/deploy-api"
python3 "$HOME/deploy-pagamentos/env-compatibilidade.py"
$C env-show | grep -E "^(ASC_SIGNUP_ALLOWED_EMAILS|ASC_SIGNUP_OPEN|COMMERCIAL_PAYMENTS_OPEN)=" | sed -E 's/=([^@]{3})[^@]*@/=\1…@/'
ids() { $C compose ps --gpu | awk 'NR>1{print $1}' | sort | while read -r n; do printf '%s ' "$n"; $C exec "$n" -- sh -c 'cut -d" " -f22 /proc/1/stat' 2>/dev/null || echo "?"; done; }
ids | grep -vE '^asc-(api|commercial) ' > "$D/rec-antes.txt"
$C compose up --gpu api commercial 2>&1 | grep -v '^$'
for i in $(seq 1 60); do curl -sfk https://192.168.1.210:8780/healthz >/dev/null && break; sleep 2; done
for i in $(seq 1 40); do $C compose ps --gpu | grep -w asc-commercial | grep -q "(healthy)" && break; sleep 3; done
echo "healthz: $(curl -sk https://192.168.1.210:8780/healthz)"
$C compose ps --gpu | awk 'NR>1{print "  "$1, $(NF-1), $NF}'
$C compose images --gpu 2>/dev/null | awk 'NR>1{print "  imagem "$1": "$2":"$3}'
ids | grep -vE '^asc-(api|commercial) ' > "$D/rec-depois.txt"
diff -q "$D/rec-antes.txt" "$D/rec-depois.txt" >/dev/null && echo "WORKERS E BANCOS INTACTOS (mesmos processos)" || { echo "ATENÇÃO"; diff "$D/rec-antes.txt" "$D/rec-depois.txt"; }
echo "painel /api/auth/config: $(curl -s https://clipper.howto.software/api/auth/config)"
echo "site /api/billing/config: $(curl -s -A 'Mozilla/5.0 Chrome/131' https://getclipper.howto.software/api/billing/config)"
$C exec asc-commercial -- node -e "console.log('remetente comercial: ' + process.env.SMTP_FROM)"
$C exec asc-api -- python3 -c "from clipper.config import get_settings as g; s=g(); print('API: signup_open =', s.signup_open, '| autorizados:', len([e for e in s.signup_allowed_emails.split(',') if e.strip()]))"
