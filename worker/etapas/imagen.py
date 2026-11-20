"""Etapas de imagen: planos (lista de planos + prompts, por el modelo, resueltos a tiempos) e imagenes (Gemini + QA visual).
Equivale a §1 paso (7) y al estado `planos` → `imagenes` de la tarea v3.1, sin Chrome."""
import os, json, re, glob
from .. import config as C, llm
from ..util import ruta_episodio, leer, escribir, escribir_json, leer_json, estado_produccion, registro, py, herramienta

def _estilo(): return leer_json(herramienta("estilo_imagen.json"))

def _columnas(ep, columna):
    md = leer(os.path.join(ep, "guion.md")); out = {}
    for m in re.finditer(r"### (E\d\d)[^\n]*\n(.*?)(?=\n### |\n## |\Z)", md, re.S):
        c = re.search(r"\*\*" + columna + r":?\*\*:?\s*(.*?)(?=\n\*\*|\Z)", m.group(2), re.S)
        if c: out[m.group(1)] = c.group(1).strip()
    return out

def _personajes_existentes():
    out = {}
    for ficha in glob.glob(os.path.join(C.DATA_DIR, "personajes", "*", "ficha.md")):
        pid = os.path.basename(os.path.dirname(ficha)); refs = glob.glob(os.path.join(os.path.dirname(ficha), "*_ref.*"))
        out[pid] = {"ficha": leer(ficha)[:1500], "referencia": bool(refs) or pid == "narrador"}
    return out

def _palabras(ep):
    segs = leer_json(os.path.join(ep, "voz", "segmentos.json")) or []
    out = {}
    for s in segs:
        voz = "testigo" if s["voz"] == "testigo" else "narrador"
        out.setdefault(f"{s['escena']} ({voz})", []).append(re.sub(r"<[^>]+>", " ", s["texto"]))
    return "\n".join(f"{k}: {' '.join(v)}" for k, v in out.items())

SYSTEM_PLANOS = """Eres el director de arte y montaje de Insomnia. Conviertes la columna IMAGEN y EFECTOS del guion en la lista de planos para HyperFrames, como JSON:
{"personajes": {"<id_snake>": {"nombre": "...", "descripcion_en": "<ficha física completa en inglés: edad, piel, pelo, ropa con colores, calzado, 'Exactly two arms, two hands, two feet'>", "nuevo": true|false}},
 "referencias": [{"id": "<id del personaje nuevo>", "formato": "3:4", "cuerpo_en": "CHARACTER SHEET: <descripcion_en> Full body, standing, three-quarter view, arms relaxed, neutral expression."}],
 "planos": [{"id": "P01", "escena": "E01", "corte": {"tipo": "palabra|inicio|fin", "escena": "E01", "voz": "narrador|testigo", "palabra": "<palabra exacta de la narración>", "k": 0, "delta": 0.0},
             "camara": "zoom_in_lento|zoom_out_lento|zoom_in_muy_lento|quieto|paralaje|sacudida", "efectos": ["niebla", "luciernagas", "vela", "humo", "narrador", "viñeta", "viñeta_leve", "respira_ala", "niebla_disuelve"],
             "refs": ["<ids de personajes que aparecen>"], "cuerpo_en": "SCENE: ... (si hay personaje: 'CHARACTER: use <descripcion_en completa>, <pose/acción>, exactly two hands...' y luego SCENE)", "luz_en": "LIGHT: ...", "composicion_en": "...", "formato": "16:9"}],
 "narrador_planos": {"<id del plano de la tarjeta>": "<escena de la tarjeta>", "<id del plano final>": "<última escena>"},
 "miniatura_fondo": "<id del plano más aterrador o del monstruo>"}
Reglas: ~1 plano cada 12–18 s de narración (un episodio de 10 min ≈ 35–42 planos; uno de 2 min ≈ 8–10); el primer plano empieza en el inicio de E01 con delta -0.8; cada corte en una palabra EXACTA de la narración de esa escena (misma ortografía/acentos; usa "voz": "testigo" si la palabra la dice el testigo); los planos de la tarjeta (E02) y del cierre (última escena) son "fondos de narrador": paisaje con una rama gruesa entrando por la derecha, VACÍA (sin búho), efectos ["niebla","luciernagas","narrador"], y van en narrador_planos; el monstruo sigue la progresión huella → sombra → silueta → detalle, y cuando se ve se describe DEMONÍACO según la regla 8 (texto adjunto); las descripciones de personajes se repiten completas en cada plano donde salgan (el generador no tiene memoria); nada de texto, marcas ni personas reales; en escenas de día usa luz_en gris-azulada sin acento cálido. No escribas el bloque de estilo ni el de prohibiciones: se añaden automáticamente. Responde SOLO el JSON."""

