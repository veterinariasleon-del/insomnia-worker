#!/usr/bin/env bash
# Arranque del worker: prepara el volumen, enlaza las cachés de HyperFrames al volumen (persisten entre despliegues),
# copia las herramientas genéricas del worker a /data/leyendas/herramientas y levanta la API.
set -euo pipefail
DATA_DIR="${DATA_DIR:-/data/leyendas}"; CACHE_DIR="${CACHE_DIR:-/data/cache}"
mkdir -p "$DATA_DIR" "$CACHE_DIR/hf" "$CACHE_DIR/hyperframes" "$CACHE_DIR/npx" "$DATA_DIR/_jobs"
# HyperFrames guarda chrome-headless-shell en ~/.cache/hyperframes y npx su caché en ~/.npm/_npx → al volumen
mkdir -p "$HOME/.cache" "$HOME/.npm"
if [ ! -L "$HOME/.cache/hyperframes" ]; then rm -rf "$HOME/.cache/hyperframes"; ln -s "$CACHE_DIR/hyperframes" "$HOME/.cache/hyperframes"; fi
if [ -d "$HOME/.npm/_npx" ] && [ ! -L "$HOME/.npm/_npx" ]; then cp -rn "$HOME/.npm/_npx/." "$CACHE_DIR/npx/" 2>/dev/null || true; rm -rf "$HOME/.npm/_npx"; fi
[ -L "$HOME/.npm/_npx" ] || ln -s "$CACHE_DIR/npx" "$HOME/.npm/_npx"
# si HyperFrames no logra descargar su Chrome, usa el Chromium del sistema (descomenta en Easypanel: HYPERFRAMES_BROWSER_PATH=/usr/bin/chromium)
bash /app/sync_herramientas.sh "$DATA_DIR" || echo "aviso: no se pudieron sincronizar herramientas (¿volumen vacío?)"
cd /app
# buzón de Drive en segundo plano (si no hay conexión a Drive configurada, se queda dormido sin estorbar)
python3 -m worker.buzon >> "$DATA_DIR/_jobs/buzon.log" 2>&1 &
exec python3 -m uvicorn worker.app:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 --timeout-keep-alive 120
