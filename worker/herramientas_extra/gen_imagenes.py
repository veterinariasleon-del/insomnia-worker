"""Genera las imágenes de los planos con la API de Gemini (Nano Banana) adjuntando las referencias de personajes,
y las revisa una por una con Claude (visión) contra su prompt y la regla 8; regenera hasta --max-regen veces.
Reemplaza la sesión de Flow por Chrome. Uso:
  python3 gen_imagenes.py <carpeta_episodio> [--solo P01,P05] [--max-regen 2] [--referencias]  (--referencias: solo las fichas de personaje)
Claves: GEMINI_API_KEY (imágenes) y ANTHROPIC_API_KEY (revisión). Salida: imagenes/PXX.jpg, imagenes/qa.json, mezcla/hoja_de_contacto_imagenes.jpg
Última línea: JSON {"ok": bool, "generadas": [...], "revisar": [...], "sin_clave": bool}"""
import json, os, sys, re, io, time, base64, glob

RAIZ = os.environ.get("LEYENDAS_RAIZ", "/data/leyendas")
MODELO = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-nano-banana-2.1"); TAMANO = os.environ.get("GEMINI_IMAGE_SIZE", "2K")
QA_MODELO = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")

def _gemini():
    from google import genai
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])

def generar(cliente, prompt, refs, formato="16:9", tamano=TAMANO):
    """Devuelve bytes PNG/JPEG. Intenta con image_size; si el modelo no lo acepta, sin él."""
    from google.genai import types
    from PIL import Image
    contenido = [prompt] + [Image.open(r) for r in refs]
    for intento in range(3):
        try:
            try:
                cfg = types.GenerateContentConfig(response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio=formato, image_size=tamano))
                r = cliente.models.generate_content(model=MODELO, contents=contenido, config=cfg)
            except Exception as e:
                if "image_size" not in str(e) and "imageSize" not in str(e): raise
                cfg = types.GenerateContentConfig(response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio=formato))
                r = cliente.models.generate_content(model=MODELO, contents=contenido, config=cfg)
            for cand in (r.candidates or []):
                for part in cand.content.parts:
                    if getattr(part, "inline_data", None) and part.inline_data.data: return part.inline_data.data
            raise RuntimeError("la respuesta no trae imagen (¿bloqueo de seguridad?): " + str(r)[:300])
        except Exception as e:
            if intento == 2: raise
            print("  gemini reintento:", str(e)[:200], flush=True); time.sleep(8 * (intento + 1))

def _claude():
    import anthropic
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=3)

REGLAS_QA = """Eres el revisor de ilustraciones de Insomnia. Revisa la imagen contra su prompt y estas reglas de la biblia:
1) estilo pintado (pinceladas, sin contornos de tinta, NO foto, NO 3D, NO caricatura plana); 2) anatomía: número correcto de brazos, manos, dedos, piernas, cabezas; nada duplicado; 3) sin texto, letras, logos ni marcas de agua; 4) un único acento cálido (ámbar/naranja) y el resto frío (azul, pizarra, teal), salvo que el prompt pida luz de día; 5) la composición pedida (qué está dónde, cuarto superior solo cielo si se pide, espacio libre para el narrador si es fondo de narrador); 6) el personaje coincide con su descripción/referencia (ropa, pelo, edad, especie); 7) regla 8: si aparece la figura terrorífica debe verse demoníaca (piel cadavérica gris-verde o calavera, ojos ámbar como único acento cálido, sonrisa demasiado ancha de dientes finos si hay boca, dedos largos de uñas negras, ropa hecha jirones que se funde con la niebla), nunca tierna ni caricatura, sin gore; si el prompt dice que NO debe haber figura, no debe haber ninguna; 8) nada que rompa la lectura (objetos flotando, caras deformes, miradas vacías en personajes humanos).
Responde SOLO JSON: {"aprobada": true|false, "gravedad": "ninguna|leve|grave", "problemas": ["..."], "correccion_prompt": "<frase en inglés para añadir al prompt y evitar el problema, o vacío>"}. Aprueba solo si no hay problemas graves (anatomía, texto, estilo fotográfico, acento cálido múltiple, figura no demoníaca o figura donde no debe haber)."""

