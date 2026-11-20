"""Trabajos asíncronos: cada etapa corre como un proceso aparte (python -m worker.run_etapa) con su log y su estado JSON
en DATA_DIR/_jobs/<id>.{log,json}. n8n crea el trabajo (POST /jobs) y consulta GET /jobs/<id> hasta que termine."""
import os, json, uuid, subprocess, datetime, sys, signal
from . import config as C
from .util import leer_json, escribir_json

os.makedirs(C.JOBS_DIR, exist_ok=True)
ETAPAS = ["guion", "auditoria", "freno_creditos", "voz", "sfx", "partitura", "mezcla", "planos", "imagenes", "render", "miniatura",
          "shorts", "textos", "publicar", "estado_cola", "reposicion", "reporte_semanal", "prueba_corta", "ping", "lote"]

def _ruta(id_, ext): return os.path.join(C.JOBS_DIR, f"{id_}.{ext}")

def activos():
    out = []
    for f in os.listdir(C.JOBS_DIR):
        if f.endswith(".json"):
            j = leer_json(os.path.join(C.JOBS_DIR, f), {})
            if j.get("estado") == "corriendo": out.append(j)
    return out

def _vivo(pid):
    try: os.kill(pid, 0); return True
    except OSError: return False

def crear(etapa, slug, params):
    if etapa not in ETAPAS: raise ValueError(f"etapa desconocida: {etapa}. Válidas: {ETAPAS}")
    for a in activos():
        if _vivo(a.get("pid", -1)):
            if a["slug"] == slug and a["etapa"] == etapa: raise RuntimeError(f"ya hay un trabajo {etapa} corriendo para {slug}: {a['id']}")
            if etapa in ("render", "imagenes", "lote") and a["etapa"] in ("render", "imagenes", "lote"): raise RuntimeError(f"ya hay un trabajo pesado corriendo ({a['etapa']} {a['slug']}): espera a que termine")
        else:
            a["estado"] = "muerto"; a["fin"] = datetime.datetime.now().isoformat(timespec="seconds"); escribir_json(_ruta(a["id"], "json"), a)
    id_ = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + etapa + "-" + uuid.uuid4().hex[:6]
    params_path = _ruta(id_, "params.json"); escribir_json(params_path, params or {})
    log = open(_ruta(id_, "log"), "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "worker.run_etapa", etapa, slug, params_path, id_],
                            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), env=C.entorno_scripts(),
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    j = {"id": id_, "etapa": etapa, "slug": slug, "params": params or {}, "estado": "corriendo", "pid": proc.pid,
         "inicio": datetime.datetime.now().isoformat(timespec="seconds"), "fin": None, "resultado": None, "error": None}
    escribir_json(_ruta(id_, "json"), j); return j

def estado(id_, cola_log=60):
    j = leer_json(_ruta(id_, "json"))
    if j is None: return None
    if j["estado"] == "corriendo" and not _vivo(j.get("pid", -1)):
        # el proceso murió sin escribir su resultado (p. ej. sin memoria)
        j["estado"] = "muerto"; j["error"] = "el proceso terminó sin reportar (¿memoria insuficiente?)"; j["fin"] = datetime.datetime.now().isoformat(timespec="seconds")
        escribir_json(_ruta(id_, "json"), j)
    try:
        lineas = open(_ruta(id_, "log"), encoding="utf-8", errors="replace").read().splitlines()
        j["log"] = "\n".join(lineas[-cola_log:])
    except FileNotFoundError: j["log"] = ""
    return j

def terminar(id_, resultado=None, error=None):
    """Lo llama run_etapa al acabar."""
    j = leer_json(_ruta(id_, "json"), {"id": id_})
    j["estado"] = "error" if error else "listo"; j["resultado"] = resultado; j["error"] = error
    j["fin"] = datetime.datetime.now().isoformat(timespec="seconds"); escribir_json(_ruta(id_, "json"), j)

def cancelar(id_):
    j = leer_json(_ruta(id_, "json"))
    if not j or j["estado"] != "corriendo": return j
    try: os.killpg(os.getpgid(j["pid"]), signal.SIGTERM)
    except Exception: pass
    j["estado"] = "cancelado"; j["fin"] = datetime.datetime.now().isoformat(timespec="seconds"); escribir_json(_ruta(id_, "json"), j); return j

def listar(n=30):
    ids = sorted((f[:-5] for f in os.listdir(C.JOBS_DIR) if f.endswith(".json") and not f.endswith(".params.json")), reverse=True)[:n]
    return [leer_json(_ruta(i, "json"), {}) for i in ids]
