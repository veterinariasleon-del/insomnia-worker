"""Etapas de texto del episodio: guion (escritura + episodio.json) y auditoría independiente (máx. 3 rondas).
Equivale a §1 (1) y (2) de la tarea v3.1. Acepta texto ya escrito (híbrido) en params["texto"]."""
import os, json, sys, re
from .. import config as C, llm
from ..util import ruta_episodio, leer, escribir, escribir_json, leer_json, estado_produccion, registro, py, extraer_json

def _cola_actual(slug):
    r = py("cola.py", "siguiente"); j = json.loads(r.stdout[r.stdout.find("{"):])
    if j.get("actual", {}).get("slug") != slug:
        # permite producir un slug distinto (p. ej. pruebas) buscando su ficha
        cola = leer_json(os.path.join(C.DATA_DIR, "cola", "cola.json"), {"episodios": []})
        for i, e in enumerate(cola["episodios"]):
            if e["slug"] == slug:
                sig = cola["episodios"][i + 1] if i + 1 < len(cola["episodios"]) else None
                return e, sig
        return {"slug": slug, "leyenda": slug, "region": "", "regla": "", "nota": ""}, j.get("siguiente")
    return j["actual"], j.get("siguiente")

def _parsear(ep):
    sys.path.insert(0, C.HERR)
    import importlib; gp = importlib.import_module("guion_parser")
    segs = gp.parse(os.path.join(ep, "guion.md"))
    chars = sum(s["chars"] for s in segs); escenas = sorted({s["escena"] for s in segs})
    return segs, chars, escenas

SYSTEM_GUION = """Eres el guionista de Insomnia, canal de YouTube de leyendas de terror de México animadas a partir de ilustraciones pintadas y narradas por Don Tecolote (un tecolote viejo que habla de tú al espectador). Escribes guiones de producción en español de México, con el formato EXACTO del guion modelo (fichas por escena con **NARRACIÓN**, **DIRECCIÓN:**, **SONIDO:**, **IMAGEN:**, **EFECTOS:**), porque un programa los lee automáticamente:
- Cada escena empieza con `### Exx · ...` seguido en la línea siguiente de `**NARRACIÓN**` y después, en su línea, `**DIRECCIÓN:**`. Sin eso el texto no se convierte en voz.
- Marcas dentro de la narración: `[pausa 0.5]` … `[pausa 2]`, `[testigo]` … `[narrador]` (voz femenina en primera persona), `[personaje: Nombre]` … `[narrador]` (el narrador cita), `[susurro]` … `[normal]`, `[más lento]` … `[normal]`, `[seco]`, `[al espectador]`. Ninguna otra marca.
- Nada de gore explícito, nada de marcas comerciales ni personas reales; los personajes terroríficos se describen demoníacos (regla 8 de biblia 04) manteniendo el estilo pintado.
- Las columnas SONIDO e IMAGEN son instrucciones concretas de producción (efectos con descripción en inglés para ElevenLabs, planos numerados), no adornos.
Respetas todo lo que dice la biblia que recibes. Entregas SOLO el guion en Markdown, completo, sin comentarios fuera de él."""

def _prompt_guion(ficha, siguiente, modelo_guion, duracion_min, extra=""):
    sig = siguiente["leyenda"] if siguiente else "la siguiente leyenda de la cola"
    return f"""Escribe el guion completo del episodio sobre **{ficha['leyenda']}** ({ficha.get('region','')}).
Ficha de la cola: regla = {ficha.get('regla','')} · nota = {ficha.get('nota','')}
Duración objetivo: {duracion_min} minutos de narración ({'7 bloques, 11 mecanismos, escalada en tres, clímax, consecuencia y cierre' if duracion_min >= 6 else 'versión corta: gancho, contrato breve, una escalada, clímax, consecuencia y cierre del narrador; mismos bloques comprimidos'}).
Requisitos no negociables: narrador de tú; testigo con nombre; regla y ruptura; marcas de dirección; columnas SONIDO e IMAGEN por escena; clímax de verdad aterrador sin gore; el gancho final del narrador (última escena) anuncia la próxima leyenda **por su nombre exacto: «{sig}»**; al final del documento incluye las secciones `## Lista de control (biblia §8)` (autoevaluación punto por punto), `## Títulos a probar (≤ 60 caracteres)` (3 títulos), `## Miniatura (dos versiones)` y `## Shorts` con los dos guiones (AVANCE 30–45 s, LA REGLA 40–59 s, ≤ 110 palabras cada uno, formato: `### SHORT AVANCE` y `### SHORT LA REGLA`, cada uno con **NARRACIÓN** y **SUBTÍTULOS** (frase gancho ≤ 6 palabras + 4–6 líneas cortas)).
{extra}

===== GUION MODELO (episodio 1; copia el formato, no el contenido) =====
{modelo_guion}
"""

