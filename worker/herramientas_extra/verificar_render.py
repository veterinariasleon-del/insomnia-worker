"""Verifica el MP4 final: duración = mezcla (±0.5 s), -14 LUFS (±1), desfase audio/mezcla = 0 (correlación), 14 fotogramas
extraídos a video/verificacion/ (con luminancia; los negros se cotejan con los cortes a negro del montaje).
Uso: python3 verificar_render.py <carpeta_episodio> [--frames 14]
Última línea: JSON {"ok": bool, "duracion": ..., "lufs": ..., "desfase_s": ..., "frames": [...], "problemas": [...]}"""
import json, os, sys, subprocess, re
import numpy as np

def dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p], capture_output=True, text=True).stdout or 0)

def loud(p):
    med = subprocess.run(["ffmpeg", "-i", p, "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True).stderr
    j = json.loads(med[med.rfind("{"):]); return float(j["input_i"]), float(j["input_tp"])

def audio(p, t0, d, sr=8000):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t0), "-t", str(d), "-i", p, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.float32)

def desfase(mp4, mezcla, sr=8000):
    """Desfase del audio del video respecto a la mezcla, en segundos (positivo = el video va tarde), por correlación en tres ventanas."""
    res = []
    total = dur(mezcla)
    if total < 40:   # clips cortos (pruebas): una sola ventana ajustada a la duración
        d = max(3.0, min(20.0, total - 4.0)); ventanas = [(2.0, d)]
    else: ventanas = [(10.0, 20.0), (total * 0.5, 20.0), (max(10.0, total - 40.0), 20.0)]
    for t0, d in ventanas:
        a = audio(mp4, t0 - 2, d + 4, sr); b = audio(mezcla, t0, d, sr)
        if len(a) < sr * (d * 0.5) or len(b) < sr * (d * 0.5): continue
        a = a - a.mean(); b = b - b.mean()
        c = np.correlate(a, b, mode="valid"); k = int(np.argmax(np.abs(c)))
        res.append(round((k / sr) - 2.0, 3))
    return float(np.median(res)) if res else None

def negros(hf_dir):
    try:
        html = open(os.path.join(hf_dir, "index.html"), encoding="utf-8").read()
        return [(float(m.group(1)), float(m.group(1)) + float(m.group(2))) for m in re.finditer(r'class="clip negro"[^>]*data-start="([\d.]+)" data-duration="([\d.]+)"', html)]
    except FileNotFoundError: return []

def main():
    ep = os.path.abspath(sys.argv[1]); n = int(sys.argv[sys.argv.index("--frames") + 1]) if "--frames" in sys.argv else 14
    C = json.load(open(os.path.join(ep, "episodio.json"))); numero = int(C.get("numero", 0))
    mp4 = os.path.join(ep, "video", f"episodio{numero:02d}.mp4"); mezcla = os.path.join(ep, C["mezcla"])
    problemas = []
    d_v, d_m = dur(mp4), dur(mezcla)
    if abs(d_v - d_m) > 0.5: problemas.append(f"duración video {d_v:.2f} s ≠ mezcla {d_m:.2f} s")
    lufs, tp = loud(mp4)
    if abs(lufs + 14) > 1.0: problemas.append(f"sonoridad {lufs} LUFS fuera de -14±1")
    if tp > -0.5: problemas.append(f"pico real {tp} dBTP por encima de -1")
    off = desfase(mp4, mezcla)
    if off is None or abs(off) > 0.08: problemas.append(f"desfase audio/mezcla {off} s")
    # fotogramas
    vdir = os.path.join(ep, "video", "verificacion"); os.makedirs(vdir, exist_ok=True)
    neg = negros(os.path.join(ep, "video", f"ep{numero:02d}")); frames = []
    from PIL import Image
    for i in range(n):
        t = round(d_v * (i + 0.5) / n, 2); f = os.path.join(vdir, f"frame_{i:02d}_{int(t)}s.jpg")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", mp4, "-frames:v", "1", "-q:v", "3", f], check=False)
        if not os.path.exists(f): problemas.append(f"no se pudo extraer el fotograma en {t} s"); continue
        lum = float(np.asarray(Image.open(f).convert("L")).mean())
        en_negro = any(a - 0.3 <= t <= b + 0.3 for a, b in neg) or t > d_v - 1.0
        if lum < 6 and not en_negro: problemas.append(f"fotograma en {t} s casi negro (lum {lum:.1f}) sin corte a negro previsto")
        frames.append({"t": t, "archivo": f, "luminancia": round(lum, 1), "negro_previsto": en_negro})
    res = {"ok": not problemas, "duracion": round(d_v, 2), "duracion_mezcla": round(d_m, 2), "lufs": lufs, "dbtp": tp, "desfase_s": off, "frames": frames, "problemas": problemas,
           "bytes": os.path.getsize(mp4) if os.path.exists(mp4) else 0}
    json.dump(res, open(os.path.join(vdir, "verificacion.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False))

if __name__ == "__main__": main()
