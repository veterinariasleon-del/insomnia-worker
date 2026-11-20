"""Etapas de audio (§1 pasos 3–6): freno de créditos, voz (ElevenLabs + whisper), efectos (biblioteca → ElevenLabs → oído),
partitura (mezcla.json escrito por el modelo y validado) y mezcla (mezcla_json.py + comprobaciones)."""
import os, json, re, shutil, subprocess, sys
from .. import config as C, llm
from ..util import ruta_episodio, leer, escribir, escribir_json, leer_json, estado_produccion, registro, py, correr, herramienta, ffprobe_dur, loudness

def _creditos():
    r = py("eleven.py", "sub"); p = r.stdout.split()
    return {"plan": p[0], "usados": int(p[1]), "limite": int(p[3]), "disponibles": int(p[3]) - int(p[1])}

def _segmentos(ep):
    voz = os.path.join(ep, "voz"); os.makedirs(voz, exist_ok=True)
    py("guion_parser.py", os.path.join(ep, "guion.md"), os.path.join(voz, "segmentos.json"))
    return leer_json(os.path.join(voz, "segmentos.json"))

def freno_creditos(slug, params):
    """Necesarios = caracteres hablados × 1.3 + 3,000 (efectos y reintentos). Devuelve si alcanza; no gasta nada."""
    ep = ruta_episodio(slug); segs = _segmentos(ep); chars = sum(s["chars"] for s in segs)
    if not C.ELEVEN_KEY: return {"alcanza": False, "error": "sin clave de ElevenLabs", "caracteres": chars}
    cr = _creditos(); necesarios = int(chars * 1.3 + int(params.get("colchon", 3000)))
    alcanza = cr["disponibles"] >= necesarios
    msg = f"créditos: necesarios {necesarios} (chars {chars}), disponibles {cr['disponibles']} de {cr['limite']} → {'alcanza' if alcanza else 'NO alcanza, faltan ' + str(necesarios - cr['disponibles'])}"
    estado_produccion(slug, msg); print(msg)
    return {"alcanza": alcanza, "necesarios": necesarios, "caracteres": chars, **cr, "faltan": max(0, necesarios - cr["disponibles"])}

def voz(slug, params):
    """guion_parser → chunks → ElevenLabs v3 (reanudable) → verificación whisper ≥ 0.88 → regenera los que fallen (máx. 2)."""
    ep = ruta_episodio(slug); vz = os.path.join(ep, "voz"); _segmentos(ep)
    if not os.path.exists(os.path.join(vz, "chunks.json")) or params.get("rehacer"):
        py("chunks_json.py", os.path.join(vz, "segmentos.json"), os.path.join(vz, "chunks.json"))
    chunks = leer_json(os.path.join(vz, "chunks.json"))
    faltan = [i for i in range(len(chunks)) if not os.path.exists(os.path.join(vz, f"chunk_{i:02d}.mp3"))]
    if not C.ELEVEN_KEY and faltan: raise RuntimeError("sin clave de ElevenLabs y faltan trozos de voz")
    antes = _creditos()["usados"] if C.ELEVEN_KEY else 0
    if faltan: py("gen_voz2.py", vz, *( ["--todo"] if params.get("rehacer") else []), timeout=3600)
    umbral = float(params.get("umbral", 0.88)); intentos = int(params.get("max_regeneraciones", 2)); historial = []
    r = py("verificar_voz.py", vz, str(umbral), timeout=3600); ver = json.loads(r.stdout.strip().splitlines()[-1]); historial.append(ver["fallan"])
    for k in range(intentos):
        if ver["ok"]: break
        print(f"regenerando trozos {ver['fallan']} (intento {k + 1})", flush=True)
        py("gen_voz2.py", vz, "--solo", ",".join(map(str, ver["fallan"])), timeout=1800)
        r = py("verificar_voz.py", vz, str(umbral), "--solo", ",".join(map(str, ver["fallan"])), timeout=1800); ver = json.loads(r.stdout.strip().splitlines()[-1]); historial.append(ver["fallan"])
    gastados = (_creditos()["usados"] - antes) if C.ELEVEN_KEY else 0
    dur = sum(ffprobe_dur(os.path.join(vz, f"chunk_{i:02d}.wav")) for i in range(len(chunks)) if os.path.exists(os.path.join(vz, f"chunk_{i:02d}.wav")))
    estado_produccion(slug, f"voz: {len(chunks)} trozos, {dur:.0f} s de voz, verificación {'OK' if ver['ok'] else 'con fallos ' + str(ver['fallan'])}, créditos gastados {gastados}")
    registro(f"{slug} · voz · {len(chunks)} trozos · {'OK' if ver['ok'] else 'fallan ' + str(ver['fallan'])} · créditos {gastados}")
    return {"trozos": len(chunks), "segundos_voz": round(dur, 1), "verificacion_ok": ver["ok"], "fallan": ver["fallan"], "creditos_gastados": gastados, "historial": historial}

