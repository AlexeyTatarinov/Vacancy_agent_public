#!/usr/bin/env bash
# Обновляет копию программы чтения страниц (web_reader/) из репозитория web_reader.
# Использование: scripts/sync_web_reader.sh /путь/к/клону/web_reader
set -euo pipefail
SRC="${1:?укажите путь к клону репозитория web_reader}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/web_reader"
rm -rf "$DEST"
cp -r "$SRC/web_reader" "$DEST"
find "$DEST" -name __pycache__ -type d -prune -exec rm -rf {} +
[ -f "$SRC/config/roles.json" ] && cp "$SRC/config/roles.json" "$(dirname "$DEST")/config/roles.json"
echo "web_reader обновлён из $SRC ($(git -C "$SRC" rev-parse --short HEAD))"
