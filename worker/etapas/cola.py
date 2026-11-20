"""Etapas de la cola: estado_cola (cambio explícito de estado con cola.py), reposicion (§3: 4 leyendas nuevas investigadas
en la web, como `pendiente`) y ping (diagnóstico del contenedor)."""
import os, json, shutil, subprocess, datetime
from .. import config as C, llm
from ..util import leer_json, escribir_json, py, registro, estado_produccion, correr

ORDEN = ["pendiente", "guion", "guion_aprobado", "voz", "sfx", "mezcla", "planos", "imagenes", "render", "entregado", "publicado", "propuesta"]

def estado_cola(slug, params):
    """params: estado = nuevo estado; forzar = permite retroceder. Nunca lo hace una etapa por su cuenta: lo pide n8n."""
    nuevo = params["estado"]
    if nuevo not in ORDEN: raise ValueError(f"estado inválido {nuevo}; válidos: {ORDEN}")
    cola = leer_json(os.path.join(C.DATA_DIR, "cola", "cola.json"), {"episodios": []})
    actual = next((e for e in cola["episodios"] if e["slug"] == slug), None)
    if actual is None:
        if slug.startswith("00-"): return {"ok": True, "nota": "las pruebas no están en la cola; no se cambia nada"}
        raise ValueError(f"{slug} no está en la cola")
    if ORDEN.index(nuevo) < ORDEN.index(actual["estado"]) and not params.get("forzar"):
        raise ValueError(f"{slug} está en {actual['estado']}; pasar a {nuevo} sería retroceder (manda forzar=true si es a propósito)")
    py("cola.py", "estado", slug, nuevo)
    registro(f"{slug} · estado {actual['estado']} → {nuevo}")
    return {"ok": True, "antes": actual["estado"], "ahora": nuevo}

SYSTEM_REPO = """Eres el investigador de Insomnia (leyendas de terror de México animadas). Debes proponer 4 leyendas nuevas para la cola, siguiendo §3: prioridad Chiapas, Oaxaca, Tabasco, Veracruz y Yucatán (después el resto de México); poco producidas en video en YouTube; con una REGLA clara del lugar (lo que no se debe hacer) y una RUPTURA posible (alguien la rompe); sin repetir el monstruo ni el mecanismo central de los últimos 4 episodios de la cola; tomando en cuenta los aprendizajes de analítica si los hay. Usa la búsqueda web para confirmar que la leyenda existe en la tradición de esa región y para recoger detalles concretos (lugar, versión local, detalle físico que queda como prueba). Responde SOLO JSON:
{"leyendas": [{"leyenda": "Nombre", "slug_base": "nombre-corto-sin-acentos", "region": "Municipio o zona, Estado", "regla": "una frase", "nota": "testigo sugerido; final; mecanismos (biblia 01) a usar; detalle físico; fuentes consultadas", "monstruo": "...", "mecanismo": "..."}]}"""

def reposicion(slug, params):
    """Añade 4 leyendas `pendiente` al final de cola.json con numeración consecutiva y regenera cola.md."""
    cola_p = os.path.join(C.DATA_DIR, "cola", "cola.json"); cola = leer_json(cola_p, {"episodios": []})
    pendientes = sum(1 for e in cola["episodios"] if e["estado"] == "pendiente")
    if pendientes >= int(params.get("umbral", 4)) and not params.get("forzar"): return {"ok": True, "nota": f"hay {pendientes} pendientes; no hace falta reponer"}
    ultimos = cola["episodios"][-8:]
    dado = llm.texto_dado(params)
    if dado:
        from ..util import extraer_json; j = extraer_json(dado)
    else:
        usuario = ("COLA ACTUAL (no repitas monstruo ni mecanismo de los últimos 4):\n" + json.dumps([{k: e.get(k) for k in ("n", "leyenda", "region", "regla", "nota", "estado")} for e in ultimos], ensure_ascii=False, indent=1) +
                   "\n\nAPRENDIZAJES DE ANALÍTICA:\n" + llm.aprendizajes() + "\n\nCRITERIOS DE GUION (biblia 01, para elegir leyendas con buen potencial):\n" + llm.biblia("01")[:6000])
        tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": int(params.get("busquedas", 8))}]
        try: j = llm.llamar_json(SYSTEM_REPO, usuario, modelo=C.ANTHROPIC_MODEL_GUION, max_tokens=6000, tools=tools)
        except Exception as e:
            print("búsqueda web no disponible, se investiga sin ella:", str(e)[:200], flush=True)
            j = llm.llamar_json(SYSTEM_REPO.replace("Usa la búsqueda web", "Usa tu conocimiento (sin inventar lugares)"), usuario, modelo=C.ANTHROPIC_MODEL_GUION, max_tokens=6000)
    n0 = max((e["n"] for e in cola["episodios"]), default=0); nuevas = []
    slugs = {e["slug"] for e in cola["episodios"]}
    for i, l in enumerate(j.get("leyendas", [])[:4], 1):
        n = n0 + i; base = l.get("slug_base") or l["leyenda"].lower().replace(" ", "-")
        slug_n = f"{n:02d}-{base}"
        if slug_n in slugs: continue
        e = {"n": n, "slug": slug_n, "leyenda": l["leyenda"], "region": l.get("region", ""), "regla": l.get("regla", ""), "nota": l.get("nota", ""), "estado": "pendiente",
             "fechas": {"pendiente": datetime.date.today().isoformat()}, "youtube_url": ""}
        cola["episodios"].append(e); nuevas.append(e)
    shutil.copy(cola_p, cola_p + f".bak-{datetime.date.today().isoformat()}")
    escribir_json(cola_p, cola); py("cola.py", "tabla")
    registro("reposición · " + ", ".join(f"{e['n']} {e['leyenda']}" for e in nuevas))
    return {"ok": True, "agregadas": [{k: e[k] for k in ("n", "slug", "leyenda", "region", "regla")} for e in nuevas], "pendientes_ahora": pendientes + len(nuevas)}

def ping(slug, params):
    """Diagnóstico: herramientas presentes, versiones, caché de modelos, espacio en disco."""
    def ver(cmd):
        try: return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip().splitlines()[0]
        except Exception as e: return f"no: {e}"
    du = shutil.disk_usage(C.DATA_DIR)
    hf_cache = os.path.join(os.path.expanduser("~"), ".cache", "hyperframes", "chrome")
    return {"version": C.VERSION, "python": ver(["python3", "--version"]), "node": ver(["node", "--version"]), "ffmpeg": ver(["ffmpeg", "-version"]),
            "hyperframes_cache_npm": os.path.isdir(os.path.expanduser("~/.npm/_npx")), "chrome_headless_shell": os.path.isdir(hf_cache) and bool(os.listdir(hf_cache)),
            "modelos_hf": os.listdir(os.path.join(C.CACHE_DIR, "hf")) if os.path.isdir(os.path.join(C.CACHE_DIR, "hf")) else [],
            "claves": C.claves_presentes(), "disco_gb_libres": round(du.free / 1e9, 1), "data_dir": C.DATA_DIR,
            "carpetas": {d: os.path.isdir(os.path.join(C.DATA_DIR, d)) for d in ("biblia", "marca", "personajes", "herramientas", "cola", "contenido", "publicar", "analitica")},
            "hora": datetime.datetime.now().isoformat(timespec="seconds")}
