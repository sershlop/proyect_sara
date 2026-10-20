"""Generación de código a partir de lenguaje natural, vía respaldo_externo (v3: multiarchivo robusto).

Módulo aislado (Principio 8): solo ESCRIBE archivos NUEVOS — nunca sobrescribe, nunca ejecuta lo generado.
Aprende por reutilización: la misma descripción ya generada antes no vuelve a llamar a la API (Principio 5).

Intención (se decide SIN gastar API, con el texto del usuario):
    unico     -> "crea un archivo de una calculadora"            -> 1 archivo (flujo de siempre)
    auto      -> "crea una página web / una API / una app"       -> el modelo decide con el MÍNIMO de archivos
    proyecto  -> "mini servidor con .html, .js y .css"           -> varios archivos dentro de su propia carpeta

NOVEDADES v3 (causa raíz de «la API no devolvió ningún archivo»):
    - Parser TOLERANTE: acepta marcadores con adornos de markdown (**<<<ARCHIVO: x>>>**), espacios,
      `>>` en vez de `>>>`, y como RESPALDO bloques ```lang con el nombre del archivo en la línea previa,
      en la valla (```html:index.html) o en un comentario inicial.
    - REINTENTO: si un proveedor devuelve algo sin archivos, se prueba con el otro (Groq -> DeepSeek) y,
      si hace falta, un segundo intento con un prompt más estricto.
    - DIAGNÓSTICO: la respuesta cruda se guarda en codigo_generado/_ultima_respuesta_proyecto.txt y el
      mensaje de error incluye el proveedor, el formato detectado y un fragmento de lo recibido.
    - Más tokens (reasoning de gpt-oss consume parte del tope) y prompt con ejemplo concreto.

Seguridad (la salida del modelo NO es de confianza, y aquí decide rutas de disco):
    - rutas relativas, sin '..', sin unidades (C:), sin '\\\\', sin nombres reservados de Windows
    - solo extensiones de texto/código permitidas (nada de .exe/.dll/.lnk/.reg...)
    - límites de cantidad y tamaño; todo se valida ANTES de escribir; si algo falla se deshace
    - jamás sobrescribe (modo exclusivo 'x'); cada proyecto va a una carpeta nueva
"""

import logging
import os
import re
import unicodedata
from typing import Optional

from database import conectar
from utils import normalizar_texto, extraer_resto

try:
    import config as _cfg
except Exception:
    _cfg = None

try:
    import respaldo_externo
except ImportError:
    respaldo_externo = None

_log = logging.getLogger("SARA-CODIGO")


# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN (todo se puede sobreescribir desde config.py)
# ═══════════════════════════════════════════════════════════════════════════

NOMBRE_CARPETA_CODIGO_GENERADO = getattr(_cfg, "NOMBRE_CARPETA_CODIGO_GENERADO", "codigo_generado")

# nombre (como lo diría el usuario, sin tildes) -> extensión. config.EXTENSIONES_LENGUAJE se suma/pisa.
EXTENSIONES_LENGUAJE = {
    "python": ".py", "py": ".py", "javascript": ".js", "js": ".js", "node": ".js", "nodejs": ".js",
    "typescript": ".ts", "ts": ".ts", "html": ".html", "css": ".css", "java": ".java", "php": ".php",
    "sql": ".sql", "cpp": ".cpp", "csharp": ".cs", "go": ".go", "golang": ".go", "rust": ".rs",
    "ruby": ".rb", "kotlin": ".kt", "swift": ".swift", "lua": ".lua", "bash": ".sh", "sh": ".sh",
    "powershell": ".ps1", "ps1": ".ps1",
}
EXTENSIONES_LENGUAJE.update(getattr(_cfg, "EXTENSIONES_LENGUAJE", {}) or {})

MAX_ARCHIVOS_PROYECTO = int(getattr(_cfg, "GENERADOR_MAX_ARCHIVOS", 10))
MAX_ARCHIVOS_AUTO = 6
MAX_BYTES_ARCHIVO = 200_000
MAX_BYTES_TOTAL = 600_000
MAX_PROFUNDIDAD = 4
# gpt-oss "razona": el razonamiento cuenta dentro del tope. 6000 se quedaba corto para proyectos de varios archivos.
MAX_TOKENS_PROYECTO = int(getattr(_cfg, "GROQ_MAX_TOKENS_PROYECTO", 12000))

EXTENSIONES_PERMITIDAS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".scss", ".json", ".md", ".txt",
    ".csv", ".xml", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".sql", ".java", ".kt", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".php", ".rb", ".lua", ".swift", ".vue", ".svelte", ".svg",
    ".sh", ".bat", ".cmd", ".ps1",           # scripts: se permiten, pero se avisa al usuario
}
EXTENSIONES_SCRIPT = {".sh", ".bat", ".cmd", ".ps1"}
NOMBRES_ESPECIALES = {"dockerfile", "makefile", ".gitignore", ".env.example", "requirements.txt"}
NOMBRES_RESERVADOS_WINDOWS = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}

