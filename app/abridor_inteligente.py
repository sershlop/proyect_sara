# 📁 abridor_inteligente.py — Apertura inteligente de archivos y carpetas de SARA (v1)
#
# Qué resuelve:
#   "abre el archivo config.py de la carpeta saradefinitiva"
#   "abre database.py de la carpeta sara 2"            (sara.2 / sara 2 / sara punto 2 son la misma)
#   "abre config.py de la carpeta core de la carpeta saradefinitiva"   (cadena de carpetas)
#   "abre main.py en descargas" · "abre C:\proyectos\app\main.py" · "abre config.py con notepad++"
#
# Cómo (Principio 8: resolver ≠ ejecutar):
#   resolver_apertura(res)  → decide A QUÉ te refieres. NO toca el sistema. Devuelve un "plan" con un estado:
#        "abrir"         listo: {"ruta","editor","es_carpeta"}
#        "elegir"        hay que preguntar (carpeta/archivo ambiguo, o con qué editor): {"pregunta","opciones","continuar"(i)}
#        "confirmar"     acción de riesgo (ejecutar un .exe): {"pregunta","continuar"(bool)}
#        "cancelado" · "no_encontrado" · "no_entendido" · "rechazado": {"texto"}
#        None            la frase no es asunto de este módulo ("abre chrome") → el flujo de siempre sigue igual
#   ejecutar_apertura(plan) → abre de verdad. core_bridge hace las preguntas (modal de la GUI) entre una y otra.
#
# Seguridad:
#   * .py .pyw .bat .cmd .ps1 .js .vbs .reg ... NUNCA se abren con el programa predeterminado (Windows los
#     EJECUTARÍA): solo con un editor. .exe/.msi/.lnk ... piden confirmación explícita.
#   * Rutas críticas del sistema (file_actions._valida_ruta_critica) se rechazan.
#
# Aislado y opcional (corolario v8): si este archivo falta o falla, core_bridge sigue con el flujo anterior.
# Memoria propia (tabla abridor_memoria en sara.db): carpetas que elegiste ante una ambigüedad y el último
# editor por extensión. /abridor muestra el estado; /abridor olvidar la borra.

ABRIDOR_VERSION = "v1-carpetas-editores"

import glob
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections import deque
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger("SARA-ABRIDOR")

try:
    import config as _cfg
except Exception:
    _cfg = None
try:
    import file_index
except Exception:
    file_index = None
try:
    import file_actions
except Exception:
    file_actions = None
try:
    from database import conectar as _conectar
except Exception:
    _conectar = None


def _c(nombre: str, defecto):
    return getattr(_cfg, nombre, defecto) if _cfg is not None else defecto


def _log_error(contexto: str, exc: Exception) -> None:
    """Principio 2: ningún error se silencia."""
    try:
        import logger as _sl
        _sl.log_excepcion("abridor_inteligente", contexto, exc)
    except Exception:
        log.warning("%s: %s: %s", contexto, type(exc).__name__, exc)


# ═══════════════════════════════════════════════════════════════════════════
# PARÁMETROS (todos opcionales en config.py; ver bloque ABRIDOR_* de la entrega)
# ═══════════════════════════════════════════════════════════════════════════

def _activo() -> bool:
    return bool(_c("ABRIDOR_ACTIVO", True))


def _preguntar_editor() -> bool:
    return bool(_c("ABRIDOR_PREGUNTAR_EDITOR", True))


def _prof_archivo() -> int:
    return int(_c("ABRIDOR_PROFUNDIDAD_ARCHIVO", 5))


def _timeout_disco() -> float:
    return float(_c("ABRIDOR_TIMEOUT_DISCO_S", 4.0))


# Se abren SOLO con editor (con el predeterminado Windows los ejecutaría o los fusionaría).
EXT_SOLO_EDITOR = frozenset({"py", "pyw", "pyz", "js", "jse", "bat", "cmd", "ps1", "psm1", "reg",
                             "vbs", "vbe", "wsf", "wsh", "hta", "sh"})
# Código/texto técnico: se ofrece editor (y además "programa predeterminado").
EXT_CON_EDITOR = frozenset(_c("ABRIDOR_EXT_CON_EDITOR", (
    "json", "html", "htm", "css", "xml", "yaml", "yml", "toml", "ini", "cfg", "env", "sql", "ts", "tsx", "jsx",
    "vue", "c", "cpp", "h", "hpp", "java", "cs", "go", "rs", "php", "rb", "lua", "ipynb")))
# Ejecutan algo al abrirse: confirmación explícita.
EXT_EJECUTABLES = frozenset({"exe", "msi", "com", "scr", "jar", "lnk", "appx", "msix"})
EXT_CONOCIDAS = (EXT_SOLO_EDITOR | EXT_CON_EDITOR | EXT_EJECUTABLES |
                 frozenset({"txt", "md", "log", "csv", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
                            "png", "jpg", "jpeg", "gif", "bmp", "svg", "webp", "zip", "rar", "7z", "mp3", "mp4",
                            "wav", "avi", "mkv", "db", "sqlite", "rtf", "odt", "lock", "gitignore"}))

_JUNK = frozenset({"node_modules", "__pycache__", ".git", ".svn", ".hg", ".venv", "venv", "site-packages",
                   "$recycle.bin", "system volume information", ".mypy_cache", ".pytest_cache"})