# ---------- efectos ----------
BIBLIOTECA = os.path.join(C.DATA_DIR, "sfx_biblioteca")

def _biblioteca():
    os.makedirs(BIBLIOTECA, exist_ok=True)
    return leer_json(os.path.join(BIBLIOTECA, "registro.json"), {})

def _columnas(ep, columna):
    """Extrae la columna SONIDO/IMAGEN/EFECTOS por escena del guion."""
    md = leer(os.path.join(ep, "guion.md")); out = {}
    for m in re.finditer(r"### (E\d\d)[^\n]*\n(.*?)(?=\n### |\n## |\Z)", md, re.S):
        c = re.search(r"\*\*" + columna + r":?\*\*:?\s*(.*?)(?=\n\*\*|\Z)", m.group(2), re.S)
        if c: out[m.group(1)] = c.group(1).strip()
    return out

SYSTEM_SFX = """Eres el diseñador de sonido de Insomnia (leyendas de terror, estilo The Magnus Archives: la ausencia asusta, foley íntimo, mono para lo normal y estéreo cuando se rompe la realidad). A partir de la columna SONIDO de cada escena del guion produces la lista de efectos que hay que tener, como JSON:
{"efectos": {"<nombre_snake_case>": {"tipo": "puntual|ambiente|musical", "dur": <segundos 1–22>, "prompts": ["<prompt en inglés para ElevenLabs sound generation, concreto, sin música salvo tipo musical>", "<variante 2>", "<variante 3>"], "correcto": "<descripción corta en inglés de lo que DEBE sonar (para el oído CLAP)>", "trampas": ["<lo que suele salir mal 1>", "<2>", "<3>", "<4>"], "influencia": 0.55, "escenas": ["E03"]}},
 "reutilizar": {"<nombre_nuevo>": "<nombre_en_biblioteca>"}}
Reglas: nombres cortos y descriptivos (viento_rafaga, puerta_cruje, piano_1); un efecto por sonido distinto del guion (no inventes extras); si un sonido equivalente ya está en la biblioteca (lista adjunta, con su descripción), NO lo generes: ponlo en "reutilizar" con el nombre que usarás; incluye siempre: impacto_grave (golpe cinematográfico), piano_1 (nota grave disonante con reverb), piano_2 (notas disonantes juntas), piano_3 (nota aguda suave) salvo que estén en biblioteca; los ambientes (grillos, viento, lluvia, interior) duran 15–22 s para poder repetirse en bucle; las trampas son descripciones de sonidos parecidos que serían un error. Responde SOLO el JSON."""

