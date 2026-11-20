"""Utilidades compartidas: rutas del episodio, registro, ffprobe, MD5, ejecución de scripts."""
import os, json, hashlib, subprocess, datetime, shutil, re, sys
from . import config as C

def ahora(): return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

def ruta_episodio(slug):
    """Los episodios viven en contenido/videos/<slug>; las pruebas en contenido/pruebas/<slug>."""
    for base in ("contenido/videos", "contenido/pruebas"):
        p = os.path.join(C.DATA_DIR, base, slug)
        if os.path.isdir(p): return p
    return os.path.join(C.DATA_DIR, "contenido/pruebas" if slug.startswith("00-") or "prueba" in slug else "contenido/videos", slug)

def leer(path, defecto=""):
    try: return open(path, encoding="utf-8").read()
    except FileNotFoundError: return defecto

def leer_json(path, defecto=None):
    try: return json.load(open(path, encoding="utf-8"))
    except FileNotFoundError: return defecto

def escribir(path, texto):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(texto)

def escribir_json(path, obj):
    escribir(path, json.dumps(obj, ensure_ascii=False, indent=1))

def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()

def ffprobe_dur(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0

def loudness(path):
    med = subprocess.run(["ffmpeg", "-i", path, "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True).stderr
    try:
        j = json.loads(med[med.rfind("{"):]); return float(j["input_i"]), float(j["input_tp"])
    except Exception: return None, None

def correr(cmd, cwd=None, env=None, check=True, timeout=None):
    """Ejecuta un comando mostrando salida en el log del trabajo (stdout heredado)."""
    print("$", " ".join(str(c) for c in cmd), flush=True)
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env or C.entorno_scripts(), text=True, capture_output=True, timeout=timeout)
    if r.stdout: print(r.stdout[-20000:], flush=True)
    if r.stderr: print(r.stderr[-8000:], file=sys.stderr, flush=True)
    if check and r.returncode != 0: raise RuntimeError(f"falló ({r.returncode}): {' '.join(str(c) for c in cmd)}")
    return r

def herramienta(nombre):
    """Ruta de un script del proyecto (volumen) o de los extras del worker."""
    p = os.path.join(C.HERR, nombre)
    if os.path.exists(p): return p
    q = os.path.join(os.path.dirname(os.path.abspath(__file__)), "herramientas_extra", nombre)
    if os.path.exists(q): return q
    raise FileNotFoundError(f"no existe la herramienta {nombre} ni en {C.HERR} ni en el worker")

def py(nombre, *args, cwd=None, check=True, timeout=None):
    return correr(["python3", herramienta(nombre), *args], cwd=cwd or C.DATA_DIR, check=check, timeout=timeout)

def estado_produccion(slug, linea):
    """Anota una línea fechada en contenido/videos/<slug>/estado_produccion.md (lo lee la siguiente corrida)."""
    ep = ruta_episodio(slug); p = os.path.join(ep, "estado_produccion.md")
    txt = leer(p, f"# Estado de producción · {slug}\n\n")
    escribir(p, txt.rstrip("\n") + f"\n- {ahora()} · {linea}\n")

def registro(linea):
    """Una línea por corrida/etapa en tarea/registro.md."""
    p = os.path.join(C.DATA_DIR, "tarea", "registro.md")
    txt = leer(p, "# Registro de corridas · Insomnia\n\n")
    escribir(p, txt.rstrip("\n") + f"\n- {ahora()} · {linea}\n")

def mover_a_to_delete(path):
    """Nunca borramos: movemos a _to_delete/ dentro de la raíz."""
    if not os.path.exists(path): return
    dest_dir = os.path.join(C.DATA_DIR, "_to_delete", datetime.date.today().isoformat()); os.makedirs(dest_dir, exist_ok=True)
    shutil.move(path, os.path.join(dest_dir, os.path.basename(path) + "_" + datetime.datetime.now().strftime("%H%M%S")))

def extraer_json(texto):
    """Saca el primer objeto/array JSON de una respuesta del modelo (tolera ```json ... ```)."""
    m = re.search(r"```(?:json)?\s*(.*?)```", texto, re.S)
    if m: texto = m.group(1)
    i = min([x for x in (texto.find("{"), texto.find("[")) if x >= 0] or [0])
    frag = texto[i:]
    # recorta hasta el último cierre
    for j in range(len(frag), 0, -1):
        try: return json.loads(frag[:j])
        except Exception: continue
    raise ValueError("la respuesta no trae JSON válido")

def dia_es(fecha):
    return ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"][fecha.weekday()]