_SISTEMA = frozenset({"windows", "program files", "program files (x86)", "programdata", "recovery", "msocache",
                      "appdata", "users", "perflogs", "$windows.~bt"})

_ALIAS_CARPETAS = {"escritorio": "Desktop", "desktop": "Desktop", "descargas": "Downloads",
                   "downloads": "Downloads", "documentos": "Documents", "documents": "Documents",
                   "imagenes": "Pictures", "fotos": "Pictures", "pictures": "Pictures", "musica": "Music",
                   "music": "Music", "videos": "Videos"}

_NOMBRES_EDITOR = {"vscode": "Visual Studio Code", "cursor": "Cursor", "pycharm": "PyCharm",
                   "npp": "Notepad++", "sublime": "Sublime Text", "idle": "IDLE (Python)",
                   "notepad": "Bloc de notas", "predeterminado": "el programa predeterminado"}
_EDITORES_ALIAS = {
    "vscode": ("visual studio code", "vs code", "vscode", "visual code"),
    "notepad": ("bloc de notas", "block de notas", "notepad"),
    "npp": ("notepad++", "notepad plus plus", "notepad plus", "notepad mas mas"),
    "pycharm": ("pycharm", "py charm"),
    "sublime": ("sublime text", "sublime"),
    "idle": ("idle",),
    "cursor": ("cursor",),
}
_EDITORES_CON_CARPETA = frozenset({"vscode", "cursor", "pycharm", "sublime"})
_CORTE = 0.85
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ═══════════════════════════════════════════════════════════════════════════
# NORMALIZACIÓN (ojo: a diferencia de utils.normalizar_texto, CONSERVA el punto: "config.py")
# ═══════════════════════════════════════════════════════════════════════════

def _sin_tildes(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(t or "")) if unicodedata.category(c) != "Mn")


def _clave(t: str) -> str:
    """Solo letras y dígitos: 'sara.2', 'sara 2' y 'Sara_2' → 'sara2'."""
    return re.sub(r"[^a-z0-9]", "", _sin_tildes(t).lower())


def _norm(t: str) -> str:
    t = _sin_tildes(t).lower()
    t = re.sub(r"[^a-z0-9._+\- ]", " ", t)
    t = re.sub(r"(?<=\w) punto (?=\w{1,6}(?:\s|$))", ".", t)       # voz: "config punto py" → "config.py"
    t = re.sub(r"\s+", " ", t).strip()
    return " ".join(tok.rstrip(".") for tok in t.split())


_PREFIJOS = ("oye sara", "hey sara", "hola sara", "ok sara", "sara", "por favor", "porfa", "abreme", "abrime",
             "abrir", "abre", "muestrame", "muestra", "edita", "editar", "lee", "leer", "ve a", "ir a")
_RE_TIPO = re.compile(r"^(?:(?:el|la|los|las|un|una|mi|mis)\s+)?"
                      r"(?P<k>archivo|fichero|documento|script|codigo|carpeta|directorio|folder)"
                      r"(?:\s+llamad[oa])?(?:\s+|$)")
_RE_SEP = re.compile(r"""
    \s+
    (?:
        (?:que\s+(?:esta|se\s+encuentra)|ubicad[oa]|guardad[oa]|localizad[oa])\s+(?:dentro\s+de|dentro\s+del|en)
      | dentro\s+(?:de|del)
      | (?:de|del|en)\s+(?:(?:la|el)\s+)?(?:carpeta|directorio|folder)
    )
    (?:\s+(?:la|el|las|los))?
    (?:\s+(?:carpeta|directorio|folder))?
    \s+
""", re.X)
_RE_DEBIL = re.compile(r"^(?:de|del|en|dentro\s+de|dentro\s+del)\s+(?:(?:la|el)\s+)?(?:(?:carpeta|directorio)\s+)?(.+)$")
_RE_EXT_TOKEN = re.compile(r"(?<![\w.])[\w\-]+(?:\.[\w\-]+)*\.(?:" +
                           "|".join(sorted(map(re.escape, EXT_CONOCIDAS), key=len, reverse=True)) + r")(?![\w.])")

_RE_EDITORES: List[Tuple[str, "re.Pattern"]] = []
for _id, _als in _EDITORES_ALIAS.items():
    for _al in _als:
        _RE_EDITORES.append((_id, re.compile(
            r"\s+(?:con|en|usando|mediante|desde)\s+(?:el\s+|la\s+|mi\s+)?(?:programa\s+)?" + re.escape(_al) + r"$")))
_RE_EDITORES.sort(key=lambda x: -len(x[1].pattern))
_RE_EDITORES.append(("predeterminado", re.compile(
    r"\s+(?:con\s+(?:el\s+)?(?:programa\s+)?(?:predeterminado|por\s+defecto)|por\s+defecto)$")))


def _quitar_prefijos(t: str) -> str:
    cambio = True
    while cambio:
        cambio = False
        for p in _PREFIJOS:
            if t.startswith(p + " "):
                t, cambio = t[len(p) + 1:], True
    return t


def _extraer_editor(t: str) -> Tuple[Optional[str], str]:
    for ed_id, rx in _RE_EDITORES:
        m = rx.search(t)
        if m:
            return ed_id, t[:m.start()].strip()
    return None, t


