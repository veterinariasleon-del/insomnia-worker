"""Configuración del worker: todo viene de variables de entorno (Easypanel → Environment).
Ninguna clave se escribe en disco ni en los registros."""
import os

DATA_DIR = os.environ.get("DATA_DIR", "/data/leyendas")          # copia de C:\Proyectos\Leyendas (volumen persistente)
CACHE_DIR = os.environ.get("CACHE_DIR", "/data/cache")            # modelos (CLAP, whisper), caché de HyperFrames
HERR = os.path.join(DATA_DIR, "herramientas")
JOBS_DIR = os.path.join(DATA_DIR, "_jobs")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "")                 # bearer que n8n manda en Authorization
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")            # auditoría, prompts, textos, QA visual, reporte
ANTHROPIC_MODEL_GUION = os.environ.get("ANTHROPIC_MODEL_GUION", "claude-opus-5-5")  # guion y reposición (lo creativo)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-nano-banana-2.1")
GEMINI_IMAGE_SIZE = os.environ.get("GEMINI_IMAGE_SIZE", "2K")
ELEVEN_KEY = os.environ.get("ELEVEN_KEY", "")
HYPERFRAMES_VERSION = os.environ.get("HYPERFRAMES_VERSION", "0.9.13")
TZ = os.environ.get("TZ", "America/Mexico_City")
VERSION = "0.2.0"
REMOTO = os.environ.get("REMOTO", "drive:Insomnia-VPS")   # salida: carpeta de Drive (Mi unidad) que lee Metricool
# entrada y pedidos: la carpeta _vps de la PC sincronizada por Drive para escritorio (sección "Ordenadores"); solo se LEE
ENTRADA_REMOTO = os.environ.get("ENTRADA_REMOTO", "drive,root_folder_id=1X11O_jWOWb-iqN2VAjRYjSVPBlT532--:")
BUZON_MINUTOS = int(os.environ.get("BUZON_MINUTOS", "10"))

def entorno_scripts():
    """Variables para los scripts del proyecto (herramientas/*.py) y HyperFrames."""
    env = dict(os.environ)
    aqui = os.path.dirname(os.path.abspath(__file__))
    env.update({"LEYENDAS_RAIZ": DATA_DIR, "HF_HOME": os.path.join(CACHE_DIR, "hf"), "TRANSFORMERS_CACHE": os.path.join(CACHE_DIR, "hf"),
                "HF_PLANTILLA": env.get("HF_PLANTILLA") or os.path.join(aqui, "assets", "hf_plantilla"), "HYPERFRAMES_VERSION": HYPERFRAMES_VERSION,
                "NODE_NO_WARNINGS": "1", "HYPERFRAMES_SKIP_SKILLS": "1", "HYPERFRAMES_NO_TELEMETRY": "1", "PYTHONUNBUFFERED": "1",
                "PYTHONPATH": HERR + os.pathsep + os.path.join(os.path.dirname(os.path.abspath(__file__)), "herramientas_extra") + os.pathsep + env.get("PYTHONPATH", "")})
    return env

def claves_presentes():
    return {"anthropic": bool(ANTHROPIC_API_KEY), "gemini": bool(GEMINI_API_KEY), "elevenlabs": bool(ELEVEN_KEY), "worker_token": bool(WORKER_TOKEN)}
