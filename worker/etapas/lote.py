"""Modo híbrido v2 (nov-2026): Claude (tarea programada) hace lo creativo y deja un PEDIDO; el VPS hace lo mecánico.

Un pedido es un JSON que la tarea deja en la carpeta compartida de Drive (pedidos/<slug>-<fecha>.json):
{
 "slug": "04-sisimite",
 "pasos": ["render", "miniaturas", "shorts", "tiktok", "hoja_contacto", "paquete"],   # en este orden; se pueden omitir
 "render": {"workers": 1},
 "miniaturas": [ {"salida": "miniatura.jpg",   "tipo": "miniatura",          "fondo": "P12", "l1": "NO LE", "l2": "CONTESTES", "sub": "", "extra": "izq"},
                 {"salida": "shorts/avance/miniatura.jpg", "tipo": "miniatura_vertical", "fondo": "P28", "l1": "...", "l2": "...", "sub": "", "extra": "0.55"} ],
 "tiktok": {"partes": 4},
 "paquete": [ {"red": "YouTube", "carpeta": "2026-11-25 mié - Título (Episodio 4 · El Sisimite)",
               "archivos": {"episodio04.mp4": "video/episodio04.mp4", "youtube.txt": "youtube.txt", "miniatura.jpg": "miniatura.jpg"}} , ...],
 "calendario": ["| fila | markdown | ... |"]          # opcional: filas que Claude ya calculó para publicar/calendario.md
}
Todo es reanudable: cada paso revisa qué existe y no repite lo verificado. El resultado y los problemas quedan en
_salida/<slug>/informe.json (lo lee la tarea del domingo) y los archivos para Jaime en _salida/publicar/<red>/<carpeta>/."""
import os, json, glob, shutil, subprocess, time, datetime
from .. import config as C
from ..util import ruta_episodio, leer_json, escribir_json, md5, ffprobe_dur, loudness, estado_produccion, registro, py, correr, herramienta

SALIDA = lambda: os.path.join(C.DATA_DIR, "_salida")


def _n(ep):
    return int(leer_json(os.path.join(ep, "episodio.json"), {}).get("numero", 0))


def _verificar_video(path, ancho=None, alto=None):
    """Medidas técnicas: duración, sonoridad, pico, resolución."""
    if not os.path.exists(path): return {"ok": False, "problema": "no existe"}
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate", "-of", "json", path], capture_output=True, text=True).stdout
    s = (json.loads(pr or "{}").get("streams") or [{}])[0]
    lufs, tp = loudness(path)
    v = {"archivo": os.path.basename(path), "duracion": round(ffprobe_dur(path), 2), "lufs": lufs, "dbtp": tp, "ancho": s.get("width"), "alto": s.get("height"),
         "fps": s.get("r_frame_rate"), "mb": round(os.path.getsize(path) / 1e6, 1)}
    prob = []
    if lufs is None or abs(lufs + 14) > 1.0: prob.append(f"sonoridad {lufs} LUFS (meta −14 ±1)")
    if tp is not None and tp > -0.9: prob.append(f"pico {tp} dBTP (máximo −1)")
    if ancho and (s.get("width"), s.get("height")) != (ancho, alto): prob.append(f"resolución {s.get('width')}×{s.get('height')} (esperada {ancho}×{alto})")
    v["ok"] = not prob; v["problemas"] = prob
    return v


