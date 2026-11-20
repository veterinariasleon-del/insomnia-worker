"""Resuelve los cortes de los planos (escenas.json) a segundos usando mezcla/tiempos_v2.json, ordena, calcula duraciones,
escribe prompts/PXX.txt y comprueba que todo sea coherente. Es el paso que antes hacía el agente a mano.
Uso: python3 resolver_planos.py <carpeta_episodio> [--min-dur 1.5]
Formato de corte: {"tipo":"palabra","escena":"E01","voz":"narrador","palabra":"Docenas","k":0,"delta":0.0}
                 {"tipo":"inicio","escena":"E01","voz":"narrador","delta":-0.8} | {"tipo":"fin","escena":"E10","k":0,"delta":0} | {"tipo":"abs","t":0.0}
Última línea de salida: JSON {"ok": bool, "problemas": [...], "planos": n}"""
import json, os, sys, re

def norm(wd): return re.sub(r"[^\wáéíóúñ]", "", wd.lower())

def main():
    ep = sys.argv[1]; min_dur = float(sys.argv[sys.argv.index("--min-dur") + 1]) if "--min-dur" in sys.argv else 1.5
    esc_p = os.path.join(ep, "escenas.json"); esc = json.load(open(esc_p, encoding="utf-8"))
    T = json.load(open(os.path.join(ep, "mezcla", "tiempos_v2.json")))
    TOTAL = T["total"]; problemas = []
    def palabras(escena, voz="narrador"):
        out = []
        for tr in T["trozos"]:
            if tr["escena"] == escena and tr["voz"] == voz:
                for wd, t0 in tr["palabras"]: out.append((wd, tr["inicio"] + t0))
        return out
    def W(escena, palabra, k=0, voz="narrador"):
        hits = [t0 for wd, t0 in palabras(escena, voz) if norm(wd) == norm(palabra)]
        if len(hits) <= k: raise KeyError(f"palabra «{palabra}» no está en {escena} ({voz}) (k={k}, hay {len(hits)})")
        return hits[k]
    def INI(escena, voz="narrador"):
        tr = [t for t in T["trozos"] if t["escena"] == escena and t["voz"] == voz]
        if not tr: raise KeyError(f"no hay trozo de {escena} ({voz})")
        return tr[0]["inicio"]
    def FIN(escena, voz="narrador", k=-1):
        tr = [t for t in T["trozos"] if t["escena"] == escena and t["voz"] == voz]
        if not tr: raise KeyError(f"no hay trozo de {escena} ({voz})")
        return tr[k]["fin"]
    def resolver(c):
        d = float(c.get("delta", 0.0)); voz = c.get("voz", "narrador")
        if c["tipo"] == "palabra": return W(c["escena"], c["palabra"], int(c.get("k", 0)), voz) + d
        if c["tipo"] == "inicio": return INI(c["escena"], voz) + d
        if c["tipo"] == "fin": return FIN(c["escena"], voz, int(c.get("k", -1))) + d
        if c["tipo"] == "abs": return float(c["t"])
        if c["tipo"] == "ancla": return T["anclas"][c["nombre"]] + d
        raise KeyError(f"tipo de corte desconocido: {c['tipo']}")
    planos = esc["planos"]
    for p in planos:
        try: p["t"] = round(max(0.0, resolver(p["corte"])), 3)
        except KeyError as e: problemas.append(f"{p['id']}: {e}"); p["t"] = None
    if problemas:
        print(json.dumps({"ok": False, "problemas": problemas, "planos": len(planos)}, ensure_ascii=False)); sys.exit(2)
    planos[0]["t"] = 0.0
    planos.sort(key=lambda p: p["t"])
    for i, p in enumerate(planos):
        fin = planos[i + 1]["t"] if i + 1 < len(planos) else TOTAL; p["dur"] = round(fin - p["t"], 3)
        if p["dur"] < min_dur and i + 1 < len(planos): problemas.append(f"{p['id']} dura {p['dur']} s (< {min_dur}); junta planos o mueve el corte")
        if p.get("camara") not in ("zoom_in_lento", "zoom_out_lento", "zoom_in_muy_lento", "quieto", "paralaje", "sacudida"): problemas.append(f"{p['id']}: cámara desconocida {p.get('camara')}")
        if p["dur"] > 40: problemas.append(f"{p['id']} dura {p['dur']} s (> 40): falta un plano intermedio")
    ids = [p["id"] for p in planos]
    if len(ids) != len(set(ids)): problemas.append("ids de plano repetidos")
    esc["planos"] = planos; esc["total"] = TOTAL
    json.dump(esc, open(esc_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.makedirs(os.path.join(ep, "prompts"), exist_ok=True)
    for p in planos: open(os.path.join(ep, "prompts", p["id"] + ".txt"), "w", encoding="utf-8").write(p["prompt"])
    for r in esc.get("referencias", []): open(os.path.join(ep, "prompts", "ref_" + r["id"] + ".txt"), "w", encoding="utf-8").write(r["prompt"])
    for p in planos: print(f"{p['id']} {p['escena']} t={p['t']:7.2f} dur={p['dur']:6.2f} {p['camara']} {p['efectos']} refs={p.get('refs', [])}")
    print(json.dumps({"ok": not problemas, "problemas": problemas, "planos": len(planos), "total": TOTAL}, ensure_ascii=False))
    sys.exit(0 if not problemas else 2)

if __name__ == "__main__": main()
