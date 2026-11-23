#!/usr/bin/env bash
# API 0.8.1 (trava do cadastro público). Prepara SEM parar nada: compila na 206 e carrega na 210.
#   na 206: bash api-0.8.1-preparar.sh 206   (precisa de ~/deploy-api/api-0.8.1.tgz = git archive HEAD backend infrastructure)
#   na 210: bash api-0.8.1-preparar.sh 210   (carrega a imagem e instala o Compose; não recria nada)
# Só passa pelo asc-ctl. Na 206 a imagem é compilada a partir de backend/ novo; o restante do repositório fica.
set -euo pipefail
NEW=0.8.1
C="sudo -n /usr/local/sbin/asc-ctl"
R="$HOME/ai-stream-clipper"
D="$HOME/deploy-api"
mkdir -p "$D" "$HOME/images"
has_image() { $C images 2>/dev/null | grep -q "ai-stream-clipper *$NEW\b\|ai-stream-clipper:$NEW"; }
case "${1:-}" in
206)
  grep -q '__version__ = "0.8.0"\|__version__ = "0.8.1"' "$R/backend/clipper/__init__.py" || { echo "ERRO: repositório da 206 fora da 0.8.x"; exit 1; }
  B="$D/antes-206-$(date +%Y%m%d-%H%M%S).tgz"; tar czf "$B" -C "$R" backend infrastructure; echo "cópia anterior: $B"
  tar xzf "$D/api-$NEW.tgz" -C "$R" backend infrastructure
  grep -q "__version__ = \"$NEW\"" "$R/backend/clipper/__init__.py" || { echo "ERRO: código $NEW não instalado"; exit 1; }
  if has_image; then echo "imagem $NEW já existe"; else $C build app "$NEW" > "$D/build-app-$NEW.log" 2>&1 || { tail -40 "$D/build-app-$NEW.log"; exit 1; }; fi
  tail -2 "$D/build-app-$NEW.log" 2>/dev/null || true
  [ -f "$HOME/images/ai-stream-clipper_$NEW.tar.gz" ] || $C image-save app "$NEW"
  "$HOME/bin/asc-send" 210 "$HOME/images/ai-stream-clipper_$NEW.tar.gz"
  echo "OK: imagem $NEW compilada na 206 e enviada para a 210"
  ;;
210)
  B="$D/antes-210-$(date +%Y%m%d-%H%M%S).tgz"; tar czf "$B" -C "$R" infrastructure; echo "cópia anterior: $B"
  tar xzf "$D/api-$NEW.tgz" -C "$R" infrastructure
  grep -q 'ASC_API_TAG:-0.8.1' "$R/infrastructure/compose/server-210.yml" || { echo "ERRO: Compose novo ausente"; exit 1; }
  if has_image; then echo "imagem $NEW já carregada"; else $C image-load "$HOME/images/ai-stream-clipper_$NEW.tar.gz"; fi
  has_image || { echo "ERRO: imagem $NEW ausente"; exit 1; }
  $C compose config --gpu | grep -q "policy: OK" && echo "Compose da 210 passa na política"
  echo "OK: imagem $NEW pronta na 210 (nada foi recriado)"
  ;;
*) echo "uso: $0 206|210"; exit 2 ;;
esac