def guion(slug, params):
    ep = ruta_episodio(slug); os.makedirs(ep, exist_ok=True)
    ficha, siguiente = _cola_actual(slug)
    if params.get("leyenda"): ficha = {**ficha, **{k: params[k] for k in ("leyenda", "region", "regla", "nota") if k in params}}
    if params.get("siguiente"): siguiente = {"leyenda": params["siguiente"]}
    duracion = float(params.get("duracion_min", 10))
    dado = llm.texto_dado(params)
    if dado:
        escribir(os.path.join(ep, "guion.md"), dado); origen = "texto dado"
    elif os.path.exists(os.path.join(ep, "guion.md")) and not params.get("rehacer"):
        origen = "ya existía"
    else:
        modelo_guion = leer(os.path.join(C.DATA_DIR, "contenido/videos/01-sombreron/guion.md"))
        system = SYSTEM_GUION + "\n\n===== BIBLIA =====\n" + llm.biblia("00", "01", "02", "04", "06") + "\n\n===== APRENDIZAJES DE ANALÍTICA =====\n" + llm.aprendizajes()
        txt = llm.llamar(system, _prompt_guion(ficha, siguiente, modelo_guion, duracion, params.get("indicaciones", "")), modelo=C.ANTHROPIC_MODEL_GUION, max_tokens=24000)
        txt = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", txt.strip())
        escribir(os.path.join(ep, "guion.md"), txt); origen = "escrito por el modelo"
    segs, chars, escenas = _parsear(ep)
    if len(escenas) < (7 if duracion >= 6 else 3): raise RuntimeError(f"el guion solo tiene {len(escenas)} escenas parseables; revisa el formato (### Exx / **NARRACIÓN** / **DIRECCIÓN:**)")
    _episodio_json(slug, ep, ficha, siguiente, params)
    estado_produccion(slug, f"guion {origen}: {len(escenas)} escenas, {chars} caracteres hablados, {len(segs)} segmentos")
    registro(f"{slug} · guion · {origen} · {chars} chars")
    return {"escenas": len(escenas), "caracteres": chars, "segmentos": len(segs), "origen": origen, "siguiente": siguiente["leyenda"] if siguiente else None}

