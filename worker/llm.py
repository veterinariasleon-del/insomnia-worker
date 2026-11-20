"""Llamadas a Claude (API de Anthropic) con la biblia del proyecto como contexto.
Cada etapa que usa el modelo acepta también texto ya escrito (params["texto"] o params["archivo_texto"]) para la
variante híbrida: entonces no se llama a la API. La clave solo vive en ANTHROPIC_API_KEY."""
import os, base64, json, time
from . import config as C
from .util import leer, extraer_json

_cliente = None
def cliente():
    global _cliente
    if _cliente is None:
        if not C.ANTHROPIC_API_KEY: raise RuntimeError("sin clave: falta ANTHROPIC_API_KEY (o manda el texto ya escrito en params.texto)")
        import anthropic
        _cliente = anthropic.Anthropic(api_key=C.ANTHROPIC_API_KEY, max_retries=3)
    return _cliente

def biblia(*nombres):
    """Concatena archivos de la biblia por prefijo numérico o nombre: biblia("00","01","04") o biblia("01-guion.md")."""
    carpeta = os.path.join(C.DATA_DIR, "biblia"); partes = []
    for n in nombres:
        cands = [f for f in sorted(os.listdir(carpeta)) if f.startswith(n)]
        for f in cands: partes.append(f"<<< biblia/{f} >>>\n" + leer(os.path.join(carpeta, f)))
    return "\n\n".join(partes)

def aprendizajes():
    return leer(os.path.join(C.DATA_DIR, "biblia", "08-aprendizajes.md"), "(todavía no hay aprendizajes registrados)")

def _img_block(path, max_lado=1400):
    from PIL import Image
    import io
    im = Image.open(path).convert("RGB"); w, h = im.size; k = max_lado / max(w, h)
    if k < 1: im = im.resize((int(w * k), int(h * k)))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}

def llamar(system, usuario, modelo=None, max_tokens=8000, imagenes=None, cache_system=True, tools=None, temperatura=None):
    """Devuelve el texto de la respuesta. `imagenes`: rutas de archivos que se adjuntan antes del texto del usuario."""
    cl = cliente(); modelo = modelo or C.ANTHROPIC_MODEL
    contenido = [_img_block(p) for p in (imagenes or [])] + [{"type": "text", "text": usuario}]
    sys_block = [{"type": "text", "text": system}]
    if cache_system and len(system) > 4000: sys_block[0]["cache_control"] = {"type": "ephemeral"}
    kw = {"model": modelo, "max_tokens": max_tokens, "system": sys_block, "messages": [{"role": "user", "content": contenido}]}
    if temperatura is not None: kw["temperature"] = temperatura
    if tools: kw["tools"] = tools
    for intento in range(3):
        try:
            if max_tokens > 16000:
                with cl.messages.stream(**kw) as s: r = s.get_final_message()
            else: r = cl.messages.create(**kw)
            break
        except Exception as e:
            if intento == 2: raise
            print("API reintento:", e, flush=True); time.sleep(10 * (intento + 1))
    textos = [b.text for b in r.content if getattr(b, "type", "") == "text"]
    uso = getattr(r, "usage", None)
    if uso: print(f"[tokens] {modelo} entrada={uso.input_tokens} salida={uso.output_tokens}", flush=True)
    return "\n".join(textos)

def llamar_json(system, usuario, **kw):
    """Como llamar(), pero exige JSON en la respuesta (reintenta una vez pidiendo solo JSON)."""
    txt = llamar(system, usuario, **kw)
    try: return extraer_json(txt)
    except ValueError:
        txt = llamar(system, usuario + "\n\nRESPONDE ÚNICAMENTE CON EL JSON PEDIDO, sin texto alrededor.", **kw)
        return extraer_json(txt)

def texto_dado(params):
    """Texto ya escrito por fuera (híbrido): params["texto"] o params["archivo_texto"] (ruta relativa al volumen)."""
    if params.get("texto"): return params["texto"]
    if params.get("archivo_texto"):
        p = params["archivo_texto"]; p = p if os.path.isabs(p) else os.path.join(C.DATA_DIR, p)
        return leer(p)
    return None
