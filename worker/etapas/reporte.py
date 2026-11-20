"""Etapa reporte_semanal (§4, domingos): historial.csv + reporte-semanal-AAAA-MM-DD.md + acciones en biblia/08-aprendizajes.md.
Fuentes de datos, en este orden: params["datos"] (lo que n8n sacó de YouTube Analytics API / TikTok), CSV o capturas en
analitica/exportes/AAAA-MM-DD/ (los deja Jaime), y las fechas de estreno de publicar/. Si no hay piezas con ≥ 7 días,
el reporte lo dice en 3 líneas y no inventa. Criterios fijos (§4.3) incrustados en el prompt."""
import os, csv, json, glob, datetime
from .. import config as C, llm, fechas as F
from ..util import leer, escribir, leer_json, registro

CRITERIOS = """CRITERIOS FIJOS (los mismos cada semana): (i) embudo de 4 preguntas por pieza y red: ¿la plataforma lo mostró? ¿se quedaron? ¿hicieron algo? ¿lo buscan?; se arregla la primera que falla. (ii) comparar contra la MEDIANA del propio canal de las últimas 4–8 semanas (Shorts con Shorts, largos con largos, TikTok con TikTok); nunca contra promedios de internet. (iii) compartidos, comentarios, guardados y suscriptores/seguidores POR CADA 1,000 VISTAS. (iv) "confirmado" solo con n ≥ 5 piezas por grupo con ≥ 7 días; si no, "tendencia temprana", siempre con n. (v) cortes fijos: leyenda y región · tipo de gancho (regla rota, objeto, hora exacta, testigo) · duración · miniatura y título · AVANCE vs LA REGLA · misma pieza YouTube vs TikTok · día y hora reales · si los Shorts llevan gente al largo. (vi) un cambio a la vez durante al menos 5 piezas. (vii) monetización: Programa de Socios de YouTube (500 susc. + 3,000 h; 1,000 + 4,000 h; desde el 1-feb-2027: 8,000 h calificadas o 20 M de vistas de Shorts en 90 días; solo cuentan horas de largos) y lo que pida TikTok en México."""

def _piezas():
    hoy = datetime.date.today(); out = []
    for c in F.leer_carpetas(F.carpeta_publicar()):
        out.append({**{k: (v.isoformat() if isinstance(v, datetime.date) else v) for k, v in c.items()}, "dias_publicada": (hoy - c["fecha"]).days})
    return out

def _exportes():
    base = os.path.join(C.DATA_DIR, "analitica", "exportes"); textos = {}
    for f in sorted(glob.glob(os.path.join(base, "*", "*.csv")))[-12:]:
        textos[os.path.relpath(f, base)] = leer(f)[:20000]
    imgs = sorted(glob.glob(os.path.join(base, "*", "*.png")) + glob.glob(os.path.join(base, "*", "*.jpg")))[-8:]
    return textos, imgs

