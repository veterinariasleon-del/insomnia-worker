"""Mezcla genérica dirigida por datos: lee `mezcla.json` (la partitura del episodio) y produce
mezcla/episodioNN_v2.mp3|wav, tiempos_v2.json y los extractos de prueba. Reemplaza al mezcla2.py
escrito a mano por episodio: la misma cadena (voz normalizada, pausas exactas, buses amb/mus/sfx,
ducking, loudnorm -14 LUFS / -1 dBTP), pero todo lo que cambia entre episodios viene del JSON.

Uso:
  python3 mezcla_json.py <carpeta_episodio> [mezcla.json] [--validar]
  --validar: solo comprueba que todas las referencias de tiempo existan y que los archivos/rellenos
             estén; no escribe audio (lo usa el LLM para corregir su partitura).

Formato de mezcla.json (resumen; el ejemplo completo del ep. 1 está en ejemplos/01-sombreron/mezcla.json):
  "salida": "episodio01_v2"            nombre base de los archivos de salida
  "inicio": 0.8, "cola_final": 17.0    silencio inicial y cola después del último trozo
  "pausas_extra": {"4": 2.5}           pausa después del trozo i (sustituye a la del chunk)
  "voces": {"testigo_fx": "..."}       cadena ffmpeg extra para la voz del testigo
  "anclas": {"nombre": REF, ...}       tiempos con nombre (se exportan a tiempos_v2.json → build_video)
  "partitura": [OP, OP, ...]           operaciones en orden (ver OPS abajo)
  "extractos": [{"nombre": "...", "desde": REF, "dur": 34}]

REF (referencia de tiempo), cualquiera de:
  número                               segundos absolutos
  "TOTAL"                              duración total
  ["ini", "E03"]                       inicio del primer trozo de la escena (narrador)
  ["ini", "E07", "testigo"]            inicio del primer trozo de esa voz en la escena
  ["ini", "E03", "narrador", 1.2]      + delta
  ["fin", "E06"]                       fin del último trozo (narrador); ["fin","E10","narrador",0] → fin del trozo k=0
  ["fin", "E10", "narrador", 0, -0.3]  k y delta
  ["pal", "E05", "callado"]            inicio de esa palabra (narrador, k=0)
  ["pal", "E07", "potrero", 0, 0.2, "testigo"]   k, delta, voz
  ["ancla", "corte", 1.1]              otra ancla + delta (las anclas pueden referirse entre sí, en orden)
  ["+", REF, REF]  ["-", REF, REF]     suma/resta (p. ej. duración = ["-", ["ini","E12"], ["ini","E11"]])

OPS (campos comunes: nivel en dB, pan 0..1 (0.5 centro), pan_fin opcional):
  {"op":"amb_sintesis","gen":"aire_cuarto","desde":REF,"hasta":REF,"nivel":0,"args":{}}
  {"op":"amb_loop","archivo":"grillos_milpa","desde":REF,"hasta":REF,"nivel":-19,"pan":0.5,"nivel_pico":0.6,"relleno":{"gen":"grillos","args":{"densidad":7}}}
  {"op":"sfx","archivo":"tecolote","t":REF,"nivel":-12,"pan":0.5,"dur_max":null,"relleno":{"gen":"guitarra_frase","args":{...}}}
  {"op":"sfx_repetir","archivo":"guitarra_lejos2","desde":REF,"hasta":REF,"margen":2.0,"cada":7.0,"nivel":-13,"pan":0.15,"relleno":{...}}
  {"op":"sfx_sintesis","gen":"toques","args":{"n":3,"sep":0.8},"t":REF,"nivel":-3,"pan":0.5}
  {"op":"corte_ambiente","t":REF,"silencio":2.5}          los ambientes se cortan de golpe y queda aire de cuarto
  {"op":"mus_pad_cuerdas","desde":REF,"hasta":REF,"nivel":-16,"f":CURVA,"niveles":CURVA}
  {"op":"mus_drone","desde":REF,"hasta":REF,"nivel":-22,"f":CURVA|número,"niveles":CURVA|número,"detune":0.6}
  {"op":"mus_loop","archivo":"cuerdas_tenso","desde":REF,"hasta":REF,"nivel":-17,"nivel_pico":0.6}   (si falta el archivo: silencio)
  {"op":"mus_shepard","desde":REF,"hasta":REF,"nivel":-23,"base":70,"octavas":5,"periodo":14}
  {"op":"mus_latido","desde":REF,"hasta":REF,"nivel":-17,"bpm":CURVA,"medio":true}
  {"op":"mus_tema","desde":REF,"dur":REF|número,"nivel":-20}
  {"op":"cortar_bus","bus":"sfx","desde":REF}             borra lo que haya en el bus desde ese instante (colas de guitarra)
  {"op":"silenciar","desde":REF,"hasta":REF,"buses":["mus"],"aire":false}
CURVA: [[REF, valor], ...] con "modo" opcional en la op ("lineal" por defecto, "escalon" para saltos) — como
  {"f": {"puntos": [[REF, 110], ...], "modo": "lineal"}}; también vale la lista directa (= lineal).
Generadores (gen) disponibles: los de herramientas/sintesis.py (aire_cuarto, grillos, viento, crujido, maiz_abriendose,
  corte_pelo, roce_tela, tecolote, tarareo, aves_amanecer, guitarra_frase, toques, riser, tema_canal) y "viento_aves" (viento+aves).
"""
import json, os, sys, subprocess, re
import numpy as np, soundfile as sf