def _extraer_tipo(t: str) -> Tuple[Optional[str], str]:
    m = _RE_TIPO.match(t)
    if not m:
        return None, re.sub(r"^(?:el|la|los|las|un|una|mi|mis)\s+", "", t)
    k = m.group("k")
    kind = "carpeta" if k in ("carpeta", "directorio", "folder") else "archivo"
    rest = t[m.end():]
    if kind == "carpeta":
        rest = re.sub(r"^(?:de|del)\s+(?:la\s+|el\s+)?", "", rest)
    return kind, rest.strip()


def _separar_ext(target: str, kind: Optional[str]) -> Tuple[str, Optional[str]]:
    m = re.match(r"^(?P<stem>.+)\.(?P<ext>[a-z0-9]{1,6})$", target)
    if m and (m.group("ext") in EXT_CONOCIDAS or kind == "archivo"):
        return m.group("stem"), m.group("ext")
    toks = target.split()
    if kind != "carpeta" and len(toks) >= 2 and toks[-1] in EXT_CONOCIDAS:      # "config py"
        return " ".join(toks[:-1]), toks[-1]
    return target, None


def _ruta_literal(orig: str) -> Optional[str]:
    """'abre C:\\proyectos\\app\\main.py' → ruta real si existe (acorta por la derecha hasta dar con ella)."""
    m = re.search(r"([A-Za-z]:[\\/][^\"<>|?*\r\n]*)", orig or "")
    if not m:
        return None
    cand = m.group(1).strip().strip("\"'").rstrip(".,;")
    while cand:
        if os.path.exists(cand):
            return os.path.normpath(cand)
        if " " not in cand:
            break
        cand = cand.rsplit(" ", 1)[0].strip()
    return None


def _analizar(orig: str) -> Optional[dict]:
    """Frase → petición estructurada, o None si no es asunto de este módulo."""
    literal = _ruta_literal(orig)
    t = _quitar_prefijos(_norm(orig))
    t = re.sub(r"\s+por favor$", "", t).strip()
    editor, t = _extraer_editor(t)
    kind, t = _extraer_tipo(t)
    if not t and not literal:
        return {"orig": orig, "literal": None, "editor": editor, "kind": kind, "stem": "", "ext": None,
                "chain": []} if kind else None

    segs = [s.strip() for s in _RE_SEP.split(t) if s.strip()]
    target, chain = (segs[0], segs[1:]) if segs else (t, [])
    chain = [re.sub(r"^(?:el|la|los|las|mi|mis)\s+", "", c) for c in chain]

    if not chain:                                   # separador débil, solo si hay un archivo con extensión
        m = _RE_EXT_TOKEN.search(t)
        if m and m.end() < len(t):
            resto = _RE_DEBIL.match(t[m.end():].strip())
            if resto:
                target, chain = t[:m.end()], [resto.group(1).strip()]

    stem, ext = _separar_ext(target, kind)
    if ext in EXT_EJECUTABLES and kind is None and not chain and not literal:
        return None                                  # "abre calc.exe" → flujo de aplicaciones
    if not (ext or kind or chain or literal):
        return None                                  # "abre chrome" → no es asunto nuestro
    if kind == "carpeta" and not ext and not chain and not literal and _clave(stem) in _ALIAS_CARPETAS:
        return None                                  # "abre la carpeta descargas" → ya lo resuelve file_actions
    return {"orig": orig, "literal": literal, "editor": editor, "kind": kind, "stem": stem, "ext": ext,
            "chain": chain}


# ═══════════════════════════════════════════════════════════════════════════
# MEMORIA PROPIA (sara.db) — carpetas elegidas y último editor por extensión
# ═══════════════════════════════════════════════════════════════════════════

_tabla_ok = False


def crear_tablas() -> None:
    global _tabla_ok
    if _tabla_ok or _conectar is None:
        return
    try:
        with _conectar() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS abridor_memoria ("
                         "clave TEXT PRIMARY KEY, valor TEXT NOT NULL, usos INTEGER DEFAULT 1, "
                         "fecha DATETIME DEFAULT CURRENT_TIMESTAMP)")
            conn.commit()
        _tabla_ok = True
    except Exception as e:
        _log_error("crear_tablas", e)


def _mem_get(clave: str) -> Optional[str]:
    if _conectar is None:
        return None
    try:
        crear_tablas()
        with _conectar() as conn:
            f = conn.execute("SELECT valor FROM abridor_memoria WHERE clave = ?", (clave,)).fetchone()
        return f[0] if f else None
    except Exception as e:
        _log_error("_mem_get", e)
        return None


def _mem_set(clave: str, valor: str) -> None:
    if _conectar is None:
        return
    try:
        crear_tablas()
        with _conectar() as conn:
            conn.execute("INSERT INTO abridor_memoria (clave, valor) VALUES (?, ?) ON CONFLICT(clave) DO UPDATE SET "
                         "valor = excluded.valor, usos = usos + 1, fecha = CURRENT_TIMESTAMP", (clave, valor))
            conn.commit()
    except Exception as e:
        _log_error("_mem_set", e)


def olvidar() -> int:
    """Borra lo aprendido (carpetas y editores). Devuelve cuántas entradas había."""
    if _conectar is None:
        return 0
    try:
        crear_tablas()
        with _conectar() as conn:
            n = conn.execute("SELECT COUNT(*) FROM abridor_memoria").fetchone()[0]
            conn.execute("DELETE FROM abridor_memoria")
            conn.commit()
        return int(n)
    except Exception as e:
        _log_error("olvidar", e)
        return 0


