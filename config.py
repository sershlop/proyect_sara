# 📁 config.py
import os

# Intentar cargar dotenv si está disponible, pero no es obligatorio
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Continuar sin dotenv

# ── SISTEMA ───────────────────────────────────
VERSION           = "0.3.0"
MOSTRAR_CONFIANZA = True

# ── BASE DE DATOS ─────────────────────────────
DB_NAME = "sara.db"

# ── EMBEDDINGS ────────────────────────────────
MODELO_EMBEDDINGS = "paraphrase-multilingual-MiniLM-L12-v2"


WATCHDOG_ACTIVO            = True
WATCHDOG_MODO              = "tiempo"   # "tiempo" o "eventos"
WATCHDOG_INTERVALO_MINUTOS = 30
WATCHDOG_RUTAS_EXTRA = [
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    
]

# ── UMBRALES DE DECISIÓN ──────────────────────
UMBRAL_PREGUNTA   = 0.65
UMBRAL_COMANDO    = 0.60
UMBRAL_INTENCION  = 0.55
UMBRAL_DUPLICADO  = 0.85
UMBRAL_SEMANTICO  = 0.75
UMBRAL_FUSION     = 0.30

# ── PESOS DE SCORING ──────────────────────────
PESO_DIFFLIB   = 0.30
PESO_BD        = 0.25
PESO_SEMANTICO = 0.45

# ── RESPALDO INTELIGENTE ──────────────────────
USAR_RESPALDO_EXTERNO  = True
UMBRAL_MINIMO_RESPALDO = 0.40
GUARDAR_RESPALDO_AUTO  = True
USAR_GEMINI_BACKUP     = True  # Desactivado por defecto - cuota gratuita agotada
UMBRAL_MINIMO_GEMINI   = 0.40
GEMINI_API_KEY         = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL           = "gemini-2.5-flash"
GEMINI_TEMPERATURA     = 0.7
GEMINI_MAX_TOKENS      = 600

# ── GROQ ──────────────────────────────────────────
USAR_GROQ_BACKUP  = True
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL        = "llama-3.1-8b-instant"
GROQ_MODEL_CODIGO = "llama-3.3-70b-versatile"
GROQ_TEMPERATURA  = 0.7
GROQ_MAX_TOKENS   = 700


# ── DEEPSEEK ──────────────────────────────────────
USAR_DEEPSEEK        = True
DEEPSEEK_API_KEY     = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL       = "deepseek-chat"
DEEPSEEK_TEMPERATURA = 0.3
DEEPSEEK_MAX_TOKENS  = 300

#qwen 
# ── QWEN LOCAL ────────────────────────────────
USAR_QWEN            = True
QWEN_MODEL           = "qwen3:0.6b"
QWEN_TEMPERATURA     = 0.2
QWEN_MAX_TOKENS      = 800
USAR_QWEN_RESPUESTAS = False##
USAR_QWEN_CODIGO     = True

# ── BÚSQUEDA Y MOCK ───────────────────────────
BUSQUEDA_EXTERNA_ACTIVA = True
MODO_MOCK               = True
TIMEOUT_EXTERNO         = 8

# ── INTERACCIÓN Y LOGGER ──────────────────────
MAX_PALABRAS_CORTAS = 3
NIVEL_CONSOLA       = "DEBUG"
NIVEL_BD            = "INFO"

# ── VOZ ───────────────────────────────────────
MODO_VOZ        = False
WAKE_WORDS      = {"sara", "zara", "sará", "sarah", "sera"}
TIMEOUT_ESCUCHA = 6
UMBRAL_ENERGIA  = 300
VELOCIDAD_VOZ   = 150
VOLUMEN_VOZ     = 1.0
IDIOMA_VOZ      = "es-MX"

# ── SISTEMA Y HARDWARE ────────────────────────
USAR_CONTROL_SISTEMA = True
INCREMENTO_VOLUMEN   = 10
INCREMENTO_BRILLO    = 10