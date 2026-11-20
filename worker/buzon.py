"""Buzón de Drive: el puente entre la PC de Jaime (tarea programada de Claude) y el VPS, sin HTTP ni SSH.

Carpeta compartida en Google Drive (REMOTO, por defecto drive:Insomnia-VPS):
  entrada/   lo que la tarea deja para el VPS: herramientas/, marca/, perfil/ y contenido/videos/<slug>/ (sin .mp4/.wav)
  pedidos/   un JSON por pedido (formato en worker/etapas/lote.py); al terminar se mueve a pedidos/hechos/
  salida/    lo que el VPS devuelve: publicar/<red>/<carpeta>/ (videos, textos, miniaturas), <slug>/informe.json, hojas de contacto
  estado.json  latido del VPS (hora, versión, pedido en curso, espacio libre) para comprobar desde la PC que está vivo

Corre en bucle cada BUZON_MINUTOS: baja entrada/ y pedidos/, ejecuta los pedidos nuevos UNO A LA VEZ (el VPS es chico) y sube salida/.
Nunca borra nada en Drive; en el volumen solo limpia lo que ya subió de _salida/ si LIMPIAR_SALIDA=1.
Uso: python3 -m worker.buzon            (bucle)   ·   python3 -m worker.buzon --una-vez"""
import os, sys, json, time, subprocess, datetime, shutil, glob
from . import config as C
from .util import leer_json, escribir_json

LOCAL_PED = lambda: os.path.join(C.DATA_DIR, "_pedidos")
SALIDA = lambda: os.path.join(C.DATA_DIR, "_salida")


def rclone(*args, check=True):
    cmd = ["rclone", *args, "--retries", "3", "--low-level-retries", "10", "--stats-one-line", "-v"]
    print("$", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print((r.stderr or "")[-2000:], flush=True)
        if check: raise RuntimeError(f"rclone falló ({r.returncode}): {' '.join(args[:3])}")
    return r


def latido(extra=None):
    total, usado, libre = shutil.disk_usage(C.DATA_DIR)
    e = {"hora": datetime.datetime.now().isoformat(timespec="seconds"), "version": C.VERSION, "libre_gb": round(libre / 1e9, 1), **(extra or {})}
    p = os.path.join(C.DATA_DIR, "_estado_vps.json"); escribir_json(p, e)
    rclone("copyto", p, f"{C.REMOTO}/estado.json", check=False)


def bajar():
    # .mp4 y .wav nunca viajan hacia el VPS: los videos se hacen aquí y los audios de trabajo no hacen falta
    rclone("copy", f"{C.REMOTO}/entrada", C.DATA_DIR, "--exclude", "*.mp4", "--exclude", "*.wav", "--exclude", "_claves/**", "--update")
    os.makedirs(os.path.join(LOCAL_PED(), "nuevos"), exist_ok=True)
    rclone("copy", f"{C.REMOTO}/pedidos", os.path.join(LOCAL_PED(), "nuevos"), "--max-depth", "1", "--include", "*.json")


def subir():
    if os.path.isdir(SALIDA()):
        rclone("copy", SALIDA(), f"{C.REMOTO}/salida", "--exclude", "*.parcial")
        if os.environ.get("LIMPIAR_SALIDA") == "1":   # los videos ya están en Drive; libera el disco del VPS
            for f in glob.glob(os.path.join(SALIDA(), "publicar", "**", "*.mp4"), recursive=True): os.remove(f)


def pendientes():
    hechos = set(os.listdir(os.path.join(LOCAL_PED(), "hechos"))) if os.path.isdir(os.path.join(LOCAL_PED(), "hechos")) else set()
    return sorted(f for f in os.listdir(os.path.join(LOCAL_PED(), "nuevos")) if f.endswith(".json") and f not in hechos)


def procesar(nombre):
    ruta = os.path.join(LOCAL_PED(), "nuevos", nombre); pedido = leer_json(ruta, {})
    slug = pedido.get("slug", "?"); latido({"pedido": nombre, "slug": slug, "inicio_pedido": datetime.datetime.now().isoformat(timespec="seconds")})
    log = os.path.join(SALIDA(), slug, "log_vps.txt"); os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "a", encoding="utf-8") as lf:
        lf.write(f"\n===== pedido {nombre} · {datetime.datetime.now():%Y-%m-%d %H:%M} =====\n"); lf.flush()
        r = subprocess.run([sys.executable, "-c", "import json,sys; from worker.etapas.lote import lote; print(json.dumps(lote(sys.argv[1], {'pedido': sys.argv[2]}), ensure_ascii=False))", slug, ruta],
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), env=C.entorno_scripts(), stdout=lf, stderr=subprocess.STDOUT)
    os.makedirs(os.path.join(LOCAL_PED(), "hechos"), exist_ok=True); shutil.copyfile(ruta, os.path.join(LOCAL_PED(), "hechos", nombre))
    subir()
    rclone("moveto", f"{C.REMOTO}/pedidos/{nombre}", f"{C.REMOTO}/pedidos/hechos/{nombre}", check=False)
    return r.returncode


def una_vez():
    try:
        bajar()
        for nombre in pendientes():
            print(f"== procesando pedido {nombre}", flush=True); procesar(nombre)
        subir(); latido({"pedido": None})
    except Exception as e:
        print("buzón: error", e, flush=True)
        try: latido({"error": str(e)[:300]})
        except Exception: pass


def main():
    if not shutil.which("rclone") or not os.environ.get("RCLONE_CONFIG_DRIVE_TOKEN"):
        print("buzón apagado: falta rclone o RCLONE_CONFIG_DRIVE_TOKEN (configurar la conexión a Drive en Easypanel)", flush=True)
        if "--una-vez" in sys.argv: return
        while True: time.sleep(3600)
    if "--una-vez" in sys.argv: una_vez(); return
    while True:
        una_vez(); time.sleep(C.BUZON_MINUTOS * 60)


if __name__ == "__main__": main()