_LENGUAJE_POR_EXT = {}
for _n, _e in EXTENSIONES_LENGUAJE.items():
    _LENGUAJE_POR_EXT.setdefault(_e, _n)


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        import logger as _logger
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[GENERADOR_CODIGO ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


# ═══════════════════════════════════════════════════════════════════════════
# BASE DE DATOS
# ═══════════════════════════════════════════════════════════════════════════

def crear_tablas() -> None:
    try:
        with conectar() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS codigo_generado_historial (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                descripcion_normalizada TEXT NOT NULL,
                descripcion_original TEXT NOT NULL,
                lenguaje TEXT NOT NULL,
                extension TEXT NOT NULL,
                ruta_archivo TEXT NOT NULL,
                fuente TEXT NOT NULL,
                veces_reutilizado INTEGER DEFAULT 0,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(descripcion_normalizada, lenguaje)
            )
            """)
    except Exception as e:
        _log_error("generador_codigo", "crear_tablas", e)


def _buscar_reutilizable(descripcion_norm: str, lenguaje: Optional[str]) -> Optional[dict]:
    try:
        with conectar() as conn:
            if lenguaje:
                fila = conn.execute(
                    "SELECT * FROM codigo_generado_historial WHERE descripcion_normalizada = ? AND lenguaje = ?",
                    (descripcion_norm, lenguaje),
                ).fetchone()
            else:
                fila = conn.execute(
                    "SELECT * FROM codigo_generado_historial WHERE descripcion_normalizada = ? ORDER BY fecha DESC LIMIT 1",
                    (descripcion_norm,),
                ).fetchone()
            return dict(fila) if fila else None
    except Exception as e:
        _log_error("generador_codigo", "_buscar_reutilizable", e)
        return None


def _marcar_reutilizado(id_: int) -> None:
    try:
        with conectar() as conn:
            conn.execute(
                "UPDATE codigo_generado_historial SET veces_reutilizado = veces_reutilizado + 1 WHERE id = ?",
                (id_,),
            )
    except Exception as e:
        _log_error("generador_codigo", "_marcar_reutilizado", e)


def _registrar_generacion(descripcion_norm: str, descripcion_original: str, lenguaje: str,
                           extension: str, ruta: str, fuente: str) -> None:
    try:
        with conectar() as conn:
            conn.execute("""
                INSERT INTO codigo_generado_historial
                    (descripcion_normalizada, descripcion_original, lenguaje, extension, ruta_archivo, fuente)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(descripcion_normalizada, lenguaje) DO UPDATE SET
                    ruta_archivo = excluded.ruta_archivo,
                    fuente = excluded.fuente,
                    fecha = CURRENT_TIMESTAMP
            """, (descripcion_norm, descripcion_original, lenguaje, extension, ruta, fuente))
    except Exception as e:
        _log_error("generador_codigo", "_registrar_generacion", e)


# ═══════════════════════════════════════════════════════════════════════════
# RUTAS Y NOMBRES
# ═══════════════════════════════════════════════════════════════════════════

def _carpeta_codigo_generado() -> str:
    raiz = os.path.dirname(os.path.abspath(__file__))
    destino = os.path.join(raiz, NOMBRE_CARPETA_CODIGO_GENERADO)
    os.makedirs(destino, exist_ok=True)
    return destino


def _slug(texto: str, maximo: int = 40) -> str:
    slug = normalizar_texto(texto).replace(" ", "_").replace("-", "_")
    slug = re.sub(r"[^a-z0-9_]", "", slug)
    return (slug[:maximo] or "codigo").strip("_") or "codigo"


def _ruta_disponible(carpeta: str, base: str, extension: str) -> str:
    ruta = os.path.join(carpeta, base + extension)
    contador = 2
    while os.path.exists(ruta):
        ruta = os.path.join(carpeta, f"{base}_{contador}{extension}")
        contador += 1
    return ruta


def _carpeta_proyecto_disponible(base_slug: str, raiz: Optional[str] = None) -> str:
    raiz = raiz or _carpeta_codigo_generado()
    destino = os.path.join(raiz, base_slug)
    contador = 2
    while os.path.exists(destino):
        destino = os.path.join(raiz, f"{base_slug}_{contador}")
        contador += 1
    return destino


def _extension_segura(extension: str) -> str:
    """La extensión que devuelve el modelo se usa para construir una ruta: nunca se confía en ella."""
    ext = (extension or "").strip().lower()
    if not ext.startswith("."):
        ext = "." + ext
    if re.fullmatch(r"\.[a-z0-9]{1,6}", ext) and ext in EXTENSIONES_PERMITIDAS:
        return ext
    return ".txt"


def _ruta_relativa_segura(ruta_cruda: str) -> Optional[str]:
    """Valida una ruta relativa propuesta por el modelo. Devuelve la ruta limpia (con '/') o None."""
    r = (ruta_cruda or "").strip().strip("'\"` ").replace("\\", "/")
    while r.startswith("./"):
        r = r[2:]
    if not r or r.startswith("/") or re.match(r"^[a-zA-Z]:", r) or "//" in r:
        return None
    segmentos = r.split("/")
    if len(segmentos) > MAX_PROFUNDIDAD or len(r) > 120:
        return None
    for s in segmentos:
        if s in ("", ".", "..") or len(s) > 60:
            return None
        if re.search(r'[<>:"|?*\x00-\x1f]', s) or s.endswith((" ", ".")):
            return None
        if s.split(".")[0].lower() in NOMBRES_RESERVADOS_WINDOWS:
            return None
    nombre = segmentos[-1].lower()
    ext = os.path.splitext(nombre)[1]
    if nombre not in NOMBRES_ESPECIALES and ext not in EXTENSIONES_PERMITIDAS:
        return None
    return "/".join(segmentos)


def _limpiar_contenido(ruta: str, contenido: str) -> str:
    lineas = contenido.strip("\r\n").split("\n")
    # Algunos modelos envuelven el archivo en ```lang ... ``` pese a que se les pide que no.
    if (len(lineas) >= 2 and re.fullmatch(r"```[\w+-]*", lineas[0].strip())
            and lineas[-1].strip() == "```" and not ruta.lower().endswith(".md")):
        lineas = lineas[1:-1]
    return "\n".join(l.rstrip("\r") for l in lineas).rstrip() + "\n"


# ═══════════════════════════════════════════════════════════════════════════
# INTENCIÓN: ¿un archivo, o un proyecto?
# ═══════════════════════════════════════════════════════════════════════════

_TIPOS = {
    "html": "html", "htm": "html", "css": "css", "js": "javascript", "javascript": "javascript",
    "node": "javascript", "nodejs": "javascript", "ts": "typescript", "typescript": "typescript",
    "py": "python", "python": "python", "java": "java", "php": "php", "cpp": "cpp", "csharp": "csharp",
    "go": "go", "golang": "go", "rust": "rust", "ruby": "ruby", "kotlin": "kotlin", "swift": "swift",
    "lua": "lua", "bash": "bash", "sh": "bash", "powershell": "powershell", "ps1": "powershell",
}
_TIPOS_WEB = {"html", "css", "javascript", "typescript"}
_FRASES_UNICO = (" un solo archivo ", " solo un archivo ", " en un archivo ", " un unico archivo ",
                 " archivo unico ", " single file ", " todo en un archivo ")
_FRASES_PROYECTO = (" proyecto ", " carpeta con ", " estructura de ", " frontend ", " backend ", " fullstack ",
                    " varios archivos ", " multiples archivos ", " varios modulos ", " varios ficheros ")
_PALABRAS_DEBILES = {"servidor", "server", "pagina", "web", "sitio", "website", "landing", "app", "aplicacion",
                     "api", "archivos", "modulos", "interfaz", "gui"}
_NO_SON_ARCHIVOS = {"node.js", "vue.js", "react.js", "next.js", "express.js", "three.js", "d3.js", "nuxt.js", "chart.js"}


def _tokens(texto: str) -> list:
    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-z0-9]+", t)


def _archivos_nombrados(texto: str) -> list:
    """'con index.html y style.css' -> ['index.html', 'style.css'] (solo nombres con extensión permitida)."""
    ext_alt = "|".join(sorted((e.lstrip(".") for e in EXTENSIONES_PERMITIDAS), key=len, reverse=True))
    encontrados = []
    for m in re.finditer(rf"(?<![\w/.-])([\w-]+(?:/[\w-]+)*\.(?:{ext_alt}))(?![\w])", (texto or "").lower()):
        nombre = m.group(1)
        if nombre not in _NO_SON_ARCHIVOS and nombre not in encontrados:
            encontrados.append(nombre)
    return encontrados


def analizar_intencion(texto: str) -> dict:
    """Devuelve {'modo': 'unico'|'auto'|'proyecto', 'archivos': [...], 'tipos': [...]}. No usa API."""
    tokens = _tokens(texto)
    plano = " " + " ".join(tokens) + " "
    tipos = sorted({_TIPOS[t] for t in tokens if t in _TIPOS})
    archivos = _archivos_nombrados(texto)

    if any(f in plano for f in _FRASES_UNICO):
        modo = "unico"
    elif len(archivos) >= 2:
        modo = "proyecto"
    elif len(set(tipos) & _TIPOS_WEB) >= 2:
        modo = "proyecto"
    elif any(f in plano for f in _FRASES_PROYECTO):
        modo = "proyecto"
    elif len(tipos) >= 2 or (set(tokens) & _PALABRAS_DEBILES):
        modo = "auto"
    else:
        modo = "unico"
    return {"modo": modo, "archivos": archivos, "tipos": tipos}


def _detectar_lenguaje_solicitado(texto_norm: str) -> Optional[str]:
    tokens = _tokens(texto_norm)
    for nombre in EXTENSIONES_LENGUAJE:
        if nombre in tokens:
            return nombre
    return None


# ═══════════════════════════════════════════════════════════════════════════
# PEDIR EL PROYECTO AL MODELO Y PARSEARLO
# ═══════════════════════════════════════════════════════════════════════════

# Marcadores tolerantes: se comparan contra la línea SIN adornos de markdown (**, `, #), y aceptan << o <<<.
_RE_ARCHIVO = re.compile(r"^<{2,3}\s*ARCHIVO\s*:\s*(.+?)\s*>{0,3}$", re.I)
_RE_PROYECTO = re.compile(r"^<{2,3}\s*PROYECTO\s*:\s*(.+?)\s*>{0,3}$", re.I)
_RE_NOTAS = re.compile(r"^<{2,3}\s*NOTAS\s*>{0,3}$", re.I)
_RE_FIN = re.compile(r"^<{2,3}\s*FIN\s*>{0,3}$", re.I)
_RE_VALLA = re.compile(r"^\s*(```|~~~)\s*([^\n]*)$")
_RE_NOMBRE_ARCH = re.compile(r"([\w.-]+(?:/[\w.-]+)*\.[A-Za-z0-9]{1,6})")


def _marca(linea: str) -> str:
    """Quita adornos de markdown alrededor de un marcador <<<...>>>."""
    s = linea.strip()
    if "<<" not in s:
        return s
    return s.strip(" \t*`#").strip()


def _construir_prompt_proyecto(descripcion: str, intencion: dict, estricto: bool = False) -> str:
    modo = intencion["modo"]
    maximo = MAX_ARCHIVOS_PROYECTO if modo == "proyecto" else MAX_ARCHIVOS_AUTO
    if modo == "proyecto":
        regla_modo = (f"Genera un PROYECTO de varios archivos (máximo {maximo}) con todo lo necesario para que funcione.")
    else:
        regla_modo = (f"Usa el MÍNIMO de archivos necesarios (máximo {maximo}). Si basta un archivo, entrega solo uno. "
                      "Si es una página o app web, separa en index.html, style.css y script.js.")
    if intencion["archivos"]:
        regla_archivos = ("Debes incluir EXACTAMENTE estos archivos, con estos nombres: "
                          + ", ".join(intencion["archivos"]) + ". Añade otros solo si son imprescindibles.\n")
    else:
        regla_archivos = ""
    extra = ("IMPORTANTE: tu respuesta anterior no se pudo leer. Empieza DIRECTAMENTE con la línea <<<PROYECTO: ...>>>, "
             "sin texto antes, sin ```, sin negritas y sin explicaciones. Cada archivo con su <<<ARCHIVO: nombre>>> "
             "y su <<<FIN>>>.\n\n") if estricto else ""
    return (
        extra +
        "Eres un generador de proyectos de código. Responde SOLO con el formato EXACTO de abajo: sin explicaciones, "
        "sin markdown, sin negritas y sin bloques de backticks. Las líneas marcador (<<<...>>>) van solas en su línea.\n\n"
        "EJEMPLO de formato (solo la forma, el contenido real depende de la petición):\n"
        "<<<PROYECTO: mini_servidor>>>\n"
        "<<<ARCHIVO: server.py>>>\n"
        "print('hola')\n"
        "<<<FIN>>>\n"
        "<<<ARCHIVO: static/index.html>>>\n"
        "<h1>Hola</h1>\n"
        "<<<FIN>>>\n"
        "<<<NOTAS>>>\n"
        "Ejecuta: python server.py\n"
        "<<<FIN>>>\n\n"
        "Es OBLIGATORIO cerrar cada archivo con <<<FIN>>> y poner el bloque <<<NOTAS>>> al final (1 a 3 líneas: cómo ejecutarlo).\n\n"
        f"{regla_modo}\n{regla_archivos}"
        "Reglas: rutas relativas con '/', sin '..' ni rutas absolutas; solo archivos de texto/código; "
        "código COMPLETO y funcional (nada de '...' ni 'resto del código'); los archivos deben enlazarse entre sí "
        "con las rutas correctas; sin librerías externas ni CDN salvo que se pidan; sin claves ni credenciales; "
        "prohibido código que borre archivos, lea credenciales o descargue y ejecute cosas. "
        "Si no se especifica lenguaje o tecnología, usa Python.\n\n"
        f"Petición del usuario: \"{descripcion}\""
    )


def _pedir_proyecto(descripcion: str, intencion: dict, proveedor: str = "groq", estricto: bool = False) -> Optional[dict]:
    """Pide el proyecto a UN proveedor ('groq' | 'deepseek'). Devuelve {'texto', 'fuente'} o None."""
    if respaldo_externo is None:
        return None
    mensajes = [{"role": "user", "content": _construir_prompt_proyecto(descripcion, intencion, estricto)}]
    try:
        if proveedor == "groq":
            llamar = getattr(respaldo_externo, "_llamar_groq", None)
            if not llamar or not respaldo_externo.groq_disponible():
                return None
            contenido = llamar(mensajes, getattr(respaldo_externo, "GROQ_MODEL_CODIGO", ""),
                               MAX_TOKENS_PROYECTO, getattr(respaldo_externo, "GROQ_TEMPERATURA", 0.3),
                               json_mode=False)
        else:
            llamar = getattr(respaldo_externo, "_llamar_deepseek", None)
            if not llamar or not respaldo_externo.deepseek_disponible():
                return None
            contenido = llamar(mensajes)
    except Exception as e:
        _log_error("generador_codigo", f"_pedir_proyecto({proveedor})", e)
        return None
    return {"texto": contenido, "fuente": proveedor} if contenido else None


def _parsear_marcadores(texto: str) -> tuple:
    nombre, notas, archivos = "", [], []
    estado, ruta_actual, buffer = None, None, []
    for linea in (texto or "").replace("\r\n", "\n").split("\n"):
        m = _marca(linea)
        ma = _RE_ARCHIVO.match(m)
        if ma:
            if estado == "archivo":                       # el modelo olvidó cerrar el anterior: el siguiente lo delata
                archivos.append((ruta_actual, "\n".join(buffer)))
            estado, ruta_actual, buffer = "archivo", ma.group(1).strip(" *`\"'"), []
            continue
        if estado is None and _RE_PROYECTO.match(m):
            nombre = _RE_PROYECTO.match(m).group(1).strip(" *`\"'")
            continue
        if _RE_NOTAS.match(m):
            if estado == "archivo":
                archivos.append((ruta_actual, "\n".join(buffer)))
            estado, buffer = "notas", []
            continue
        if _RE_FIN.match(m):
            if estado == "archivo":
                archivos.append((ruta_actual, "\n".join(buffer)))
            elif estado == "notas":
                notas = [l for l in buffer if l.strip()]
            estado, ruta_actual, buffer = None, None, []
            continue
        if estado in ("archivo", "notas"):
            buffer.append(linea)
    truncado = estado == "archivo"                        # terminó sin <<<FIN>>>: respuesta cortada
    if estado == "notas":
        notas = [l for l in buffer if l.strip()]
    return nombre, archivos, notas, truncado


def _nombre_en(texto: str) -> Optional[str]:
    """Primer token con pinta de archivo y extensión permitida dentro de una línea."""
    for m in _RE_NOMBRE_ARCH.finditer(texto or ""):
        n = m.group(1)
        if os.path.splitext(n.lower())[1] in EXTENSIONES_PERMITIDAS or n.lower() in NOMBRES_ESPECIALES:
            return n
    return None


def _parsear_markdown(texto: str) -> list:
    """Respaldo: bloques ```lang ...``` con el nombre en la línea previa, en la info de la valla
    (```html:index.html / ```html title="index.html") o en un comentario de la primera línea del bloque."""
    lineas = (texto or "").replace("\r\n", "\n").split("\n")
    archivos, i = [], 0
    while i < len(lineas):
        m = _RE_VALLA.match(lineas[i])
        if not m:
            i += 1
            continue
        valla, info = m.group(1), m.group(2)
        previa = next((l for l in reversed(lineas[max(0, i - 3):i]) if l.strip()), "")
        j, cuerpo = i + 1, []
        while j < len(lineas) and not lineas[j].strip().startswith(valla):
            cuerpo.append(lineas[j])
            j += 1
        cerrado = j < len(lineas)
        nombre = _nombre_en(info) or _nombre_en(previa)
        if not nombre and cuerpo and re.match(r"^\s*(//|#|<!--|/\*|--)", cuerpo[0]):
            nombre = _nombre_en(cuerpo[0])
            if nombre:
                cuerpo = cuerpo[1:]
        if nombre and cerrado:
            archivos.append((nombre, "\n".join(cuerpo)))
        i = j + 1
    return archivos


def _parsear_proyecto(texto: str) -> dict:
    """Devuelve {'nombre', 'archivos': [(ruta, contenido)], 'notas', 'truncado', 'formato'}."""
    nombre, archivos, notas, truncado = _parsear_marcadores(texto)
    formato = "marcadores"
    if not archivos:
        arch_md = _parsear_markdown(texto)
        if arch_md:
            archivos, formato, truncado = arch_md, "markdown", False
    return {"nombre": nombre, "archivos": archivos, "notas": " ".join(n.strip() for n in notas)[:400],
            "truncado": truncado, "formato": formato}


def _validar_archivos(archivos: list, maximo: int) -> tuple:
    """Devuelve ([(ruta_limpia, contenido)], error). Cualquier archivo inválido invalida TODO el proyecto."""
    if not archivos:
        return [], "el modelo no devolvió ningún archivo"
    if len(archivos) > maximo:
        return [], f"el modelo devolvió {len(archivos)} archivos (máximo {maximo})"
    limpios, vistos, total = [], set(), 0
    for ruta_cruda, contenido in archivos:
        ruta = _ruta_relativa_segura(ruta_cruda)
        if ruta is None:
            return [], f"ruta no permitida: {ruta_cruda!r}"
        if ruta.lower() in vistos:
            return [], f"archivo repetido: {ruta}"
        vistos.add(ruta.lower())
        if "\x00" in contenido:
            return [], f"{ruta} parece binario"
        texto = _limpiar_contenido(ruta, contenido)
        tam = len(texto.encode("utf-8"))
        if not texto.strip():
            return [], f"{ruta} está vacío"
        if tam > MAX_BYTES_ARCHIVO:
            return [], f"{ruta} es demasiado grande"
        total += tam
        if total > MAX_BYTES_TOTAL:
            return [], "el proyecto es demasiado grande"
        limpios.append((ruta, texto))
    return limpios, ""


def _volcar_respuesta(texto: str, proveedor: str, motivo: str) -> str:
    """Guarda la respuesta cruda del modelo para diagnosticar (sobrescribe el volcado anterior; no es código generado)."""
    try:
        ruta = os.path.join(_carpeta_codigo_generado(), "_ultima_respuesta_proyecto.txt")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(f"# proveedor: {proveedor}\n# motivo: {motivo}\n# longitud: {len(texto or '')}\n\n{texto or ''}")
        return ruta
    except Exception as e:
        _log_error("generador_codigo", "_volcar_respuesta", e)
        return ""


def _fragmento(texto: str, n: int = 140) -> str:
    return re.sub(r"\s+", " ", (texto or "")).strip()[:n]


def _escribir_proyecto(carpeta: str, archivos: list) -> list:
    """Escribe todo o nada. Devuelve las rutas absolutas. Lanza excepción (tras deshacer) si algo falla."""
    base_real = os.path.realpath(carpeta)
    creados_archivos, creados_dirs = [], []
    try:
        os.makedirs(carpeta, exist_ok=False)
        creados_dirs.append(carpeta)
        for ruta_rel, contenido in archivos:
            destino = os.path.join(carpeta, *ruta_rel.split("/"))
            if os.path.commonpath([base_real, os.path.realpath(destino)]) != base_real:
                raise ValueError(f"ruta fuera de la carpeta del proyecto: {ruta_rel}")
            sub = os.path.dirname(destino)
            if not os.path.isdir(sub):
                os.makedirs(sub)
                creados_dirs.append(sub)
            with open(destino, "x", encoding="utf-8") as f:       # 'x' = jamás sobrescribe
                creados_archivos.append(destino)
                f.write(contenido)
        return creados_archivos
    except Exception:
        for a in creados_archivos:
            try:
                os.remove(a)
            except OSError:
                pass
        for d in sorted(creados_dirs, key=len, reverse=True):
            try:
                os.rmdir(d)
            except OSError:
                pass
        raise


# ═══════════════════════════════════════════════════════════════════════════
# FLUJOS DE GENERACIÓN
# ═══════════════════════════════════════════════════════════════════════════

def _generar_archivo_unico(descripcion, descripcion_norm, texto_original, lenguaje_solicitado,
                           carpeta: Optional[str] = None, nombre: Optional[str] = None) -> dict:
    """Flujo de siempre: UN archivo (JSON de respaldo_externo.generar_codigo).
    carpeta / nombre (opcionales): destino y nombre pedidos por el usuario («main.py en descargas»)."""
    base_nombre, ext_nombre = (os.path.splitext(nombre) if nombre else ("", ""))
    ext_nombre = ext_nombre.lower() if ext_nombre.lower() in EXTENSIONES_PERMITIDAS else ""
    if ext_nombre and not lenguaje_solicitado:
        lenguaje_solicitado = _LENGUAJE_POR_EXT.get(ext_nombre)        # «main.py» → pide Python al modelo
    generado = respaldo_externo.generar_codigo(descripcion, lenguaje_solicitado)
    if not generado:
        return {"estado": "error", "texto": "No pude generar el código — la API no respondió o falló."}

    ext_pedida = EXTENSIONES_LENGUAJE.get(lenguaje_solicitado) if lenguaje_solicitado else None
    extension = _extension_segura(ext_nombre or ext_pedida or generado.get("extension"))
    lenguaje_final = lenguaje_solicitado or generado.get("lenguaje") or "python"

    ruta_final = _ruta_disponible(carpeta or _carpeta_codigo_generado(),
                                  (base_nombre or nombre) if nombre else _slug(descripcion), extension)
    try:
        with open(ruta_final, "x", encoding="utf-8") as f:
            f.write(generado["codigo"])
    except Exception as e:
        _log_error("generador_codigo", "resolver_generar_codigo (escritura)", e)
        return {"estado": "error", "texto": "Generé el código pero no pude guardarlo en disco."}

    _registrar_generacion(descripcion_norm, texto_original, lenguaje_final, extension, ruta_final, generado["fuente"])
    return {"estado": "generado", "rutas": [ruta_final], "ruta": ruta_final,
            "texto": f"Código generado ({generado['fuente']}) y guardado en: {ruta_final}"}


def _obtener_proyecto_valido(descripcion: str, intencion: dict) -> tuple:
    """Prueba (proveedor, estricto) en orden hasta obtener archivos válidos.
    Devuelve (parsed, archivos, fuente, None) o (None, None, None, mensaje_de_error)."""
    maximo = MAX_ARCHIVOS_PROYECTO if intencion["modo"] == "proyecto" else MAX_ARCHIVOS_AUTO
    intentos = [("groq", False), ("deepseek", False), ("groq", True)]
    bitacora, sin_respuesta = [], True
    for proveedor, estricto in intentos:
        r = _pedir_proyecto(descripcion, intencion, proveedor, estricto)
        etiqueta = proveedor + (" (estricto)" if estricto else "")
        if not r:
            bitacora.append(f"{etiqueta}: sin respuesta")
            continue
        sin_respuesta = False
        parsed = _parsear_proyecto(r["texto"])
        if parsed["truncado"] and not parsed["archivos"]:
            motivo = "respuesta cortada antes de cerrar el primer archivo"
        else:
            archivos, error = _validar_archivos(parsed["archivos"], maximo)
            if not error:
                _log.info(f"✓ proyecto válido vía {etiqueta} ({parsed['formato']}, {len(archivos)} archivos)")
                return parsed, archivos, r["fuente"], None
            motivo = error
        ruta = _volcar_respuesta(r["texto"], etiqueta, motivo)
        _log.warning(f"⚠️ {etiqueta}: {motivo} · formato={parsed['formato']} · respuesta[:140]={_fragmento(r['texto'])!r}")
        bitacora.append(f"{etiqueta}: {motivo}")
        ultimo = (etiqueta, motivo, r["texto"], ruta)
    if sin_respuesta:
        return None, None, None, "No pude generar el proyecto — ninguna API respondió (revisa claves y conexión)."
    etiqueta, motivo, texto, ruta = ultimo
    pista = f" Respuesta guardada en: {ruta}" if ruta else ""
    return None, None, None, (f"No logré un proyecto válido tras {len(bitacora)} intentos ({'; '.join(bitacora)}). "
                              f"Empezaba así: «{_fragmento(texto, 100)}».{pista}")


def _generar_multiarchivo(descripcion, descripcion_norm, texto_original, intencion, carpeta: Optional[str] = None) -> dict:
    parsed, archivos, fuente, error = _obtener_proyecto_valido(descripcion, intencion)
    if error:
        _log_error("generador_codigo", "_generar_multiarchivo (obtención)", ValueError(error))
        return {"estado": "error", "texto": error}

    avisos = []
    scripts = [r for r, _ in archivos if os.path.splitext(r)[1].lower() in EXTENSIONES_SCRIPT]
    if scripts:
        avisos.append(f"Incluye scripts ({', '.join(scripts)}): revísalos antes de ejecutarlos.")

    try:
        if len(archivos) == 1:                                     # el modelo decidió que bastaba uno
            ruta_rel, contenido = archivos[0]
            nombre, ext = os.path.splitext(os.path.basename(ruta_rel))
            ruta_final = _ruta_disponible(carpeta or _carpeta_codigo_generado(), _slug(nombre or descripcion), ext.lower() or ".txt")
            with open(ruta_final, "x", encoding="utf-8") as f:
                f.write(contenido)
            rutas, destino_registro = [ruta_final], ruta_final
            lenguaje = _LENGUAJE_POR_EXT.get(ext.lower(), "texto")
            ext_registro = ext.lower()
            cabecera = f"Código generado ({fuente}) y guardado en: {ruta_final}"
            listado = []
        else:
            carpeta_proy = _carpeta_proyecto_disponible(_slug(parsed["nombre"] or descripcion, 30), carpeta)
            rutas = _escribir_proyecto(carpeta_proy, archivos)
            destino_registro, lenguaje, ext_registro = carpeta_proy, "proyecto", ""
            cabecera = f"Proyecto generado ({fuente}, {len(archivos)} archivos) en: {carpeta_proy}"
            listado = [f"  - {r}" for r, _ in archivos]
    except Exception as e:
        _log_error("generador_codigo", "_generar_multiarchivo (escritura)", e)
        return {"estado": "error", "texto": "Generé el código pero no pude guardarlo en disco (no se dejó nada a medias)."}

    _registrar_generacion(descripcion_norm, texto_original, lenguaje, ext_registro, destino_registro, fuente)
    lineas = [cabecera, *listado]
    if parsed["notas"]:
        lineas.append(f"Cómo usarlo: {parsed['notas']}")
    lineas.extend(avisos)
    return {"estado": "generado", "rutas": rutas, "ruta": destino_registro, "texto": "\n".join(lineas)}


def resolver_generar_codigo(resultado: dict) -> Optional[dict]:
    if resultado.get("categoria") != "GENERAR_CODIGO":
        return None

    texto_original = resultado.get("texto_original", "")
    texto_norm = resultado.get("texto_norm", "") or normalizar_texto(texto_original)
    patron = resultado.get("patron")
    if isinstance(patron, str):                    # extraer_resto espera una lista de verbos, no un str
        patron = [patron]

    descripcion = (extraer_resto(texto_norm, patron) if patron else "").strip() or texto_norm

    # Destino y nombre dichos por el usuario («main.py en descargas»). Vienen de file_manager.analizar_creacion
    # (la misma lógica que crea carpetas: no se duplica). Por la ruta «crea un archivo…» llegan ya resueltos en
    # resultado["creacion"]; por «crea un script…» se intentan aquí y, si no aplican o fallan, todo sigue como siempre.
    creacion = resultado.get("creacion")
    if creacion is None:
        try:
            import file_manager as _fm
            p = _fm.analizar_creacion(texto_original)
            if p["estado"] == "ok" and (p["destino_explicito"] or p["nombre_explicito"]):
                creacion = p
        except Exception as e:
            _log_error("generador_codigo", "resolver_generar_codigo (destino)", e)
    carpeta_destino = (creacion or {}).get("base")
    nombre_archivo = (creacion or {}).get("nombre") if (creacion or {}).get("tipo") == "archivo" else None
    if creacion:
        desc_c = (creacion.get("descripcion") or creacion.get("resto") or "").strip()
        if desc_c:
            descripcion = ("Código " + desc_c) if desc_c.split()[0].lower() in ("que", "para", "con", "donde") else desc_c

    descripcion_norm = normalizar_texto(descripcion)
    intencion = analizar_intencion(texto_original or texto_norm)
    if nombre_archivo:
        intencion = dict(intencion, modo="unico")                 # un nombre de archivo concreto = un solo archivo
    lenguaje_solicitado = _detectar_lenguaje_solicitado(texto_original or texto_norm)

    # Reutilización (archivo o carpeta de proyecto): sin gastar API. Si el usuario pidió un destino o un nombre
    # concreto NO se reutiliza: tiene que quedar donde lo pidió.
    previo = None if (carpeta_destino or nombre_archivo) else \
        _buscar_reutilizable(descripcion_norm, lenguaje_solicitado if intencion["modo"] == "unico" else None)
    if previo and os.path.exists(previo["ruta_archivo"]):
        _marcar_reutilizado(previo["id"])
        return {"estado": "reutilizado", "ruta": previo["ruta_archivo"],
                "texto": f"Ya había generado algo así — reutilizando: {previo['ruta_archivo']}"}

    if respaldo_externo is None or not (respaldo_externo.groq_disponible() or respaldo_externo.deepseek_disponible()):
        return {"estado": "no_disponible",
                "texto": "No tengo configurada ninguna API de generación de código (Groq/DeepSeek) todavía."}

    if intencion["modo"] == "unico":
        return _generar_archivo_unico(descripcion, descripcion_norm, texto_original, lenguaje_solicitado,
                                      carpeta_destino, nombre_archivo)
    return _generar_multiarchivo(texto_original or descripcion, descripcion_norm, texto_original, intencion,
                                 carpeta_destino)
