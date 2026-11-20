"""Etapa publicar (§6): crea en publicar/ (en el VOLUMEN del VPS, nunca en la PC) una carpeta por pieza terminada
`AAAA-MM-DD_dia_20-00_TIPO_slug` con el video, youtube.txt, miniatura.jpg y, en los Shorts, tiktok.txt; comprueba MD5
y añade la fila a publicar/calendario.md. Nunca mueve, renombra ni borra carpetas existentes; si la carpeta de la pieza
ya existe, solo completa lo que falte. Con params["prueba"]=true usa publicar_pruebas/ (no toca el calendario real).
params: tipo = EPISODIO | SHORT-AVANCE | SHORT-REGLA | todo (defecto: todo lo que esté listo)."""
import os, shutil, datetime
from .. import config as C, fechas as F
from ..util import ruta_episodio, leer, escribir, leer_json, md5, estado_produccion, registro, ffprobe_dur

def _fuentes(ep, tipo):
    n = int(leer_json(os.path.join(ep, "episodio.json"), {}).get("numero", 0))
    if tipo == "EPISODIO":
        return {"video": os.path.join(ep, "video", f"episodio{n:02d}.mp4"), "youtube.txt": os.path.join(ep, "youtube.txt"), "miniatura.jpg": os.path.join(ep, "miniatura.jpg")}
    sub = "avance" if tipo == "SHORT-AVANCE" else "regla"; base = os.path.join(ep, "shorts", sub)
    return {"video": os.path.join(base, "short.mp4"), "youtube.txt": os.path.join(base, "youtube.txt"), "miniatura.jpg": os.path.join(base, "miniatura.jpg"), "tiktok.txt": os.path.join(base, "tiktok.txt")}

def _copiar_verificado(src, dst):
    if os.path.exists(dst) and md5(dst) == md5(src): return "ya estaba"
    tmp = dst + ".parcial"; shutil.copyfile(src, tmp)
    if md5(tmp) != md5(src):
        os.remove(tmp); raise RuntimeError(f"MD5 distinto al copiar {os.path.basename(src)}")
    os.replace(tmp, dst); return "copiado"

def _calendario(base, fecha, tipo, slug, carpeta, duracion):
    p = os.path.join(base, "calendario.md")
    txt = leer(p, "# Calendario de estrenos · Insomnia\n\nLa fecha de cada carpeta de `publicar/` es la fecha de estreno (20:00, hora del centro de México). Si cambias una fecha, cambia el nombre de la carpeta.\n\n| Fecha | Día | Hora | Pieza | Episodio | Duración | Carpeta |\n|---|---|---|---|---|---|---|\n")
    if carpeta in txt: return False
    fila = f"| {fecha.isoformat()} | {F.DIAS[fecha.weekday()]} | 20:00 | {tipo} | {slug} | {int(duracion // 60)}:{int(duracion % 60):02d} | `{carpeta}` |"
    lineas = txt.rstrip("\n").splitlines()
    # inserta ordenado por fecha dentro de la tabla
    filas = [l for l in lineas if l.startswith("| 20")]; resto = [l for l in lineas if not l.startswith("| 20")]
    filas.append(fila); filas.sort()
    escribir(p, "\n".join(resto + filas) + "\n"); return True

def publicar(slug, params):
    ep = ruta_episodio(slug); prueba = bool(params.get("prueba")) or slug.startswith("00-")
    base = F.carpeta_publicar(prueba); os.makedirs(base, exist_ok=True)
    hoy = datetime.date.fromisoformat(params["hoy"]) if params.get("hoy") else datetime.date.today()
    tipos = F.TIPOS if params.get("tipo", "todo") == "todo" else [params["tipo"]]
    fechas = F.fechas_piezas(slug, hoy, base)
    hechas, saltadas = {}, {}
    for tipo in tipos:
        fu = _fuentes(ep, tipo)
        if not os.path.exists(fu["video"]):
            saltadas[tipo] = "sin video todavía"; continue
        faltan = [k for k, v in fu.items() if not os.path.exists(v)]
        if faltan and not params.get("permitir_incompleta"):
            saltadas[tipo] = f"faltan {faltan} (corre las etapas textos/miniatura/shorts)"; continue
        fecha = fechas[tipo]; nombre = F.nombre_carpeta(fecha, tipo, slug); carpeta = os.path.join(base, nombre)
        # si ya existe una carpeta de este TIPO y slug (aunque con otra fecha), se respeta y se completa
        existentes = [c for c in F.leer_carpetas(base) if c["tipo"] == tipo and c["slug"] == slug]
        if existentes: nombre = existentes[-1]["nombre"]; carpeta = os.path.join(base, nombre); fecha = existentes[-1]["fecha"]
        os.makedirs(carpeta, exist_ok=True); copias = {}
        for k, src in fu.items():
            if not os.path.exists(src): continue
            dst = os.path.join(carpeta, os.path.basename(src) if k == "video" else k)
            copias[os.path.basename(dst)] = _copiar_verificado(src, dst)
        dur = ffprobe_dur(fu["video"])
        if not prueba: _calendario(base, fecha, tipo, slug, nombre, dur)
        hechas[tipo] = {"carpeta": nombre, "fecha": fecha.isoformat(), "copias": copias, "duracion": round(dur, 1), "md5_video": md5(fu["video"])}
    resumen = "; ".join(f"{t}: {h['carpeta']}" for t, h in hechas.items()) or "nada nuevo"
    estado_produccion(slug, f"publicar{' (PRUEBA)' if prueba else ''}: {resumen}; saltadas: {saltadas or 'ninguna'}; motivo de fechas: {fechas['motivo']}")
    registro(f"{slug} · publicar · {resumen}")
    return {"carpetas": hechas, "saltadas": saltadas, "fechas": {k: v.isoformat() for k, v in fechas.items() if k != "motivo"}, "motivo": fechas["motivo"], "base": base, "prueba": prueba}