def _episodio_json(slug, ep, ficha, siguiente, params):
    """episodio.json: lo que build_video y la voz necesitan saber del episodio. El modelo lo rellena a partir del guion final."""
    p = os.path.join(ep, "episodio.json"); modelo = leer_json(os.path.join(C.DATA_DIR, "contenido/videos/01-sombreron/episodio.json"), {})
    actual = leer_json(p, {}) or {}
    n = re.match(r"(\d+)", slug); numero = int(n.group(1)) if n else 0
    base = {"numero": numero, "slug": slug, "leyenda": ficha["leyenda"], "titulo_youtube": "", "mezcla": f"mezcla/episodio{numero:02d}_v2.mp3"}
    if actual.get("tarjeta") and not params.get("rehacer"): return actual
    guion = leer(os.path.join(ep, "guion.md"))
    system = ("Eres el asistente de montaje de Insomnia. A partir del guion devuelves SOLO un JSON con la misma estructura del ejemplo, adaptado al guion: "
              "`tarjeta` (chico/grande: nombre de la leyenda en mayúsculas; `ancla` = [escena, palabra exacta del guion donde aparece el nombre de la leyenda en la escena de la tarjeta]), "
              "`rotulo` (html con lugar y fecha del guion; `escena` = la del contrato), `negros` (cortes a negro que pida la columna EFECTOS: cada uno con `inicio` = [escena, palabra, k, delta] o ['ini', escena, delta] y `dur` en segundos, `salida` 0.6–1.5; si el guion no pide negros, lista vacía), "
              "`t_corte` = [escena, palabra, 0, 0.15] del golpe del clímax, `cierre` (`ancla` = [última escena, palabra exacta con que el narrador nombra la próxima leyenda], `delta` 2.0, `siguiente` = nombre exacto de la próxima leyenda, `pie` = pregunta para comentarios), "
              "`escenas_lentas` (lista de escenas de escalada 3 y clímax que se narran más lento), `cortes_voz_despues_de` (lista de [escena, 'últimas palabras'] donde la voz debe dejar un hueco largo para un efecto; puede ser vacía). "
              "Las palabras ancla deben existir EXACTAMENTE en la narración de esa escena (misma ortografía y acentos). No incluyas narrador_planos (lo pone otra etapa).")
    usuario = f"EJEMPLO (episodio 1):\n{json.dumps(modelo, ensure_ascii=False, indent=1)}\n\nGUION:\n{guion}\n\nPróxima leyenda: {siguiente['leyenda'] if siguiente else '(la que diga el guion)'}"
    try:
        j = llm.llamar_json(system, usuario, max_tokens=3000)
    except RuntimeError as e:
        if "sin clave" in str(e):
            j = {**modelo, "tarjeta": {**modelo.get("tarjeta", {}), "grande": ficha["leyenda"].upper()}, "negros": [], "cierre": {**modelo.get("cierre", {}), "siguiente": siguiente["leyenda"] if siguiente else ""}}
            print("sin clave: episodio.json provisional copiado del ep. 1; revísalo", flush=True)
        else: raise
    j.update(base); j.pop("narrador_planos", None)
    # comprobación: las palabras ancla existen en el guion
    segs, _, _ = _parsear(ep); por_escena = {}
    for s in segs: por_escena.setdefault(s["escena"], []).append(re.sub(r"<[^>]+>", " ", s["texto"]).lower())
    def existe(ref):
        if not isinstance(ref, list) or not ref or ref[0] in ("ini", "ancla", "abs"): return True
        esc, pal = ref[0], str(ref[1]); return any(re.sub(r"[^\wáéíóúñ]", "", pal.lower()) in re.sub(r"[^\wáéíóúñ ]", "", t) for t in por_escena.get(esc, []))
    avisos = [k for k, ref in (("tarjeta", j.get("tarjeta", {}).get("ancla")), ("cierre", j.get("cierre", {}).get("ancla")), ("t_corte", j.get("t_corte"))) if ref and not existe(ref)]
    j["_avisos"] = avisos
    escribir_json(p, j)
    if avisos: print("AVISO episodio.json: anclas no encontradas en el guion:", avisos, flush=True)
    return j

SYSTEM_AUDITOR = """Eres un lector-auditor independiente de guiones de terror para el canal Insomnia. NO escribiste este guion y no conoces las intenciones del autor; juzgas solo lo que está en la página, como si el dueño del canal fuera a revisar cada detalle. Tu vara es el guion del episodio 1 (adjunto) y la biblia. Debes devolver SOLO un JSON:
{"aprobado": true|false, "calificacion_global": 1-5, "puntos": [{"punto": "<cada punto de la lista de control §8 de biblia 01, los 11 mecanismos, la lista negra de palabras, la regla 8 de imagen (monstruo demoníaco), formato parseable (### Exx / **NARRACIÓN** / **DIRECCIÓN:**), gancho final con el nombre exacto de la próxima leyenda, duración, Shorts incluidos>", "calificacion": 1-5, "problema": "...", "cambio_concreto": "qué texto cambiar y por qué"}], "resumen": "3 líneas"}
Apruebas solo si TODOS los puntos tienen calificación ≥ 4 y no hay palabras de la lista negra. Sé exigente con el miedo: si el clímax no asusta de verdad, no pasa."""

