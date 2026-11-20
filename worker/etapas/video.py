"""Etapas de video (§1 estado imagenes → entregado): render (montaje, check, snapshots revisados por el modelo, render,
verificación), miniatura (arte_canal.py con plano y textos elegidos por el modelo) y shorts (build_short.py, pendiente)."""
import os, json, re
from .. import config as C, llm
from ..util import ruta_episodio, leer, escribir, escribir_json, leer_json, estado_produccion, registro, py, herramienta, md5, ffprobe_dur

def _numero(ep): return int(leer_json(os.path.join(ep, "episodio.json"), {}).get("numero", 0))

def _qa_frames(ep, rutas, contexto):
    """El modelo mira los fotogramas (como hacía el agente) y señala planos negros, texto cortado, títere mal puesto, etc."""
    if not C.ANTHROPIC_API_KEY or not rutas: return {"revisado": False}
    system = ("Eres el verificador visual de Insomnia. Te doy fotogramas del video montado (en orden de tiempo) y el contexto. Busca errores de montaje: cuadro negro o casi negro donde no se espera, imagen gris de 'pendiente', "
              "tarjeta de título o rótulo cortados o ilegibles, búho narrador flotando o cortado, pantalla final con texto fuera de lugar, imagen estirada. Responde SOLO JSON: {\"ok\": true|false, \"problemas\": [{\"fotograma\": n, \"problema\": \"...\"}]}. "
              "Un cuadro negro es correcto solo si el contexto dice que ahí hay un corte a negro.")
    try:
        v = llm.llamar_json(system, "CONTEXTO:\n" + contexto + f"\n\nFotogramas adjuntos en orden: {len(rutas)}.", imagenes=rutas[:14], max_tokens=1500, temperatura=0)
        v["revisado"] = True; return v
    except Exception as e:
        return {"revisado": False, "error": str(e)[:200]}

def render(slug, params):
    ep = ruta_episodio(slug); n = _numero(ep); mp4 = os.path.join(ep, "video", f"episodio{n:02d}.mp4")
    esc = leer_json(os.path.join(ep, "escenas.json")); faltan = sorted({p.get("imagen") or p["id"] for p in esc["planos"] if not os.path.exists(os.path.join(ep, "imagenes", (p.get("imagen") or p["id"]) + ".jpg"))})
    if faltan and not params.get("permitir_placeholders"): raise RuntimeError(f"faltan imágenes para renderizar: {faltan}")
    epj = leer_json(os.path.join(ep, "episodio.json"), {}); mezcla = os.path.join(ep, epj["mezcla"])
    if not os.path.exists(mezcla): raise RuntimeError("falta la mezcla " + epj["mezcla"])
    if os.path.exists(mp4) and not params.get("rehacer"):
        r = py("verificar_render.py", ep, check=False); v = json.loads(r.stdout.strip().splitlines()[-1])
        if v.get("ok"): return {"origen": "ya existía y verifica", **{k: v[k] for k in ("duracion", "lufs", "desfase_s", "bytes")}}
        print("el MP4 existente no verifica; se vuelve a renderizar:", v.get("problemas"), flush=True)
    # 1) montaje + check + snapshots (sin render) → QA visual
    r = py("render_hf.py", ep, "--solo-build", "--snapshots", str(params.get("snapshots", 10)), timeout=3600)
    b = json.loads(r.stdout.strip().splitlines()[-1])
    neg = epj.get("negros", []); ctx = f"Episodio {slug}: {len(esc['planos'])} planos; tarjeta de título en {epj.get('tarjeta', {}).get('ancla')}; cortes a negro: {json.dumps(neg, ensure_ascii=False)}; pantalla final desde {epj.get('cierre', {}).get('ancla')}. Duración {b.get('duracion')} s. Los fotogramas se tomaron en los instantes de su nombre."
    qa = _qa_frames(ep, b.get("snapshots", []), ctx)
    escribir_json(os.path.join(ep, "video", "qa_snapshots.json"), qa)
    graves = [p for p in qa.get("problemas", [])] if qa.get("revisado") and not qa.get("ok", True) else []
    if graves and not params.get("ignorar_qa"):
        estado_produccion(slug, f"render DETENIDO: la revisión de fotogramas encontró problemas: {graves}")
        raise RuntimeError("problemas en el montaje antes de renderizar: " + json.dumps(graves, ensure_ascii=False)[:600])
    # 2) render completo (largo: 45–90 min)
    r = py("render_hf.py", ep, "--snapshots", "0", "--workers", str(params.get("workers", 1)), timeout=6 * 3600)
    # 3) verificación
    r = py("verificar_render.py", ep, check=False); v = json.loads(r.stdout.strip().splitlines()[-1])
    qa2 = _qa_frames(ep, [f["archivo"] for f in v.get("frames", [])], ctx + " Estos fotogramas son del MP4 final.")
    escribir_json(os.path.join(ep, "video", "qa_final.json"), qa2)
    ok = v.get("ok") and (qa2.get("ok", True) if qa2.get("revisado") else True)
    h = md5(mp4) if os.path.exists(mp4) else None
    escribir(os.path.join(ep, "video", f"episodio{n:02d}.md5"), f"{h}  episodio{n:02d}.mp4\n")
    estado_produccion(slug, f"render: episodio{n:02d}.mp4 {v.get('duracion')} s, {v.get('lufs')} LUFS, desfase {v.get('desfase_s')} s, {v.get('bytes', 0) // 1_000_000} MB, MD5 {h}; {'OK' if ok else 'PROBLEMAS: ' + str(v.get('problemas')) + str(qa2.get('problemas'))}")
    registro(f"{slug} · render · {v.get('duracion')} s · {'OK' if ok else 'revisar'}")
    if not ok and not params.get("ignorar_qa"): raise RuntimeError("el video no pasó la verificación: " + json.dumps({"tecnica": v.get("problemas"), "visual": qa2.get("problemas")}, ensure_ascii=False)[:800])
    return {"ok": ok, "mp4": mp4, "md5": h, **{k: v.get(k) for k in ("duracion", "lufs", "dbtp", "desfase_s", "bytes")}, "vista_previa": os.path.join(ep, "video", f"episodio{n:02d}_720p_vista_previa.mp4")}

