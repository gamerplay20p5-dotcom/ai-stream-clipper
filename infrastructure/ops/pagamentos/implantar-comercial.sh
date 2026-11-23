#!/usr/bin/env bash
# Na 210, como asc: instala o código (~/deploy-pagamentos/branch-pay.tgz = git archive HEAD commercial infrastructure),
# compila a imagem comercial TAG e recria só o asc-commercial, provando que os outros contêineres não reiniciaram.
# Uso: bash implantar-comercial.sh 1.0.3
set -euo pipefail
TAG="$1"
C="sudo -n /usr/local/sbin/asc-ctl"
R="$HOME/ai-stream-clipper"; D="$HOME/deploy-pagamentos"
ids() { $C compose ps --gpu | awk 'NR>1{print $1}' | sort | while read -r n; do printf '%s ' "$n"; $C exec "$n" -- sh -c 'cut -d" " -f22 /proc/1/stat' 2>/dev/null || echo "?"; done; }
B="$D/antes-$(date +%Y%m%d-%H%M%S).tgz"; tar czf "$B" -C "$R" commercial infrastructure; echo "cópia anterior: $B"
tar xzf "$D/branch-pay.tgz" -C "$R" commercial infrastructure
grep -q "ASC_COMMERCIAL_TAG:-$TAG" "$R/infrastructure/compose/server-210.yml" || { echo "ERRO: compose não usa $TAG"; exit 1; }
ids | grep -v '^asc-commercial' > "$D/inicio-antes.txt"
$C build commercial "$TAG" > "$D/build-$TAG.log" 2>&1 || { tail -30 "$D/build-$TAG.log"; exit 1; }
echo "imagem $TAG compilada"
$C compose up --gpu commercial 2>&1 | grep -v '^$'
for i in $(seq 1 30); do $C compose ps --gpu | grep -w asc-commercial | grep -q "(healthy)" && break; sleep 3; done
$C compose ps --gpu | awk 'NR>1{print "  "$1, $(NF-1), $NF}'
$C compose logs commercial --tail 3 | sed 's/^/  log: /'
ids | grep -v '^asc-commercial' > "$D/inicio-depois.txt"
diff -q "$D/inicio-antes.txt" "$D/inicio-depois.txt" >/dev/null && echo "OUTROS CONTÊINERES INTACTOS (mesmos processos)" || { echo "ATENÇÃO"; diff "$D/inicio-antes.txt" "$D/inicio-depois.txt"; }
