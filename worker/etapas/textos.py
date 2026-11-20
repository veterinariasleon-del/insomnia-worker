"""Etapa textos: youtube.txt del episodio largo (modelo: ep. 1), y youtube.txt + tiktok.txt de los dos Shorts (§2),
con las fechas de estreno de §6. El modelo decide títulos, descripción, etiquetas, cortes de anuncios y pies; los
capítulos, la duración, las fechas y el correo del canal se calculan aquí. Híbrido: params["texto"] con el JSON de
textos ya escrito (o el youtube.txt completo del largo)."""
import os, re, json, datetime
from .. import config as C, llm, fechas as F
from ..util import ruta_episodio, leer, escribir, leer_json, escribir_json, estado_produccion, registro, ffprobe_dur, extraer_json

def _mmss(t): t = int(round(t)); return f"{t // 60:02d}:{t % 60:02d}"

def _titulos_escenas(ep):
    md = leer(os.path.join(ep, "guion.md"))
    return dict(re.findall(r"### (E\d\d) · ([^\n]*)", md))

def _capitulos(ep, total):
    """Un capítulo por escena (inicio del primer trozo), el primero en 00:00, ninguno menor de 10 s."""
    T = leer_json(os.path.join(ep, "mezcla", "tiempos_v2.json"), {"trozos": []}); titulos = _titulos_escenas(ep)
    inicios = {}
    for tr in T["trozos"]: inicios.setdefault(tr["escena"], tr["inicio"])
    caps = sorted((t, titulos.get(e, e)) for e, t in inicios.items())
    out = []
    for t, tit in caps:
        t = 0.0 if not out else t
        if out and t - out[-1][0] < 10: continue
        out.append((t, re.sub(r"\s*\(.*?\)\s*", " ", tit).strip(" ·-—")))
    return [(_mmss(t), tit) for t, tit in out]

def _correo():
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", leer(os.path.join(C.DATA_DIR, "perfil", "datos-del-perfil.md")))
    return m.group(0) if m else "[CORREO DEL CANAL — pendiente de crear]"

def _secciones_guion(ep):
    g = leer(os.path.join(ep, "guion.md")); out = {}
    for k, pat in (("titulos", r"## Títulos.*?(?=\n## |\Z)"), ("miniatura", r"## Miniatura.*?(?=\n## |\Z)"), ("shorts", r"## Shorts.*?(?=\n## |\Z)")):
        m = re.search(pat, g, re.S); out[k] = m.group(0) if m else ""
    out["narracion"] = re.sub(r"\*\*(DIRECCIÓN|SONIDO|IMAGEN|EFECTOS):?\*\*:?.*?(?=\n\*\*|\n### |\n## |\Z)", "", g, flags=re.S)[:14000]
    return out

SYSTEM = """Eres el editor de publicación de Insomnia (YouTube + TikTok), canal de leyendas de terror de México narradas por Don Tecolote. Escribes en español de México, directo, sin spoilers del final. Reglas de YouTube que aplicas: título ≤ 60 caracteres con la promesa o la regla rota + " | Leyenda de <región>"; las dos primeras líneas de la descripción se ven sin abrir "más" (gancho concreto con lugar, fecha y regla); etiquetas en minúsculas separadas por comas, ≤ 480 caracteres en total, de lo más específico a lo general; no declarar contenido de IA (es animación evidente); anuncios a mitad solo si el video dura ≥ 8 min, colocados A MANO en cortes naturales (antes de la primera noche y en el amanecer tras el clímax; nunca dentro del clímax). Shorts: título ≤ 60 con la promesa; descripción de 3 líneas que incluya "Episodio completo: <título del largo>"; hashtags #Shorts #Leyendas #Terror #<Leyenda>. TikTok: pie de 1–2 líneas con gancho en la primera, 3–5 hashtags en español, sin enlaces ni menciones a YouTube por nombre ("la historia completa está en el canal"). Respondes SOLO con el JSON pedido."""

