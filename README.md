# insomnia-worker

El "cuerpo" de la producción automática del canal **Insomnia**: un contenedor con Python, ffmpeg, Node 22 + Chromium
(HyperFrames), el oído (CLAP) y whisper, que ejecuta **todas las etapas** de un episodio sobre una copia de
`C:\Proyectos\Leyendas` guardada en un volumen (`/data/leyendas`). n8n es el "cerebro": decide qué etapa toca,
llama a este worker por HTTP y avisa a Jaime. Equivale, etapa por etapa, a la tarea programada v3.1
(`tarea/tarea-insomnia-produccion.md`).

Principios: nada de código por episodio (la partitura de mezcla y los planos son JSON que escribe el modelo y valida el
worker); todo reanudable; nunca regenera voz o efectos ya verificados; nunca borra (mueve a `_to_delete/`); nunca
publica; las claves solo viven en variables de entorno.

## Etapas (`POST /jobs {"etapa": ..., "slug": ..., "params": {...}}`)

| Etapa | Qué hace | Deja |
|---|---|---|
| `guion` | Claude (Opus) escribe el guion con biblia 00/01/02/04/06 + guion del ep. 1 como modelo; `episodio.json` | `guion.md`, `episodio.json` |
| `auditoria` | lector independiente (otro prompt, sin contexto del autor) → correcciones, máx. 3 rondas | `auditoria/ronda_N.json` |
| `freno_creditos` | caracteres × 1.3 + 3,000 ≤ créditos disponibles de ElevenLabs | — |
| `voz` | guion_parser → chunks → ElevenLabs v3 → whisper ≥ 0.88 → regenera solo lo que falla (máx. 2) | `voz/chunk_NN.*`, `verificacion.json` |
| `sfx` | lista desde SONIDO → biblioteca `sfx_biblioteca/` → ElevenLabs + oído (máx. 3 intentos) → alta en biblioteca | `sfx/*.mp3`, `registro.json`, hoja de contacto |
| `partitura` | el modelo escribe `mezcla.json` (formato documentado en `mezcla_json.py`), validado hasta 3 veces | `mezcla.json` |
| `mezcla` | `mezcla_json.py` → −14 LUFS / −1 dBTP, extractos, whisper en extractos | `mezcla/episodioNN_v2.mp3`, `tiempos_v2.json` |
| `planos` | el modelo escribe planos + prompts (regla 8) → `resolver_planos.py` los ancla a palabras → `prompts/` | `escenas.json`, `prompts/PXX.txt`, fichas de personajes |
| `imagenes` | Gemini `nano-banana-2.1` 16:9 2K con referencias → QA visual de Claude por imagen (≤ 2 regeneraciones) | `imagenes/PXX.jpg`, hoja de contacto |
| `render` | texturas → `build_video.py` → `hyperframes check` → snapshots revisados por Claude → render por segmentos → verificación (duración, LUFS, desfase, fotogramas) → 720p | `video/episodioNN.mp4` (+ MD5) |
| `miniatura` | plano y textos elegidos por el modelo → `arte_canal.py miniatura` | `miniatura.jpg` 2560×1440 |
| `textos` | `youtube.txt` (modelo ep. 1, capítulos reales, fecha §6) + `youtube.txt`/`tiktok.txt` de los Shorts | `youtube.txt`, `shorts/*/youtube.txt`, `tiktok.txt` |
| `shorts` | `build_short.py` (vive en la PC; copiarlo al volumen) + miniatura vertical 1080×1920 | `shorts/avance|regla/short.mp4`, `miniatura.jpg` |
| `publicar` | carpetas `AAAA-MM-DD_dia_20-00_TIPO_slug` en `publicar/` del volumen, MD5, `calendario.md` (§6) | `publicar/...` |
| `estado_cola` | `cola.py estado <slug> <estado>` (solo lo pide n8n; nunca retrocede sin `forzar`) | `cola/cola.json`, `cola.md` |
| `reposicion` | 4 leyendas nuevas investigadas en la web → `pendiente` (§3) | `cola/cola.json` |
| `reporte_semanal` | historial + reporte con los criterios fijos de §4 + `biblia/08-aprendizajes.md` | `analitica/reporte-semanal-*.md` |
| `prueba_corta` | **el experimento de 2 minutos**: todas las etapas seguidas sobre `contenido/pruebas/00-prueba` → `publicar_pruebas/` | todo lo anterior, fuera de la cola |
| `ping` | diagnóstico del contenedor (versiones, cachés, disco) | — |