def _limitar(path):
    """Si el pico pasa de −1 dBTP: limitador a −3 dBFS y AAC 256k (regla de §8), sin tocar el video."""
    tmp = path + ".lim.mp4"
    correr(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-c:v", "copy", "-af", "alimiter=limit=0.708:level=false", "-c:a", "aac", "-b:a", "256k", tmp])
    os.replace(tmp, path)


def paso_render(ep, slug, pedido, inf):
    from . import video as V
    r = V.render(slug, {"workers": (pedido.get("render") or {}).get("workers", 1), "ignorar_qa": True, "snapshots": 0})
    inf["render"] = r
    if not r.get("ok", True): inf["problemas"].append("render: " + json.dumps(r, ensure_ascii=False)[:300])


def paso_miniaturas(ep, slug, pedido, inf):
    hechas = []
    for m in pedido.get("miniaturas", []):
        out = os.path.join(ep, m["salida"]); os.makedirs(os.path.dirname(out), exist_ok=True)
        fondo = os.path.join(ep, "imagenes", m["fondo"] + ".jpg")
        if not os.path.exists(fondo): inf["problemas"].append(f"miniatura {m['salida']}: no existe la imagen {m['fondo']}"); continue
        args = [m.get("tipo", "miniatura"), fondo, out, m.get("l1", ""), m.get("l2", "")] + [x for x in (m.get("sub", ""), m.get("extra", "")) if x != ""]
        py("arte_canal.py", *args)
        hechas.append(m["salida"])
    inf["miniaturas"] = hechas


def paso_shorts(ep, slug, pedido, inf):
    res = {}
    for tipo in ("avance", "regla"):
        d = os.path.join(ep, "shorts", tipo); out = os.path.join(d, "short.mp4")
        if not os.path.exists(os.path.join(d, "short.json")): res[tipo] = "sin short.json (no se hizo)"; continue
        if not os.path.exists(out):
            correr(["python3", herramienta("build_short.py"), d], timeout=4 * 3600)
        v = _verificar_video(out, 1080, 1920)
        if v.get("dbtp") is not None and v["dbtp"] > -0.9: _limitar(out); v = _verificar_video(out, 1080, 1920)
        lim = 45.5 if tipo == "avance" else 59.5
        if v.get("duracion", 0) > lim: v["ok"] = False; v["problemas"].append(f"dura {v['duracion']} s (máximo {lim})")
        res[tipo] = v
        if not v["ok"]: inf["problemas"].append(f"short {tipo}: {v['problemas']}")
    inf["shorts"] = res


def paso_tiktok(ep, slug, pedido, inf):
    if not os.path.exists(os.path.join(ep, "tiktok", "partes.json")):
        inf["tiktok"] = "sin tiktok/partes.json (no se hizo)"; return
    n = int((pedido.get("tiktok") or {}).get("partes", 4)); bt = herramienta("build_tiktok_partes.py")
    calc = correr(["python3", bt, ep, "--partes", str(n), "--solo-calculo"], check=False)
    res = {"calculo": (calc.stdout or "")[-1500:]}
    for k in range(1, n + 1):
        out = os.path.join(ep, "tiktok", f"parte{k}", "parte.mp4")
        if not os.path.exists(out):
            for intento in (1, 2):   # si se corta, se repite la misma parte (regla §8)
                r = correr(["python3", bt, ep, "--partes", str(n), "--solo", str(k)], check=False, timeout=3 * 3600)
                if os.path.exists(out): break
                print(f"parte {k}: intento {intento} sin video ({r.returncode})", flush=True)
        v = _verificar_video(out, 1080, 1920)
        if v.get("dbtp") is not None and v["dbtp"] > -0.9: _limitar(out); v = _verificar_video(out, 1080, 1920)
        if not 60 <= v.get("duracion", 0) <= 595: v["problemas"].append(f"dura {v.get('duracion')} s (1 a 9:55 min; TikTok publica solo hasta 10 min)"); v["ok"] = False
        res[f"parte{k}"] = v
        if not v["ok"]: inf["problemas"].append(f"tiktok parte {k}: {v.get('problemas') or v.get('problema')}")
    inf["tiktok"] = res


def _hoja(video, salida, n=12, cols=4, ancho=480):
    """Hoja de contacto: n fotogramas repartidos con su minuto:segundo, en una sola imagen (Claude revisa 1 imagen, no 14)."""
    d = ffprobe_dur(video)
    if d <= 0: return None
    tmp = salida + "_tmp"; os.makedirs(tmp, exist_ok=True)
    tiempos = [d * (i + 0.5) / n for i in range(n)]
    for i, t in enumerate(tiempos):
        etiqueta = f"{int(t // 60)}\\:{int(t % 60):02d}"
        correr(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
                "-vf", f"scale={ancho}:-2,drawtext=text='{etiqueta}':x=8:y=8:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.6",
                os.path.join(tmp, f"f{i:02d}.jpg")], check=False)
    filas = (n + cols - 1) // cols
    correr(["ffmpeg", "-y", "-loglevel", "error", "-i", os.path.join(tmp, "f%02d.jpg"), "-vf", f"tile={cols}x{filas}:padding=6:color=black", "-frames:v", "1", "-q:v", "3", salida], check=False)
    shutil.rmtree(tmp, ignore_errors=True)
    return salida if os.path.exists(salida) else None


def paso_hoja_contacto(ep, slug, pedido, inf):
    dest = os.path.join(SALIDA(), slug); os.makedirs(dest, exist_ok=True); n = _n(ep); hojas = []
    largos = [os.path.join(ep, "video", f"episodio{n:02d}.mp4")]
    for v in largos:
        h = _hoja(v, os.path.join(dest, "hoja_episodio.jpg"), n=16, cols=4)
        if h: hojas.append(os.path.basename(h))
    for tipo in ("avance", "regla"):
        h = _hoja(os.path.join(ep, "shorts", tipo, "short.mp4"), os.path.join(dest, f"hoja_short_{tipo}.jpg"), n=8, cols=8, ancho=200)
        if h: hojas.append(os.path.basename(h))
    for p in sorted(glob.glob(os.path.join(ep, "tiktok", "parte*", "parte.mp4"))):
        k = os.path.basename(os.path.dirname(p))
        h = _hoja(p, os.path.join(dest, f"hoja_tiktok_{k}.jpg"), n=6, cols=6, ancho=200)
        if h: hojas.append(os.path.basename(h))
    vp = os.path.join(ep, "video", f"episodio{n:02d}_720p_vista_previa.mp4")
    if os.path.exists(vp): shutil.copyfile(vp, os.path.join(dest, os.path.basename(vp))); hojas.append(os.path.basename(vp))
    inf["hojas"] = hojas


