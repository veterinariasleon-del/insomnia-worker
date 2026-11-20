"""Etapa prueba_corta: el experimento de ~2 minutos que pidió Jaime. Corre TODAS las etapas en orden sobre un episodio de
prueba fuera de la cola (contenido/pruebas/00-prueba), con los mismos frenos y verificaciones que un episodio real, y deja
el resultado en publicar_pruebas/. Se detiene en la primera etapa que falle y deja dicho dónde.
params: leyenda, region, regla, nota (ficha de la mini-leyenda; si faltan, usa una de prueba), siguiente (leyenda que se
anuncia al final; por defecto el episodio actual de la cola), duracion_min (2), desde (etapa desde la que reanudar)."""
import os, json, time
from .. import config as C
from ..util import ruta_episodio, escribir_json, leer_json, estado_produccion, registro, py

ORDEN = ["guion", "auditoria", "freno_creditos", "voz", "sfx", "partitura", "mezcla", "planos", "imagenes", "render", "miniatura", "textos", "shorts", "publicar"]

FICHA_DEFECTO = {"leyenda": "El Perro del Camposanto", "region": "Villaflores, Chiapas",
                 "regla": "Si un perro negro te acompaña de noche por el camino del panteón, no lo llames por ningún nombre.",
                 "nota": "Testigo: Ramiro, 19 años, regresa de una fiesta a pie. Mecanismos: regla y ruptura, hora exacta (1:40), detalle físico (huellas de patas que terminan en huellas de pies descalzos). Final: el perro responde al nombre con voz de persona. Versión corta de 2 minutos."}

def prueba_corta(slug, params):
    from . import guion as G, audio as A, imagen as I, video as V, textos as T, publicar as P
    slug = slug if slug.startswith("00-") else "00-prueba"
    ep = ruta_episodio(slug); os.makedirs(ep, exist_ok=True)
    ficha = {**FICHA_DEFECTO, **{k: params[k] for k in ("leyenda", "region", "regla", "nota") if params.get(k)}}
    dur = float(params.get("duracion_min", 2))
    try:
        r = py("cola.py", "siguiente", check=False); cola = json.loads(r.stdout[r.stdout.find("{"):])
    except Exception: cola = {}
    siguiente = params.get("siguiente") or (cola.get("actual") or {}).get("leyenda") or "La Tisigua"
    epj_p = os.path.join(ep, "episodio.json")
    if not os.path.exists(epj_p): escribir_json(epj_p, {"numero": 0, "slug": slug, "leyenda": ficha["leyenda"], "titulo_youtube": "", "mezcla": "mezcla/episodio00_v2.mp3"})
    etapas = {
        "guion": lambda: G.guion(slug, {**ficha, "siguiente": siguiente, "duracion_min": dur, "indicaciones": "Es un EPISODIO CORTO DE PRUEBA de ~2 minutos (3–5 escenas): gancho, regla, una sola escalada, clímax y cierre con el gancho a la próxima leyenda. Incluye los dos guiones de Shorts aunque sean de 20 s."}),
        "auditoria": lambda: G.auditoria(slug, {"duracion_min": dur, "max_rondas": 2}),
        "freno_creditos": lambda: A.freno_creditos(slug, {"colchon": 1500}),
        "voz": lambda: A.voz(slug, {}), "sfx": lambda: A.sfx(slug, {}), "partitura": lambda: A.partitura(slug, {}), "mezcla": lambda: A.mezcla(slug, {}),
        "planos": lambda: I.planos(slug, {}), "imagenes": lambda: I.imagenes(slug, {}),
        "render": lambda: V.render(slug, {"snapshots": 6}), "miniatura": lambda: V.miniatura(slug, {}),
        "textos": lambda: T.textos(slug, {"prueba": True}), "shorts": lambda: V.shorts(slug, {}),
        "publicar": lambda: P.publicar(slug, {"prueba": True, "permitir_incompleta": True}),
    }
    desde = params.get("desde", "guion"); resultados = {}; t0 = time.time()
    for etapa in ORDEN[ORDEN.index(desde):]:
        ti = time.time(); print(f"\n===== PRUEBA · etapa {etapa} =====", flush=True)
        try: res = etapas[etapa]()
        except Exception as e:
            resultados[etapa] = {"error": f"{type(e).__name__}: {e}"[:600]}
            estado_produccion(slug, f"PRUEBA detenida en {etapa}: {e}")
            registro(f"{slug} · prueba · detenida en {etapa} · {str(e)[:150]}")
            return {"ok": False, "detenida_en": etapa, "resultados": resultados, "minutos": round((time.time() - t0) / 60, 1), "reanudar": f"POST /jobs {{etapa: prueba_corta, slug: {slug}, params: {{desde: '{etapa}'}}}}"}
        resultados[etapa] = {**(res if isinstance(res, dict) else {"res": res}), "segundos": round(time.time() - ti)}
        if etapa == "freno_creditos" and not res.get("alcanza"):
            return {"ok": False, "detenida_en": etapa, "resultados": resultados, "motivo": f"faltan {res.get('faltan')} créditos de ElevenLabs"}
        if etapa == "auditoria" and not res.get("aprobado"):
            return {"ok": False, "detenida_en": etapa, "resultados": resultados, "motivo": "la auditoría no aprobó el guion de prueba"}
        if etapa == "shorts" and not res.get("ok", True): print("shorts incompletos (no bloquea la prueba):", res, flush=True)
    registro(f"{slug} · prueba · completa en {round((time.time() - t0) / 60, 1)} min")
    pub = resultados.get("publicar", {})
    return {"ok": True, "resultados": resultados, "minutos": round((time.time() - t0) / 60, 1), "video": resultados.get("render", {}).get("mp4"), "publicar_pruebas": pub.get("carpetas")}
