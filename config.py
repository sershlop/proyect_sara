# 📁 config.py
import os
GUI_PORT = 8765
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
MARGEN_EMPATE     = 0.08

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
QWEN_TEMPERATURA     = 0.0
QWEN_MAX_TOKENS      = 150
USAR_QWEN_RESPUESTAS = False
USAR_QWEN_CODIGO     = True
QWEN_MAX_CONTEXT      = 2048  
QWEN_FORMAT           = "json"

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
SHELL_LISTA_BLANCA: frozenset = frozenset({
    "systeminfo", "wmic", "winver",
    "tasklist", "query process",
    "ipconfig", "nslookup", "ping", "netstat",
    "dir", "tree", "vol",
    "python --version", "python -v",
    "node --version", "node -v",
    "npm --version", "npm -v",
    "git --version",
    "pip --version", "pip -v",
    "pip show", "pip list",
    "where", "echo",
    "powershell -command get-date",
    "powershell -command get-process",
    "powershell -command get-service",
    "powershell -command get-volume",
    "powershell -command get-wmiobject",
    "powershell -command [system.environment]",
})

SHELL_LISTA_NEGRA: frozenset = frozenset({
    "format", "del /f /s /q", "rd /s /q",
    "rmdir /s /q", "erase /f /s /q",
    "reg delete", "reg add hklm", "reg add hkcu\\system",
    "net user /add", "net localgroup administrators",
    "shutdown /f", "shutdown /r /f",
    "netsh firewall set", "netsh advfirewall set allprofiles state off",
    "sc delete", "sc stop",
    "cmd /c del", "cmd /c rd", "cmd /c format",
    "powershell -command remove-item -recurse",
    "powershell -encodedcommand",
    "powershell -enc",
    "iex (", "invoke-expression", "invoke-webrequest",
    "certutil -decode", "bitsadmin /transfer",
})

SHELL_ZONA_AMARILLA: frozenset = frozenset({
    "taskkill", "shutdown", "restart",
    "net stop", "net start", "net user",
    "reg add", "reg export", "reg import",
    "schtasks /create", "schtasks /delete", "schtasks /run",
    "pip install", "pip uninstall",
    "npm install", "npm uninstall",
    "winget install", "winget uninstall",
    "git push", "git reset", "git clean",
    "powershell -command stop-process",
    "powershell -command remove-item",
    "powershell -command set-executionpolicy",
})

# Verbos de reproducción — usados por intent_router.py
VERBOS_REPRODUCCION: frozenset = frozenset({
    "pon", "pone", "ponme", "reproduce", "reproducir", "toca", "tocar",
    "play", "escuchar", "escuchame", "suena", "sonar",
    "activa", "shuffle",
})

# Sentinel — vigilancia proactiva del sistema
SENTINEL_ACTIVO             = True
SENTINEL_INTERVALO_SEGUNDOS = 45    # frecuencia de chequeo del daemon
SENTINEL_UMBRAL_DISCO_PCT   = 10.0  # % libre mínimo antes de alerta
SENTINEL_UMBRAL_RAM_PCT     = 90.0  # % uso máximo antes de alerta
SENTINEL_UMBRAL_BATERIA_PCT = 20    # % carga mínima antes de alerta
SENTINEL_UMBRAL_CPU_APP_PCT  = 40.0   # % CPU sostenido de una app para alertar
SENTINEL_UMBRAL_SARA_CPU_PCT = 40.0   # % CPU de SARA misma para alertar
SENTINEL_UMBRAL_SARA_RAM_MB  = 400.0  # MB RAM de SARA misma para alertar
SENTINEL_CICLOS_CPU_SOSTENIDO = 3     # Ciclos consecutivos antes de alertar por CPU
SENTINEL_COOLDOWN_USB_SEG    = 30.0   # Cooldown para evento USB (corto, es puntual)
SENTINEL_COOLDOWN_CPU_APP_SEG = 300.0 # Cooldown para alerta de app CPU alta (5 min)
# ── GESTIÓN DE ARCHIVOS DESDE LENGUAJE NATURAL ────────────────
VERBOS_GESTION_ARCHIVOS: frozenset = frozenset({
    "crea", "crea la", "crea el", "crea una", "crea un",
    "mueve", "mover", "mueve el", "mueve la",
    "copia", "copiar", "copia el", "copia la",
    "renombra", "renombrar", "renombra el", "renombra la",
    "lista", "listar", "lista el", "lista la", "lista los",
    "elimina", "eliminar", "borra", "borrar",          # zona amarilla
    "pesa", "cuanto pesa", "tamaño de", "peso de",
})

