#!/usr/bin/env bash
# Na 210, como asc: instala o código e SÓ compila a imagem comercial TAG. O contêiner em execução não é
# recriado; a nova imagem passa a valer no próximo `asc-ctl compose up --gpu commercial`.
# Uso: bash compilar-comercial.sh 1.0.3
set -euo pipefail
TAG="$1"; C="sudo -n /usr/local/sbin/asc-ctl"; R="$HOME/ai-stream-clipper"; D="$HOME/deploy-pagamentos"
B="$D/antes-$(date +%Y%m%d-%H%M%S).tgz"; tar czf "$B" -C "$R" commercial infrastructure; echo "cópia anterior: $B"
tar xzf "$D/branch-pay.tgz" -C "$R" commercial infrastructure
grep -q "ASC_COMMERCIAL_TAG:-$TAG" "$R/infrastructure/compose/server-210.yml" || { echo "ERRO: compose não usa $TAG"; exit 1; }
antes=$($C exec asc-commercial -- sh -c 'cut -d" " -f22 /proc/1/stat')
$C build commercial "$TAG" > "$D/build-$TAG.log" 2>&1 || { tail -30 "$D/build-$TAG.log"; exit 1; }
grep -E "Legal pages" "$D/build-$TAG.log" | sed 's/^/  build: /'
echo "imagem $TAG compilada"
depois=$($C exec asc-commercial -- sh -c 'cut -d" " -f22 /proc/1/stat')
[ "$antes" = "$depois" ] && echo "asc-commercial em execução intocado (mesmo processo)" || echo "ATENÇÃO: o processo do asc-commercial mudou"
$C compose ps --gpu | awk 'NR>1{print "  "$1, $(NF-1), $NF}'
$C compose images --gpu 2>/dev/null | grep -i commercial | sed 's/^/  em uso: /'
