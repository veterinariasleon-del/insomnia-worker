"""Punto de entrada de cada trabajo: python -m worker.run_etapa <etapa> <slug> <params.json> <job_id>
Ejecuta la etapa y escribe el resultado en _jobs/<id>.json. Cualquier excepción queda como error legible."""
import sys, json, traceback, importlib, os
from . import jobs, config as C
from .util import leer_json, registro, estado_produccion

MODULOS = {
    "guion": ("worker.etapas.guion", "guion"), "auditoria": ("worker.etapas.guion", "auditoria"),
    "freno_creditos": ("worker.etapas.audio", "freno_creditos"), "voz": ("worker.etapas.audio", "voz"),
    "sfx": ("worker.etapas.audio", "sfx"), "partitura": ("worker.etapas.audio", "partitura"), "mezcla": ("worker.etapas.audio", "mezcla"),
    "planos": ("worker.etapas.imagen", "planos"), "imagenes": ("worker.etapas.imagen", "imagenes"),
    "render": ("worker.etapas.video", "render"), "miniatura": ("worker.etapas.video", "miniatura"), "shorts": ("worker.etapas.video", "shorts"),
    "textos": ("worker.etapas.textos", "textos"), "publicar": ("worker.etapas.publicar", "publicar"),
    "estado_cola": ("worker.etapas.cola", "estado_cola"), "reposicion": ("worker.etapas.cola", "reposicion"),
    "reporte_semanal": ("worker.etapas.reporte", "reporte_semanal"), "prueba_corta": ("worker.etapas.prueba", "prueba_corta"),
    "ping": ("worker.etapas.cola", "ping"), "lote": ("worker.etapas.lote", "lote"),
}

def main():
    etapa, slug, params_path, job_id = sys.argv[1:5]
    params = leer_json(params_path, {}) or {}
    print(f"== etapa {etapa} · {slug} · job {job_id} · params {json.dumps(params, ensure_ascii=False)[:500]}", flush=True)
    try:
        mod, fn = MODULOS[etapa]
        resultado = getattr(importlib.import_module(mod), fn)(slug, params)
        jobs.terminar(job_id, resultado=resultado)
        print("== LISTO", json.dumps(resultado, ensure_ascii=False)[:2000], flush=True)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        traceback.print_exc()
        try:
            estado_produccion(slug, f"ERROR en {etapa}: {err}"); registro(f"{slug} · {etapa} · ERROR · {err[:200]}")
        except Exception: pass
        jobs.terminar(job_id, error=err); print("== ERROR", err, flush=True); sys.exit(1)

if __name__ == "__main__": main()
