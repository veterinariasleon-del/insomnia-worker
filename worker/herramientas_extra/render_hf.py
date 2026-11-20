"""Montaje y render con HyperFrames, de principio a fin y reanudable:
texturas → proyecto HyperFrames (plantilla + build_video.py) → check → snapshots → render por segmentos (--resume) → 720p.
Uso: python3 render_hf.py <carpeta_episodio> [--solo-build] [--snapshots 10] [--workers 1]
Última línea: JSON {"ok": bool, "mp4": ..., "snapshots": [...], "check": "...", "duracion": s}"""
import json, os, sys, subprocess, shutil, re, time

RAIZ = os.environ.get("LEYENDAS_RAIZ", "/data/leyendas")
HERR = os.path.join(RAIZ, "herramientas")
AQUI = os.path.dirname(os.path.abspath(__file__))
PLANTILLA = next((p for p in [os.environ.get("HF_PLANTILLA"), os.path.join(os.path.dirname(AQUI), "assets", "hf_plantilla"), "/app/worker/assets/hf_plantilla"] if p and os.path.isdir(p)), None) \
    or sys.exit("no encuentro la plantilla de HyperFrames (HF_PLANTILLA)")
VER = os.environ.get("HYPERFRAMES_VERSION", "0.9.13")

def hf_cmd():
    b = os.environ.get("HYPERFRAMES_BIN") or shutil.which("hyperframes")
    return [b] if b else ["npx", "-y", f"hyperframes@{VER}"]

def run(cmd, cwd, log=None, check=True, timeout=None):
    print("$", " ".join(cmd), flush=True)
    with open(log, "a") if log else open(os.devnull, "w") as lf:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        salida = []
        for line in p.stdout:
            salida.append(line); lf.write(line); lf.flush()
            if len(salida) % 40 == 0: print(line.rstrip()[:160], flush=True)
        p.wait(timeout=timeout)
    txt = "".join(salida)
    if check and p.returncode != 0: raise RuntimeError(f"falló ({p.returncode}): {' '.join(cmd)}\n{txt[-1500:]}")
    return txt

def preparar_proyecto(ep, numero):
    hf = os.path.join(ep, "video", f"ep{numero:02d}"); os.makedirs(os.path.join(hf, "assets", "fx"), exist_ok=True)
    for f in os.listdir(PLANTILLA):
        src = os.path.join(PLANTILLA, f); dst = os.path.join(hf, "assets" if f.endswith((".js", ".woff2")) else "", f)
        if not os.path.exists(dst): shutil.copy(src, dst)
    if not os.path.exists(os.path.join(hf, "package.json")):
        json.dump({"name": f"ep{numero:02d}", "private": True, "type": "module", "scripts": {"check": f"npx --yes hyperframes@{VER} check", "render": f"npx --yes hyperframes@{VER} render"}}, open(os.path.join(hf, "package.json"), "w"), indent=2)
    if not os.path.exists(os.path.join(hf, "meta.json")):
        json.dump({"id": f"ep{numero:02d}", "name": f"ep{numero:02d}", "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, open(os.path.join(hf, "meta.json"), "w"), indent=2)
    fx = os.path.join(hf, "assets", "fx")
    if not os.path.exists(os.path.join(fx, "niebla1.png")):
        subprocess.run(["python3", os.path.join(HERR, "texturas.py"), fx], check=True)
    return hf

def main():
    ep = os.path.abspath(sys.argv[1]); solo_build = "--solo-build" in sys.argv
    n_snap = int(sys.argv[sys.argv.index("--snapshots") + 1]) if "--snapshots" in sys.argv else 10
    workers = sys.argv[sys.argv.index("--workers") + 1] if "--workers" in sys.argv else "1"
    C = json.load(open(os.path.join(ep, "episodio.json"))); numero = int(C.get("numero", 0))
    hf = preparar_proyecto(ep, numero); log = os.path.join(ep, "video", "render.log")
    run(["python3", os.path.join(HERR, "build_video.py"), ep, hf], cwd=RAIZ, log=log)
    T = json.load(open(os.path.join(ep, "mezcla", "tiempos_v2.json"))); TOTAL = float(T["total"])
    chk = run(hf_cmd() + ["check"], cwd=hf, log=log, check=False)
    errores = re.search(r"(\d+) error", chk); n_err = int(errores.group(1)) if errores else (0 if "error" not in chk.lower() else -1)
    if n_err not in (0,): raise RuntimeError("hyperframes check reporta errores:\n" + chk[-2000:])
    mp4 = os.path.join(ep, "video", f"episodio{numero:02d}.mp4")
    res = {"ok": True, "check": "0 errores", "duracion": TOTAL, "mp4": mp4, "proyecto": hf}
    # snapshots en 10 momentos repartidos (más tarjeta y cierre)
    snap_dir = os.path.join(ep, "video", "snapshots")
    if n_snap > 0:
        momentos = sorted(set([round(TOTAL * (i + 0.5) / n_snap, 2) for i in range(n_snap)] + [1.0, round(TOTAL - 3.0, 2)]))
        shutil.rmtree(snap_dir, ignore_errors=True)
        try:
            run(hf_cmd() + ["snapshot", "--at", ",".join(str(m) for m in momentos), "--no-end", "-o", snap_dir, hf], cwd=hf, log=log, timeout=1800)
            res["snapshots"] = sorted(os.path.join(snap_dir, f) for f in os.listdir(snap_dir) if f.endswith(".png"))
        except Exception as e:
            print("snapshot falló (no bloquea):", str(e)[:300], flush=True); res["snapshots"] = []
    if solo_build:
        print(json.dumps(res, ensure_ascii=False)); return
    env = dict(os.environ, HF_SEGMENTED_CAPTURE="true", HF_SEGMENT_FRAMES=os.environ.get("HF_SEGMENT_FRAMES", "300"))
    cmd = hf_cmd() + ["render", "-c", "index.html", "--resume", "--no-low-memory-mode", "--workers", str(workers), "-o", mp4]
    for intento in range(3):
        print(f"render intento {intento + 1}", flush=True)
        p = subprocess.Popen(cmd, cwd=hf, env=env, stdout=open(log, "a"), stderr=subprocess.STDOUT); p.wait()
        if p.returncode == 0 and os.path.exists(mp4): break
        time.sleep(20)
    else:
        raise RuntimeError("el render falló 3 veces; ver video/render.log")
    # 720p para vista previa
    prev = os.path.join(ep, "video", f"episodio{numero:02d}_720p_vista_previa.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4, "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "fast", "-crf", "26", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", prev], check=False)
    res["vista_previa"] = prev
    print(json.dumps(res, ensure_ascii=False))

if __name__ == "__main__": main()
