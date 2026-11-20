# Worker de Insomnia: Python + ffmpeg + Node 22 + Chromium (HyperFrames) + CLAP/whisper (CPU).
# Easypanel: servicio App → Source GitHub → Build "Dockerfile" → puerto 8000 → volumen /data.
FROM python:3.11-bookworm

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    TZ=America/Mexico_City LANG=C.UTF-8 \
    DATA_DIR=/data/leyendas CACHE_DIR=/data/cache HF_HOME=/data/cache/hf \
    HYPERFRAMES_VERSION=0.9.13 HYPERFRAMES_NO_TELEMETRY=1 HYPERFRAMES_SKIP_SKILLS=1 NODE_NO_WARNINGS=1

# ffmpeg, libsndfile, fuentes básicas y Chromium (trae todas las librerías que necesita Chrome; además sirve de respaldo
# si HyperFrames no puede descargar su chrome-headless-shell: HYPERFRAMES_BROWSER_PATH=/usr/bin/chromium).
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg libsndfile1 chromium fonts-liberation fonts-noto-color-emoji ca-certificates curl gnupg git tini rclone \
    && rm -rf /var/lib/apt/lists/*

# Node 22 (HyperFrames exige >= 22)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*

# torch solo CPU (pesa ~200 MB en vez de ~2 GB) y el resto de dependencias
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# usuario sin privilegios; /data lo monta Easypanel como volumen
RUN useradd -m -u 1000 insomnia && mkdir -p /data/leyendas /data/cache && chown -R insomnia:insomnia /data /app
USER insomnia
ENV HOME=/home/insomnia PATH=/home/insomnia/.local/bin:$PATH

# deja HyperFrames en la caché de npx para que el arranque no dependa de npm
RUN npx -y hyperframes@${HYPERFRAMES_VERSION} --version || true

COPY --chown=insomnia:insomnia worker /app/worker
COPY --chown=insomnia:insomnia ejemplos /app/ejemplos
COPY --chown=insomnia:insomnia sync_herramientas.sh entrypoint.sh /app/
WORKDIR /app
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 CMD curl -fsS http://localhost:8000/salud || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
