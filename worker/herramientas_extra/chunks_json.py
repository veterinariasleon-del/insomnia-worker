"""Agrupa los segmentos de guion_parser en trozos por escena y voz (versión genérica de chunks.py).
Uso: python3 chunks_json.py <segmentos.json> <chunks.json> [--cortes "E10|se detuvo;E05|callado"]
--cortes: después del segmento de esa escena cuyo texto termina con esas palabras, se fuerza un corte (hueco largo para un efecto).
También lee episodio.json si está junto a la carpeta voz: campo "cortes_voz_despues_de": [[escena, "palabras finales"], ...]."""
import json, sys, os, re

def norm(t): return re.sub(r"[^\wáéíóúñ ]", "", re.sub(r"<[^>]+>", " ", t).lower()).strip()

def main():
    segs = json.load(open(sys.argv[1])); out = sys.argv[2]
    cortes_spec = []
    if "--cortes" in sys.argv:
        for c in sys.argv[sys.argv.index("--cortes") + 1].split(";"):
            if "|" in c: e, p = c.split("|", 1); cortes_spec.append([e.strip(), p.strip()])
    ep_json = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(out))), "episodio.json")
    if os.path.exists(ep_json):
        cortes_spec += json.load(open(ep_json)).get("cortes_voz_despues_de", [])
    CORTES = set()
    for esc, fin in cortes_spec:
        f = norm(fin)
        for i, s in enumerate(segs):
            if s["escena"] == esc and norm(s["texto"]).endswith(f): CORTES.add(i)
    chunks = []
    for i, s in enumerate(segs):
        voz = "testigo" if s["voz"] == "testigo" else "narrador"
        texto = ("[whispers] " if s["whisper"] else "") + s["texto"] + (" [normal tone]" if s["whisper"] else "")
        nuevo = (not chunks or chunks[-1]["escena"] != s["escena"] or chunks[-1]["voz"] != voz or (i - 1) in CORTES)
        if nuevo:
            chunks.append({"escena": s["escena"], "voz": voz, "texto": texto, "segmentos": [i], "pausa_despues": s["pausa_despues"], "slow": s["slow"]})
        else:
            c = chunks[-1]; p = c["pausa_despues"]
            c["texto"] += f' <break time="{min(p, 3.0)}s"/> ' + texto; c["segmentos"].append(i); c["pausa_despues"] = s["pausa_despues"]
    json.dump(chunks, open(out, "w"), ensure_ascii=False, indent=1)
    print(len(chunks), "trozos; cortes forzados después de", sorted(CORTES))
    for k, c in enumerate(chunks): print(f'{k:02d} {c["escena"]} {c["voz"]:8} segs={c["segmentos"]} p={c["pausa_despues"]} chars={len(c["texto"])}')

if __name__ == "__main__": main()