def _json_textos(ep, slug, hechos):
    secs = _secciones_guion(ep)
    usuario = f"""HECHOS (no los cambies):
{json.dumps(hechos, ensure_ascii=False, indent=1)}

SECCIONES DEL GUION:
{secs['titulos']}
{secs['miniatura']}
{secs['shorts']}

NARRACIÓN (para el gancho y la descripción):
{secs['narracion']}

Devuelve este JSON:
{{"titulo": "...", "alternos": ["...", "..."], "gancho": "dos líneas, la regla y quién la rompió (lugar, fecha)", "cuerpo": "un párrafo: qué cuenta Don Tecolote, con nombres, horas y lo que quedó al amanecer, sin el final",
 "pregunta": "pregunta para comentarios (misma del cierre del guion si la hay)", "proxima": "una frase sobre la próxima leyenda ({hechos['proxima_leyenda']})", "lugar_ficticio": "qué nombres/lugares son ficticios en este guion",
 "hashtags": ["#Leyendas", "#Terror", "#<Región>", "#<Leyenda>", "#HistoriasDeTerror"], "etiquetas": ["etiqueta 1", "... hasta 25"], "midrolls": ["mm:ss", "mm:ss"] (vacío si dura menos de 8 min), "primer_comentario": "...",
 "lista_reproduccion": "Leyendas de <región>", "tema": "<monstruo o leyenda en una palabra>", "gancho_tipo": "regla rota|objeto|hora exacta|testigo",
 "shorts": {{"avance": {{"titulo": "...", "descripcion": "3 líneas", "hashtags": "#Shorts #Leyendas #Terror #<Leyenda>", "tiktok_pie": "1–2 líneas", "tiktok_hashtags": "#leyendas #terror #<region> #<leyenda> #historiasdeterror"}},
            "regla": {{"titulo": "...", "descripcion": "3 líneas", "hashtags": "...", "tiktok_pie": "...", "tiktok_hashtags": "..."}}}}}}"""
    return llm.llamar_json(SYSTEM, usuario, max_tokens=4000, temperatura=0.4)

