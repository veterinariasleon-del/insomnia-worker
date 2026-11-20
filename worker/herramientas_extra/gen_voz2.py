"""Voz con ElevenLabs v3 encadenada (versión genérica de gen_voz.py): velocidades por escena desde episodio.json
("escenas_lentas") y marca.json, regeneración selectiva con --solo.
Uso: python3 gen_voz2.py <carpeta_voz> [--todo] [--solo 3,7]
Reanudable: salta los trozos que ya tienen mp3 (salvo --todo o --solo)."""
import json, os, sys, base64, subprocess, re
from eleven import req

carpeta = sys.argv[1]; todo = "--todo" in sys.argv
solo = set(int(x) for x in sys.argv[sys.argv.index("--solo") + 1].split(",")) if "--solo" in sys.argv else None
segs = json.load(open(os.path.join(carpeta, "chunks.json")))
RAIZ = os.environ.get("LEYENDAS_RAIZ", "/data/leyendas")
marca = json.load(open(os.path.join(RAIZ, "marca", "marca.json")))
V = marca["voces"]
VOZ = {"narrador": V["narrador"]["voice_id"], "testigo": V["testigo"]["voice_id"]}
ep_json = os.path.join(os.path.dirname(os.path.abspath(carpeta)), "episodio.json")
EP = json.load(open(ep_json)) if os.path.exists(ep_json) else {}
LENTAS = set(EP.get("escenas_lentas", []))
aj_n = V["narrador"].get("ajustes", {}); aj_t = V["testigo"].get("ajustes", {})
def velocidad(s):
    if s["voz"] == "testigo": return aj_t.get("speed", 0.95)
    if s["escena"] in LENTAS or s.get("slow"): return aj_n.get("speed_escalada", 0.87)
    return aj_n.get("speed_contrato", 0.9)
def limpio(t): return re.sub(r"<[^>]+>", " ", t).strip()

antes = req("GET", "/user/subscription")["character_count"]
ids = {"narrador": [], "testigo": []}
for i, s in enumerate(segs):
    out = os.path.join(carpeta, f"chunk_{i:02d}.mp3")
    if solo is not None and i not in solo: continue
    if os.path.exists(out) and not todo and solo is None: continue
    voz_real = "testigo" if s["voz"] == "testigo" else "narrador"
    texto = s["texto"]
    body = {"text": texto, "model_id": V[voz_real].get("modelo_principal", "eleven_v3"), "language_code": "es",
            "voice_settings": {"stability": (aj_t if voz_real == "testigo" else aj_n).get("stability", 0.5),
                               "similarity_boost": (aj_t if voz_real == "testigo" else aj_n).get("similarity_boost", 0.85),
                               "use_speaker_boost": True, "speed": velocidad(s)}}
    if ids[voz_real]: body["previous_request_ids"] = ids[voz_real][-3:]
    h = {}
    try:
        d = req("POST", f"/text-to-speech/{VOZ[voz_real]}/with-timestamps", body=body, params={"output_format": "mp3_44100_128"}, headers_out=h)
    except Exception as e:
        print(i, "fallo con continuidad, reintento simple:", e)
        body.pop("previous_request_ids", None)
        d = req("POST", f"/text-to-speech/{VOZ[voz_real]}/with-timestamps", body=body, params={"output_format": "mp3_44100_128"}, headers_out=h)
    audio = base64.b64decode(d["audio_base64"]); ali = d.get("alignment") or d.get("normalized_alignment")
    rid = h.get("request-id") or h.get("x-request-id")
    if rid: ids[voz_real].append(rid)
    crudo = out.replace(".mp3", "_crudo.mp3"); open(crudo, "wb").write(audio)
    wav = out.replace(".mp3", ".wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", crudo, "-af",
                    "silenceremove=start_periods=1:start_threshold=-52dB:start_silence=0.12,areverse,silenceremove=start_periods=1:start_threshold=-52dB:start_silence=0.25,areverse,afade=t=in:d=0.015,areverse,afade=t=in:d=0.03,areverse",
                    "-ar", "48000", "-ac", "1", wav], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-b:a", "128k", out], check=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", wav], capture_output=True, text=True).stdout)
    if ali: json.dump(ali, open(out.replace(".mp3", "_align.json"), "w"))
    print(f"{i:02d} {s['escena']} {s['voz']:13} {dur:5.1f}s  v={velocidad(s)}  {texto[:50]}", flush=True)
despues = req("GET", "/user/subscription")["character_count"]
print("créditos gastados:", despues - antes, "| usados", despues)