def revisar(cl, ruta, prompt):
    from PIL import Image
    im = Image.open(ruta).convert("RGB"); w, h = im.size; k = 1400 / max(w, h)
    if k < 1: im = im.resize((int(w * k), int(h * k)))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
    r = cl.messages.create(model=QA_MODELO, max_tokens=800, temperature=0, system=REGLAS_QA,
                           messages=[{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}},
                                                                   {"type": "text", "text": "PROMPT DE ESTA IMAGEN:\n" + prompt}]}])
    txt = "".join(b.text for b in r.content if b.type == "text")
    m = re.search(r"\{.*\}", txt, re.S)
    try: return json.loads(m.group(0))
    except Exception: return {"aprobada": False, "gravedad": "grave", "problemas": ["respuesta QA ilegible: " + txt[:200]], "correccion_prompt": ""}

def refs_de(ids, ep):
    """Rutas de referencia por id de personaje: personajes/<id>/*_ref.jpg|png, personajes/<id>_ref.jpg; narrador → poses/base_sinrama.png."""
    out = []
    for i in ids:
        cands = glob.glob(os.path.join(RAIZ, "personajes", i, "*_ref.*")) + glob.glob(os.path.join(RAIZ, "personajes", i + "_ref.*")) + glob.glob(os.path.join(RAIZ, "personajes", i, "*_demoniaco_ref.*"))
        if i == "narrador": cands = [os.path.join(RAIZ, "personajes", "narrador", "poses", "base_sinrama.png")] + cands
        cands = [c for c in cands if os.path.exists(c)]
        if cands: out.append(cands[0])
        else: print(f"  aviso: sin referencia para {i}", flush=True)
    return out