def _youtube_largo(ep, slug, j, hechos):
    caps = "\n".join(f"{t} {tit}" for t, tit in hechos["capitulos"])
    etiquetas = ", ".join(j.get("etiquetas", []))
    while len(etiquetas) > 490 and ", " in etiquetas: etiquetas = etiquetas.rsplit(", ", 1)[0]
    mid = j.get("midrolls") or []
    n = hechos["numero"]; dur = hechos["duracion_s"]
    return f"""INSOMNIA · Episodio {n} · {hechos['leyenda']} — datos para subir a YouTube
=====================================================================
Archivo de video:   episodio{n:02d}.mp4  (1920×1080, 30 fps, {_mmss(dur)})
Miniatura:          miniatura.jpg   (2560×1440)
Fecha de preparación: {hechos['hoy']}
ESTRENO PROGRAMADO: {hechos['estreno_largo_legible']}  (carpeta en publicar/: la fecha del nombre manda)


TÍTULO (principal, {len(j['titulo'])} caracteres)
---------------------------------
{j['titulo']}

Títulos alternos para "Probar y comparar" (Studio permite hasta 3; gana el que más tiempo de visualización logre, no el de más clics):
2) {j['alternos'][0] if j.get('alternos') else ''}
3) {j['alternos'][1] if len(j.get('alternos', [])) > 1 else ''}


DESCRIPCIÓN (copiar completa; las dos primeras líneas son las que se ven sin abrir "más")
----------------------------------------------------------------------------------------
{j['gancho']}

{j['cuerpo']}

🕯️ CAPÍTULOS
{caps}

📩 {j.get('pregunta', '¿En tu pueblo cómo le dicen? Cuéntamelo en los comentarios.')} Si tienes una historia que te pasó a ti o a tu familia, mándamela a: {hechos['correo']}

🦉 Próxima leyenda: {j.get('proxima', hechos['proxima_leyenda'])} Suscríbete y activa la campanita antes de que baje la niebla.

Esta es una leyenda popular recreada: {j.get('lugar_ficticio', 'los nombres, lugares y fechas son ficticios')}; cualquier parecido con personas reales es coincidencia. Guion, narración, ilustraciones y animación son producción original de Insomnia, con apoyo de herramientas de inteligencia artificial. Contenido para mayores de 13 años: terror atmosférico, sin violencia gráfica.

{' '.join(j.get('hashtags', []))}


ETIQUETAS (pegar en "Etiquetas", separadas por comas; {len(etiquetas)} caracteres, el límite es 500)
--------------------------------------------------------------------------------------
{etiquetas}


AJUSTES AL SUBIR (en el orden en que los pregunta YouTube Studio)
-----------------------------------------------------------------
Detalles
- Título y descripción: los de arriba.
- Miniatura: subir miniatura.jpg.
- Lista de reproducción: "{j.get('lista_reproduccion', 'Leyendas de México')}" (crearla si no existe) y agregar el video.
- Audiencia: "No, no es contenido creado para niños". Restricción de edad: NO (es terror atmosférico; restringirlo a 18+ mata el alcance).
- Uso de IA / contenido alterado: NO marcar. Es animación evidente con un personaje propio; YouTube solo pide declararlo cuando algo parece real.
- Mostrar más → Etiquetas: las de arriba.
- Idioma del video: Español (Latinoamérica). Certificación de subtítulos: Ninguna.
- Fecha de grabación: dejar vacía. Ubicación: {hechos.get('region', '')} (opcional).
- Licencia: Licencia estándar de YouTube. Permitir inserción: sí. Publicar en feed de suscripciones: sí.
- Categoría: Entretenimiento.
- Comentarios: Permitir todos (moderar los que puedan ser inapropiados). Mostrar cuántos "me gusta": sí.

Elementos del video
- Subtítulos: YouTube los autogenera en español.
- Pantalla final: el video ya trae la pantalla final dibujada desde {hechos['cierre_mmss']} (hueco izquierdo = video, círculo derecho = suscribirse). Colocar encima: "Video: el más reciente" en el rectángulo izquierdo y "Suscribirse" en el círculo derecho; duración de {hechos['cierre_mmss']} a {_mmss(dur)}.
- Tarjetas: ninguna por ahora.

Monetización (cuando el canal esté en el Programa de Socios)
- {('El video dura más de 8 min: permitir anuncios a mitad, colocados A MANO (no automáticos) en ' + ' y '.join(mid) + '. Nunca dentro del clímax.') if mid else 'El video dura menos de 8 min: sin anuncios a mitad.'}

Visibilidad
- Programar como "Público" para el {hechos['estreno_largo_legible']} (hora del centro de México). No usar "Estreno" hasta tener comunidad.

Después de publicar
- Fijar el primer comentario: "{j.get('primer_comentario', '')}"
- A los 7 días: revisar en Studio el CTR de la miniatura y la retención a los 30 s; si el CTR queda por debajo del 4 %, activar "Probar y comparar" con los títulos 2 y 3.
- Registro para analitica/videos.csv → tema: {j.get('tema', hechos['leyenda'])}; formato: largo; gancho: {j.get('gancho_tipo', '')}; duración: {int(dur)} s; estreno: {hechos['estreno_largo']}.
"""

def _youtube_short(tipo, s, hechos):
    fecha = hechos["estreno_avance_legible"] if tipo == "avance" else hechos["estreno_regla_legible"]
    nombre = "AVANCE" if tipo == "avance" else "LA REGLA"
    return f"""INSOMNIA · Short {nombre} · {hechos['leyenda']} — datos para subir a YouTube (Shorts)
=====================================================================
Archivo: short.mp4 (1080×1920). Miniatura: miniatura.jpg (9:16; en YouTube se sube desde Studio en computadora).
ESTRENO PROGRAMADO: {fecha}

TÍTULO ({len(s['titulo'])} caracteres)
{s['titulo']}

DESCRIPCIÓN
{s['descripcion']}
Episodio completo: {hechos['titulo_largo']}
{s['hashtags']}

AJUSTES
- Video relacionado: el episodio largo "{hechos['titulo_largo']}" (y pega su enlace en la descripción cuando esté publicado).
- Audiencia: no es para niños. Sin restricción de edad. No marcar contenido de IA.
- Programar como Público: {fecha}.
"""