# ═══════════════════════════════════════════════════════════════════════════
# PUNTUACIÓN Y RECORRIDO DE DISCO
# ═══════════════════════════════════════════════════════════════════════════

def _puntuar(q: str, nombre: str, es_archivo: bool = False) -> float:
    base = os.path.splitext(nombre)[0] if es_archivo else nombre
    c = _clave(base)
    if not c or not q:
        return 0.0
    if c == q:
        return 1.0
    partes = [_clave(x) for x in re.split(r"[\s_.\-]+", base) if x]
    if q in partes:
        return 0.92
    if len(q) >= 4 and c.startswith(q):
        return 0.90
    if len(q) >= 5 and q in c:
        return 0.86
    if len(q) >= 4 and abs(len(q) - len(c)) <= 3:
        s = SequenceMatcher(None, q, c).ratio()
        if s >= 0.90:
            return 0.85 + (s - 0.90) * 0.5
    return 0.0


def _recorrer(base: str, max_prof: int, deadline: float, ignorar_sistema: bool = False, tope: int = 60000):
    """BFS: genera (ruta, nombre, es_dir, prof). Lo superficial se ve primero."""
    cola = deque([(base, 0)])
    vistos = 0
    while cola:
        actual, prof = cola.popleft()
        if prof >= max_prof or time.monotonic() > deadline:
            continue
        try:
            with os.scandir(actual) as it:
                for e in it:
                    vistos += 1
                    if vistos > tope:
                        return
                    try:
                        es_dir = e.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    nombre = e.name
                    yield e.path, nombre, es_dir, prof + 1
                    bajo = nombre.lower()
                    if es_dir and bajo not in _JUNK and not (ignorar_sistema and bajo in _SISTEMA):
                        cola.append((e.path, prof + 1))
        except OSError:
            continue


def _dir_proyecto() -> str:
    return os.path.dirname(os.path.abspath(getattr(_cfg, "__file__", None) or __file__))


def _raices_disco() -> List[Tuple[str, int]]:
    raices, vistas = [], set()

    def add(ruta, prof):
        if ruta and os.path.isdir(ruta) and os.path.normcase(os.path.abspath(ruta)) not in vistas:
            vistas.add(os.path.normcase(os.path.abspath(ruta)))
            raices.append((ruta, prof))
    add(os.path.dirname(_dir_proyecto()), 3)
    add(os.path.expanduser("~"), 4)
    if sys.platform.startswith("win"):
        for letra in "CDEFGH":
            add(f"{letra}:\\", 3)
    return raices


def _ruta_alias(q: str) -> Optional[str]:
    nombre = _ALIAS_CARPETAS.get(q)
    if not nombre:
        return None
    ruta = None
    try:
        if file_index is not None:
            ruta = file_index.ruta_carpeta_conocida_windows(file_index.FOLDERIDS_CONOCIDOS.get(nombre))
    except Exception as e:
        _log_error("_ruta_alias", e)
    ruta = ruta or os.path.join(os.path.expanduser("~"), nombre)
    return ruta if os.path.isdir(ruta) else None


def _cand(ruta: str, score: float, origen: str, prof: int = 0, es_carpeta: Optional[bool] = None) -> dict:
    return {"ruta": ruta, "nombre": os.path.basename(ruta.rstrip("\\/")) or ruta, "score": score,
            "origen": origen, "prof": prof,
            "es_carpeta": os.path.isdir(ruta) if es_carpeta is None else es_carpeta}


def _buscar_carpetas(nombre: str, base: Optional[str] = None) -> List[dict]:
    q = _clave(nombre)
    if not q:
        return []
    out: Dict[str, dict] = {}

    def add(c: dict):
        k = os.path.normcase(os.path.abspath(c["ruta"]))
        if os.path.isdir(c["ruta"]) and (k not in out or out[k]["score"] < c["score"]):
            out[k] = c

    if base:                                                       # dentro de una carpeta ya resuelta
        for ruta, nom, es_dir, prof in _recorrer(base, _prof_archivo(), time.monotonic() + _timeout_disco()):
            if es_dir:
                s = _puntuar(q, nom)
                if s >= _CORTE:
                    add(_cand(ruta, s, "disco", prof, True))
        return list(out.values())

    aprendida = _mem_get("carpeta:" + q)                           # lo que elegiste otra vez
    if aprendida and os.path.isdir(aprendida):
        add(_cand(aprendida, 1.0, "aprendido", -1, True))
    ra = _ruta_alias(q)
    if ra:
        add(_cand(ra, 1.0, "alias", 0, True))
    proyecto = _dir_proyecto()
    for p in (proyecto, os.path.dirname(proyecto)):
        s = _puntuar(q, os.path.basename(p))
        if s >= _CORTE:
            add(_cand(p, s, "proyecto", 0, True))
    if file_index is not None:
        try:
            for r in file_index.buscar_archivo(nombre, limite=30, solo_carpetas=True):
                s = _puntuar(q, r.get("nombre", ""))
                if s >= _CORTE:
                    add(_cand(r["ruta"], s, "índice", 0, True))
        except Exception as e:
            _log_error("_buscar_carpetas (índice)", e)
    if not any(c["score"] >= 1.0 for c in out.values()):           # nada exacto: se busca en disco
        deadline = time.monotonic() + _timeout_disco()
        for raiz, prof_max in _raices_disco():
            mejor = None
            for ruta, nom, es_dir, prof in _recorrer(raiz, prof_max, deadline, ignorar_sistema=True):
                if mejor is not None and prof > mejor + 1:           # un nivel más: por si hay un duplicado cercano
                    break
                if es_dir:
                    s = _puntuar(q, nom)
                    if s >= _CORTE:
                        # prof=0: en una búsqueda global los duplicados se PREGUNTAN (no gana "el más superficial")
                        add(_cand(ruta, s, "disco", 0, True))
                        if s >= 1.0 and mejor is None:
                            mejor = prof
                            deadline = min(deadline, time.monotonic() + 1.5)   # a las demás raíces, poco tiempo
            if time.monotonic() > deadline:
                break
    return list(out.values())


