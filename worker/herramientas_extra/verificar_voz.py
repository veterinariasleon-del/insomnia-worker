"""Verifica cada trozo de voz con faster-whisper: transcribe y compara con el texto pedido (similitud ≥ umbral),
y detecta etiquetas pronunciadas ("whispers", "break", "normal tone"). Escribe voz/verificacion.json.
Uso: python3 verificar_voz.py <carpeta_voz> [umbral=0.88] [--solo 3,7]
Salida (stdout, última línea): JSON {"ok": bool, "fallan": [i,...], "detalle": {...}}"""
import json, os, sys, re, difflib

def norm(t):
    t = re.sub(r"<[^>]+>|\[[^\]]+\]", " ", t).lower()
    t = re.sub(r"[^\wáéíóúñü ]", " ", t); return re.sub(r"\s+", " ", t).strip()

def main():
    carpeta = sys.argv[1]; umbral = float(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else 0.88
    solo = set(int(x) for x in sys.argv[sys.argv.index("--solo") + 1].split(",")) if "--solo" in sys.argv else None
    from faster_whisper import WhisperModel
    modelo = WhisperModel(os.environ.get("WHISPER_MODELO", "small"), device="cpu", compute_type="int8", download_root=os.environ.get("HF_HOME"))
    chunks = json.load(open(os.path.join(carpeta, "chunks.json")))
    prev_p = os.path.join(carpeta, "verificacion.json"); prev = json.load(open(prev_p)) if os.path.exists(prev_p) else {"detalle": {}}
    detalle = prev.get("detalle", {}); fallan = []
    for i, c in enumerate(chunks):
        if solo is not None and i not in solo:
            if not detalle.get(str(i), {}).get("ok", False): fallan.append(i)
            continue
        wav = os.path.join(carpeta, f"chunk_{i:02d}.wav")
        if not os.path.exists(wav): detalle[str(i)] = {"ok": False, "motivo": "falta el wav"}; fallan.append(i); continue
        segs, _ = modelo.transcribe(wav, language="es", beam_size=5, vad_filter=False)
        trans = " ".join(s.text for s in segs)
        esperado = norm(c["texto"]); obtenido = norm(trans)
        sim = difflib.SequenceMatcher(None, esperado, obtenido).ratio()
        etiquetas = [w for w in ("whispers", "whisper", "normal tone", "break time", "break") if w in obtenido and w not in esperado]
        ok = sim >= umbral and not etiquetas
        detalle[str(i)] = {"ok": ok, "similitud": round(sim, 3), "etiquetas_pronunciadas": etiquetas, "transcripcion": trans[:400]}
        print(f"{i:02d} {'OK ' if ok else 'MAL'} sim={sim:.3f} {('etiquetas: ' + str(etiquetas)) if etiquetas else ''}", flush=True)
        if not ok: fallan.append(i)
    res = {"ok": not fallan, "umbral": umbral, "fallan": sorted(set(fallan)), "detalle": detalle}
    json.dump(res, open(prev_p, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({"ok": res["ok"], "fallan": res["fallan"]}))

if __name__ == "__main__": main()