RAIZ = os.environ.get("LEYENDAS_RAIZ", "/data/leyendas")
sys.path.insert(0, os.path.join(RAIZ, "herramientas")); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sintesis as S
SR = S.SR

def db(x): return 10 ** (x / 20)

class Mezcla:
    def __init__(self, ep, cfg, validar=False):
        self.EP = ep; self.cfg = cfg; self.validar = validar
        self.VOZ = os.path.join(ep, "voz"); self.SFX = os.path.join(ep, "sfx"); self.OUT = os.path.join(ep, "mezcla")
        os.makedirs(self.OUT, exist_ok=True)
        self.chunks = json.load(open(os.path.join(self.VOZ, "chunks.json")))
        self.problemas = []
        self.anclas = {}

    # ---------- audio ----------
    def leer(self, path):
        tmp = path + ".48k.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-ar", str(SR), "-ac", "1", tmp], check=True)
        y, _ = sf.read(tmp, dtype="float32"); os.remove(tmp); return y

    def archivo(self, nombre):
        p = os.path.join(self.SFX, nombre + ".mp3")
        if not os.path.exists(p): return None
        y = self.leer(p); return y / (np.abs(y).max() + 1e-9)

    @staticmethod
    def rms_activo(y):
        n = int(0.05 * SR); fr = y[: len(y) // n * n].reshape(-1, n); r = np.sqrt((fr ** 2).mean(axis=1)); r = r[r > 0.003]
        return float(np.sqrt((r ** 2).mean())) if len(r) else 1e-3

    def trozo_listo(self, i):
        """Normaliza el trozo a -20 dB RMS, inserta las pausas <break> que la voz no respetó y devuelve tiempos de palabra."""
        y = self.leer(os.path.join(self.VOZ, f"chunk_{i:02d}.wav")); y = y * (db(-20) / self.rms_activo(y))
        a = json.load(open(os.path.join(self.VOZ, f"chunk_{i:02d}_align.json")))
        chars = "".join(a["characters"]); st = a["character_start_times_seconds"]; en = a["character_end_times_seconds"]
        limpio = re.sub(r"\[[^\]]+\]", lambda m: " " * len(m.group(0)), chars)
        m0 = re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ¿¡\"]", limpio); off = st[m0.start()] if m0 else 0.0
        env = np.abs(y); k = int(0.03 * SR); env = np.convolve(env, np.ones(k) / k, mode="same")
        piezas, cursor, inserciones = [], 0, []
        for m in re.finditer(r'<break time="([\d.]+)s"/>', chars):
            deseado = float(m.group(1)); i0, i1 = m.start(), m.end() - 1
            t_prev = en[max(i0 - 1, 0)] - off; t_next = st[min(i1 + 1, len(st) - 1)] - off
            hueco_real = max(0.0, t_next - t_prev)
            centro = int(max(0.0, (t_prev + t_next) / 2) * SR); rad = int(0.35 * SR)
            a0, a1 = max(cursor + 1, centro - rad), min(len(y) - 1, centro + rad)
            corte = a0 + int(np.argmin(env[a0:a1])) if a1 > a0 else max(cursor, min(centro, len(y)))
            faltante = max(0.0, deseado - hueco_real)
            if faltante > 0.02:
                seg = y[cursor:corte].copy(); f = int(0.012 * SR)
                if len(seg) > 2 * f: seg[-f:] *= np.linspace(1, 0, f)
                piezas.append(seg); piezas.append(np.zeros(int(faltante * SR), np.float32)); inserciones.append((corte, faltante)); cursor = corte
                y[corte: corte + f] *= np.linspace(0, 1, f)[: len(y[corte: corte + f])]
        piezas.append(y[cursor:])
        def t_nuevo(t_orig): return t_orig + sum(s for pos, s in inserciones if pos / SR <= t_orig)
        palabras = []
        for m in re.finditer(r"[^\s<>\[\]\"/]+", limpio):
            w = m.group(0)
            if w.startswith("time=") or w == "break" or re.fullmatch(r"[\d.]+s", w): continue
            palabras.append([w, round(t_nuevo(max(0.0, st[m.start()] - off)), 3)])
        return np.concatenate(piezas), palabras

    def linea_de_tiempo(self):
        tl = []; t = float(self.cfg.get("inicio", 0.8)); extra = {int(k): v for k, v in self.cfg.get("pausas_extra", {}).items()}
        for i, c in enumerate(self.chunks):
            if self.validar:
                dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                            os.path.join(self.VOZ, f"chunk_{i:02d}.wav")], capture_output=True, text=True).stdout or 0)
                a = json.load(open(os.path.join(self.VOZ, f"chunk_{i:02d}_align.json")))
                limpio = re.sub(r"\[[^\]]+\]", lambda m: " " * len(m.group(0)), "".join(a["characters"])); st = a["character_start_times_seconds"]
                pal = [[m.group(0), round(st[m.start()], 3)] for m in re.finditer(r"[^\s<>\[\]\"/]+", limpio)
                       if not (m.group(0).startswith("time=") or m.group(0) == "break" or re.fullmatch(r"[\d.]+s", m.group(0)))]
                y = np.zeros(int(dur * SR), np.float32)
            else:
                y, pal = self.trozo_listo(i)
            tl.append({"i": i, "escena": c["escena"], "voz": c["voz"], "inicio": round(t, 3), "fin": round(t + len(y) / SR, 3), "palabras": pal, "_audio": y})
            t += len(y) / SR + extra.get(i, c["pausa_despues"])
        self.tl = tl; self.TOTAL = t + float(self.cfg.get("cola_final", 17.0)); self.N = int(self.TOTAL * SR)

    # ---------- referencias de tiempo ----------
    @staticmethod
    def norm_w(w): return re.sub(r"[^\wáéíóúñ]", "", w.lower())

    def W(self, escena, palabra, k=0, voz="narrador"):
        hits = []
        for e in self.tl:
            if e["escena"] == escena and e["voz"] == voz:
                hits += [e["inicio"] + p[1] for p in e["palabras"] if self.norm_w(p[0]) == self.norm_w(palabra)]
        if len(hits) <= k: raise KeyError(f"palabra no encontrada: {escena} «{palabra}» (voz {voz}, k={k}; hay {len(hits)})")
        return hits[k]

    def INI(self, escena, voz="narrador", k=0):
        tr = [e for e in self.tl if e["escena"] == escena and e["voz"] == voz]
        if len(tr) <= k: raise KeyError(f"no hay trozo {k} de {escena} ({voz})")
        return tr[k]["inicio"]

    def FIN(self, escena, voz="narrador", k=-1):
        tr = [e for e in self.tl if e["escena"] == escena and e["voz"] == voz]
        if not tr or (k >= 0 and len(tr) <= k): raise KeyError(f"no hay trozo {k} de {escena} ({voz})")
        return tr[k]["fin"]

    def T(self, ref):
        """Resuelve una REF a segundos."""
        if isinstance(ref, (int, float)): return float(ref)
        if ref == "TOTAL": return self.TOTAL
        if not isinstance(ref, list) or not ref: raise KeyError(f"referencia inválida: {ref!r}")
        op = ref[0]
        if op == "abs": return float(ref[1])
        if op == "ini": return self.INI(ref[1], ref[2] if len(ref) > 2 else "narrador") + (ref[3] if len(ref) > 3 else 0)
        if op == "fin": return self.FIN(ref[1], ref[2] if len(ref) > 2 else "narrador", ref[3] if len(ref) > 3 else -1) + (ref[4] if len(ref) > 4 else 0)
        if op == "pal": return self.W(ref[1], ref[2], ref[3] if len(ref) > 3 else 0, ref[5] if len(ref) > 5 else "narrador") + (ref[4] if len(ref) > 4 else 0)
        if op == "ancla":
            if ref[1] not in self.anclas: raise KeyError(f"ancla desconocida (o definida después): {ref[1]}")
            return self.anclas[ref[1]] + (ref[2] if len(ref) > 2 else 0)
        if op == "+": return self.T(ref[1]) + self.T(ref[2])
        if op == "-": return self.T(ref[1]) - self.T(ref[2])
        raise KeyError(f"referencia desconocida: {ref!r}")

    def curva(self, c, desde):
        """Devuelve f(x) con x relativo a `desde`. c = número | lista de [REF, valor] | {"puntos": [...], "modo": "lineal|escalon"}."""
        if isinstance(c, (int, float)): return lambda x: float(c)
        modo = "lineal"; pts = c
        if isinstance(c, dict): pts = c["puntos"]; modo = c.get("modo", "lineal")
        xs = [self.T(p[0]) - desde for p in pts]; vs = [float(p[1]) for p in pts]
        if modo == "escalon":
            def f(x):
                v = vs[0]
                for xi, vi in zip(xs, vs):
                    if x >= xi: v = vi
                return v
            return f
        return lambda x: float(np.interp(x, xs, vs))

    # ---------- buses ----------
    def poner(self, b, y, t0, nivel_db=0.0, pan=0.5, pan_fin=None):
        i0 = int(t0 * SR); n = min(len(y), self.N - i0)
        if n <= 0 or i0 < 0: return
        g = db(nivel_db); p = np.linspace(pan, pan if pan_fin is None else pan_fin, n)
        self.bus[b][i0:i0 + n, 0] += y[:n] * g * np.sqrt(1 - p); self.bus[b][i0:i0 + n, 1] += y[:n] * g * np.sqrt(p)

    def gen(self, spec, dur=None):
        """Sintetiza con herramientas/sintesis.py: {"gen": nombre, "args": {...}}. `dur` se pasa si el generador lo acepta."""
        nombre = spec["gen"]; args = dict(spec.get("args", {}))
        if nombre == "viento_aves":
            return S.viento(dur, args.get("fuerza", 0.45)) + S.aves_amanecer(dur, args.get("n", 6))
        fn = getattr(S, nombre, None)
        if fn is None: raise KeyError(f"generador desconocido: {nombre}")
        import inspect
        params = list(inspect.signature(fn).parameters)
        if dur is not None and params and params[0] == "dur" and "dur" not in args: args["dur"] = dur
        return fn(**args).astype(np.float32)

    def sfx_y(self, op):
        y = self.archivo(op["archivo"]) if op.get("archivo") else None
        if y is None:
            if not op.get("relleno"):
                self.problemas.append(f"falta sfx/{op.get('archivo')}.mp3 y no hay relleno"); return None
            y = self.gen(op["relleno"])
            y = y / (np.abs(y).max() + 1e-9)
        if op.get("dur_max"): y = S.fade(y[: int(op["dur_max"] * SR)].copy(), 0.01, 0.5)
        return y

    def loop(self, nombre, dur, relleno=None, nivel_pico=0.6):
        y = self.archivo(nombre) if nombre else None
        if y is None:
            if relleno is None: return np.zeros(int(dur * SR), np.float32)
            return self.gen(relleno, dur)
        y = S.fade(y * nivel_pico, 1.5, 1.5); x = int(1.5 * SR); n = int(dur * SR); out = np.zeros(n + len(y), np.float32); pos = 0
        while pos < n: out[pos:pos + len(y)] += y; pos += len(y) - x
        return out[:n]

    # ---------- capas musicales (idénticas a mezcla2.py del ep. 1) ----------
    def pad_cuerdas(self, dur, f_curva, nivel_curva):
        t = S.t_axis(dur); pasos = t[:: SR // 10]
        f = np.interp(t, pasos, [f_curva(x) for x in pasos]); lv = np.interp(t, pasos, [nivel_curva(x) for x in pasos])
        y = np.zeros_like(t)
        for det in (-0.6, 0.0, 0.7):
            ph = 2 * np.pi * np.cumsum(f * 2 ** (det / 1200)) / SR
            for h in range(1, 9): y += np.sin(h * ph) / h
        y *= (1 + 0.08 * np.sin(2 * np.pi * 5.2 * t)); y *= (0.85 + 0.15 * np.sin(2 * np.pi * 0.23 * t))
        y = S.lowpass_fast(y.astype(np.float32), 1600) * lv / 3
        ph2 = 2 * np.pi * np.cumsum(f * 4) / SR
        y += (0.12 * np.sin(ph2) * (1 + 0.1 * np.sin(2 * np.pi * 6 * t)) * lv).astype(np.float32)
        return S.fade(y, 3.0, 0.02)

    def latido_medio(self, dur, bpm_curva):
        y = S.latido(dur, bpm_curva); t = S.t_axis(dur); z = np.zeros_like(y); tt = 0.0
        while tt < dur:
            i0 = int(tt * SR); seg = np.arange(min(int(0.12 * SR), len(y) - i0)) / SR
            if len(seg) == 0: break
            z[i0:i0 + len(seg)] += 0.6 * np.sin(2 * np.pi * 130 * seg) * np.exp(-28 * seg)
            tt += 60.0 / bpm_curva(tt)
        return (y + S.lowpass_fast(z, 400)).astype(np.float32)

    def silenciar(self, t0, t1, buses):
        i0, i1 = int(t0 * SR), int(t1 * SR); a = int(0.03 * SR)
        for b in buses: self.bus[b][max(0, i0 - a):i0] *= np.linspace(1, 0, min(a, i0))[:, None]; self.bus[b][i0:i1] = 0

    # ---------- partitura ----------
    def ejecutar_op(self, op):
        k = op["op"]; nivel = op.get("nivel", 0.0); pan = op.get("pan", 0.5); pan_fin = op.get("pan_fin")
        if k == "amb_sintesis":
            d0, d1 = self.T(op["desde"]), self.T(op["hasta"]); dur = max(0.0, d1 - d0)
            if dur <= 0: return
            self.poner("amb", self.gen({"gen": op["gen"], "args": op.get("args", {})}, dur), d0, nivel, pan, pan_fin)
        elif k == "amb_loop" or k == "mus_loop":
            d0, d1 = self.T(op["desde"]), self.T(op["hasta"]); dur = max(0.0, d1 - d0)
            if dur <= 0: return
            if op.get("archivo") and self.archivo(op["archivo"]) is None and not op.get("relleno") and k == "amb_loop":
                self.problemas.append(f"falta sfx/{op['archivo']}.mp3 y no hay relleno")
            self.poner("amb" if k == "amb_loop" else "mus", self.loop(op.get("archivo"), dur, op.get("relleno"), op.get("nivel_pico", 0.6)), d0, nivel, pan, pan_fin)
        elif k == "sfx":
            t0 = self.T(op["t"]); y = self.sfx_y(op)
            if y is not None: self.poner("sfx", y, t0, nivel, pan, pan_fin)
        elif k == "sfx_repetir":
            d0, d1 = self.T(op["desde"]), self.T(op["hasta"]); tt = d0; y = self.sfx_y(op)
            if y is None: return
            while tt < d1 - op.get("margen", 2.0): self.poner("sfx", y, tt, nivel, pan, pan_fin); tt += float(op["cada"])
        elif k == "sfx_sintesis":
            y = self.gen({"gen": op["gen"], "args": op.get("args", {})}); self.poner("sfx", y, self.T(op["t"]), nivel, pan, pan_fin)
        elif k == "corte_ambiente":
            tc = self.T(op["t"]); sil = float(op.get("silencio", 2.5)); i_c = int(tc * SR); r = int(0.02 * SR)
            self.bus["amb"][i_c - r: i_c] *= np.linspace(1, 0, r)[:, None]; self.bus["amb"][i_c: i_c + int(sil * SR)] = 0
            self.poner("amb", S.aire_cuarto(sil), tc, 0)
        elif k == "mus_pad_cuerdas":
            d0, d1 = self.T(op["desde"]), self.T(op["hasta"]); dur = d1 - d0
            if dur <= 0: return
            self.poner("mus", self.pad_cuerdas(dur, self.curva(op["f"], d0), self.curva(op.get("niveles", 1.0), d0)), d0, nivel, pan, pan_fin)
        elif k == "mus_drone":
            d0, d1 = self.T(op["desde"]), self.T(op["hasta"]); dur = d1 - d0
            if dur <= 0: return
            self.poner("mus", S.drone(dur, self.curva(op["f"], d0), self.curva(op.get("niveles", 1.0), d0), detune=op.get("detune", 0.6)), d0, nivel, pan, pan_fin)
        elif k == "mus_shepard":
            d0, d1 = self.T(op["desde"]), self.T(op["hasta"]); dur = d1 - d0
            if dur <= 0: return
            self.poner("mus", S.shepard(dur, base=op.get("base", 70), octavas=op.get("octavas", 5), periodo=op.get("periodo", 14), nivel=op.get("nivel_interno", 1.0)), d0, nivel, pan, pan_fin)
        elif k == "mus_latido":
            d0, d1 = self.T(op["desde"]), self.T(op["hasta"]); dur = d1 - d0
            if dur <= 0: return
            bpm = self.curva(op["bpm"], d0)
            y = self.latido_medio(dur, bpm) if op.get("medio", True) else S.latido(dur, bpm)
            self.poner("mus", y, d0, nivel, pan, pan_fin)
        elif k == "mus_tema":
            d0 = self.T(op["desde"]); dur = self.T(op["dur"]) if not isinstance(op["dur"], (int, float)) else float(op["dur"])
            if dur <= 0: return
            self.poner("mus", S.tema_canal(dur), d0, nivel, pan, pan_fin)
        elif k == "cortar_bus":
            self.bus[op["bus"]][int(self.T(op["desde"]) * SR):] = 0
        elif k == "silenciar":
            d0, d1 = self.T(op["desde"]), self.T(op["hasta"])
            if d1 <= d0: return
            self.silenciar(d0, d1, op["buses"])
            if op.get("aire"): self.poner("amb", S.aire_cuarto(d1 - d0), d0, 0)
        else:
            raise KeyError(f"op desconocida: {k}")

    def correr(self):
        self.linea_de_tiempo()
        # anclas, en orden (pueden referirse a anteriores)
        for nombre, ref in self.cfg.get("anclas", {}).items():
            try: self.anclas[nombre] = self.T(ref)
            except KeyError as e: self.problemas.append(f"ancla {nombre}: {e}")
        if self.validar:
            # solo comprobar referencias y archivos
            for op in self.cfg.get("partitura", []):
                for campo in ("t", "desde", "hasta"):
                    if campo in op:
                        try: self.T(op[campo])
                        except KeyError as e: self.problemas.append(f"op {op['op']} {campo}: {e}")
                if "dur" in op and not isinstance(op["dur"], (int, float)):
                    try: self.T(op["dur"])
                    except KeyError as e: self.problemas.append(f"op {op['op']} dur: {e}")
                for campo in ("f", "niveles", "bpm"):
                    if campo in op and not isinstance(op[campo], (int, float)):
                        pts = op[campo]["puntos"] if isinstance(op[campo], dict) else op[campo]
                        for p in pts:
                            try: self.T(p[0])
                            except KeyError as e: self.problemas.append(f"op {op['op']} {campo}: {e}")
                if op.get("archivo") and not os.path.exists(os.path.join(self.SFX, op["archivo"] + ".mp3")) and not op.get("relleno") and op["op"] != "mus_loop":
                    self.problemas.append(f"falta sfx/{op['archivo']}.mp3 (op {op['op']}) y no hay relleno")
                if op.get("relleno") and not hasattr(S, op["relleno"]["gen"]) and op["relleno"]["gen"] != "viento_aves":
                    self.problemas.append(f"generador desconocido: {op['relleno']['gen']}")
            for x in self.cfg.get("extractos", []):
                try: self.T(x["desde"])
                except KeyError as e: self.problemas.append(f"extracto {x['nombre']}: {e}")
            return
        self.bus = {k: np.zeros((self.N, 2), np.float32) for k in ["voz_n", "voz_t", "amb", "mus", "sfx"]}
        for e in self.tl: self.poner("voz_t" if e["voz"] == "testigo" else "voz_n", e["_audio"], e["inicio"])
        for op in self.cfg.get("partitura", []):
            try: self.ejecutar_op(op)
            except KeyError as e: self.problemas.append(f"op {op.get('op')}: {e}")
        if self.problemas:
            print("PROBLEMAS:", *self.problemas, sep="\n  "); raise SystemExit(2)
        self.mezclar()

    def proc(self, b, extra=""):
        raw = os.path.join(self.OUT, f"{b}_raw.wav"); sf.write(raw, self.bus[b], SR)
        cad = "highpass=f=80,equalizer=f=250:t=q:w=1.5:g=-2,equalizer=f=3500:t=q:w=1.2:g=2,deesser=i=0.3:m=0.5:f=0.5,acompressor=threshold=-18dB:ratio=2:attack=15:release=120:makeup=2dB" + extra
        out = os.path.join(self.OUT, f"{b}_proc.wav"); subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-af", cad, out], check=True)
        y, _ = sf.read(out, dtype="float32")
        if len(y) < self.N: y = np.vstack([y, np.zeros((self.N - len(y), 2), np.float32)])
        return y[: self.N]

    def mezclar(self):
        fx_t = self.cfg.get("voces", {}).get("testigo_fx", ",aecho=0.8:0.55:38|71:0.35|0.18,volume=-1dB")
        voz = self.proc("voz_n") + self.proc("voz_t", fx_t)
        rms = self.rms_activo(voz.mean(axis=1)); voz *= db(-20) / rms
        env = np.abs(voz).mean(axis=1); k = int(0.15 * SR); env = np.convolve(env, np.ones(k) / k, mode="same"); env = np.clip(env / (np.percentile(env, 97) + 1e-9), 0, 1)
        duck = (1 - 0.5 * env)[:, None]
        mix = voz + (self.bus["amb"] + self.bus["mus"]) * duck + self.bus["sfx"]
        pre = os.path.join(self.OUT, "v2_pre.wav"); sf.write(pre, np.clip(mix, -1, 1), SR)
        med = subprocess.run(["ffmpeg", "-i", pre, "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"], capture_output=True, text=True).stderr
        j = json.loads(med[med.rfind("{"):])
        base = self.cfg.get("salida", "episodio_v2")
        wav = os.path.join(self.OUT, base + ".wav"); mp3 = os.path.join(self.OUT, base + ".mp3")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", pre, "-af",
                        f"loudnorm=I=-14:TP=-1:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true,alimiter=limit=0.89:level=false",
                        "-ar", str(SR), wav], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-b:a", "192k", mp3], check=True)
        for e in self.tl: e.pop("_audio", None)
        json.dump({"total": round(self.TOTAL, 2), "anclas": self.anclas, "trozos": self.tl},
                  open(os.path.join(self.OUT, "tiempos_v2.json"), "w"), ensure_ascii=False, indent=1)
        for x in self.cfg.get("extractos", []):
            a, d = self.T(x["desde"]), float(x["dur"])
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0, a):.2f}", "-t", str(d), "-i", wav, "-af", "afade=t=in:d=0.3,afade=t=out:st=" + str(d - 0.5) + ":d=0.5", "-b:a", "192k", os.path.join(self.OUT, x["nombre"] + ".mp3")], check=True)
        # limpieza de intermedios pesados (los wav de buses); se conserva el wav final
        for f in ("voz_n_raw.wav", "voz_t_raw.wav", "voz_n_proc.wav", "voz_t_proc.wav", "v2_pre.wav"):
            p = os.path.join(self.OUT, f)
            if os.path.exists(p): os.remove(p)
        print(json.dumps({"ok": True, "mp3": mp3, "duracion": round(self.TOTAL, 2), "anclas": {k: round(v, 2) for k, v in self.anclas.items()}}, ensure_ascii=False))

if __name__ == "__main__":
    ep = sys.argv[1]; validar = "--validar" in sys.argv
    resto = [a for a in sys.argv[2:] if not a.startswith("--")]
    cfg_path = resto[0] if resto else os.path.join(ep, "mezcla.json")
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    m = Mezcla(ep, cfg, validar=validar); m.correr()
    if validar:
        print(json.dumps({"ok": not m.problemas, "problemas": m.problemas, "duracion": round(m.TOTAL, 2), "anclas": {k: round(v, 2) for k, v in m.anclas.items()}}, ensure_ascii=False))
        sys.exit(0 if not m.problemas else 2)