LIMITE_MB = 470   # Metricool: 500 MB por video; se deja margen


def _comprimir(src, dst):
    """H.264 a la tasa justa para quedar debajo de LIMITE_MB (audio AAC 192k intacto en nivel)."""
    d = ffprobe_dur(src); kbps = int((LIMITE_MB * 8e3) / max(d, 1) - 220)
    correr(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps*1.3)}k",
            "-bufsize", f"{kbps*2}k", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", dst], timeout=4 * 3600)
    if os.path.getsize(dst) > 500e6: raise RuntimeError(f"tras comprimir sigue pesando {os.path.getsize(dst)/1e6:.0f} MB")


def paso_paquete(ep, slug, pedido, inf):
    """Copia (con MD5) cada pieza a _salida/publicar/<red>/<carpeta>/ según el plan que escribió Claude. Nunca borra."""
    hechas = []
    for pieza in pedido.get("paquete", []):
        carpeta = os.path.join(SALIDA(), "publicar", pieza["red"], pieza["carpeta"]); os.makedirs(carpeta, exist_ok=True)
        faltan = []
        for nombre, rel in pieza["archivos"].items():
            src = os.path.join(ep, rel); dst = os.path.join(carpeta, nombre)
            if not os.path.exists(src): faltan.append(rel); continue
            if src.endswith(".mp4") and os.path.getsize(src) > LIMITE_MB * 1e6:
                if not os.path.exists(dst):   # Metricool acepta hasta 500 MB por video: se recomprime sin tocar el original
                    _comprimir(src, dst + ".parcial.mp4"); os.replace(dst + ".parcial.mp4", dst)
                continue
            if os.path.exists(dst) and md5(dst) == md5(src): continue
            shutil.copyfile(src, dst + ".parcial")
            if md5(dst + ".parcial") != md5(src): raise RuntimeError(f"MD5 distinto al copiar {rel}")
            os.replace(dst + ".parcial", dst)
        hechas.append({"red": pieza["red"], "carpeta": pieza["carpeta"], "faltan": faltan})
        if faltan: inf["problemas"].append(f"paquete {pieza['carpeta']}: faltan {faltan}")
    if pedido.get("calendario"):
        p = os.path.join(SALIDA(), "publicar", "calendario_filas_nuevas.md")
        with open(p, "a", encoding="utf-8") as f: f.write("\n".join(pedido["calendario"]) + "\n")
    inf["paquete"] = hechas


PASOS = {"render": paso_render, "miniaturas": paso_miniaturas, "shorts": paso_shorts, "tiktok": paso_tiktok,
         "hoja_contacto": paso_hoja_contacto, "paquete": paso_paquete}


def lote(slug, params):
    """Corre un pedido completo. params = el pedido (o {"pedido": ruta})."""
    pedido = leer_json(params["pedido"]) if params.get("pedido") else params
    slug = pedido.get("slug", slug); ep = ruta_episodio(slug)
    dest = os.path.join(SALIDA(), slug); os.makedirs(dest, exist_ok=True)
    inf_p = os.path.join(dest, "informe.json")
    inf = {"slug": slug, "inicio": datetime.datetime.now().isoformat(timespec="seconds"), "pasos": {}, "problemas": []}
    for paso in pedido.get("pasos", list(PASOS)):
        t0 = time.time(); print(f"\n===== {slug} · {paso} =====", flush=True)
        try:
            PASOS[paso](ep, slug, pedido, inf); inf["pasos"][paso] = {"ok": True, "minutos": round((time.time() - t0) / 60, 1)}
        except Exception as e:
            inf["pasos"][paso] = {"ok": False, "minutos": round((time.time() - t0) / 60, 1), "error": f"{type(e).__name__}: {e}"[:800]}
            inf["problemas"].append(f"{paso}: {e}"[:400]); escribir_json(inf_p, inf)
            if paso == "render": break        # sin video no tiene caso seguir
        escribir_json(inf_p, inf)
    inf["fin"] = datetime.datetime.now().isoformat(timespec="seconds"); inf["ok"] = not inf["problemas"]
    escribir_json(inf_p, inf)
    estado_produccion(slug, f"VPS lote: {'OK' if inf['ok'] else 'PROBLEMAS: ' + '; '.join(inf['problemas'])[:500]}")
    registro(f"{slug} · VPS lote · {'OK' if inf['ok'] else 'revisar'}")
    return inf
