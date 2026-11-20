"""Fechas de estreno según §6 de la tarea v3.1 (bandeja de salida `publicar/`).

Regla: la referencia es el último EPISODIO que haya en publicar/ (por nombre de carpeta; si Jaime cambió una fecha a mano,
esa es la buena). Episodio largo nuevo = esa fecha + 7 días, o el día siguiente a la corrida si va más tarde; Short AVANCE =
un día antes de su largo; Short LA REGLA = 4 días después de su largo. Si publicar/ está vacía, el largo va al día siguiente
de la corrida. Toda carpeta queda al menos un día después de la corrida. Fase 2: dos largos por semana (martes y viernes).
Si la pieza ya tiene su propia carpeta en publicar/, manda esa fecha (nunca se recalcula)."""
import os, re, datetime
from . import config as C

TIPOS = ("EPISODIO", "SHORT-AVANCE", "SHORT-REGLA")
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
PATRON = re.compile(r"^(\d{4}-\d{2}-\d{2})_([^_]+)_(\d{2}-\d{2})_(EPISODIO|SHORT-AVANCE|SHORT-REGLA)_(.+)$")
HORA = "20-00"

def carpeta_publicar(prueba=False):
    return os.path.join(C.DATA_DIR, "publicar_pruebas" if prueba else "publicar")

def leer_carpetas(base):
    """[{fecha, dia, hora, tipo, slug, nombre}] de las carpetas válidas de publicar/."""
    out = []
    if not os.path.isdir(base): return out
    for d in sorted(os.listdir(base)):
        m = PATRON.match(d)
        if m and os.path.isdir(os.path.join(base, d)):
            try: f = datetime.date.fromisoformat(m.group(1))
            except ValueError: continue
            out.append({"fecha": f, "dia": m.group(2), "hora": m.group(3), "tipo": m.group(4), "slug": m.group(5), "nombre": d})
    return out

def fase():
    """1 (un largo por semana) o 2 (martes y viernes). Se cambia escribiendo '2' en publicar/fase.txt."""
    try: return int(open(os.path.join(C.DATA_DIR, "publicar", "fase.txt")).read().strip() or 1)
    except Exception: return 1

def _siguiente_mar_vie(desde):
    """Primer martes (1) o viernes (4) estrictamente posterior a `desde`."""
    d = desde + datetime.timedelta(days=1)
    while d.weekday() not in (1, 4): d += datetime.timedelta(days=1)
    return d

def fecha_largo(slug, hoy=None, base=None):
    """Fecha de estreno del episodio largo de `slug`. Si ya tiene carpeta EPISODIO, esa; si no, la regla de §6."""
    hoy = hoy or datetime.date.today(); base = base or carpeta_publicar()
    carpetas = leer_carpetas(base)
    propias = [c for c in carpetas if c["tipo"] == "EPISODIO" and c["slug"] == slug]
    if propias: return max(c["fecha"] for c in propias), "carpeta propia"
    episodios = [c["fecha"] for c in carpetas if c["tipo"] == "EPISODIO"]
    minimo = hoy + datetime.timedelta(days=1)
    if not episodios: return minimo, "publicar vacía → día siguiente a la corrida"
    ultimo = max(episodios)
    if fase() == 2:
        f = _siguiente_mar_vie(ultimo)
        while f < minimo: f = _siguiente_mar_vie(f)
        return f, f"fase 2: siguiente martes/viernes después del último EPISODIO ({ultimo})"
    f = ultimo + datetime.timedelta(days=7)
    if f < minimo: return minimo, f"último EPISODIO {ultimo} + 7 quedaba atrás → día siguiente a la corrida"
    return f, f"último EPISODIO {ultimo} + 7 días"

def fechas_piezas(slug, hoy=None, base=None):
    """{"EPISODIO": fecha, "SHORT-AVANCE": fecha, "SHORT-REGLA": fecha, "motivo": str}. Las carpetas propias ya existentes mandan."""
    hoy = hoy or datetime.date.today(); base = base or carpeta_publicar()
    largo, motivo = fecha_largo(slug, hoy, base)
    minimo = hoy + datetime.timedelta(days=1)
    out = {"EPISODIO": largo, "SHORT-AVANCE": max(largo - datetime.timedelta(days=1), minimo), "SHORT-REGLA": largo + datetime.timedelta(days=4), "motivo": motivo}
    for c in leer_carpetas(base):
        if c["slug"] == slug and c["tipo"] in out: out[c["tipo"]] = c["fecha"]
    return out

def nombre_carpeta(fecha, tipo, slug, hora=HORA):
    return f"{fecha.isoformat()}_{DIAS[fecha.weekday()]}_{hora}_{tipo}_{slug}"

def fecha_legible(fecha, hora="20:00"):
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    nombres = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    return f"{nombres[fecha.weekday()]} {fecha.day} de {meses[fecha.month - 1]} de {fecha.year}, {hora}"