def planos(slug, params):
    ep = ruta_episodio(slug); esc_p = os.path.join(ep, "escenas.json"); est = _estilo()
    if llm.texto_dado(params): escribir(esc_p, llm.texto_dado(params))
    if not os.path.exists(esc_p) or params.get("rehacer"):
        ejemplo = leer_json(os.path.join(C.DATA_DIR, "contenido/videos/01-sombreron/escenas.json"), {"planos": []})
        ej = json.dumps([{k: p[k] for k in ("id", "escena", "corte", "camara", "efectos", "refs")} for p in ejemplo.get("planos", [])[:12]], ensure_ascii=False)
        system = SYSTEM_PLANOS + "\n\n===== REGLA 8 Y ESTILO (biblia 04) =====\n" + llm.biblia("04") + "\n\n===== REGLA 8 EN INGLÉS (para los prompts) =====\n" + est["MONSTRUO_REGLA_8"] + "\n\n===== EJEMPLO DE ESTRUCTURA (ep. 1, primeros planos) =====\n" + ej
        usuario = ("COLUMNA IMAGEN:\n" + "\n".join(f"{k}: {v}" for k, v in _columnas(ep, "IMAGEN").items()) + "\n\nCOLUMNA EFECTOS:\n" + "\n".join(f"{k}: {v}" for k, v in _columnas(ep, "EFECTOS").items()) +
                   "\n\nPERSONAJES YA EXISTENTES (reutiliza su id y su ficha si es el mismo personaje; el narrador siempre existe):\n" + json.dumps(_personajes_existentes(), ensure_ascii=False)[:6000] +
                   "\n\nPALABRAS POR ESCENA Y VOZ (solo puedes cortar en estas):\n" + _palabras(ep))
        problemas = None
        for intento in range(1, int(params.get("max_intentos", 3)) + 1):
            j = llm.llamar_json(system, usuario + (f"\n\nEL INTENTO ANTERIOR TUVO ESTOS PROBLEMAS; corrígelos:\n{problemas}" if problemas else ""), max_tokens=20000, temperatura=0.3)
            esc = _componer(j, est)
            escribir_json(esc_p, esc)
            r = py("resolver_planos.py", ep, check=False); ultima = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-600:]
            try: v = json.loads(ultima)
            except Exception: v = {"ok": False, "problemas": [ultima]}
            print(f"intento {intento}: {v.get('ok')} {v.get('problemas')}", flush=True)
            if v.get("ok"): break
            problemas = "\n".join(v.get("problemas", []))
        else:
            raise RuntimeError("los planos no pasaron la validación: " + (problemas or "")[:800])
        # episodio.json: narrador_planos y miniatura_fondo
        epj = leer_json(os.path.join(ep, "episodio.json"), {}); epj["narrador_planos"] = j.get("narrador_planos", {}); epj["miniatura_fondo"] = j.get("miniatura_fondo"); escribir_json(os.path.join(ep, "episodio.json"), epj)
        # fichas de personajes nuevos
        for pid, per in j.get("personajes", {}).items():
            carpeta = os.path.join(C.DATA_DIR, "personajes", pid); f = os.path.join(carpeta, "ficha.md")
            if not os.path.exists(f):
                os.makedirs(carpeta, exist_ok=True); escribir(f, f"# {per.get('nombre', pid)}\n\nEpisodio: {slug}\n\nDescripción (prompt):\n\n{per.get('descripcion_en','')}\n")
    else:
        r = py("resolver_planos.py", ep, check=False)
    esc = leer_json(esc_p)
    estado_produccion(slug, f"planos: {len(esc['planos'])} planos, {len(esc.get('referencias', []))} referencias nuevas; prompts en prompts/")
    registro(f"{slug} · planos · {len(esc['planos'])}")
    return {"planos": len(esc["planos"]), "referencias": [r["id"] for r in esc.get("referencias", [])], "narrador_planos": leer_json(os.path.join(ep, "episodio.json"), {}).get("narrador_planos")}