def sfx(slug, params):
    """Lista desde SONIDO (modelo) → biblioteca → ElevenLabs con el oído (gen_sfx.py, máx. 3 intentos) → registro, hoja de contacto, alta en biblioteca."""
    ep = ruta_episodio(slug); sx = os.path.join(ep, "sfx"); os.makedirs(sx, exist_ok=True)
    pedidos_p = os.path.join(sx, "pedidos.json"); bib = _biblioteca()
    if llm.texto_dado(params): escribir(pedidos_p, llm.texto_dado(params))
    if not os.path.exists(pedidos_p) or params.get("rehacer"):
        sonido = _columnas(ep, "SONIDO")
        lista_bib = "\n".join(f"- {k}: {v.get('correcto','')} (dur {v.get('dur','?')} s)" for k, v in bib.items()) or "(vacía)"
        system = SYSTEM_SFX + "\n\n===== BIBLIA DE SONIDO =====\n" + llm.biblia("03")
        usuario = "COLUMNA SONIDO POR ESCENA:\n" + "\n".join(f"{k}: {v}" for k, v in sonido.items()) + f"\n\nBIBLIOTECA DISPONIBLE:\n{lista_bib}"
        j = llm.llamar_json(system, usuario, max_tokens=8000, temperatura=0.3); escribir_json(pedidos_p, j)
    ped = leer_json(pedidos_p); efectos = ped.get("efectos", {}); reut = ped.get("reutilizar", {})
    registro_p = os.path.join(sx, "registro.json"); reg = leer_json(registro_p, {})
    # 1) reutilizar desde la biblioteca
    for nuevo, viejo in reut.items():
        src = os.path.join(BIBLIOTECA, viejo + ".mp3"); dst = os.path.join(sx, nuevo + ".mp3")
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy(src, dst); reg[nuevo] = {"aprobado": True, "origen": f"biblioteca:{viejo}", **{k: bib[viejo][k] for k in ("correcto", "trampas") if k in bib.get(viejo, {})}}
    for nombre, e in list(efectos.items()):
        if nombre in bib and not os.path.exists(os.path.join(sx, nombre + ".mp3")):
            shutil.copy(os.path.join(BIBLIOTECA, nombre + ".mp3"), os.path.join(sx, nombre + ".mp3")); reg[nombre] = {"aprobado": True, "origen": "biblioteca", "correcto": bib[nombre].get("correcto")}
    escribir_json(registro_p, reg)
    # 2) generar los que faltan con el oído
    faltan = {n: e for n, e in efectos.items() if not (reg.get(n, {}).get("aprobado") and os.path.exists(os.path.join(sx, n + ".mp3")))}
    gastados = 0
    if faltan:
        if not C.ELEVEN_KEY: raise RuntimeError(f"sin clave de ElevenLabs y faltan {len(faltan)} efectos: {list(faltan)}")
        antes = _creditos()["usados"]
        escribir_json(os.path.join(sx, "pedidos_faltantes.json"), {n: {"prompts": e["prompts"][:3], "dur": min(22, max(0.5, float(e.get("dur", 4)))), "correcto": e["correcto"], "trampas": e.get("trampas", []), "influencia": e.get("influencia", 0.55)} for n, e in faltan.items()})
        py("gen_sfx.py", sx, os.path.join(sx, "pedidos_faltantes.json"), timeout=5400)
        gastados = _creditos()["usados"] - antes
    reg = leer_json(registro_p, {})
    aprobados = [n for n in efectos if reg.get(n, {}).get("aprobado")]; revisar = [n for n in efectos if reg.get(n) and not reg[n].get("aprobado")]
    # 3) alta en biblioteca de los aprobados nuevos (nunca sobreescribe)
    for n in aprobados:
        if n not in bib and os.path.exists(os.path.join(sx, n + ".mp3")) and not str(reg[n].get("origen", "")).startswith("biblioteca"):
            shutil.copy(os.path.join(sx, n + ".mp3"), os.path.join(BIBLIOTECA, n + ".mp3"))
            bib[n] = {"correcto": efectos[n]["correcto"], "trampas": efectos[n].get("trampas", []), "dur": efectos[n].get("dur"), "tipo": efectos[n].get("tipo"), "origen": slug}
    escribir_json(os.path.join(BIBLIOTECA, "registro.json"), bib)
    # 4) hoja de contacto (todos los efectos seguidos, 0.6 s de silencio entre ellos)
    _hoja_sfx(sx, [n for n in efectos if os.path.exists(os.path.join(sx, n + ".mp3"))], os.path.join(ep, "mezcla", "hoja_de_contacto_sfx.mp3"))
    estado_produccion(slug, f"efectos: {len(aprobados)}/{len(efectos)} aprobados por el oído; para revisión humana: {revisar or 'ninguno'}; reutilizados de biblioteca: {len(reut)}; créditos {gastados}")
    registro(f"{slug} · sfx · {len(aprobados)}/{len(efectos)} aprobados · revisar {revisar} · créditos {gastados}")
    return {"efectos": len(efectos), "aprobados": aprobados, "revision_humana": revisar, "reutilizados": reut, "creditos_gastados": gastados}