def _buscar_archivos(p: dict, base: Optional[str]) -> List[dict]:
    q, ext_req = _clave(p["stem"]), p["ext"]
    out: Dict[str, dict] = {}
    quiere_dirs = p["kind"] is None and not ext_req

    def considerar(ruta, nom, es_dir, prof):
        if es_dir:
            if not quiere_dirs:
                return
            s = _puntuar(q, nom)
        else:
            if p["kind"] == "carpeta":
                return
            if ext_req and os.path.splitext(nom)[1].lower() != "." + ext_req:
                return
            s = _puntuar(q, nom, True)
        if s >= _CORTE:
            out[os.path.normcase(os.path.abspath(ruta))] = _cand(ruta, s, "disco", prof, es_dir)
        return s

    deadline = time.monotonic() + _timeout_disco()
    if base:
        mejor = None
        for ruta, nom, es_dir, prof in _recorrer(base, _prof_archivo(), deadline):
            if mejor is not None and prof > mejor:
                break
            s = considerar(ruta, nom, es_dir, prof)
            if s is not None and s >= 1.0 and mejor is None:
                mejor = prof
        return list(out.values())

    if file_index is not None:                                     # sin carpeta: índice + carpeta del proyecto
        try:
            solo = True if p["kind"] == "carpeta" else (False if (ext_req or p["kind"] == "archivo") else None)
            for r in file_index.buscar_archivo(p["stem"], limite=40, solo_carpetas=solo):
                if os.path.exists(r.get("ruta", "")):
                    considerar(r["ruta"], r.get("nombre", ""), bool(r.get("es_carpeta")), 0)
        except Exception as e:
            _log_error("_buscar_archivos (índice)", e)
    for ruta, nom, es_dir, prof in _recorrer(_dir_proyecto(), 4, deadline):
        considerar(ruta, nom, es_dir, 0 if prof <= 1 else prof)
    return list(out.values())


def _usos(ruta: str) -> int:
    try:
        if file_index is not None:
            f = file_index.obtener_archivo_por_ruta(ruta)
            return int((f or {}).get("veces_accedido") or 0)
    except Exception:
        pass
    return 0


def _decidir(cands: List[dict], margen: float = 0.04) -> Tuple[str, object]:
    """('no', None) | ('ok', cand) | ('ambiguo', [cands]). Lo más superficial gana; el empate real se pregunta."""
    if not cands:
        return "no", None
    cands = sorted(cands, key=lambda c: (-round(c["score"], 4), c["prof"]))
    mejor = cands[0]
    grupo = [c for c in cands if mejor["score"] - c["score"] < margen and c["prof"] == mejor["prof"]]
    if len(grupo) == 1:
        return "ok", mejor
    for c in grupo:
        c["usos"] = _usos(c["ruta"])
    grupo.sort(key=lambda c: -c["usos"])
    if grupo[0]["usos"] > grupo[1]["usos"]:
        return "ok", grupo[0]
    return "ambiguo", grupo


# ═══════════════════════════════════════════════════════════════════════════
# EDITORES INSTALADOS
# ═══════════════════════════════════════════════════════════════════════════

_cache_ed: Tuple[float, List[dict]] = (0.0, [])


def _app_path(exes) -> Optional[str]:
    try:
        import winreg
    except ImportError:
        return None
    for exe in exes:
        clave = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
        for raiz in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for vista in (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)):
                try:
                    with winreg.OpenKey(raiz, clave, 0, winreg.KEY_READ | vista) as k:
                        v = (winreg.QueryValueEx(k, "")[0] or "").strip().strip('"')
                    if v and os.path.isfile(v):
                        return v
                except OSError:
                    continue
    return None


def _glob_pycharm(pf, local) -> List[str]:
    pats = []
    if pf:
        pats.append(os.path.join(pf, "JetBrains", "PyCharm*", "bin", "pycharm64.exe"))
    if local:
        pats.append(os.path.join(local, "Programs", "PyCharm*", "bin", "pycharm64.exe"))
        pats.append(os.path.join(local, "JetBrains", "Toolbox", "apps", "PyCharm*", "*", "*", "bin", "pycharm64.exe"))
    res: List[str] = []
    for pat in pats:
        res += sorted(glob.glob(pat), reverse=True)
    return res