Otras rutas: `GET /salud` (sin token), `GET /estado` (cola, publicar/, créditos, archivos del episodio), `GET /jobs`,
`GET /jobs/{id}` (estado + cola del log), `DELETE /jobs/{id}`, `GET /archivos?ruta=` (descarga), `GET /archivos/listar?ruta=`,
`GET /archivos/texto?ruta=`, `PUT /archivos/texto`, `POST /archivos` (subida; `extraer_zip=true` para carpetas),
`GET /etapas`. Todas menos `/salud` llevan `Authorization: Bearer <WORKER_TOKEN>`.

Variante híbrida (plan Pro, sin API de Anthropic): las etapas de texto aceptan `params.texto` o `params.archivo_texto`
con lo que ya escribió la tarea programada de Claude; entonces no se llama a la API.

## Desplegar en Easypanel

1. **Servicio**: en el proyecto donde ya está n8n → `+ Service` → **App** → nombre `insomnia-worker`.
2. **Source**: GitHub → `<usuario>/insomnia-worker`, rama `main`, build path `/`. **Build**: Dockerfile (`./Dockerfile`).
   (Repo privado: Easypanel pide un token de GitHub en *Settings → GitHub*, o usa la opción *Git* con llave de despliegue.)
3. **Environment** (pegar en `.env` syntax; las claves se copian desde `C:\Proyectos\Leyendas\_claves\*.txt`, nunca desde un chat):
   ```
   WORKER_TOKEN=<cadena larga aleatoria; la misma va en la credencial "Header Auth" de n8n>
   ELEVEN_KEY=<clave de ElevenLabs>
   ANTHROPIC_API_KEY=<clave de Anthropic>            # vacío en la variante híbrida
   ANTHROPIC_MODEL=claude-sonnet-5-5
   ANTHROPIC_MODEL_GUION=claude-opus-5-5
   GEMINI_API_KEY=<clave de Google AI Studio>        # vacío si las imágenes siguen en Flow
   GEMINI_IMAGE_MODEL=gemini-nano-banana-2.1
   GEMINI_IMAGE_SIZE=2K
   WHISPER_MODELO=small
   TZ=America/Mexico_City
   DATA_DIR=/data/leyendas
   CACHE_DIR=/data/cache
   ```
4. **Mounts**: Volume `insomnia-data` → `/data` (aquí viven la copia del proyecto, los modelos y los videos; ≥ 40 GB).
5. **Resources**: memoria límite **≥ 6144 MB** (render con Chromium + CLAP + whisper); CPU 2 cores. El VPS necesita
   8 GB de RAM (KVM 2 o superior).
6. **Domains**: no hace falta dominio público; n8n lo alcanza por la red interna de Easypanel:
   `http://<proyecto>_insomnia-worker:8000` (confirmar el nombre en *Service → Internal*). Si se expone con dominio,
   dejar siempre el token.
7. **Deploy**. La primera vez tarda 10–15 min (torch, Chromium). El primer `render` descarga el chrome-headless-shell de
   HyperFrames (~150 MB) y el primer `sfx`/`voz` los modelos CLAP (~600 MB) y whisper (~500 MB) al volumen.
8. **Cargar el proyecto**: comprimir `C:\Proyectos\Leyendas` SIN `.wav` ni `.mp4` ni `_claves` y subirlo:
   `POST /archivos` con `ruta=.`, `extraer_zip=true`; o carpeta por carpeta. Después `POST /jobs {"etapa":"ping","slug":"-"}`.
9. **Descargar resultados**: `GET /archivos?ruta=publicar/<carpeta>/episodioNN.mp4`, o instalar en Easypanel el servicio
   *File Browser* (plantilla) montado sobre el mismo volumen `/data` con usuario y contraseña, para descargar desde el navegador.
   La PC de Jaime ya no interviene: los videos no se copian a `C:` (está lleno).

## Memoria y tiempos (referencia del episodio 1, 553 s)

render ≈ 45 min en 1 worker (nube); en KVM 2 esperar 60–90 min. CLAP ≈ 1.5 GB RAM al verificar; whisper small ≈ 1 GB;
Chromium de render 1.5–2.5 GB. Un episodio ocupa ≈ 1.2 GB en el volumen (imágenes 2K + wav de voz + MP4).

## Probar en local (sin Docker)

```
export DATA_DIR=/ruta/a/copia/de/Leyendas WORKER_TOKEN=prueba
python3 -m uvicorn worker.app:app --port 8000
bash tests/smoke.sh http://localhost:8000 prueba
```

## Pendientes conocidos

- `build_short.py` está solo en la PC (`herramientas/`); copiarlo al volumen y ajustar su interfaz en `worker/etapas/video.py::shorts`.
- Nombre interno del servicio en la red de Easypanel (confirmar al desplegar).
- La API de ElevenLabs cuenta créditos con retraso de segundos: `creditos_gastados` es orientativo.