def _hoja_sfx(sx, nombres, salida):
    if not nombres: return
    os.makedirs(os.path.dirname(salida), exist_ok=True); lista = os.path.join(sx, "_hoja_lista.txt")
    with open(lista, "w") as f:
        for n in nombres: f.write(f"file '{os.path.abspath(os.path.join(sx, n + '.mp3'))}'\n")
    # concat con silencios: más simple, usar filtro apad por archivo
    ins = []; filt = []
    for i, n in enumerate(nombres): ins += ["-i", os.path.join(sx, n + ".mp3")]; filt.append(f"[{i}:a]apad=pad_dur=0.6[a{i}]")
    filt_s = ";".join(filt) + ";" + "".join(f"[a{i}]" for i in range(len(nombres))) + f"concat=n={len(nombres)}:v=0:a=1[out]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *ins, "-filter_complex", filt_s, "-map", "[out]", "-b:a", "128k", salida], check=False)
    os.remove(lista)

# ---------- partitura (mezcla.json) ----------
def _palabras_por_escena(ep):
    """Palabras disponibles para anclar, por escena y voz (desde segmentos.json; sin etiquetas)."""
    segs = leer_json(os.path.join(ep, "voz", "segmentos.json")) or []
    out = {}
    for s in segs:
        voz = "testigo" if s["voz"] == "testigo" else "narrador"
        txt = re.sub(r"<[^>]+>", " ", s["texto"]); out.setdefault(f"{s['escena']} ({voz})", []).append(txt)
    return "\n".join(f"{k}: {' '.join(v)}" for k, v in out.items())

def partitura(slug, params):
    """El modelo escribe mezcla.json (la partitura) siguiendo el formato del ep. 1; se valida con mezcla_json.py --validar; hasta 3 intentos."""
    ep = ruta_episodio(slug); p = os.path.join(ep, "mezcla.json")
    if llm.texto_dado(params): escribir(p, llm.texto_dado(params))
    if os.path.exists(p) and not params.get("rehacer"):
        r = py("mezcla_json.py", ep, p, "--validar", check=False); return {"origen": "ya existía", "validacion": r.stdout.strip().splitlines()[-1][:1000]}
    sx = os.path.join(ep, "sfx"); reg = leer_json(os.path.join(sx, "registro.json"), {}); ped = leer_json(os.path.join(sx, "pedidos.json"), {}).get("efectos", {})
    disponibles = "\n".join(f"- {n}: {ped.get(n, {}).get('correcto', reg[n].get('correcto', ''))} ({ped.get(n, {}).get('tipo', '?')}, {'aprobado' if reg[n].get('aprobado') else 'SIN aprobar'})" for n in sorted(reg) if os.path.exists(os.path.join(sx, n + ".mp3")))
    doc = leer(herramienta("mezcla_json.py")).split('"""')[1]
    ejemplo = leer(os.path.join(ep, "..", "01-sombreron", "mezcla.json")) or leer(os.path.join(C.DATA_DIR, "contenido/videos/01-sombreron/mezcla.json"))
    if not ejemplo: ejemplo = leer(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "ejemplos", "01-sombreron", "mezcla.json"))
    epj = leer_json(os.path.join(ep, "episodio.json"), {}); numero = epj.get("numero", 0)
    system = ("Eres el mezclador de Insomnia. Escribes la PARTITURA del episodio como JSON para mezcla_json.py, siguiendo biblia 03 (la ausencia asusta; mono para lo normal, estéreo cuando se rompe; "
              "tensión que sube por acto con pad + drone + latido + Shepard; UN solo riser→silencio→golpe en el clímax; silencio absoluto 2–3 s antes de la consecuencia; tema del canal en la tarjeta y el cierre; niveles de la tabla de biblia 03). "
              "Solo puedes anclar en palabras que existan en la lista de palabras por escena (misma ortografía). Solo puedes usar archivos de la lista de efectos disponibles o rellenos sintéticos (gen). Responde SOLO el JSON.\n\n===== FORMATO (documentación de mezcla_json.py) =====\n" + doc +
              "\n\n===== EJEMPLO COMPLETO: PARTITURA DEL EPISODIO 1 =====\n" + ejemplo + "\n\n===== BIBLIA 03 =====\n" + llm.biblia("03"))
    base = (f"Episodio {slug}. Campo \"salida\": \"episodio{numero:02d}_v2\". Las anclas deben incluir al menos: callado-o-equivalente del primer corte de ambiente si lo hay, corte (golpe del clímax), toques-o-equivalente del momento de silencio absoluto, nada (fin del silencio), amanecer (inicio de la consecuencia), cierre (inicio del cierre del narrador). "
            f"Usa en episodio.json estos nombres: {json.dumps({k: epj.get(k) for k in ('negros', 't_corte', 'cierre', 'tarjeta') if k in epj}, ensure_ascii=False)} (si episodio.json referencia ['ancla', X], define X).\n\n"
            f"COLUMNA SONIDO DEL GUION:\n" + "\n".join(f"{k}: {v}" for k, v in _columnas(ep, "SONIDO").items()) + f"\n\nEFECTOS DISPONIBLES EN sfx/:\n{disponibles}\n\nPALABRAS POR ESCENA Y VOZ:\n{_palabras_por_escena(ep)}")
    problemas = None
    for intento in range(1, int(params.get("max_intentos", 3)) + 1):
        usuario = base + (f"\n\nTU INTENTO ANTERIOR TUVO ESTOS PROBLEMAS; corrígelos:\n{problemas}" if problemas else "")
        j = llm.llamar_json(system, usuario, max_tokens=16000, temperatura=0.2); escribir_json(p, j)
        r = py("mezcla_json.py", ep, p, "--validar", check=False)
        ultima = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-800:]
        try: v = json.loads(ultima)
        except Exception: v = {"ok": False, "problemas": [ultima[:800]]}
        print(f"intento {intento}: {v}", flush=True)
        if v.get("ok"):
            estado_produccion(slug, f"partitura escrita y validada (intento {intento}); duración estimada {v.get('duracion')} s")
            return {"ok": True, "intento": intento, "anclas": v.get("anclas")}
        problemas = "\n".join(v.get("problemas", []))
    estado_produccion(slug, f"partitura NO válida tras {intento} intentos: {problemas[:400]}")
    raise RuntimeError("la partitura no pasó la validación: " + problemas[:800])

