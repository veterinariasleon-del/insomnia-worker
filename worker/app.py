"""API HTTP del worker de Insomnia (FastAPI). n8n la usa como "manos": estado, trabajos por etapa y archivos.
Todas las rutas salvo /salud exigen `Authorization: Bearer <WORKER_TOKEN>`."""
import os, json, subprocess, zipfile, shutil, io, datetime
from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File, Form, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, Any
from . import config as C, jobs
from .util import leer_json, py, ruta_episodio

app = FastAPI(title="Insomnia worker", version=C.VERSION)

def auth(authorization: Optional[str] = Header(None)):
    if not C.WORKER_TOKEN: raise HTTPException(503, "WORKER_TOKEN no configurado en el worker")
    if authorization != f"Bearer {C.WORKER_TOKEN}": raise HTTPException(401, "token inválido")

def _segura(ruta):
    """Ruta relativa dentro del volumen (sin escapar de DATA_DIR)."""
    base = os.path.realpath(C.DATA_DIR); p = os.path.realpath(os.path.join(base, ruta.lstrip("/\\")))
    if not p.startswith(base + os.sep) and p != base: raise HTTPException(400, "ruta fuera del volumen")
    return p

@app.get("/salud")
def salud():
    return {"ok": True, "version": C.VERSION, "hora": datetime.datetime.now().isoformat(timespec="seconds"), "data_dir": C.DATA_DIR,
            "claves": C.claves_presentes(), "volumen_montado": os.path.isdir(os.path.join(C.DATA_DIR, "biblia"))}

@app.get("/estado", dependencies=[Depends(auth)])
def estado(creditos: bool = True):
    """Lo que la tarea hacía en §0: cola.py siguiente, carpetas de publicar/, créditos de ElevenLabs, trabajos activos."""
    out = {"hoy": datetime.date.today().isoformat(), "dia_semana": datetime.date.today().weekday(), "version": C.VERSION}
    r = py("cola.py", "siguiente", check=False)
    try: out["cola"] = json.loads(r.stdout[r.stdout.find("{"):])
    except Exception: out["cola"] = {"error": (r.stdout + r.stderr)[-500:]}
    r = py("cola.py", "faltan", check=False); out["pendientes"] = int(r.stdout.strip() or 0) if r.returncode == 0 else None
    pub = os.path.join(C.DATA_DIR, "publicar")
    out["publicar"] = sorted(d for d in os.listdir(pub) if os.path.isdir(os.path.join(pub, d))) if os.path.isdir(pub) else []
    episodios = [d for d in out["publicar"] if "_EPISODIO_" in d]
    out["ultimo_episodio_publicar"] = episodios[-1][:10] if episodios else None
    if out["ultimo_episodio_publicar"]:
        dias = (datetime.date.fromisoformat(out["ultimo_episodio_publicar"]) - datetime.date.today()).days
        out["freno_produccion"] = dias > 14; out["dias_adelante"] = dias
    else: out["freno_produccion"] = False
    if creditos and C.ELEVEN_KEY:
        r = py("eleven.py", "sub", check=False)
        try:
            partes = r.stdout.split(); out["elevenlabs"] = {"plan": partes[0], "usados": int(partes[1]), "limite": int(partes[3]), "disponibles": int(partes[3]) - int(partes[1])}
        except Exception: out["elevenlabs"] = {"error": (r.stdout + r.stderr)[-300:]}
    else: out["elevenlabs"] = {"error": "sin clave"} if not C.ELEVEN_KEY else None
    slug = (out.get("cola") or {}).get("actual", {}).get("slug") if isinstance(out.get("cola"), dict) else None
    if slug:
        ep = ruta_episodio(slug); out["episodio"] = {"slug": slug, "carpeta": ep,
            "archivos": {k: os.path.exists(os.path.join(ep, v)) for k, v in {"guion": "guion.md", "episodio_json": "episodio.json", "chunks": "voz/chunks.json",
                         "mezcla_json": "mezcla.json", "mezcla_mp3": f"mezcla/episodio{slug[:2]}_v2.mp3", "escenas": "escenas.json", "imagenes": "imagenes", "video": f"video/episodio{slug[:2]}.mp4"}.items()}}
        out["episodio"]["imagenes_listas"] = len([f for f in os.listdir(os.path.join(ep, "imagenes"))]) if os.path.isdir(os.path.join(ep, "imagenes")) else 0
    out["trabajos_activos"] = [{k: a[k] for k in ("id", "etapa", "slug", "inicio")} for a in jobs.activos()]
    return out