def _tiktok(tipo, s, hechos):
    fecha = hechos["estreno_avance_legible"] if tipo == "avance" else hechos["estreno_regla_legible"]
    return f"""INSOMNIA · TikTok · Short {'AVANCE' if tipo == 'avance' else 'LA REGLA'} · {hechos['leyenda']}
Archivo: el mismo short.mp4. Portada: miniatura.jpg. Programar: {fecha}.

PIE (copiar tal cual)
{s['tiktok_pie']}
{s['tiktok_hashtags']}

AJUSTES: permitir comentarios; permitir dúo/pegar: sí; sin enlaces; marcar "contenido generado con IA" solo si TikTok lo exige al subir.
"""

def textos(slug, params):
    ep = ruta_episodio(slug); epj = leer_json(os.path.join(ep, "episodio.json"), {}); prueba = bool(params.get("prueba")) or slug.startswith("00-")
    hoy = datetime.date.fromisoformat(params["hoy"]) if params.get("hoy") else datetime.date.today()
    fe = F.fechas_piezas(slug, hoy, F.carpeta_publicar(prueba))
    mezcla = os.path.join(ep, epj.get("mezcla", "")); dur = ffprobe_dur(mezcla) if os.path.exists(mezcla) else float(leer_json(os.path.join(ep, "mezcla", "tiempos_v2.json"), {}).get("total", 0))
    T = leer_json(os.path.join(ep, "mezcla", "tiempos_v2.json"), {"anclas": {}})
    cierre_t = T.get("anclas", {}).get("cierre"); cierre_t = cierre_t + float(epj.get("cierre", {}).get("delta", 2.0)) if cierre_t is not None else max(0.0, dur - 60)
    from .guion import _cola_actual
    ficha, siguiente = _cola_actual(slug)
    hechos = {"numero": int(epj.get("numero", 0)), "slug": slug, "leyenda": epj.get("leyenda", ficha.get("leyenda", slug)), "region": ficha.get("region", ""), "duracion_s": round(dur, 1),
              "capitulos": _capitulos(ep, dur), "cierre_mmss": _mmss(cierre_t), "correo": _correo(), "hoy": hoy.isoformat(),
              "proxima_leyenda": epj.get("cierre", {}).get("siguiente") or (siguiente or {}).get("leyenda", ""),
              "estreno_largo": fe["EPISODIO"].isoformat(), "estreno_largo_legible": F.fecha_legible(fe["EPISODIO"]),
              "estreno_avance_legible": F.fecha_legible(fe["SHORT-AVANCE"]), "estreno_regla_legible": F.fecha_legible(fe["SHORT-REGLA"])}
    dado = llm.texto_dado(params); j = None
    if dado:
        try: j = extraer_json(dado)
        except ValueError:
            escribir(os.path.join(ep, "youtube.txt"), dado); j = None
    jp = os.path.join(ep, "textos.json")
    if j is None and os.path.exists(jp) and not params.get("rehacer"): j = leer_json(jp)
    if j is None and not (dado and not params.get("rehacer")): j = _json_textos(ep, slug, hechos)
    if j:
        j["titulo"] = j.get("titulo", "")[:100]; escribir_json(jp, j); hechos["titulo_largo"] = j["titulo"]
        escribir(os.path.join(ep, "youtube.txt"), _youtube_largo(ep, slug, j, hechos))
        epj["titulo_youtube"] = j["titulo"]; escribir_json(os.path.join(ep, "episodio.json"), epj)
        for tipo in ("avance", "regla"):
            s = (j.get("shorts") or {}).get(tipo)
            if not s: continue
            carpeta = os.path.join(ep, "shorts", tipo); os.makedirs(carpeta, exist_ok=True)
            escribir(os.path.join(carpeta, "youtube.txt"), _youtube_short(tipo, s, hechos)); escribir(os.path.join(carpeta, "tiktok.txt"), _tiktok(tipo, s, hechos))
    estado_produccion(slug, f"textos: youtube.txt («{hechos.get('titulo_largo', '')}») + Shorts; estreno largo {hechos['estreno_largo']} ({fe['motivo']})")
    registro(f"{slug} · textos · estreno {hechos['estreno_largo']}")
    return {"titulo": hechos.get("titulo_largo"), "capitulos": len(hechos["capitulos"]), "estrenos": {k: v.isoformat() for k, v in fe.items() if k != "motivo"}, "motivo": fe["motivo"]}
