 # 📋 config.py — Configuración central de SARA v2.0 (COMPLETO)
#
# Este archivo es el NÚCLEO de configuración. Si cambia algo aquí,
# afecta a TODO el sistema. Por eso está centralizado en UN solo lugar.

import os
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════
# RUTAS Y DIRECTORIOS
# ═══════════════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent

# El .env se carga AQUÍ, antes de cualquier os.getenv(). Antes se cargaba al final del archivo, cuando
# GEMINI_API_KEY / GROQ_API_KEY / DEEPSEEK_API_KEY ya se habían leído (vacías) → respaldo externo apagado.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")   # utf-8-sig tolera .env guardado con BOM
except ImportError:
    pass
DB_PATH = BASE_DIR / "sara.db"
LOG_PATH = BASE_DIR / "sara.log"

# ═══════════════════════════════════════════════════════════════════════════
# VERSIÓN Y SCHEMA
# ═══════════════════════════════════════════════════════════════════════════

SARA_VERSION = "2.0"
SCHEMA_VERSION_ACTUAL = 3  # Cambiar si alteras BD

# ═══════════════════════════════════════════════════════════════════════════
# OLLAMA / LLM LOCAL
# ═══════════════════════════════════════════════════════════════════════════

OLLAMA_URL = "http://localhost:11434/api/generate"
#QWEN_MODEL = "llama3.2:3b"
QWEN_MODEL = "llava-phi3"
QWEN_TEMPERATURA = 0.0
QWEN_MAX_TOKENS = 150
QWEN_TIMEOUT_SEGUNDOS = 45
QWEN_FORMAT = "json"
QWEN_MAX_CONTEXT = 2048

# ═══════════════════════════════════════════════════════════════════════════
# MODELOS DE EMBEDDINGS
# ═══════════════════════════════════════════════════════════════════════════

MODELO_EMBEDDINGS = "paraphrase-multilingual-MiniLM-L12-v2"

# ═══════════════════════════════════════════════════════════════════════════
# RESPALDO EXTERNO (Gemini, Groq, DeepSeek)
# ═══════════════════════════════════════════════════════════════════════════

def _env(nombre, defecto=""):
    """Lee una variable de entorno sin espacios/comillas sobrantes."""
    return (os.getenv(nombre, defecto) or "").strip().strip('"').strip("'")

# Los NOMBRES DE MODELO cambian con el tiempo (los proveedores retiran modelos): se pueden sobrescribir
# desde el .env (GEMINI_MODEL=..., GROQ_MODEL=..., DEEPSEEK_MODEL=...) sin tocar este archivo.
# max_tokens holgado: los modelos actuales "piensan" y ese razonamiento cuenta contra el límite;
# con 500 el JSON de respuesta puede llegar cortado o vacío.
GEMINI_API_KEY = _env("GEMINI_API_KEY")
USAR_GEMINI_BACKUP = bool(GEMINI_API_KEY)
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_TEMPERATURA = 0.5
GEMINI_MAX_TOKENS = 2048