def hoja_contacto(ep, planos):
    from PIL import Image, ImageDraw
    rutas = [(p["id"], os.path.join(ep, "imagenes", p["id"] + ".jpg")) for p in planos]
    rutas = [(i, r) for i, r in rutas if os.path.exists(r)]
    if not rutas: return None
    cols = 5; w, h = 384, 216; filas = (len(rutas) + cols - 1) // cols
    hoja = Image.new("RGB", (cols * w, filas * (h + 22)), (12, 16, 28)); d = ImageDraw.Draw(hoja)
    for k, (i, r) in enumerate(rutas):
        im = Image.open(r).convert("RGB").resize((w, h)); x, y = (k % cols) * w, (k // cols) * (h + 22)
        hoja.paste(im, (x, y + 22)); d.text((x + 6, y + 4), i, fill=(230, 230, 240))
    out = os.path.join(ep, "mezcla", "hoja_de_contacto_imagenes.jpg"); os.makedirs(os.path.dirname(out), exist_ok=True); hoja.save(out, quality=85); return out

def main():
    ep = sys.argv[1]; solo = set(sys.argv[sys.argv.index("--solo") + 1].split(",")) if "--solo" in sys.argv else None
    max_regen = int(sys.argv[sys.argv.index("--max-regen") + 1]) if "--max-regen" in sys.argv else 2
    solo_refs = "--referencias" in sys.argv
    esc = json.load(open(os.path.join(ep, "escenas.json"), encoding="utf-8"))
    if not os.environ.get("GEMINI_API_KEY"):
        print(json.dumps({"ok": False, "sin_clave": True, "generadas": [], "revisar": [], "error": "falta GEMINI_API_KEY"})); sys.exit(3)
    gem = _gemini(); cl = _claude() if os.environ.get("ANTHROPIC_API_KEY") else None
    qa_p = os.path.join(ep, "imagenes", "qa.json"); os.makedirs(os.path.join(ep, "imagenes"), exist_ok=True)
    qa = json.load(open(qa_p)) if os.path.exists(qa_p) else {}
    generadas, revisar_h = [], []
    # 1) referencias de personajes nuevos (cuerpo entero, fondo liso) → personajes/<id>/<id>_ref.jpg + ficha
    for r in esc.get("referencias", []):
        if r.get("ref") == "narrador" or r["id"].startswith("narrador"): continue   # el narrador ya tiene poses
        if refs_de([r["id"]], ep) and not (solo and r["id"] in solo): continue
        if solo and r["id"] not in solo and not solo_refs: continue
        carpeta = os.path.join(RAIZ, "personajes", r["id"]); os.makedirs(carpeta, exist_ok=True)
        print("referencia", r["id"], flush=True)
        dest = os.path.join(carpeta, r["id"] + "_ref.jpg"); prompt = r["prompt"]
        for intento in range(max_regen + 1):
            data = generar(gem, prompt, [], r.get("formato", "3:4"))
            from PIL import Image
            Image.open(io.BytesIO(data)).convert("RGB").save(dest, quality=92)
            v = revisar(cl, dest, prompt) if cl else {"aprobada": True, "problemas": []}
            qa["ref_" + r["id"]] = v; print(f"  QA ref {r['id']}: {v.get('aprobada')} {v.get('problemas')}", flush=True)
            if v.get("aprobada"): break
            prompt = r["prompt"] + " " + v.get("correccion_prompt", "")
        if not os.path.exists(os.path.join(carpeta, "ficha.md")):
            open(os.path.join(carpeta, "ficha.md"), "w", encoding="utf-8").write(f"# {r['id']}\n\nReferencia: `{r['id']}_ref.jpg` (generada automáticamente).\n\nPrompt:\n\n{r['prompt']}\n")
        json.dump(qa, open(qa_p, "w"), ensure_ascii=False, indent=1)
    if solo_refs:
        print(json.dumps({"ok": True, "generadas": [], "revisar": [], "sin_clave": False})); return
    # 2) planos
    for p in esc["planos"]:
        pid = p["id"]; dest = os.path.join(ep, "imagenes", pid + ".jpg")
        if solo and pid not in solo: continue
        if os.path.exists(dest) and qa.get(pid, {}).get("aprobada") and not solo: continue
        refs = refs_de(p.get("refs", []), ep); prompt = p["prompt"]
        if refs: prompt = "Use the attached reference image(s) for the character(s)' appearance (same face, clothes, colors), adapted to the pose and scene described. " + prompt
        print(f"{pid} ({p['escena']}) refs={len(refs)}", flush=True)
        ok = False
        for intento in range(max_regen + 1):
            data = generar(gem, prompt, refs, p.get("formato", "16:9"))
            from PIL import Image
            Image.open(io.BytesIO(data)).convert("RGB").save(dest, quality=93)
            v = revisar(cl, dest, p["prompt"]) if cl else {"aprobada": True, "problemas": [], "gravedad": "sin QA"}
            v["intento"] = intento + 1; qa[pid] = v; json.dump(qa, open(qa_p, "w"), ensure_ascii=False, indent=1)
            print(f"  QA: {'OK' if v.get('aprobada') else 'rechazada'} {v.get('gravedad')} {v.get('problemas')}", flush=True)
            if v.get("aprobada"): ok = True; break
            prompt = prompt + " IMPORTANT FIX: " + (v.get("correccion_prompt") or "; ".join(v.get("problemas", [])))
        generadas.append(pid)
        if not ok: revisar_h.append(pid)
    hoja = hoja_contacto(ep, esc["planos"])
    print(json.dumps({"ok": not revisar_h, "generadas": generadas, "revisar": revisar_h, "sin_clave": False, "hoja": hoja}, ensure_ascii=False))

if __name__ == "__main__": main()