# Rutas base reconocidas por nombre natural
RUTAS_NATURALES: dict = {
    "escritorio":  "Desktop",
    "desktop":     "Desktop",
    "documentos":  "Documents",
    "documents":   "Documents",
    "descargas":   "Downloads",
    "downloads":   "Downloads",
    "imagenes":    "Pictures",
    "pictures":    "Pictures",
    "musica":      "Music",
    "music":       "Music",
    "videos":      "Videos",
    "temp":        "AppData\\Local\\Temp",
}

# Extensiones de texto/datos que se pueden leer con gestión de archivos
EXTENSIONES_LEGIBLES: frozenset = frozenset({
    ".txt", ".md", ".csv", ".json", ".xml", ".ini",
    ".cfg", ".log", ".py", ".js", ".html", ".css", ".bat",
})
# ── RED Y CONECTIVIDAD ────────────────────────────────────────────────
HOSTS_PING_DEFAULT: list = [
    "8.8.8.8",        # Google DNS
    "1.1.1.1",        # Cloudflare DNS
    "208.67.222.222", # OpenDNS
]

VERBOS_RED: frozenset = frozenset({
    "ping", "haz ping", "conectividad", "tengo internet", "hay internet",
    "conexiones activas", "que conexiones", "qué conexiones",
    "velocidad de red", "velocidad de conexion", "adaptadores",
    "mi dns", "el dns", "ver dns", "configuracion de red",
    "abrir puerto", "cerrar puerto", "regla de firewall",
    "ruta de red", "tabla de rutas", "arp",
})
# ── PRODUCTIVIDAD ─────────────────────────────────────────────────────────
VERBOS_TAREA: frozenset = frozenset({
    "añade una tarea", "añade tarea", "agrega una tarea", "agrega tarea",
    "nueva tarea", "crea una tarea", "crear tarea",
    "mis tareas", "ver tareas", "lista de tareas", "que tareas tengo",
    "completar tarea", "marcar tarea", "tarea completada",
    "eliminar tarea", "borrar tarea",
    "tareas pendientes", "tareas de hoy",
})

VERBOS_RECORDATORIO: frozenset = frozenset({
    "recuerdame", "recuérdame", "pon un recordatorio", "crea un recordatorio",
    "añade un recordatorio", "nuevo recordatorio",
    "mis recordatorios", "ver recordatorios", "que recordatorios tengo",
    "eliminar recordatorio", "borrar recordatorio",
    "recordatorio para", "avisame", "avísame",
})

VERBOS_NOTA: frozenset = frozenset({
    "anota", "toma nota", "toma una nota", "nueva nota", "crea una nota",
    "guarda esto", "guarda una nota", "escribe una nota",
    "mis notas", "ver notas", "busca en mis notas",
    "nota fijada", "fijar nota", "borrar nota", "eliminar nota",
})

# Repeticiones de recordatorio reconocidas
REPETICIONES_RECORDATORIO: dict = {
    "diario":    "diario",
    "cada dia":  "diario",
    "cada día":  "diario",
    "semanal":   "semanal",
    "cada semana": "semanal",
    "mensual":   "mensual",
    "cada mes":  "mensual",
    "ninguna":   "ninguna",
    "una vez":   "ninguna",
}
# ── AUTOMATIZACIÓN DE DESARROLLO ─────────────────────────────────────────
VERBOS_DEV: frozenset = frozenset({
    "ejecuta", "ejecutar", "corre", "correr", "lanza", "lanzar",
    "levanta", "levantar", "inicia el servidor", "arranca el servidor",
    "compila", "compilar", "construye", "construir", "build",
    "pruebas", "tests", "testea", "correr tests", "ejecutar tests",
    "entorno virtual", "crea el entorno", "activa el entorno",
    "log de git", "commits", "ultimos commits", "historial git",
    "rama actual", "que rama", "qué rama",
    "ver errores", "errores del log", "ultimos errores",
    "dependencias", "listar dependencias",
})

EXTENSIONES_EJECUTABLES_DEV: frozenset = frozenset({
    ".py", ".js", ".ts", ".sh", ".bat", ".ps1",
    ".rb", ".go", ".rs", ".php",
})

# Puertos de servidores de desarrollo conocidos
PUERTOS_DEV_CONOCIDOS: dict = {
    "django":    8000,
    "flask":     5000,
    "fastapi":   8000,
    "uvicorn":   8000,
    "react":     3000,
    "vue":       5173,
    "angular":   4200,
    "node":      3000,
    "express":   3000,
    "nextjs":    3000,
    "svelte":    5173,
    "vite":      5173,
    "streamlit": 8501,
    "jupyter":   8888,
}