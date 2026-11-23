#!/usr/bin/env bash
# API 0.8.1 (trava do cadastro público). Prepara SEM parar nada: compila na 206 e carrega na 210.
#   na 206: bash api-0.8.1-preparar.sh 206   (precisa de ~/deploy-api/api-<TAG>.tgz = git archive HEAD backend dashboard infrastructure)
#   na 210: bash api-0.8.1-preparar.sh 210   (carrega a imagem e instala o Compose; não recria nada)
# VERSION é a versão do app (backend e painel); TAG é a tag da imagem (0.8.1-r2 = 2ª compilação da 0.8.1,
# com o painel também em 0.8.1). Só passa pelo asc-ctl.
set -euo pipefail
VERSION=0.8.1
TAG="${TAG:-0.8.1-r2}"
C="sudo -n /usr/local/sbin/asc-ctl"
R="$HOME/ai-stream-clipper"
D="$HOME/deploy-api"
mkdir -p "$D" "$HOME/images"
has_image() { $C images 2>/dev/null | grep -q "^ai-stream-clipper:$TAG[[:space:]]"; }
case "${1:-}" in
206)
  B="$D/antes-206-$(date +%Y%m%d-%H%M%S).tgz"; tar czf "$B" -C "$R" backend dashboard infrastructure; echo "cópia anterior: $B"
  tar xzf "$D/api-$TAG.tgz" -C "$R" backend dashboard infrastructure
  grep -q "__version__ = \"$VERSION\"" "$R/backend/clipper/__init__.py" || { echo "ERRO: backend $VERSION não instalado"; exit 1; }
  grep -q "\"version\": \"$VERSION\"" "$R/dashboard/package.json" || { echo "ERRO: painel $VERSION não instalado"; exit 1; }
  if has_image; then echo "imagem $TAG já existe"; else $C build app "$TAG" > "$D/build-app-$TAG.log" 2>&1 || { tail -40 "$D/build-app-$TAG.log"; exit 1; }; fi
  tail -2 "$D/build-app-$TAG.log" 2>/dev/null || true
  [ -f "$HOME/images/ai-stream-clipper_$TAG.tar.gz" ] || $C image-save app "$TAG"
  "$HOME/bin/asc-send" 210 "$HOME/images/ai-stream-clipper_$TAG.tar.gz"
  echo "OK: imagem $TAG compilada na 206 e enviada para a 210"
  ;;
210)
  B="$D/antes-210-$(date +%Y%m%d-%H%M%S).tgz"; tar czf "$B" -C "$R" infrastructure; echo "cópia anterior: $B"
  tar xzf "$D/api-$TAG.tgz" -C "$R" infrastructure
  grep -q "ASC_API_TAG:-$TAG}" "$R/infrastructure/compose/server-210.yml" || { echo "ERRO: Compose novo ausente"; exit 1; }
  if has_image; then echo "imagem $TAG já carregada"; else $C image-load "$HOME/images/ai-stream-clipper_$TAG.tar.gz"; fi
  has_image || { echo "ERRO: imagem $TAG ausente"; exit 1; }
  $C compose config --gpu | grep -q "policy: OK" && echo "Compose da 210 passa na política"
  echo "OK: imagem $TAG pronta na 210 (nada foi recriado)"
  ;;
*) echo "uso: $0 206|210"; exit 2 ;;
esac