def _componer(j, est):
    """Arma los prompts completos (estilo + cuerpo + luz + composición + prohibido) como hacía planos.py del ep. 1."""
    planos = []
    for p in j["planos"]:
        formato = p.get("formato", "16:9"); luz = p.get("luz_en") or est["LUZ_NOCHE"]
        prompt = f"Generate ONE image, landscape {formato}. {est['ESTILO']} {p['cuerpo_en']} {luz} COMPOSITION: {p.get('composicion_en','')} {est['PROHIBIDO']}"
        planos.append({"id": p["id"], "escena": p["escena"], "corte": p["corte"], "refs": p.get("refs", []), "formato": formato, "prompt": prompt,
                       "camara": p.get("camara", "zoom_in_lento"), "efectos": [e for e in p.get("efectos", []) if e in est["efectos"]]})
    refs = []
    for r in j.get("referencias", []):
        formato = r.get("formato", "3:4")
        refs.append({"id": r["id"], "formato": formato, "prompt": f"Generate ONE image, portrait {formato}. {est['ESTILO']} {r['cuerpo_en']} {est['REFERENCIA_FONDO']} {est['PROHIBIDO']}"})
    return {"planos": planos, "referencias": refs, "personajes": j.get("personajes", {})}

def imagenes(slug, params):
    """Referencias nuevas → imágenes de cada plano con Gemini + QA visual (hasta 2 regeneraciones) → hoja de contacto."""
    ep = ruta_episodio(slug)
    if not os.path.exists(os.path.join(ep, "escenas.json")): raise RuntimeError("falta escenas.json (corre la etapa planos)")
    if not C.GEMINI_API_KEY: raise RuntimeError("sin clave: falta GEMINI_API_KEY; alternativa: generar en Flow y subir imagenes/PXX.jpg con POST /archivos")
    args = [ep, "--max-regen", str(params.get("max_regen", 2))]
    if params.get("solo"): args += ["--solo", ",".join(params["solo"])]
    r = py("gen_imagenes.py", *args, check=False, timeout=4 * 3600)
    ultima = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "{}"
    try: v = json.loads(ultima)
    except Exception: v = {"ok": False, "error": (r.stdout + r.stderr)[-800:]}
    esc = leer_json(os.path.join(ep, "escenas.json")); faltan = [p["id"] for p in esc["planos"] if not os.path.exists(os.path.join(ep, "imagenes", p["id"] + ".jpg"))]
    estado_produccion(slug, f"imágenes: {len(esc['planos']) - len(faltan)}/{len(esc['planos'])} en imagenes/; para revisión humana: {v.get('revisar') or 'ninguna'}; faltan: {faltan or 'ninguna'}")
    registro(f"{slug} · imagenes · {len(esc['planos']) - len(faltan)}/{len(esc['planos'])} · revisar {v.get('revisar')}")
    if faltan: raise RuntimeError(f"faltan imágenes: {faltan}; detalle: {v.get('error', '')[:300]}")
    return {"ok": v.get("ok", False), "planos": len(esc["planos"]), "revision_humana": v.get("revisar", []), "hoja": v.get("hoja")}