def miniatura(slug, params):
    """miniatura.jpg 16:9 del largo (arte_canal.py miniatura) con el plano y las líneas que decide la etapa textos (miniatura.json) o el modelo aquí."""
    ep = ruta_episodio(slug); mj = os.path.join(ep, "miniatura.json"); out = os.path.join(ep, "miniatura.jpg")
    if os.path.exists(out) and not params.get("rehacer"): return {"origen": "ya existía", "miniatura": out}
    m = leer_json(mj) or {}
    if not m or params.get("rehacer"):
        epj = leer_json(os.path.join(ep, "episodio.json"), {}); guion = leer(os.path.join(ep, "guion.md"))
        ideas = re.search(r"## Miniatura.*?(?=\n## |\Z)", guion, re.S); titulos = re.search(r"## Títulos.*?(?=\n## |\Z)", guion, re.S)
        esc = leer_json(os.path.join(ep, "escenas.json"), {"planos": []})
        system = ("Eres el diseñador de miniaturas de Insomnia (YouTube). Elige el plano más aterrador (el monstruo demoníaco o el detalle físico de la leyenda) y escribe el texto: "
                  "L1 y L2 son dos líneas MUY cortas en mayúsculas (2–3 palabras cada una, Cinzel), con la promesa o la regla rota, sin spoiler del final; sub es una línea corta opcional (región o 'Leyenda de Chiapas'). "
                  "lado = 'izq' o 'der' (dónde va el texto, al lado contrario del elemento importante de la imagen). Responde SOLO JSON {\"fondo\": \"Pxx\", \"l1\": \"...\", \"l2\": \"...\", \"sub\": \"...\", \"lado\": \"izq|der\"}.")
        planos_txt = "\n".join(f"{p['id']} ({p['escena']}): {p['prompt'][:220]}" for p in esc["planos"])
        try:
            m = llm.llamar_json(system, f"Sugerencia de episodio.json: miniatura_fondo = {epj.get('miniatura_fondo')}\n{(ideas.group(0) if ideas else '')}\n{(titulos.group(0) if titulos else '')}\n\nPLANOS:\n{planos_txt}", max_tokens=600, temperatura=0.4)
        except RuntimeError as e:
            if "sin clave" not in str(e): raise
            # híbrido/sin clave: miniatura provisional con el nombre de la leyenda (Jaime o la tarea de Claude pueden dejar miniatura.json)
            ley = (epj.get("leyenda") or slug).upper().split()
            m = {"fondo": epj.get("miniatura_fondo") or "", "l1": " ".join(ley[:2])[:16], "l2": " ".join(ley[2:4])[:16] or "LA LEYENDA", "sub": "", "lado": "izq"}
            print("sin clave: miniatura provisional", m, flush=True)
        escribir_json(mj, m)
    fondo = os.path.join(ep, "imagenes", m.get("fondo", "") + ".jpg")
    if not os.path.exists(fondo):
        cands = sorted(os.listdir(os.path.join(ep, "imagenes"))) if os.path.isdir(os.path.join(ep, "imagenes")) else []
        if not cands: raise RuntimeError("no hay imágenes para la miniatura")
        fondo = os.path.join(ep, "imagenes", cands[len(cands) * 2 // 3])
    py("arte_canal.py", "miniatura", fondo, out, m.get("l1", ""), m.get("l2", ""), m.get("sub", ""), m.get("lado", "izq"))
    estado_produccion(slug, f"miniatura: {os.path.basename(fondo)} · «{m.get('l1')} / {m.get('l2')}»")
    return {"miniatura": out, **m}

def shorts(slug, params):
    """Dos Shorts (AVANCE y LA REGLA) con build_short.py. El script vive en herramientas/ de la PC de Jaime (no estaba en la copia
    de la nube): si no está en el volumen, esta etapa lo dice en claro y no inventa nada. TODO: integrar su interfaz exacta cuando se copie."""
    ep = ruta_episodio(slug)
    try: bs = herramienta("build_short.py")
    except FileNotFoundError:
        estado_produccion(slug, "shorts: falta herramientas/build_short.py en el volumen (copiarlo desde C:\\Proyectos\\Leyendas\\herramientas)")
        return {"ok": False, "pendiente": "falta build_short.py en el volumen; súbelo con POST /archivos ruta=herramientas/build_short.py"}
    hechos = {}
    for tipo in ("avance", "regla"):
        out = os.path.join(ep, "shorts", tipo, "short.mp4")
        if os.path.exists(out) and not params.get("rehacer"): hechos[tipo] = {"origen": "ya existía", "mp4": out, "duracion": ffprobe_dur(out)}; continue
        # Interfaz supuesta (ajustar al copiar el script real): build_short.py <carpeta_episodio> <avance|regla>
        r = py("build_short.py", ep, tipo, check=False, timeout=3 * 3600)
        if r.returncode != 0 or not os.path.exists(out):
            estado_produccion(slug, f"shorts {tipo}: build_short.py falló; revisar su interfaz. Salida: {(r.stdout + r.stderr)[-300:]}")
            hechos[tipo] = {"ok": False, "error": (r.stdout + r.stderr)[-500:]}; continue
        d = ffprobe_dur(out); lim = 45 if tipo == "avance" else 59
        hechos[tipo] = {"mp4": out, "duracion": round(d, 1), "ok": d <= lim + 0.5}
    for tipo in ("avance", "regla"):
        if os.path.exists(os.path.join(ep, "shorts", tipo, "short.mp4")):
            try: hechos.setdefault(tipo, {})["miniatura"] = _miniatura_vertical(ep, tipo, params)
            except Exception as e: hechos.setdefault(tipo, {})["miniatura_error"] = str(e)[:300]
    estado_produccion(slug, f"shorts: {json.dumps({k: v.get('duracion') for k, v in hechos.items()})}")
    return {"ok": all(v.get("ok", True) for v in hechos.values()), "shorts": hechos}

def _miniatura_vertical(ep, tipo, params):
    """miniatura.jpg 1080×1920 del Short (§2): 2–3 palabras en Cinzel, la imagen más demoníaca o el detalle físico, tecolote abajo."""
    carpeta = os.path.join(ep, "shorts", tipo); out = os.path.join(carpeta, "miniatura.jpg"); mj = os.path.join(carpeta, "miniatura.json")
    if os.path.exists(out) and not params.get("rehacer"): return out
    m = leer_json(mj) or {}
    if not m:
        esc = leer_json(os.path.join(ep, "escenas.json"), {"planos": []}); tx = leer_json(os.path.join(ep, "textos.json"), {}) or {}
        titulo = ((tx.get("shorts") or {}).get(tipo) or {}).get("titulo", "")
        epj = leer_json(os.path.join(ep, "episodio.json"), {})
        system = ("Eres el diseñador de miniaturas verticales (9:16) de Insomnia. Responde SOLO JSON {\"fondo\": \"Pxx\", \"l1\": \"...\", \"l2\": \"...\", \"sub\": \"...\", \"cx\": 0.5}: "
                  "fondo = el plano más demoníaco (AVANCE) o el del detalle físico de la regla (LA REGLA); l1 y l2 = 2–3 palabras cada una en mayúsculas, la promesa o la regla; sub = «Leyenda de <región>» o vacío; cx = centro horizontal (0–1) del recorte vertical sobre la imagen 16:9 donde está lo importante.")
        planos_txt = "\n".join(f"{p['id']} ({p['escena']}): {p['prompt'][:200]}" for p in esc["planos"])
        try: m = llm.llamar_json(system, f"Short {tipo.upper()} · título: {titulo} · miniatura_fondo sugerido: {epj.get('miniatura_fondo')}\n\nPLANOS:\n{planos_txt}", max_tokens=400, temperatura=0.4)
        except RuntimeError as e:
            if "sin clave" not in str(e): raise
            m = {"fondo": epj.get("miniatura_fondo") or (esc["planos"][len(esc["planos"]) * 2 // 3]["id"] if esc["planos"] else ""), "l1": (titulo or "NO LO").upper()[:14], "l2": "LLAMES", "sub": "", "cx": 0.5}
        escribir_json(mj, m)
    fondo = os.path.join(ep, "imagenes", m.get("fondo", "") + ".jpg")
    if not os.path.exists(fondo):
        cands = sorted(os.listdir(os.path.join(ep, "imagenes"))) if os.path.isdir(os.path.join(ep, "imagenes")) else []
        if not cands: raise RuntimeError("no hay imágenes para la miniatura vertical")
        fondo = os.path.join(ep, "imagenes", cands[len(cands) * 2 // 3])
    py("arte_canal.py", "miniatura_vertical", fondo, out, m.get("l1", ""), m.get("l2", ""), m.get("sub", ""), str(m.get("cx", 0.5)))
    return out