def _escanear_editores() -> List[dict]:
    env = os.environ
    pf, pf86, local = env.get("ProgramFiles"), env.get("ProgramFiles(x86)"), env.get("LOCALAPPDATA")
    win = env.get("SystemRoot") or env.get("windir")

    def j(*partes):
        return os.path.join(*partes) if all(partes) else None

    defs = [
        ("vscode", [j(local, "Programs", "Microsoft VS Code", "Code.exe"), j(pf, "Microsoft VS Code", "Code.exe"),
                    j(pf86, "Microsoft VS Code", "Code.exe")], ["Code.exe"]),
        ("cursor", [j(local, "Programs", "cursor", "Cursor.exe"), j(local, "Programs", "Cursor", "Cursor.exe")],
         ["Cursor.exe"]),
        ("pycharm", _glob_pycharm(pf, local), ["pycharm64.exe"]),
        ("npp", [j(pf, "Notepad++", "notepad++.exe"), j(pf86, "Notepad++", "notepad++.exe"),
                 j(local, "Programs", "Notepad++", "notepad++.exe")], ["notepad++.exe"]),
        ("sublime", [j(pf, "Sublime Text", "sublime_text.exe"), j(pf, "Sublime Text 3", "sublime_text.exe"),
                     j(pf86, "Sublime Text", "sublime_text.exe")], ["sublime_text.exe"]),
    ]
    lista: List[dict] = []
    for ed_id, rutas, apps in defs:
        exe = next((r for r in rutas if r and os.path.isfile(r)), None) or _app_path(apps)
        if exe:
            lista.append({"id": ed_id, "nombre": _NOMBRES_EDITOR[ed_id], "exe": exe, "args": []})
    if not any(e["id"] == "vscode" for e in lista):                # VS Code que solo está en el PATH ("code")
        w = shutil.which("code")
        if w:
            exe = os.path.join(os.path.dirname(os.path.dirname(w)), "Code.exe")
            if os.path.isfile(exe):
                lista.append({"id": "vscode", "nombre": _NOMBRES_EDITOR["vscode"], "exe": exe, "args": []})
    pyw = os.path.join(sys.base_prefix, "pythonw.exe")
    if os.path.isfile(pyw):
        lista.append({"id": "idle", "nombre": _NOMBRES_EDITOR["idle"], "exe": pyw, "args": ["-m", "idlelib"],
                      "solo_ext": frozenset({"py", "pyw"})})
    notepad = os.path.join(win, "System32", "notepad.exe") if win else None
    if notepad and os.path.isfile(notepad):
        lista.append({"id": "notepad", "nombre": _NOMBRES_EDITOR["notepad"], "exe": notepad, "args": []})
    elif sys.platform.startswith("win"):
        lista.append({"id": "notepad", "nombre": _NOMBRES_EDITOR["notepad"], "exe": "notepad.exe", "args": []})
    return lista


def detectar_editores(ext: Optional[str] = None) -> List[dict]:
    """Editores instalados (cacheado 2 min). IDLE solo aplica a .py/.pyw."""
    global _cache_ed
    try:
        if time.monotonic() - _cache_ed[0] > 120 or not _cache_ed[1]:
            _cache_ed = (time.monotonic(), _escanear_editores())
    except Exception as e:
        _log_error("detectar_editores", e)
        return []
    return [e for e in _cache_ed[1] if not e.get("solo_ext") or (ext or "") in e["solo_ext"]]


# ═══════════════════════════════════════════════════════════════════════════
# RESOLUCIÓN (sin tocar el sistema)
# ═══════════════════════════════════════════════════════════════════════════

def _corta(ruta: str, n: int = 60) -> str:
    home = os.path.expanduser("~")
    r = ("~" + ruta[len(home):]) if ruta.lower().startswith(home.lower()) else ruta
    return r if len(r) <= n else "…" + r[-(n - 1):]


def _etiqueta(c: dict, base: Optional[str]) -> str:
    icono = "📁" if c["es_carpeta"] else "📄"
    try:
        if base and os.path.commonpath([os.path.abspath(c["ruta"]), os.path.abspath(base)]) == os.path.abspath(base):
            return f"{icono} {os.path.relpath(c['ruta'], base)}"
    except ValueError:
        pass
    return f"{icono} {_corta(c['ruta'])}"


def _pedir_cual(pregunta: str, cands: List[dict], base: Optional[str], siguiente: Callable) -> dict:
    cands = cands[:5]
    return {"estado": "elegir", "pregunta": pregunta, "opciones": [_etiqueta(c, base) for c in cands],
            "continuar": lambda i: siguiente(cands[int(i)])}


def _critica(ruta: str) -> bool:
    try:
        if file_actions is not None:
            return bool(file_actions._valida_ruta_critica(ruta))
    except Exception:
        pass
    r = (ruta or "").lower()
    return any(r.startswith(x) for x in ("c:\\windows", "c:\\program files", "c:\\programdata"))


def _paso_cadena(p: dict, pendientes: List[str], base: Optional[str]) -> dict:
    if not pendientes:
        return _paso_objetivo(p, base)
    nombre = pendientes[0]
    est, dato = _decidir(_buscar_carpetas(nombre, base))
    if est == "no":
        donde = f" dentro de «{os.path.basename(base.rstrip(os.sep)) or base}»" if base else ""
        return {"estado": "no_encontrado", "texto": f"No encontré la carpeta «{nombre}»{donde}."}
    if est == "ambiguo":
        return _pedir_cual(f"Encontré varias carpetas «{nombre}». ¿Cuál es?", dato, base,
                           lambda c: _tras_carpeta(p, pendientes, nombre, c, base is None))
    # Salió de una búsqueda en disco (lenta): se aprende para que la próxima vez sea instantáneo.
    return _tras_carpeta(p, pendientes, nombre, dato, base is None and dato.get("origen") == "disco")