class NuevoJob(BaseModel):
    etapa: str
    slug: str
    params: Optional[dict[str, Any]] = None

@app.post("/jobs", dependencies=[Depends(auth)])
def crear_job(j: NuevoJob):
    try: return jobs.crear(j.etapa, j.slug, j.params or {})
    except (ValueError, RuntimeError) as e: raise HTTPException(409, str(e))

@app.get("/jobs", dependencies=[Depends(auth)])
def listar_jobs(n: int = 30): return jobs.listar(n)

@app.get("/jobs/{id_}", dependencies=[Depends(auth)])
def ver_job(id_: str, log: int = 60):
    j = jobs.estado(id_, log)
    if not j: raise HTTPException(404, "no existe ese trabajo")
    return j

@app.delete("/jobs/{id_}", dependencies=[Depends(auth)])
def cancelar_job(id_: str):
    j = jobs.cancelar(id_)
    if not j: raise HTTPException(404, "no existe ese trabajo")
    return j

@app.get("/archivos/listar", dependencies=[Depends(auth)])
def listar(ruta: str = "", recursivo: bool = False):
    p = _segura(ruta)
    if not os.path.exists(p): raise HTTPException(404, "no existe")
    if os.path.isfile(p): return [{"nombre": os.path.basename(p), "bytes": os.path.getsize(p), "tipo": "archivo"}]
    out = []
    for raiz, dirs, files in os.walk(p):
        rel = os.path.relpath(raiz, p)
        for d in sorted(dirs): out.append({"nombre": os.path.normpath(os.path.join(rel, d)), "tipo": "carpeta"})
        for f in sorted(files):
            fp = os.path.join(raiz, f); out.append({"nombre": os.path.normpath(os.path.join(rel, f)), "tipo": "archivo", "bytes": os.path.getsize(fp),
                                                   "modificado": datetime.datetime.fromtimestamp(os.path.getmtime(fp)).isoformat(timespec="seconds")})
        if not recursivo: break
    return out

@app.get("/archivos", dependencies=[Depends(auth)])
def descargar(ruta: str):
    p = _segura(ruta)
    if not os.path.isfile(p): raise HTTPException(404, "no existe")
    return FileResponse(p, filename=os.path.basename(p))

@app.get("/archivos/texto", dependencies=[Depends(auth)])
def texto(ruta: str, max_bytes: int = 200000):
    p = _segura(ruta)
    if not os.path.isfile(p): raise HTTPException(404, "no existe")
    with open(p, "rb") as f: b = f.read(max_bytes)
    return {"ruta": ruta, "bytes": os.path.getsize(p), "truncado": os.path.getsize(p) > max_bytes, "texto": b.decode("utf-8", errors="replace")}

@app.post("/archivos", dependencies=[Depends(auth)])
async def subir(ruta: str = Form(...), archivo: UploadFile = File(...), extraer_zip: bool = Form(False), sobrescribir: bool = Form(True)):
    """Sube un archivo al volumen (ruta relativa de destino). Con extraer_zip=true, `ruta` es la carpeta y el zip se extrae ahí."""
    p = _segura(ruta)
    if extraer_zip:
        os.makedirs(p, exist_ok=True); data = await archivo.read()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                dest = os.path.realpath(os.path.join(p, info.filename))
                if not dest.startswith(os.path.realpath(p)): raise HTTPException(400, "zip con rutas fuera de destino")
                if info.is_dir(): os.makedirs(dest, exist_ok=True); continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if os.path.exists(dest) and not sobrescribir: continue
                with z.open(info) as src, open(dest, "wb") as dst: shutil.copyfileobj(src, dst)
        return {"ok": True, "extraido_en": ruta, "archivos": len(z.infolist())}
    if os.path.exists(p) and not sobrescribir: raise HTTPException(409, "ya existe")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        while True:
            chunk = await archivo.read(1 << 20)
            if not chunk: break
            f.write(chunk)
    return {"ok": True, "ruta": ruta, "bytes": os.path.getsize(p)}

class Texto(BaseModel):
    ruta: str
    texto: str

@app.put("/archivos/texto", dependencies=[Depends(auth)])
def escribir_texto(t: Texto):
    """Escribe un archivo de texto (p. ej. un guion producido fuera del worker, o historial.csv desde n8n)."""
    p = _segura(t.ruta); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write(t.texto); return {"ok": True, "ruta": t.ruta, "bytes": len(t.texto.encode())}

@app.get("/etapas", dependencies=[Depends(auth)])
def etapas(): return jobs.ETAPAS