def reporte_semanal(slug, params):
    hoy = datetime.date.today(); an = os.path.join(C.DATA_DIR, "analitica"); os.makedirs(an, exist_ok=True)
    salida = os.path.join(an, f"reporte-semanal-{hoy.isoformat()}.md")
    piezas = _piezas(); con7 = [p for p in piezas if p["dias_publicada"] >= 7]
    hist_p = os.path.join(an, "historial.csv"); hist = leer(hist_p, "")
    if not con7 and not params.get("forzar"):
        txt = f"# Reporte semanal · {hoy.isoformat()}\n\nTodavía no hay piezas con 7 días publicadas ({len(piezas)} piezas en publicar/, la más antigua tiene {max([p['dias_publicada'] for p in piezas], default=0)} días).\nNo hay nada que medir esta semana; no se inventa nada.\nPróxima revisión: el domingo que viene.\n"
        escribir(salida, txt); registro("reporte semanal · sin piezas con 7 días"); return {"ok": True, "reporte": salida, "piezas_con_7_dias": 0}
    exportes, imgs = _exportes()
    datos = params.get("datos") or {}
    reportes_previos = sorted(glob.glob(os.path.join(an, "reporte-semanal-*.md")))[-2:]
    system = ("Eres el analista de Insomnia (YouTube + TikTok). Escribes el reporte semanal en español de México, con números y n, sin inventar: si falta un dato, escribe «sin dato». " + CRITERIOS +
              "\nResponde SOLO JSON: {\"filas_historial\": [[...]] (una por pieza y red, mismas columnas que la cabecera de historial.csv; `vistas` = con interacción en YouTube; `mostrado` = impresiones o mostrado en el feed; `pct_no_deslizo` = «se quedaron a verlo» / retención a 3 s; `guardados` solo TikTok), "
              "\"reporte_md\": \"Markdown completo: tabla de la semana por pieza y red; 3 mejores y 3 peores con la pregunta del embudo que lo explica; «lo que ya sabemos» (confirmado, con n) vs «tendencias tempranas»; 3 acciones concretas para la semana (qué cambiar, en cuántas piezas, qué métrica debe moverse y cuánto); revisión de las acciones de la semana anterior; avance a monetización en las dos redes\", "
              "\"aprendizajes\": [{\"hallazgo\": \"...\", \"evidencia\": \"...\", \"n\": 0, \"cambio\": \"qué se cambia en las piezas futuras (solo títulos, miniaturas, ganchos, duración, pies de TikTok, orden de la cola)\"}], \"propuestas_identidad\": [\"cambios de estilo/narrador/calendario que solo se proponen\"], \"resumen\": \"6 líneas para Jaime\"}")
    usuario = (f"HOY: {hoy.isoformat()}\n\nPIEZAS EN publicar/ (fecha de estreno = fecha de la carpeta):\n{json.dumps(piezas, ensure_ascii=False, indent=1)}\n\n"
               f"CABECERA Y CONTENIDO ACTUAL DE historial.csv:\n{hist[:8000] or 'fecha_reporte,red,pieza,tipo,estreno,mostrado,ctr,pct_no_deslizo,vistas,duracion_media_s,pct_visto,likes,comentarios,compartidos,guardados,suscriptores,fuente_principal,notas'}\n\n"
               f"videos.csv:\n{leer(os.path.join(an, 'videos.csv'))[:6000]}\n\nDATOS DE LAS API (n8n):\n{json.dumps(datos, ensure_ascii=False)[:30000] or '(ninguno)'}\n\n"
               f"EXPORTES CSV DE JAIME:\n{json.dumps(exportes, ensure_ascii=False)[:40000] or '(ninguno)'}\n\nREPORTES ANTERIORES (para revisar sus acciones):\n" + "\n\n".join(leer(r)[:5000] for r in reportes_previos) +
               f"\n\nAPRENDIZAJES ACUMULADOS:\n{llm.aprendizajes()[:6000]}")
    j = llm.llamar_json(system, usuario, max_tokens=9000, imagenes=imgs if not datos else None, temperatura=0.2)
    escribir(salida, f"# Reporte semanal · {hoy.isoformat()}\n\n" + j.get("reporte_md", ""))
    filas = j.get("filas_historial") or []
    if filas:
        nuevo = not os.path.exists(hist_p)
        with open(hist_p, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if nuevo: w.writerow(["fecha_reporte", "red", "pieza", "tipo", "estreno", "mostrado", "ctr", "pct_no_deslizo", "vistas", "duracion_media_s", "pct_visto", "likes", "comentarios", "compartidos", "guardados", "suscriptores", "fuente_principal", "notas"])
            for fila in filas: w.writerow(fila)
    ap = os.path.join(C.DATA_DIR, "biblia", "08-aprendizajes.md")
    if j.get("aprendizajes"):
        txt = leer(ap, "# Aprendizajes de analítica · Insomnia\n\nUna línea por hallazgo: fecha · hallazgo · evidencia · n · qué se cambió.\n\n")
        for a in j["aprendizajes"]: txt = txt.rstrip("\n") + f"\n- {hoy.isoformat()} · {a.get('hallazgo')} · evidencia: {a.get('evidencia')} · n={a.get('n')} · cambio: {a.get('cambio')}"
        escribir(ap, txt + "\n")
    registro(f"reporte semanal · {len(con7)} piezas con 7 días · {len(filas)} filas · {len(j.get('aprendizajes', []))} aprendizajes")
    return {"ok": True, "reporte": salida, "piezas_con_7_dias": len(con7), "filas_historial": len(filas), "resumen": j.get("resumen"), "propuestas_identidad": j.get("propuestas_identidad", [])}
