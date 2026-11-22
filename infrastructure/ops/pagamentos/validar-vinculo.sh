#!/usr/bin/env bash
# Na 210, como asc. Depois que o administrador acrescentou ASC_BILLING_SECRET ao /etc/asc/asc.env:
# recria SÓ asc-api e asc-commercial (que leem o segredo) e valida o vínculo assinado nos dois sentidos.
# Workers e bancos não são recriados. Nenhum valor secreto é impresso.
set -euo pipefail
C="sudo -n /usr/local/sbin/asc-ctl"
D="$HOME/deploy-pagamentos"; mkdir -p "$D"
ids() { $C compose ps --gpu | awk 'NR>1{print $1}' | sort | while read -r n; do printf '%s ' "$n"; $C exec "$n" -- sh -c 'cut -d" " -f22 /proc/1/stat' 2>/dev/null || echo "?"; done; }
$C env-show | grep -q '^ASC_BILLING_SECRET=\*\*\*$' || { echo "ERRO: ASC_BILLING_SECRET ainda não está no /etc/asc/asc.env"; exit 1; }
echo "ASC_BILLING_SECRET presente (valor não exibido)"
ids | grep -vE '^asc-(api|commercial) ' > "$D/link-antes.txt"
$C compose up --gpu api commercial 2>&1 | grep -v '^$'
for svc in asc-api asc-commercial; do
  for i in $(seq 1 60); do $C compose ps --gpu | grep -w "$svc" | grep -q "(healthy)" && break; [ "$svc" = asc-api ] && curl -sfk https://192.168.1.210:8780/healthz >/dev/null && break; sleep 3; done
done
curl -sk https://192.168.1.210:8780/healthz; echo
$C compose ps --gpu | awk 'NR>1{print "  "$1, $(NF-1), $NF}'
$C compose logs commercial --tail 3 | sed 's/^/  log: /'
ids | grep -vE '^asc-(api|commercial) ' > "$D/link-depois.txt"
diff -q "$D/link-antes.txt" "$D/link-depois.txt" >/dev/null && echo "WORKERS E BANCOS INTACTOS (mesmos processos)" || { echo "ATENÇÃO"; diff "$D/link-antes.txt" "$D/link-depois.txt"; }

echo "== comercial -> API (assinado, somente leitura: /internal/billing/state)"
$C exec asc-commercial -- node --input-type=module -e '
import { randomBytes } from "node:crypto"
import { httpStateReader, outboxSettings } from "./server/outbox.js"
const s = outboxSettings()
const refs = [{ provider: "stripe", subscription_ref: "sub_asc_link_check" }]
const items = await httpStateReader(s)(refs)
console.log(`  segredo certo: HTTP 200, ${items.length} item(ns) para a referência de teste`)
try { await httpStateReader({ ...s, secret: randomBytes(32).toString("hex") })(refs); console.log("  ERRO: segredo errado foi aceito") }
catch (e) { console.log(`  segredo errado: recusado (${e.code})`) }'

echo "== API -> comercial (assinado, somente leitura: /internal/billing/status)"
$C exec asc-api -- python3 -c "$(cat "$(dirname "$0")/api-para-comercial.py")"

echo "== público (deve continuar sem checkout e sem cadastro)"
curl -s -w '\n' -A 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131' https://getclipper.howto.software/api/billing/config
curl -s -w '\n' https://clipper.howto.software/api/auth/config
