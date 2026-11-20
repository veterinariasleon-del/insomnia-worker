#!/usr/bin/env bash
# Copia las herramientas genéricas del worker (worker/herramientas_extra/*.py y *.json) a <DATA_DIR>/herramientas/.
# El volumen sigue siendo la fuente de verdad de los scripts originales del proyecto (build_video.py, oido.py, etc.);
# aquí solo se añaden/actualizan los genéricos nuevos (mezcla_json.py, gen_imagenes.py, render_hf.py, ...).
# Nunca borra nada; si un archivo del volumen es más nuevo que el del worker, lo respeta y avisa.
set -euo pipefail
DATA_DIR="${1:-${DATA_DIR:-/data/leyendas}}"
SRC="$(cd "$(dirname "$0")" && pwd)/worker/herramientas_extra"
DST="$DATA_DIR/herramientas"
mkdir -p "$DST"
for f in "$SRC"/*.py "$SRC"/*.json; do
  [ -e "$f" ] || continue
  b="$(basename "$f")"
  if [ -e "$DST/$b" ] && [ "$DST/$b" -nt "$f" ] && ! cmp -s "$f" "$DST/$b"; then
    echo "respetado (más nuevo en el volumen): herramientas/$b"
  else
    cp -p "$f" "$DST/$b"
  fi
done
echo "herramientas sincronizadas en $DST"