def mezcla(slug, params):
    """mezcla_json.py → episodioNN_v2.mp3 + tiempos_v2.json + extractos; comprueba -14 LUFS/-1 dBTP y que la voz se entienda en los extractos."""
    ep = ruta_episodio(slug); p = os.path.join(ep, "mezcla.json")
    if not os.path.exists(p): raise RuntimeError("falta mezcla.json (corre la etapa partitura)")
    cfg = leer_json(p); base = cfg.get("salida", "episodio_v2"); mp3 = os.path.join(ep, "mezcla", base + ".mp3")
    if os.path.exists(mp3) and not params.get("rehacer"): return {"origen": "ya existía", "mp3": mp3, "duracion": ffprobe_dur(mp3)}
    py("mezcla_json.py", ep, p, timeout=3600)
    lufs, tp = loudness(mp3); dur = ffprobe_dur(mp3)
    ok = lufs is not None and abs(lufs + 14) <= 1.0 and tp <= -0.5
    # inteligibilidad: whisper sobre los extractos (si hay)
    palabras = {}
    try:
        from faster_whisper import WhisperModel
        m = WhisperModel(os.environ.get("WHISPER_MODELO", "small"), device="cpu", compute_type="int8", download_root=os.environ.get("HF_HOME"))
        for x in cfg.get("extractos", []):
            f = os.path.join(ep, "mezcla", x["nombre"] + ".mp3")
            if os.path.exists(f):
                segs, _ = m.transcribe(f, language="es"); palabras[x["nombre"]] = sum(len(s.text.split()) for s in segs)
    except Exception as e: print("whisper extractos:", e)
    epj = leer_json(os.path.join(ep, "episodio.json"), {}); epj["mezcla"] = f"mezcla/{base}.mp3"; escribir_json(os.path.join(ep, "episodio.json"), epj)
    estado_produccion(slug, f"mezcla: {base}.mp3 {dur:.1f} s, {lufs} LUFS, {tp} dBTP {'OK' if ok else 'FUERA DE RANGO'}; palabras en extractos {palabras}")
    registro(f"{slug} · mezcla · {dur:.0f} s · {lufs} LUFS · {'OK' if ok else 'revisar'}")
    return {"ok": ok, "mp3": mp3, "duracion": round(dur, 1), "lufs": lufs, "dbtp": tp, "palabras_extractos": palabras}