def auditoria(slug, params):
    """Audita con un lector independiente; corrige con el guionista; máximo 3 rondas. Deja auditoria/ronda_N.json y el veredicto."""
    ep = ruta_episodio(slug); ficha, siguiente = _cola_actual(slug)
    max_rondas = int(params.get("max_rondas", 3)); duracion = float(params.get("duracion_min", 10))
    modelo_guion = leer(os.path.join(C.DATA_DIR, "contenido/videos/01-sombreron/guion.md"))
    system_aud = SYSTEM_AUDITOR + "\n\n===== BIBLIA =====\n" + llm.biblia("01", "02", "04") + "\n\n===== GUION DE REFERENCIA (EP. 1) =====\n" + modelo_guion
    os.makedirs(os.path.join(ep, "auditoria"), exist_ok=True)
    veredicto = None
    for ronda in range(1, max_rondas + 1):
        guion = leer(os.path.join(ep, "guion.md"))
        sig = siguiente["leyenda"] if siguiente else "(ver cola)"
        veredicto = llm.llamar_json(system_aud, f"Próxima leyenda que debe anunciarse al final: «{sig}». Duración objetivo: {duracion} min.\n\nGUION A AUDITAR:\n{guion}", max_tokens=6000, temperatura=0)
        escribir_json(os.path.join(ep, "auditoria", f"ronda_{ronda}.json"), veredicto)
        malos = [p for p in veredicto.get("puntos", []) if int(p.get("calificacion", 0)) < 4]
        print(f"ronda {ronda}: aprobado={veredicto.get('aprobado')} global={veredicto.get('calificacion_global')} puntos<4={len(malos)}", flush=True)
        if veredicto.get("aprobado") and not malos:
            estado_produccion(slug, f"auditoría aprobada en la ronda {ronda} (global {veredicto.get('calificacion_global')})")
            registro(f"{slug} · auditoría · aprobada ronda {ronda}")
            return {"aprobado": True, "ronda": ronda, "resumen": veredicto.get("resumen")}
        if ronda == max_rondas: break
        # corrección por el guionista (que sí conoce la biblia completa)
        system_g = SYSTEM_GUION + "\n\n===== BIBLIA =====\n" + llm.biblia("00", "01", "02", "04", "06")
        cambios = json.dumps(malos, ensure_ascii=False, indent=1)
        nuevo = llm.llamar(system_g, f"Un auditor independiente revisó tu guion y pide estos cambios concretos (JSON):\n{cambios}\n\nResumen del auditor: {veredicto.get('resumen','')}\n\nReescribe el guion COMPLETO aplicando todos los cambios, conservando el formato exacto y todo lo que ya estaba bien. Próxima leyenda a anunciar: «{sig}».\n\nGUION ACTUAL:\n{guion}",
                          modelo=C.ANTHROPIC_MODEL_GUION, max_tokens=24000)
        nuevo = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", nuevo.strip())
        escribir(os.path.join(ep, "auditoria", f"guion_antes_ronda_{ronda + 1}.md"), guion); escribir(os.path.join(ep, "guion.md"), nuevo)
        _parsear(ep)  # falla si rompió el formato
        _episodio_json(slug, ep, ficha, siguiente, {"rehacer": True})
    estado_produccion(slug, f"auditoría NO aprobada tras {max_rondas} rondas: {veredicto.get('resumen','')[:300]}")
    registro(f"{slug} · auditoría · NO aprobada ({max_rondas} rondas)")
    return {"aprobado": False, "ronda": max_rondas, "resumen": veredicto.get("resumen"), "puntos_pendientes": [p for p in veredicto.get("puntos", []) if int(p.get("calificacion", 0)) < 4]}