def _tras_carpeta(p: dict, pendientes: List[str], nombre: str, cand: dict, aprender: bool) -> dict:
    if aprender:
        _mem_set("carpeta:" + _clave(nombre), cand["ruta"])
    return _paso_cadena(p, pendientes[1:], cand["ruta"])


def _paso_objetivo(p: dict, base: Optional[str]) -> dict:
    if p["kind"] == "carpeta" and not p["ext"]:
        cands = _buscar_carpetas(p["stem"], base)
    else:
        cands = _buscar_archivos(p, base)
    est, dato = _decidir(cands)
    cosa = "la carpeta" if p["kind"] == "carpeta" else "el archivo" if (p["kind"] == "archivo" or p["ext"]) else "nada llamado"
    nombre_txt = p["stem"] + (("." + p["ext"]) if p["ext"] else "")
    if est == "no":
        donde = f" en «{os.path.basename(base.rstrip(os.sep)) or base}»" if base else ""
        return {"estado": "no_encontrado", "texto": f"No encontré {cosa} «{nombre_txt}»{donde}."}
    if est == "ambiguo":
        return _pedir_cual(f"Encontré varios «{nombre_txt}». ¿Cuál abro?", dato, base,
                           lambda c: _tras_objetivo(p, c, aprender=(base is None and c["es_carpeta"])))
    return _tras_objetivo(p, dato, aprender=(base is None and dato["es_carpeta"] and dato.get("origen") == "disco"))


def _tras_objetivo(p: dict, cand: dict, aprender: bool = False) -> dict:
    ruta = cand["ruta"]
    if _critica(ruta):
        return {"estado": "rechazado", "texto": "Esa ruta es del sistema; no la abro."}
    if aprender:
        _mem_set("carpeta:" + _clave(p["stem"]), ruta)
    if cand["es_carpeta"] or os.path.isdir(ruta):
        ed = p["editor"] if p["editor"] in _EDITORES_CON_CARPETA and any(
            e["id"] == p["editor"] for e in detectar_editores()) else None
        return {"estado": "abrir", "ruta": ruta, "editor": ed, "es_carpeta": True}
    return _plan_archivo(p, ruta)


def _plan_archivo(p: dict, ruta: str) -> dict:
    if p.get("solo_resolver"):          # envío por Telegram: solo resolver la ruta, sin preguntar por editor
        return {"estado": "abrir", "ruta": ruta, "editor": None, "es_carpeta": False}
    ext = os.path.splitext(ruta)[1].lower().lstrip(".")
    nombre = os.path.basename(ruta)
    if ext in EXT_EJECUTABLES:
        def _tras(ok):
            return ({"estado": "abrir", "ruta": ruta, "editor": None, "es_carpeta": False} if ok
                    else {"estado": "cancelado", "texto": "Cancelado, no ejecuté nada."})
        return {"estado": "confirmar", "continuar": _tras,
                "pregunta": f"«{nombre}» es un programa: abrirlo lo EJECUTA. ¿Lo ejecuto?\n    {ruta}"}
    if ext in EXT_SOLO_EDITOR or ext in EXT_CON_EDITOR or p["editor"]:
        return _plan_editor(p, ruta, ext, nombre)
    return {"estado": "abrir", "ruta": ruta, "editor": None, "es_carpeta": False}


def _plan_editor(p: dict, ruta: str, ext: str, nombre: str) -> dict:
    solo = ext in EXT_SOLO_EDITOR
    editores = detectar_editores(ext)
    pedido, aviso = p["editor"], ""

    def abrir(ed_id):
        return {"estado": "abrir", "ruta": ruta, "editor": ed_id, "es_carpeta": False}

    if pedido == "predeterminado":
        if not solo:
            return abrir(None)
        aviso = f"Los .{ext} no los abro con el programa predeterminado (Windows los ejecutaría). "
    elif pedido:
        if any(e["id"] == pedido for e in editores):
            return abrir(pedido)
        aviso = f"No encontré {_NOMBRES_EDITOR.get(pedido, pedido)} instalado. "

    if not editores:
        return abrir(None) if not solo or not sys.platform.startswith("win") else abrir("notepad")
    if len(editores) == 1:                                         # solo el Bloc de notas: sin preguntar
        return abrir(editores[0]["id"] if solo else None)

    ids = [e["id"] for e in editores]
    etiquetas = {e["id"]: e["nombre"] for e in editores}
    pref = _mem_get("editor:" + ext)
    if pref in ids:
        if not _preguntar_editor() and not pedido:
            return abrir(pref)
        ids.remove(pref)
        ids.insert(0, pref)
        etiquetas[pref] += "  (el de la última vez)"
    if not solo:
        ids.append("predeterminado")
        etiquetas["predeterminado"] = "Programa predeterminado"

    def _eligio(i):
        ed_id = ids[int(i)]
        _mem_set("editor:" + ext, ed_id)
        return abrir(None if ed_id == "predeterminado" else ed_id)
    return {"estado": "elegir", "pregunta": f"{aviso}¿Con qué abro «{nombre}»?",
            "opciones": [etiquetas[i] for i in ids], "continuar": _eligio}