GROQ_API_KEY = _env("GROQ_API_KEY")
USAR_GROQ_BACKUP = bool(GROQ_API_KEY)
GROQ_MODEL = _env("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_MODEL_CODIGO = _env("GROQ_MODEL_CODIGO", GROQ_MODEL)
GROQ_TEMPERATURA = 0.5
GROQ_MAX_TOKENS = 1024

DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY")
USAR_DEEPSEEK = bool(DEEPSEEK_API_KEY)
DEEPSEEK_MODEL = _env("DEEPSEEK_MODEL", "deepseek-flash")
DEEPSEEK_TEMPERATURA = 0.5
DEEPSEEK_MAX_TOKENS = 2048

RESPALDO_EXTERNO_TIMEOUT_SEGUNDOS = 30

# ═══════════════════════════════════════════════════════════════════════════
# UMBRALES DE CLASIFICACIÓN (Motor de 3 capas)
# ═══════════════════════════════════════════════════════════════════════════

UMBRAL_VECTORIAL_CLASIFICACION = 0.80
UMBRAL_CONFIANZA_QWEN = 0.65
UMBRAL_APRENDIZAJE_CLASIFICACION = 0.75
CACHE_INTENCIONES_TTL_SEGUNDOS = 30

# ═══════════════════════════════════════════════════════════════════════════
# CATEGORÍAS PRINCIPALES (90+ categorías)
# ═══════════════════════════════════════════════════════════════════════════

CATEGORIAS_REGISTRADAS = frozenset({
    # APLICACIONES
    "ABRIR_APLICACION", "CERRAR_APLICACION",
    # ARCHIVOS Y CARPETAS
    "ABRIR_ARCHIVO", "BUSCAR_ARCHIVO", "CREAR_CARPETA", "ABRIR_CARPETA",
    "COPIAR_ARCHIVO", "MOVER_ARCHIVO", "RENOMBRAR_ARCHIVO",
    "ELIMINAR_ARCHIVO", "ELIMINAR_CARPETA", "COMPRIMIR_ARCHIVO",
    "DESCOMPRIMIR_ARCHIVO", "CAMBIAR_PERMISOS_ARCHIVO",
    # NAVEGACIÓN WEB
    "BUSCAR_EN_NAVEGADOR", "ABRIR_NAVEGADOR", "NAVEGADOR_WEB",
    # PRODUCTIVIDAD
    "CREAR_TAREA", "LISTAR_TAREAS", "COMPLETAR_TAREA", "ELIMINAR_TAREA",
    "CREAR_RECORDATORIO", "LISTAR_RECORDATORIOS", "ELIMINAR_RECORDATORIO",
    "CREAR_NOTA", "LISTAR_NOTAS", "ELIMINAR_NOTA",
    # INFORMACIÓN DEL SISTEMA
    "INFO_IP", "INFO_BATERIA", "INFO_RECURSOS", "INFO_NOMBRE",
    "INFO_PROCESOS", "INFO_ALMACENAMIENTO", "INFO_RED",
    # SHELL/COMANDOS
    "COMANDO_SISTEMA", "LISTAR_DIRECTORIO", "CAMBIAR_DIRECTORIO",
    # CONTROL DE PROCESOS
    "LISTAR_PROCESOS", "MATAR_PROCESO", "PRIORIDAD_PROCESO",
    # MEMORIA Y APRENDIZAJE
    "GUARDAR_PREFERENCIA", "GUARDAR_RESTRICCION", "OBTENER_CONTEXTO",
    # ESTADO DEL SISTEMA
    "MONITOREAR_CPU", "MONITOREAR_RAM", "MONITOREAR_DISCO",
    # INFORMACIÓN / CONSULTAS
    "CONSULTA_GENERAL", "PREGUNTA_SISTEMA",
    # AYUDA
    "SOLICITAR_AYUDA", "LISTAR_COMANDOS",
    # INACTIVIDAD / SIN INTENCIÓN
    "DESCONOCIDA",
})

CATEGORIA_DESCONOCIDA = "DESCONOCIDA"

# ═══════════════════════════════════════════════════════════════════════════
# DESCRIPCIONES DE CATEGORÍAS (para intent_engine y UI)
# ═══════════════════════════════════════════════════════════════════════════

DESCRIPCIONES_CATEGORIAS = {
    "ABRIR_APLICACION": "Abre una aplicación del sistema (calc, notepad, Chrome, etc.)",
    "CERRAR_APLICACION": "Cierra una aplicación abierta",
    "ABRIR_ARCHIVO": "Abre un archivo específico con su programa asociado",
    "BUSCAR_ARCHIVO": "Busca archivos en el sistema por nombre",
    "CREAR_CARPETA": "Crea una nueva carpeta",
    "ABRIR_CARPETA": "Abre una carpeta (Descargas, Documentos, etc.)",
    "COPIAR_ARCHIVO": "Copia un archivo a otra ubicación",
    "MOVER_ARCHIVO": "Mueve un archivo a otra carpeta",
    "RENOMBRAR_ARCHIVO": "Cambia el nombre de un archivo",
    "ELIMINAR_ARCHIVO": "Elimina un archivo de forma segura",
    "BUSCAR_EN_NAVEGADOR": "Busca un término en Google o en un navegador",
    "ABRIR_NAVEGADOR": "Abre un navegador web (Chrome, Firefox, Edge)",
    "CREAR_TAREA": "Crea una tarea o TODO para completar",
    "LISTAR_TAREAS": "Muestra todas las tareas pendientes",
    "COMPLETAR_TAREA": "Marca una tarea como completada",
    "CREAR_RECORDATORIO": "Crea un recordatorio para una fecha/hora",
    "CREAR_NOTA": "Crea una nota o apunte",
    "INFO_RECURSOS": "Muestra CPU, RAM, Disco disponible",
    "INFO_IP": "Muestra dirección IP local y pública",
    "INFO_BATERIA": "Muestra estado de la batería",
    "COMANDO_SISTEMA": "Ejecuta un comando de PowerShell",
    "LISTAR_PROCESOS": "Muestra procesos en ejecución",
    "CONSULTA_GENERAL": "Pregunta general al sistema",
    "DESCONOCIDA": "Comando no reconocido o sin categoría clara",
}

# ═══════════════════════════════════════════════════════════════════════════
# VERBOS POR CATEGORÍA (para parsing y dispatch)
# ═══════════════════════════════════════════════════════════════════════════

VERBOS_ABRIR_APLICACION = frozenset({
    "abre", "abrir", "abreme", "abrime", "ejecuta", "ejecutar",
    "inicia", "iniciar", "lanza", "lanzar", "arranca", "arrancar",
    "pon", "corre", "abridor", "levanta", "saca",
})

VERBOS_CERRAR_APLICACION = frozenset({
    "cierra", "cerrar", "cierrame", "finaliza", "finalizar",
    "termina", "terminar", "mata", "matar", "ciérralo", "quita",
    "apaga", "detén", "stop", "fin",
})

VERBOS_BUSCAR_ARCHIVO = frozenset({
    "busca", "buscar", "encuentra", "encontrar", "localiza", "localizar",
    "dónde está", "dame", "get", "find", "search",
})

VERBOS_ABRIR_ARCHIVO = frozenset({
    "abre", "abrir", "lee", "leer", "muestra", "mostrar",
    "visualiza", "visualizar", "edita", "editar",
})

VERBOS_CREAR_CARPETA = frozenset({
    "crea", "crear", "nueva", "nuevo", "hacer", "haz",
})

VERBOS_CREAR_TAREA = frozenset({
    "crea", "crear", "tarea", "TODO", "apunta", "anotar",
    "recordar", "acuerdate", "recuerda", "hazme recordar",
})

VERBOS_CREAR_RECORDATORIO = frozenset({
    "recordar", "recuérdame", "acuerdate", "avísame", "alerta",
    "alarma", "notificación", "notificame",
})

VERBOS_NAVEGADOR_WEB = frozenset({
    "busca", "buscar", "google", "bing", "youtube", "abre",
    "abrir", "entra", "entrar", "abridor", "navega",
})

VERBOS_INFORMACIÓN = frozenset({
    "cuál", "cuantos", "cuanta", "cuales", "información",
    "info", "dime", "dame", "muestra", "mostrar", "cuanto", "qué",
})

# ═══════════════════════════════════════════════════════════════════════════
# PALABRAS DE RELLENO (para quitar_verbos_y_relleno)
# ═══════════════════════════════════════════════════════════════════════════

RELLENO_COMUN = frozenset({
    "la", "el", "los", "las", "mi", "mis", "un", "una",
    "por", "favor", "por favor", "porfa", "plis",
    "aplicacion", "programa", "app", "aplicaciones",
    "programas", "ventana", "de", "en", "archivo", "carpeta",
    "tarea", "todo", "recordatorio", "nota", "navegador",
    "archivo", "archivos", "carpetas", "documentos",
})

# ═══════════════════════════════════════════════════════════════════════════
# APLICACIONES SISTEMA FIJAS (sin buscar en índice)
# ═══════════════════════════════════════════════════════════════════════════

APLICACIONES_SISTEMA_FIJAS = {
    # Nombre normalizado → (exe, argumentos)
    "calculadora": ("C:\\Windows\\System32\\calc.exe", ""),
    "calc": ("C:\\Windows\\System32\\calc.exe", ""),
    "bloc de notas": ("C:\\Windows\\System32\\notepad.exe", ""),
    "notepad": ("C:\\Windows\\System32\\notepad.exe", ""),
    "explorador": ("C:\\Windows\\explorer.exe", ""),
    "explorer": ("C:\\Windows\\explorer.exe", ""),
    "paint": ("C:\\Windows\\System32\\mspaint.exe", ""),
    "cmd": ("C:\\Windows\\System32\\cmd.exe", ""),
    "powershell": ("C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", ""),
    "configuración": ("ms-settings:", ""),
    "settings": ("ms-settings:", ""),
    "administrador de tareas": ("C:\\Windows\\System32\\taskmgr.exe", ""),
    "task manager": ("C:\\Windows\\System32\\taskmgr.exe", ""),
    "regedit": ("C:\\Windows\\regedit.exe", ""),
    "registro": ("C:\\Windows\\regedit.exe", ""),
    "panel de control": ("control.exe", ""),
    "control panel": ("control.exe", ""),
}

# ═══════════════════════════════════════════════════════════════════════════
# NAVEGADORES COMUNES
# ═══════════════════════════════════════════════════════════════════════════

NAVEGADORES_RUTAS = {
    "chrome": [
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    ],
    "firefox": [
        "C:\\Program Files\\Mozilla Firefox\\firefox.exe",
        "C:\\Program Files (x86)\\Mozilla Firefox\\firefox.exe",
    ],
    "edge": [
        "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
        "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    ],
    "explorer": [
        "C:\\Program Files\\Internet Explorer\\iexplore.exe",
    ],
    "safari": [
        "C:\\Program Files\\Safari\\safari.exe",
    ],
    "opera": [
        "C:\\Program Files\\Opera\\opera.exe",
        "C:\\Program Files (x86)\\Opera\\opera.exe",
    ],
}

NAVEGADOR_PREDETERMINADO = "chrome"

# ═══════════════════════════════════════════════════════════════════════════
# BÚSQUEDAS WEB PREDETERMINADAS
# ═══════════════════════════════════════════════════════════════════════════

URLS_NAVEGADOR_COMUNES = {
    "google": "https://www.google.com/search?q=",
    "youtube": "https://www.youtube.com/results?search_query=",
    "bing": "https://www.bing.com/search?q=",
    "github": "https://github.com/search?q=",
    "wikipedia": "https://es.wikipedia.org/wiki/",
    "whatsapp": "https://web.whatsapp.com",
    "gmail": "https://mail.google.com",
    "outlook": "https://outlook.office.com",
    "reddit": "https://www.reddit.com/search/?q=",
}

# ═══════════════════════════════════════════════════════════════════════════
# UMBRALES PARA ACCIONES
# ═══════════════════════════════════════════════════════════════════════════

UMBRAL_ABRIR_APLICACION = 0.80
MARGEN_AMBIGUEDAD_APLICACION = 0.05
UMBRAL_MINIMO_CERRAR_APLICACION = 0.45
TIMEOUT_CIERRE_NORMAL_SEGUNDOS = 5

# ═══════════════════════════════════════════════════════════════════════════
# SHELL / COMANDOS SISTEMA
# ═══════════════════════════════════════════════════════════════════════════

INTERPRETE_SHELL = "powershell.exe"  # shell.py usa flags de PowerShell (-NoProfile -Command); cmd.exe los rechazaba
TIMEOUT_COMANDO_SISTEMA_SEGUNDOS = 30

PLANTILLAS_COMANDO_SISTEMA = {
    "listar directorio": "Get-ChildItem",
    "listar archivos": "Get-ChildItem",
    "mostrar procesos": "Get-Process | Format-Table Name, Id, WorkingSet",
    "limpiar basura": "Remove-Item \"$env:TEMP\\*\" -Recurse -Force -ErrorAction SilentlyContinue",
}
# ── ZONAS DE RIESGO (v2, endurecidas) ──────────────────────────────────────
# Política:  BLANCA = se ejecuta sola (solo lectura, sintaxis estricta) · AMARILLA = TODO LO DEMÁS: pide permiso
#            literal al usuario · NEGRA = NUNCA, aunque la proponga la IA local o la externa y aunque el usuario diga que sí.
# Orden de evaluación (lo hace shell.py): NEGRA primero → luego BLANCA → si no, AMARILLA.
# Las listas están escritas para funcionar igual con re.search, re.match o re.fullmatch, con o sin IGNORECASE.
# Quien las APLICA es shell.py: debe evaluarlas sobre el comando FINAL (después de traducir con la IA).

def _neg(*patrones):
    """Cada patrón negro se envuelve en (?is)\\A.*? : insensible a mayúsculas, multilínea y sirve igual con match,
    search o fullmatch. El ancla \\A es importante: sin ella re.search probaba cada posición y un comando largo
    tardaba minutos (backtracking cuadrático)."""
    return tuple(r"(?is)\A.*?(?:" + p + r")" for p in patrones)


# Raíces de disco y rutas críticas (para los borrados): C:\, D:\*, C:\Windows\..., C:\Program Files\..., C:\ProgramData,
# C:\Users (raíz) y C:\Users\<perfil> (el perfil entero), %SystemRoot%, $env:windir...
_RUTA_CRITICA = (
    r"(?:[a-z]:\\?\*?(?=[\s'\"]|$)"
    r"|[a-z]:\\(?:windows|program\s+files(?:\s+\(x86\))?|programdata|recovery|boot)\b"
    r"|[a-z]:\\users\\?\*?(?=[\s'\"]|$)"
    r"|[a-z]:\\users\\[^\\\s'\"]+\\?\*?(?=[\s'\"]|$)"
    r"|\$?env:(?:systemroot|windir|systemdrive|programfiles|programdata|userprofile|homedrive)\b"
    r"|%(?:systemroot|windir|systemdrive|programfiles|programdata|userprofile)%"
    r"|\\system32\b)"
)
_VERBOS_BORRAR = r"\b(?:remove-item|ri|rm|rmdir|rd|del|erase)\b"

PATRONES_ZONA_NEGRA = _neg(
    # — los originales (se conservan) —
    r"format\s+c:", r"del.*c:\\", r"rm\s+-rf\s+/", r"dd\s+if=", r"dd\s+of=",
    # — 1. discos, particiones, borrado seguro —
    r"format(?:\.com)?\s+[a-z]:", r"\bformat-volume\b", r"\bclear-disk\b", r"\bremove-partition\b",
    r"\binitialize-disk\b", r"\bset-disk\b", r"\bdiskpart\b", r"\bcipher(?:\.exe)?\s+/w", r"\bsdelete\b",
    r"\bfsutil\s+(?:usn|file\s+setzerodata)", r"\bconvert\s+[a-z]:\s+/fs",
    # — 2. borrar raíces de disco, Windows, Program Files, ProgramData o el perfil entero —
    _VERBOS_BORRAR + r".*?" + _RUTA_CRITICA,
    # — 3. tocar la seguridad del sistema —
    r"\b(?:set|add|remove)-mppreference\b", r"\bdisable-windowsoptionalfeature\b",
    r"\bset-netfirewallprofile\b.*-enabled\s+(?:false|0)", r"\bnetsh\b.*\b(?:adv)?firewall\b.*\b(?:off|disable)\b",
    r"\bsc(?:\.exe)?\s+(?:stop|delete|config)\s+(?:windefend|wscsvc|mpssvc|eventlog|winmgmt)\b",
    r"\bstop-service\b.*\b(?:windefend|mpssvc|eventlog|wscsvc)\b", r"\bset-executionpolicy\b", r"\bbcdedit\b",
    r"\bvssadmin\b.*\bdelete\b", r"\bwbadmin\b.*\bdelete\b", r"\bwevtutil\b.*\b(?:cl|clear-log)\b",
    r"\bclear-eventlog\b", r"\btakeown\b", r"\bicacls\b.*(?:/grant|/reset|/deny|/setowner)", r"\bsecedit\b",
    r"\breg(?:\.exe)?\s+(?:delete|add|import|load|restore)\b",
    r"\b(?:remove|set|new)-itemproperty\b.*\bhk(?:lm|cu|ey_local_machine|ey_current_user)\b",
    # — 4. descargar y ejecutar, código oculto, elevación —
    r"\binvoke-expression\b", r"\biex\b", r"-encodedcommand\b",
    r"\b(?:powershell|pwsh)(?:\.exe)?\b.*\s-e(?:nc)?\s+[a-z0-9+/=]{8,}", r"\bfrombase64string\b", r"\[char\]\s*\d",
    r"\bdownload(?:string|file|data|fileasync)\b", r"\bnet\.webclient\b",
    r"\b(?:invoke-webrequest|iwr|invoke-restmethod|irm|curl|wget|start-bitstransfer)\b.*"
    r"\.(?:exe|msi|ps1|bat|cmd|vbs|js|dll|scr|jar|hta)\b",
    r"\b(?:invoke-webrequest|iwr|invoke-restmethod|irm|curl|wget)\b.*\|\s*(?:iex|invoke-expression)\b",
    r"\bcertutil\b.*-(?:urlcache|decode|encode)", r"\bbitsadmin\b", r"\bmshta\b", r"\bregsvr32\b",
    r"\brundll32\b", r"\b(?:wscript|cscript)\b", r"-verb\s+runas\b", r"&\s*\(", r"\.invoke\s*\(",
    r"\[(?:system\.)?reflection\.assembly\]", r"\badd-type\b.*-(?:typedefinition|memberdefinition)\b",
    r"\w`\w",                                   # Remove-Ite`m: el acento grave en medio de una palabra = ofuscación
    # — 5. cuentas, persistencia, robo de credenciales —
    r"\bnet1?\s+user\b.*(?:/add|/delete)", r"\bnet1?\s+localgroup\s+administrators\b",
    r"\b(?:new|remove|set|disable)-localuser\b", r"\badd-localgroupmember\b", r"\bschtasks\b.*/(?:create|change)\b",
    r"\bregister-scheduledtask\b", r"\bnew-service\b", r"\bsc(?:\.exe)?\s+create\b", r"currentversion\\run",
    r"\bwmic\b.*(?:process\s+call|shadowcopy|\bdelete\b)", r"\bnetsh\s+wlan\s+show\s+profile.*key\s*=\s*clear",
    r"\bmimikatz\b", r"\bsekurlsa\b", r"\blsass\b", r"\bntds\.dit\b", r"system32\\config\\sam\b", r"\bcmdkey\b",
    r"\bvaultcmd\b",
    # — 6. matar procesos vitales o todos a la vez —
    r"\b(?:stop-process|taskkill|kill|spps)\b.*\b(?:winlogon|csrss|lsass|wininit|smss|services|svchost|dwm)\b",
    r"\btaskkill\b.*/im\s+\*", r"\bget-process\b\s*\*?\s*\|\s*(?:stop-process|kill|spps)\b",
)

# Lo que se le dice a la IA externa (en lugar de regex crudas, que a un LLM no le sirven).
DESCRIPCION_ZONA_NEGRA = (
    "formatear, borrar o particionar discos (format, Format-Volume, Clear-Disk, diskpart, cipher /w)",
    "borrar la raíz de un disco, C:\\Windows, Program Files, ProgramData, C:\\Users o la carpeta entera de un usuario",
    "tocar el Registro (reg add/delete/import, New-/Set-/Remove-ItemProperty en HKLM o HKCU)",
    "desactivar o modificar antivirus, firewall o servicios de seguridad (Set-MpPreference, netsh advfirewall, sc stop)",
    "Set-ExecutionPolicy, bcdedit, vssadmin delete, wevtutil cl, takeown, icacls /grant, secedit",
    "descargar y ejecutar código (Invoke-Expression/iex, Invoke-WebRequest/curl/wget hacia .exe .ps1 .bat..., DownloadString, certutil, bitsadmin, mshta, rundll32)",
    "comandos codificados u ofuscados (-EncodedCommand, FromBase64String, [char], acento grave dentro de palabras, & (...))",
    "pedir permisos de administrador (-Verb RunAs)",
    "crear usuarios, tareas programadas o servicios, o entrar al grupo de administradores",
    "robar credenciales (mimikatz, lsass, ntds.dit, cmdkey, netsh wlan ... key=clear)",
    "matar procesos vitales (winlogon, csrss, lsass, services...) o todos a la vez (taskkill /im *, Get-Process | Stop-Process)",
)

# BLANCA: solo lectura y sintaxis ESTRICTA: <comando> [argumentos simples]. Anclada de principio a fin, así que
# "dir & del x", "ls (Remove-Item x)", "dir.ps1", "ls.exe" o "Remove-Item C:\tools" ya NO pasan por contener «dir»/«ls».
# Sin tuberías, redirecciones, subexpresiones, bloques ni rutas de red (\\servidor). Un solo argumento libre.
_ARGS_BLANCA = r"(?:\s+[\w\-\.:\\/,*~'\" ]*)?"
_NO_UNC = r"(?!.*(?:^|[\s'\"])\\\\)"
_COMANDOS_BLANCA = (
    r"dir", r"ls", r"gci", r"get-childitem", r"get-process", r"gps", r"get-date", r"get-location", r"pwd",
    r"get-service", r"get-volume", r"get-netipaddress", r"hostname", r"whoami", r"ipconfig", r"systeminfo",
)
PATRONES_ZONA_BLANCA = tuple(r"(?is)^" + _NO_UNC + c + _ARGS_BLANCA + r"$" for c in _COMANDOS_BLANCA)

# Si el comando contiene cualquiera de estos, JAMÁS es blanco (se prueba como subcadena).
OPERADORES_SHELL_PROHIBIDOS_EN_BLANCA = {";", "|", "&&", "||", "&", "`", "$", ">", "<", "(", ")", "{", "}",
                                         "\n", "\r", "@", "%"}

VERBOS_BORRADO_ARCHIVOS = {"borrar", "borrado", "del", "delete", "rm", "eliminar", "quitar"}

PROGRAMAS_INTERACTIVOS = {"cmd.exe", "powershell.exe", "bash", "sh", "python", "python.exe"}

FLAGS_FUERZAN_INTERACTIVO = {"-i", "--interactive", "-noexit", "-NoExit"}

# ═══════════════════════════════════════════════════════════════════════════
# FRASES SIN INTENCIÓN ACCIONABLE
# ═══════════════════════════════════════════════════════════════════════════

FRASES_SIN_INTENCION_ACCIONABLE = frozenset({
    "hola", "hola sara", "buenos dias", "buenas noches",
    "que tal", "cómo estás", "cómo está", "como estas",
    "ayuda", "help", "soporte", "support",
})

# ═══════════════════════════════════════════════════════════════════════════
# PILAR 1: MEMORIA CORE (restricciones, preferencias, contexto)
# ═══════════════════════════════════════════════════════════════════════════

MEMORIA_CORE_ENABLED = True

# ═══════════════════════════════════════════════════════════════════════════
# PILAR 2: HÁBITOS (detección de patrones, proactividad)
# ═══════════════════════════════════════════════════════════════════════════

HABITOS_ENABLED = True
VENTANA_HISTORIAL_HABITOS_DIAS = 30
FRECUENCIA_MINIMA_PATRON = 3  # mínimo 3 veces para considerar un patrón

# ═══════════════════════════════════════════════════════════════════════════
# PILAR 3: SALUD DEL SISTEMA (monitores, alertas)
# ═══════════════════════════════════════════════════════════════════════════

SALUD_SISTEMA_ENABLED = True
UMBRAL_ALERTA_CPU = 85  # % CPU
UMBRAL_ALERTA_RAM = 90  # % RAM
UMBRAL_ALERTA_DISCO = 85  # % Disco
INTERVALO_CHEQUEO_SEGUNDOS = 5

# ═══════════════════════════════════════════════════════════════════════════
# API y CONECTIVIDAD
# ═══════════════════════════════════════════════════════════════════════════

API_TIMEOUT = 30
MAX_RETRIES = 3

# ═══════════════════════════════════════════════════════════════════════════
# LOGS
# ═══════════════════════════════════════════════════════════════════════════

LOG_LEVEL = "INFO"
LOG_MAX_SIZE = 5 * 1024 * 1024  # 5 MB
LOG_BACKUP_COUNT = 3

# ═══════════════════════════════════════════════════════════════════════════
# LOAD ENV VARIABLES (override de configuración por .env)
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# EXTRAS DE INTEGRACIÓN GUI — solo se definen si NO existían más arriba.
# Varios módulos (database, main, productivity, web_navigator, habitos...)
# importan estos nombres de config; si faltan, caían en fallbacks vacíos
# (frozenset()) o directamente en ImportError.
# ═══════════════════════════════════════════════════════════════════════════

def _d(nombre, valor):
    globals().setdefault(nombre, valor)

_d("DB_NAME", str(DB_PATH))                 # database.py hace `from config import DB_NAME`
_d("UMBRAL_CREAR_COMANDO", 3)
_d("UMBRAL_CONFIRMACION_HECHO", 2)

_d("MEMORIA_LOCAL_ACTIVA", True)
_d("MEMORIA_INFO_DIAS", 180)       # una respuesta guardada caduca tras este tiempo
_d("MEMORIA_PURGA_DIAS", 90)       # lo que no se use en este tiempo se borra
# Productividad
_d("MINUTOS_DEFECTO_SIN_FECHA", 60)
_d("REPETICIONES_RECORDATORIO", {"ninguna": 0, "diaria": 1, "semanal": 7})
_d("VERBOS_TAREA", frozenset({"crea una tarea", "crear tarea", "agrega una tarea", "anade una tarea",
                              "nueva tarea", "apunta tarea", "pon una tarea"}))
_d("VERBOS_TAREA_LISTAR", frozenset({"mis tareas", "lista mis tareas", "ver tareas", "muestra mis tareas", "tareas pendientes"}))
_d("VERBOS_TAREA_COMPLETAR", frozenset({"completa la tarea", "marca la tarea", "termine la tarea"}))
_d("VERBOS_TAREA_ELIMINAR", frozenset({"elimina la tarea", "borra la tarea"}))
_d("VERBOS_RECORDATORIO", frozenset({"recuerdame", "crea un recordatorio", "nuevo recordatorio", "avisame"}))
_d("VERBOS_RECORDATORIO_LISTAR", frozenset({"mis recordatorios", "lista recordatorios", "ver recordatorios"}))
_d("VERBOS_RECORDATORIO_ELIMINAR", frozenset({"elimina el recordatorio", "borra el recordatorio"}))
_d("VERBOS_NOTA", frozenset({"crea una nota", "nueva nota", "guarda una nota", "anota", "apunta nota"}))
_d("VERBOS_NOTA_LISTAR", frozenset({"mis notas", "lista mis notas", "ver notas"}))
_d("VERBOS_NOTA_BUSCAR", frozenset({"busca en mis notas", "buscar en notas"}))
_d("VERBOS_NOTA_ELIMINAR", frozenset({"elimina la nota", "borra la nota"}))
_d("CEL_ACTIVO", True)
_d("CEL_HOST", "0.0.0.0")
_d("CEL_PUERTO", 8770)
_d("CEL_POLL_S", 25)

_d("CEL_MAX_ORDENES_MIN", 20)
_d("CEL_REMOTO_BLOQUEADAS", ("llamar", "sms"))  
_d("ENERGIA_VIVA_ACTIVA", True)             # False = SARA no pide a Windows que siga ejecutándose con la pantalla apagada
_d("ENERGIA_VIVA_LATIDO_S", 5)              # cada cuántos segundos mide si el proceso estuvo congelado
_d("ENERGIA_VIVA_UMBRAL_CONGELADA_S", 20)   # hueco mínimo (s) para registrar «🧊 congelada»
# Información del sistema
_d("VERBOS_INFO_IP", frozenset({"cual es mi ip", "mi ip", "dime mi ip", "direccion ip"}))
_d("VERBOS_INFO_BATERIA", frozenset({"cuanta bateria", "estado de la bateria", "nivel de bateria"}))
_d("VERBOS_INFO_RECURSOS", frozenset({"uso de cpu", "uso de ram", "recursos del sistema", "cuanta ram"}))
_d("VERBOS_INFO_NOMBRE", frozenset({"nombre del equipo", "como se llama mi equipo"}))

# Archivos / carpetas / shell
_d("VERBOS_ARCHIVO", frozenset({"busca el archivo", "busca archivo", "encuentra el archivo", "donde esta el archivo", "busca la carpeta"}))
_d("VERBOS_CARPETA_ESPECIAL", frozenset({"abre la carpeta", "abre carpeta", "abre el escritorio", "abre descargas", "abre documentos"}))
_d("VERBOS_COMANDO_SISTEMA", frozenset({"ejecuta el comando", "corre el comando", "ejecuta comando"}))
_d("VERBOS_ELIMINAR_ARCHIVO", frozenset({"elimina el archivo", "borra el archivo", "elimina archivo", "borra archivo",
                                         "elimina la carpeta", "borra la carpeta"}))
_d("VERBOS_MOVER_ARCHIVO", frozenset({"mueve el archivo", "mueve la carpeta", "mover archivo", "mueve"}))
_d("VERBOS_COPIAR_ARCHIVO", frozenset({"copia el archivo", "copia la carpeta", "copiar archivo", "copia"}))
_d("VERBOS_RENOMBRAR_ARCHIVO", frozenset({"renombra el archivo", "renombra", "cambia el nombre de"}))
_d("VERBOS_COMPRIMIR_ARCHIVO", frozenset({"comprime la carpeta", "comprime el archivo", "comprime", "zippea"}))
_d("UMBRAL_ABRIR_ARCHIVO", 0.75)
_d("MARGEN_AMBIGUEDAD_ARCHIVO", 0.05)

# Navegador
_d("VERBOS_BUSCAR_EN_NAVEGADOR", frozenset({"busca en google", "busca en internet", "googlea", "busca en youtube",
                                            "abre youtube", "abre google", "busca en el navegador", "abre gmail", "abre whatsapp"}))
_d("VERBOS_GENERAR_CODIGO", frozenset({"genera un codigo", "escribe un codigo", "hazme un script", "crea un script",
      "programa una funcion", "crea un proyecto", "genera un proyecto", "crea un programa", "hazme un programa",
      "crea una pagina web", "hazme una pagina web", "crea un mini servidor", "crea un archivo de codigo"}))
_d("VERBOS_PREGUNTA_INFORMATIVA", frozenset({"que es", "quien fue", "quien es", "cuentame sobre", "explicame"}))
_d("SITIOS_WEB_CONOCIDOS", {
    "google":    {"alias": ["google"], "url_base": "https://www.google.com/search?q=", "url_home": "https://www.google.com"},
    "youtube":   {"alias": ["youtube"], "url_base": "https://www.youtube.com/results?search_query=", "url_home": "https://www.youtube.com"},
    "bing":      {"alias": ["bing"], "url_base": "https://www.bing.com/search?q=", "url_home": "https://www.bing.com"},
    "github":    {"alias": ["github"], "url_base": "https://github.com/search?q=", "url_home": "https://github.com"},
    "wikipedia": {"alias": ["wikipedia"], "url_base": "https://es.wikipedia.org/w/index.php?search=", "url_home": "https://es.wikipedia.org"},
    "reddit":    {"alias": ["reddit"], "url_base": "https://www.reddit.com/search/?q=", "url_home": "https://www.reddit.com"},
    "gmail":     {"alias": ["gmail"], "url_base": "https://mail.google.com/mail/u/0/#search/", "url_home": "https://mail.google.com"},
    "whatsapp":  {"alias": ["whatsapp"], "url_base": "https://web.whatsapp.com/", "url_home": "https://web.whatsapp.com"},
    "outlook":   {"alias": ["outlook"], "url_base": "https://outlook.office.com/mail/", "url_home": "https://outlook.office.com"},
    "spotify":   {"alias": ["spotify"], "url_base": "https://open.spotify.com/search/", "url_home": "https://open.spotify.com"},
    "netflix":   {"alias": ["netflix"], "url_base": "https://www.netflix.com/search?q=", "url_home": "https://www.netflix.com"},
    "amazon":    {"alias": ["amazon"], "url_base": "https://www.amazon.com.mx/s?k=", "url_home": "https://www.amazon.com.mx"},
    "mercadolibre": {"alias": ["mercadolibre", "mercado libre"], "url_base": "https://listado.mercadolibre.com.mx/", "url_home": "https://www.mercadolibre.com.mx"},
    "twitch":    {"alias": ["twitch"], "url_base": "https://www.twitch.tv/search?term=", "url_home": "https://www.twitch.tv"},
    "tiktok":    {"alias": ["tiktok"], "url_base": "https://www.tiktok.com/search?q=", "url_home": "https://www.tiktok.com"},
    "twitter":   {"alias": ["twitter"], "url_base": "https://x.com/search?q=", "url_home": "https://x.com"},
    "facebook":  {"alias": ["facebook"], "url_base": "https://www.facebook.com/search/top?q=", "url_home": "https://www.facebook.com"},
    "instagram": {"alias": ["instagram"], "url_base": "https://www.instagram.com/", "url_home": "https://www.instagram.com"},
    "linkedin":  {"alias": ["linkedin"], "url_base": "https://www.linkedin.com/search/results/all/?keywords=", "url_home": "https://www.linkedin.com"},
    "maps":      {"alias": ["google maps", "maps", "mapas"], "url_base": "https://www.google.com/maps/search/", "url_home": "https://www.google.com/maps"},
    "drive":     {"alias": ["google drive", "drive"], "url_base": "https://drive.google.com/drive/search?q=", "url_home": "https://drive.google.com"},
})
_d("ALIAS_NAVEGADOR", {
    "chrome":  {"alias": ["chrome", "google chrome"], "procesos": ["chrome.exe"]},
    "firefox": {"alias": ["firefox", "mozilla"], "procesos": ["firefox.exe"]},
    "edge":    {"alias": ["edge", "microsoft edge"], "procesos": ["msedge.exe"]},
    "opera":   {"alias": ["opera"], "procesos": ["opera.exe"]},
})
_d("RUTAS_PREDECIDAS_NAVEGADORES", {
    **{k: list(v) for k, v in NAVEGADORES_RUTAS.items()},
    "chrome": list(NAVEGADORES_RUTAS.get("chrome", [])) +
              ["C:\\Users\\{username}\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe"],
})
_d("UMBRAL_RAM_NAVEGADOR_PESADO_MB", 200)
_d("INSTRUCCIONES_CONVERSACIONALES", frozenset({"por favor", "oye sara", "sara", "podrias", "puedes", "quiero que"}))
_d("VERBOS_PREVIOS_SITIO", frozenset({"abre", "abrir", "entra", "ve", "ir"}))
_d("VERBOS_BUSQUEDA_IGNORAR", frozenset({"busca", "buscar", "buscame", "encuentra", "investiga", "googlea"}))

# Hábitos
_d("VENTANA_HABITOS_SEGUNDOS", 60)
_d("RETENCION_HABITOS_DIAS", 30)
_d("UMBRAL_DETECCIONES_HABITO", 3)
_d("UMBRAL_RECHAZO_DESCARTE", 3)
_d("TOLERANCIA_VENTANA_HORARIA_MINUTOS", 15)
_d("INTERVALO_REINDEXADO_APLICACIONES_SEGUNDOS", 3600)
_d("CEL_MAX_MB", 500)                 # tope por transferencia (ya comprimido)
_d("CEL_CARPETA_RECIBIDO", "")        # "" = <carpeta de config.py>/carpetas_creadas/recibido_celular
_d("CEL_HUELLA_CAPS", ("llamar", "sms", "notificaciones_leer"))   # laptop→celular: piden huella EN el celular
_d("CEL_MAX_CUERPO_SUBIDA", 1_500_000)
_d("CEL_BLOB_TTL_S", 1800)
# Voz de entrada (STT) — stt.py usa estos valores; sin ellos usa los mismos por defecto
_d("STT_MODELO", "base")
_d("STT_IDIOMA", "es")
_d("STT_BEAM", 2)
_d("STT_SILENCIO_CIERRE_S", 0.8)      # silencio que cierra una frase
_d("STT_MIN_VOZ_S", 0.35)             # menos voz que esto se descarta como ruido
_d("STT_UMBRAL_POS_DEFECTO", 35)      # posición inicial del slider (0-100)
_d("STT_REQUERIR_ACTIVACION", True)   # False = atiende cualquier frase (comportamiento anterior)
_d("STT_PALABRAS_ACTIVACION", ("sara", "sarah", "zara", "sarra"))
# Abridor inteligente (abridor_inteligente.py) — todos opcionales
_d("ABRIDOR_ACTIVO", True)
_d("ABRIDOR_PREGUNTAR_EDITOR", True)      # False = usa siempre el último editor elegido para esa extensión
_d("ABRIDOR_PROFUNDIDAD_ARCHIVO", 5)      # niveles de subcarpetas a revisar dentro de la carpeta pedida
_d("ABRIDOR_TIMEOUT_DISCO_S", 4.0)        # tope de la búsqueda en disco de carpetas no indexadas
# Navegador inteligente (navegador_activo.py) — todos opcionales
_d("NAV_INTELIGENTE_ACTIVO", True)        # False = vuelve al orden fijo chrome > edge > firefox...
_d("NAV_RASTREAR_USO", True)              # mide qué navegador tiene el foco (solo segundos por navegador y día)
_d("NAV_RASTREO_INTERVALO_S", 3)          # cada cuántos segundos mira la ventana con el foco
_d("NAV_VIDA_MEDIA_DIAS", 7)              # el uso de hace 7 días pesa la mitad que el de hoy
_d("NAV_SEG_POR_ELECCION", 120)           # cada vez que dices "en <navegador>" suma estos segundos de uso
_d("NAV_BONUS_PREDETERMINADO_S", 600)     # empujón al navegador predeterminado de Windows (cubre el inicio sin datos)
_d("WA_ACTIVO", True)
_d("WA_PUERTO", 8765)
_d("WA_CONFIRMAR_ENVIO", True)          # siempre pregunta antes de mandar a un contacto
_d("WA_IGNORAR_GRUPOS", True)
_d("WA_LEER_CONTENIDO_EN_VOZ", False)   # False: dice "mensaje de Juan" sin leer el contenido en voz alta

_d("WA_MAX_ENVIOS_MIN", 8)

# SARA-Net: control remoto por tu chat contigo mismo (palabra de activación obligatoria)
_d("WA_NET_ACTIVO", False)             # valor inicial; después manda /whatsapp net on|off (se guarda en la BD)
_d("WA_NET_PALABRAS", ("sara", "sarah"))
_d("WA_NET_MAX_EDAD_S", 180)           # una orden más vieja que esto no se ejecuta (llegó tarde)
_d("WA_NET_MAX_COMANDOS_MIN", 4)
_d("WA_NET_POLL_S", 2.0)
_d("WA_NET_VOZ", True)                 # True: además de contestar en el chat, SARA lo dice en voz alta en el PC
_d("WA_NET_CATEGORIAS_BLOQUEADAS", ("COMANDO_SISTEMA", "ELIMINAR_ARCHIVO"))   # no se ejecutan por este canal

# Telegram (telegram_bot.py): bot PRIVADO = canal de control remoto. El token va en .env (TELEGRAM_BOT_TOKEN), NO aquí.
_d("TG_ACTIVO", True)
_d("TG_NET_ACTIVO", False)             # valor inicial; después manda /telegram net on|off (se guarda en la BD)
_d("TG_VOZ_ACTIVO", False)             # aceptar notas de voz (/telegram voz on|off)
_d("TG_RESPUESTA_AUDIO", False)        # contestar además con una nota de voz de SARA (/telegram audio on|off)
_d("TG_BOCINA", False)                 # True: la respuesta también se dice en voz alta en el PC (/telegram bocina on|off)
_d("TG_AVISOS", False)                 # True: recordatorios vencidos también al chat del bot (/telegram avisos on|off)
_d("TG_REQUERIR_PALABRA", False)       # True: cada mensaje debe empezar con «Sara» (/telegram palabra on|off)
_d("TG_PALABRAS", ("sara", "sarah"))
_d("TG_MAX_EDAD_S", 180)               # una orden más vieja que esto no se ejecuta
_d("TG_MAX_COMANDOS_MIN", 6)
_d("TG_VOZ_MAX_S", 30)                 # nota de voz más larga que esto: se rechaza
_d("TG_VOZ_MAX_BYTES", 3_000_000)
_d("TG_SLASH_PERMITIDOS", ("/salud",))  # comandos "/" de SARA aceptados por Telegram (solo lectura)
_d("TG_CATEGORIAS_BLOQUEADAS", ("COMANDO_SISTEMA", "ELIMINAR_ARCHIVO"))   # no se ejecutan por este canal

# Seguridad común de los canales remotos (WhatsApp y Telegram). Se SUMA a WA_NET_/TG_CATEGORIAS_BLOQUEADAS.
_d("REMOTO_CATEGORIAS_BLOQUEADAS", (
    "COMANDO_SISTEMA", "ELIMINAR_ARCHIVO", "ELIMINAR_CARPETA", "CAMBIAR_PERMISOS_ARCHIVO",
    "MATAR_PROCESO", "PRIORIDAD_PROCESO", "CAMBIAR_DIRECTORIO", "EJECUTAR_PAQUETE",
    "GENERAR_CODIGO"))      # GENERAR_CODIGO: puede escribir .bat/.ps1 en una ruta absoluta (p. ej. la carpeta de Inicio)
# LISTA BLANCA opcional: si lleva categorías, por los canales remotos SOLO se ejecutan esas (y nada más).
# () = apagada (solo rige la lista negra). Mira /categorias en la ventana antes de llenarla.
_d("REMOTO_CATEGORIAS_PERMITIDAS", ())
# Comandos "/" que un canal remoto puede mandar (solo lectura). Los demás solo valen desde la ventana de SARA.
_d("REMOTO_SLASH_PERMITIDOS", ("/salud",))

# Modo «IA externa» (comando /ia en la ventana; se guarda en sara_ajustes.json y manda sobre este valor al arrancar).
# True = la limpieza de frases y búsquedas usa Groq/Gemini en lugar de Ollama (más rápido si Ollama va lento).
_d("IA_EXTERNA_PREFERIDA", False)
_d("BUSQUEDA_TIMEOUT_EXTERNO_S", 4.0)       # tiempo máximo de la IA externa al limpiar una búsqueda

# Parámetros que el código ya leía con este valor por defecto y que no estaban declarados aquí (W7).
# Declararlos NO cambia el comportamiento: son los mismos valores. Cámbialos aquí, ya no en el código.
_d('CONV_ACTIVA', True)
_d('CONV_ATAJO_COMANDOS', True)
_d('CONV_ATAJO_MOTOR', True)
_d('CONV_COBERTURA_S', 3.0)
_d('CONV_HISTORIAL_TURNOS', 6)
_d('CONV_MAX_TOKENS', 700)
_d('CONV_MODELO', '')
_d('CONV_NOMBRE_USUARIO', os.getenv('SARA_USUARIO') or 'usuario')
_d('CONV_TEMPERATURA', 0.6)
_d('CONV_TIMEOUT_S', 8.0)
_d('LIMPIEZA_NUM_CTX', 1024)
_d('OLLAMA_KEEP_ALIVE', '30m')
_d('PULSO_CALOR_ACTIVO', True)
_d('PULSO_CALOR_S', 90)
_d('RAPIDO_TIMEOUT_S', 1.5)
_d('VOL_PERMITIR_REMOTO', True)
# Reintento con limpieza de frase (STT ligero): si "abre calculatora" falla, la IA externa la limpia UNA vez
_d("REINTENTO_LIMPIEZA_ACTIVO", True)
_d("REINTENTO_MAX_CARACTERES", 120)
# Cadena de limpieza (un solo ciclo): 1º IA LOCAL (Ollama) → 2º IA EXTERNA (Groq/Gemini) como último filtro
_d("LIMPIEZA_USAR_LOCAL", True)
_d("LIMPIEZA_MODELO_LOCAL", "")             # "" = usa QWEN_MODEL
_d("LIMPIEZA_TIMEOUT_LOCAL_S", 7.0)         # si Ollama no responde en este tiempo, pasa a la externa
_d("LIMPIEZA_PAUSA_LOCAL_S", 60)            # tras un fallo de Ollama, no se vuelve a intentar durante este rato
_d("LIMPIEZA_USAR_EXTERNA", True)
_d("LIMPIEZA_TIMEOUT_EXTERNO_S", 5.0)
_d("LIMPIEZA_MAX_TOKENS", 300)
_d("LIMPIEZA_UMBRAL_PARECIDO", 0.5)         # la propuesta debe parecerse a lo dicho (0-1) o ser un nombre instalado
# Limpieza de BÚSQUEDAS web ("pon X en youtube"): SIEMPRE por la IA local antes de buscar
_d("BUSQUEDA_IA_ACTIVA", True)
_d("BUSQUEDA_TIMEOUT_LOCAL_S", 6.0)         # si Ollama no responde a tiempo, se busca con las reglas de siempre
_d("BUSQUEDA_UMBRAL_PARECIDO", 0.72)        # cada palabra propuesta debe parecerse a algo que dijiste (0-1)
_d("BUSQUEDA_COBERTURA_MIN", 0.6)           # debe conservar al menos este % de lo que ya sacaban las reglas
# Cómo suele oír Whisper algunas palabras → cómo las entiende SARA. Solo se aplica a frases que EMPIEZAN con
# abre/cierra/ejecuta/inicia/lanza. Añade aquí lo que veas que se confunde (clave en minúsculas).
_d("CORRECCIONES_FONETICAS", {
    "geequis": "gx", "ge equis": "gx", "yequis": "gx", "ge x": "gx", "gequis": "gx",
    "yutub": "youtube", "yutube": "youtube", "iutub": "youtube",
    "wasap": "whatsapp", "guasap": "whatsapp", "watsap": "whatsapp",
    "cróm": "chrome", "crom": "chrome",
    "espotifai": "spotify", "spotifay": "spotify",
    "dis cord": "discord", "discor": "discord",
})
# Optimizador proactivo (optimizador.py) — /auto on|off lo guarda sara_ajustes.json
_d("AUTO_OPTIMIZACION", False)
_d("OPT_RAM_AVISO", 80)                 # % de RAM usada: empieza a avisar
_d("OPT_RAM_CRITICO", 90)               # % de RAM usada: crítico (auto puede cerrar basura en segundo plano)
_d("OPT_RAM_RESTAURAR", 65)             # por debajo de esto, auto devuelve la IA a local
_d("OPT_CPU_SOSTENIDO", 85)             # % medio de CPU durante OPT_MUESTRAS_CPU ciclos (~20 s c/u)
_d("OPT_MUESTRAS_CPU", 3)
_d("OPT_CPU_PROCESO_ALTO", 25)          # % de CPU (del total) a partir del cual se le baja la prioridad a un proceso
_d("OPT_REPETICION_S", 900)             # sin /auto: cada cuánto se repite un aviso igual
_d("OPT_LIMPIEZA_INTERVALO_S", 1800)    # limpieza de temporales en modo auto
_d("OPT_TEMP_DIAS", 3)                  # solo temporales con más de N días
_d("OPT_MIN_SEGUNDO_PLANO_MB", 150)     # un proceso sin ventana debe pesar esto para ser candidato
_d("OPT_MAX_CIERRES", 3)                # máx. apps de segundo plano cerradas por ciclo
# Apagar el equipo (apagado.py). Siempre con advertencia, confirmación y cuenta atrás cancelable (shutdown /a).
# Desde la ventana de SARA: modal de confirmación. Desde Telegram: botones + código de 5 dígitos que llega por WhatsApp.
# Por WhatsApp (SARA-Net) NO se permite apagar (solo cancelar el apagado).
_d("APAGADO_ACTIVO", True)                 # False = SARA no apaga el equipo por ningún canal
_d("APAGADO_DEMORA_S", 30)                 # cuenta atrás por defecto (s); «apaga el equipo en 5 minutos» la cambia
_d("APAGADO_DEMORA_MIN_S", 10)
_d("APAGADO_DEMORA_MAX_S", 86400)
_d("APAGADO_REMOTO_PERMITIDO", True)       # False = por Telegram no se puede apagar (solo cancelar)
_d("APAGADO_REMOTO_DEMORA_MIN_S", 30)      # por Telegram nunca menos: hay que poder cancelar
_d("APAGADO_CONFIRM_S", 60)                # segundos para pulsar «Sí, apagar» en Telegram
_d("APAGADO_CODIGO_MODO", "totp")           # "totp" (app autenticadora del celular, por defecto) | "whatsapp" (envío por WA, apagado por defecto) | "auto"
_d("APAGADO_CODIGO_PARA", ("apagar", "reiniciar"))   # acciones que por Telegram piden el código del 2º factor (las demás: solo el botón)
_d("APAGADO_CODIGO_DIGITOS", 5)                # solo modo whatsapp (el TOTP siempre es de 6)
_d("APAGADO_CODIGO_VALIDEZ_S", 120)        # vigencia del código que llega por WhatsApp
_d("APAGADO_CODIGO_INTENTOS", 3)           # tras esos fallos se cancela la solicitud…
_d("APAGADO_BLOQUEO_S", 600)               # …y el apagado remoto queda bloqueado este tiempo (y te avisa por WhatsApp)
# Envío de archivos / pantalla / alertas por Telegram (envio_archivos.py, pantalla.py, alertas.py, desbloqueo.py)
_d("ENVIO_CARPETAS_AUTORIZADAS", ())      # () = Escritorio, Documentos, Descargas, Imágenes. Pon rutas completas para restringir/ampliar
_d("ENVIO_MAX_MB", 45)                    # tope de Telegram para bots: 50 MB
_d("DESBLOQUEO_VENTANA_S", 300)           # un TOTP correcto abre archivos+pantalla este tiempo (apagar/reiniciar siguen pidiéndolo siempre)
_d("DESBLOQUEO_PEDIR_S", 120)             # tiempo para escribir el código tras la petición
_d("DESBLOQUEO_INTENTOS", 3)
_d("DESBLOQUEO_BLOQUEO_S", 600)
_d("PANTALLA_ESCALA", 2)                  # 2 = captura a la mitad de tamaño (archivo más ligero)
_d("PANTALLA_DESCRIBIR", False)           # True = «dime qué pasa en la pantalla» manda la imagen a Gemini (externo)
_d("PANTALLA_MODELOS_VISION", ("gemini-3.5-flash", "gemini-2.5-flash", "gemini-3.1-flash-lite"))   # reserva si el modelo principal falla (503, etc.)
_d("PANTALLA_VISION_LOCAL", True)                   # describir con Ollama (llava-phi3): la imagen no sale de tu PC
_d("PANTALLA_VISION_ORDEN", ("gemini", "local"))   # orden de intento; Gemini solo si PANTALLA_DESCRIBIR está activo
_d("PANTALLA_MODELO_LOCAL", "")                     # "" = usa QWEN_MODEL
_d("PANTALLA_TIMEOUT_LOCAL_S", 90)                  # en CPU, la primera carga del modelo puede tardar
_d("ALERTAS_ACTIVAS", True)               # maestro; lo persiste sara_ajustes.json
_d("ALERTAS_HILO", True)
_d("ALERTAS_INTERVALO_S", 30)
_d("ALERTAS_REPETIR_S", 1800)             # antirrebote por alerta
_d("ALERTAS_BATERIA_PCT", 20)
_d("ALERTAS_BATERIA_CRITICA_PCT", 10)
_d("ALERTAS_DISCO_LIBRE_PCT", 10)
_d("ALERTAS_WIFI_MIN_S", 60)
_d("ALERTAS_CALOR_CPU", 90)
_d("ALERTAS_CALOR_MUESTRAS", 6)           # ×30 s = 3 min de CPU alta
_d("ALERTAS_TEMP_C", 85)                  # solo si el equipo expone sensor
# Switches (interruptores.py): se guardan en sara_ajustes.json y mandan sobre estos valores al arrancar
_d("VERIFICACION_ACTIVA", True)           # False = no se pide el código TOTP en archivos, pantalla, cámara ni apagado remoto
# Deshacer (deshacer.py): papelera propia reversible
_d("DESHACER_ACTIVO", True)               # False = eliminar vuelve a send2trash / borrado de siempre
_d("DESHACER_MAX", 20)                    # operaciones que se recuerdan
_d("DESHACER_PAPELERA_DIAS", 30)          # la papelera de SARA se purga sola pasado este tiempo
# Antirrobo (antirrobo.py) y modos (modos.py)
_d("ANTIRROBO_CAMARA", 0)                 # índice de la webcam
_d("MODOS_MAX_ACCIONES", 10)
# (el .env ya se cargó al inicio del archivo)
CEL_HUELLA_CAPS = ()    # laptop → celular: ninguna orden pide huella (llamar, sms, notificaciones, etc.)
if __name__ == "__main__":
    print("✅ config.py cargado correctamente")
    print(f"📊 {len(CATEGORIAS_REGISTRADAS)} categorías registradas")
    print(f"🔧 Version: {SARA_VERSION}, Schema: {SCHEMA_VERSION_ACTUAL}")