def resolver_apertura(res: dict) -> Optional[dict]:
    """Punto de entrada para core_bridge. None = no es asunto mío (o falló): el flujo anterior sigue intacto."""
    if not _activo():
        return None
    try:
        p = _analizar(res.get("texto_original") or "")
        
        if p is None:
            return None
        p["solo_resolver"] = bool(res.get("solo_resolver"))   # envío: resolver sin preguntar por editor
        if p["literal"]:
            lit = p["literal"]
            return _tras_objetivo(p, _cand(lit, 1.0, "ruta"))
        if not p["stem"] and not p["chain"]:
            return {"estado": "no_entendido", "texto": "¿Qué archivo o carpeta quieres abrir?"}
        plan = _paso_cadena(p, list(reversed(p["chain"])), None)      # de la carpeta más externa a la más interna
        if (plan.get("estado") == "no_encontrado" and res.get("categoria") != "ABRIR_ARCHIVO"
                and not p["chain"] and not p["ext"]):
            return None                  # "abre el documento de word": que lo intente el flujo de aplicaciones
        return plan
    except Exception as e:
        _log_error("resolver_apertura", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# EJECUCIÓN
# ═══════════════════════════════════════════════════════════════════════════

def _abrir_predeterminado(ruta: str) -> None:
    if hasattr(os, "startfile"):
        os.startfile(ruta)
    else:                                                          # solo pruebas fuera de Windows
        subprocess.Popen(["xdg-open", ruta], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _lanzar_editor(ed: dict, ruta: str) -> None:
    subprocess.Popen([ed["exe"], *ed.get("args", []), ruta], shell=False, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_SIN_VENTANA,
                     close_fds=True)


def ejecutar_apertura(plan: dict) -> dict:
    """-> {"exito","texto","ruta","nombre","no_habito"}. Nunca lanza."""
    ruta = (plan or {}).get("ruta") or ""
    nombre = os.path.basename(ruta.rstrip("\\/")) or ruta
    try:
        if not ruta or not os.path.exists(ruta):
            return {"exito": False, "texto": f"Ya no existe: {ruta}", "ruta": ruta, "nombre": nombre, "no_habito": True}
        if _critica(ruta):
            return {"exito": False, "texto": "Esa ruta es del sistema; no la abro.", "ruta": ruta,
                    "nombre": nombre, "no_habito": True}
        ext = os.path.splitext(ruta)[1].lower().lstrip(".")
        ed_id = plan.get("editor")
        ed = next((e for e in detectar_editores(ext) if e["id"] == ed_id), None) if ed_id else None
        if ed_id and ed is None and ext in EXT_SOLO_EDITOR:          # nunca startfile sobre .py/.bat/...
            ed = next((e for e in detectar_editores(ext) if e["id"] == "notepad"), None)
        if ed is not None:
            _lanzar_editor(ed, ruta)
            texto = f"Abriendo {nombre} con {ed['nombre']}..."
        elif ext in EXT_SOLO_EDITOR:
            return {"exito": False, "texto": f"No encontré ningún editor para abrir {nombre} sin ejecutarlo.",
                    "ruta": ruta, "nombre": nombre, "no_habito": True}
        else:
            _abrir_predeterminado(ruta)
            texto = f"Abriendo {'la carpeta ' if plan.get('es_carpeta') else ''}{nombre}..."
        if file_index is not None:
            try:
                file_index.registrar_ruta(ruta)
                file_index.registrar_acceso(ruta)
            except Exception as e:
                _log_error("ejecutar_apertura (índice)", e)
        # Un hábito reabre con startfile: para código/ejecutables eso EJECUTARÍA el script → no se registra.
        return {"exito": True, "texto": texto, "ruta": ruta, "nombre": nombre,
                "no_habito": ext in EXT_SOLO_EDITOR or ext in EXT_EJECUTABLES}
    except Exception as e:
        _log_error("ejecutar_apertura", e)
        return {"exito": False, "texto": f"Encontré {nombre} pero no pude abrirlo.", "ruta": ruta,
                "nombre": nombre, "no_habito": True}


def diagnostico() -> str:
    eds = detectar_editores()
    aprendido = []
    if _conectar is not None:
        try:
            crear_tablas()
            with _conectar() as conn:
                aprendido = [f"{f[0]} → {f[1]}" for f in conn.execute(
                    "SELECT clave, valor FROM abridor_memoria ORDER BY fecha DESC LIMIT 8").fetchall()]
        except Exception as e:
            _log_error("diagnostico", e)
    return ("Abridor inteligente:\n"
            f"Versión de abridor_inteligente.py: {ABRIDOR_VERSION}\n"
            f"Estado: {'ACTIVO' if _activo() else 'APAGADO (ABRIDOR_ACTIVO)'} · "
            f"preguntar editor: {'sí' if _preguntar_editor() else 'no'}\n"
            f"Editores detectados: {', '.join(e['nombre'] for e in eds) or 'ninguno'}\n"
            f"Aprendido: {'; '.join(aprendido) or '—'}\n"
            "Uso: /abridor · /abridor olvidar")
