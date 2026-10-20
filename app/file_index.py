# 📁 file_index.py — Índice y resolución de archivos/carpetas (v8, compatible con GUI)
#
# Solo LECTURA sobre el sistema de archivos (Principio 8): resuelve "a qué te refieres".
# Abrir/mover/borrar vive en file_actions.py y file_manager.py.
#
# API pública (la usan file_actions, file_manager, core_bridge y main):
#   crear_tablas, registrar_ruta, registrar_acceso, carpeta_conocida,
#   buscar_archivo(consulta, limite, carpeta_objetivo, solo_carpetas)  -> [{nombre, ruta, es_carpeta, score, ...}]
#   resolver_consulta_y_carpeta(texto, patron, palabras_ruido, forzar_respaldo_llm) -> (consulta, carpeta|None)
#   ejecutar_reindexado(), iniciar_reindexado_automatico()
#   responder_desde_clasificacion(resultado)  (categoría ARCHIVO: buscar sin abrir)
#   FOLDERIDS_CONOCIDOS, ruta_carpeta_conocida_windows, extraer_resto (re-export)

import ctypes
import json
import os
import sys
import threading
import time
import uuid
from typing import Dict, List, Optional, Tuple

from database import conectar
from utils import normalizar_texto, similitud, extraer_resto, limpiar_ruido

try:
    import logger as _logger
except ImportError:
    _logger = None

try:
    import config as _cfg
except Exception:
    _cfg = None


def _c(nombre: str, defecto):
    return getattr(_cfg, nombre, defecto) if _cfg is not None else defecto


CARPETAS_INDEXADAS_NOMBRES = _c("CARPETAS_INDEXADAS_NOMBRES",
                                ["Documents", "Desktop", "Downloads", "Pictures", "Music", "Videos"])
INTERVALO_REINDEXADO_SEGUNDOS = _c("INTERVALO_REINDEXADO_SEGUNDOS", 600)
TTL_CACHE_BUSQUEDAS_ARCHIVOS_SEGUNDOS = _c("TTL_CACHE_BUSQUEDAS_ARCHIVOS_SEGUNDOS", 300)
MAX_ARCHIVOS_INDEXADOS = _c("MAX_ARCHIVOS_INDEXADOS", 150_000)
MAX_SEGUNDOS_REINDEXADO = _c("MAX_SEGUNDOS_REINDEXADO", 120)

_DIRS_IGNORADOS = {"node_modules", "__pycache__", ".git", ".svn", ".venv", "venv", "$recycle.bin",
                   "system volume information", ".idea", ".vscode"}


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        if _logger is None:
            raise ImportError("logger.py no disponible")
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[FILE_INDEX ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


# ═══════════════════════════════════════════════════════════════════════════
# CARPETAS CONOCIDAS DE WINDOWS
# ═══════════════════════════════════════════════════════════════════════════

FOLDERIDS_CONOCIDOS: Dict[str, str] = {
    "Desktop":   "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "Pictures":  "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Music":     "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "Videos":    "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}


def ruta_carpeta_conocida_windows(guid: Optional[str]) -> Optional[str]:
    """Resuelve una carpeta conocida (respeta redirecciones de OneDrive). None fuera de Windows."""
    if not guid or not sys.platform.startswith("win"):
        return None
    try:
        class _GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID(guid)
        g = _GUID(u.time_low, u.time_mid, u.time_hi_version,
                  (ctypes.c_ubyte * 8)(*(u.bytes[8:])))
        ptr = ctypes.c_wchar_p()
        res = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(ptr))
        if res != 0:
            return None
        ruta = ptr.value
        ctypes.windll.ole32.CoTaskMemFree(ptr)
        return ruta
    except Exception as e:
        _log_error("file_index", "ruta_carpeta_conocida_windows", e)
        return None


def _raices_a_indexar() -> List[str]:
    raices = []
    for nombre in CARPETAS_INDEXADAS_NOMBRES:
        ruta = ruta_carpeta_conocida_windows(FOLDERIDS_CONOCIDOS.get(nombre)) or \
               os.path.join(os.path.expanduser("~"), nombre)
        if os.path.isdir(ruta) and ruta not in raices:
            raices.append(ruta)
    return raices


# ═══════════════════════════════════════════════════════════════════════════
# ESQUEMA (tolerante a bases de datos existentes)
# ═══════════════════════════════════════════════════════════════════════════

_COLUMNAS_ARCHIVOS = {
    "nombre": "TEXT", "nombre_normalizado": "TEXT", "extension": "TEXT",
    "es_carpeta": "INTEGER DEFAULT 0", "tamanio_kb": "INTEGER DEFAULT 0",
    "ultima_modificacion": "REAL", "veces_accedido": "INTEGER DEFAULT 0",
    "ultimo_acceso": "DATETIME", "ultimo_indexado": "DATETIME", "relevancia": "REAL DEFAULT 0.5",
}


def crear_tablas() -> None:
    try:
        with conectar() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS indice_archivos (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre              TEXT NOT NULL,
                nombre_normalizado  TEXT NOT NULL,
                ruta                TEXT NOT NULL,
                extension           TEXT,
                es_carpeta          INTEGER NOT NULL DEFAULT 0,
                tamanio_kb          INTEGER DEFAULT 0,
                ultima_modificacion REAL,
                veces_accedido      INTEGER DEFAULT 0,
                ultimo_acceso       DATETIME,
                ultimo_indexado     DATETIME DEFAULT CURRENT_TIMESTAMP,
                relevancia          REAL DEFAULT 0.5
            )""")
            existentes = {f["name"] for f in conn.execute("PRAGMA table_info(indice_archivos)").fetchall()}
            for col, ddl in _COLUMNAS_ARCHIVOS.items():
                if col not in existentes:
                    conn.execute(f"ALTER TABLE indice_archivos ADD COLUMN {col} {ddl}")
            # ON CONFLICT(ruta) exige UNIQUE: se deduplica y se garantiza con un índice único.
            conn.execute("DELETE FROM indice_archivos WHERE id NOT IN (SELECT MIN(id) FROM indice_archivos GROUP BY ruta)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_archivo_ruta_u ON indice_archivos(ruta)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_archivo_nombre_norm ON indice_archivos(nombre_normalizado)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_archivo_es_carpeta ON indice_archivos(es_carpeta)")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS historial_busquedas_archivo (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                termino_busqueda    TEXT NOT NULL,
                archivo_encontrado  TEXT,
                id_archivo          INTEGER,
                fecha               DATETIME DEFAULT CURRENT_TIMESTAMP,
                fue_exitosa         INTEGER DEFAULT 0
            )""")
            conn.commit()
    except Exception as e:
        _log_error("file_index", "crear_tablas", e)


# ═══════════════════════════════════════════════════════════════════════════
# REGISTRO
# ═══════════════════════════════════════════════════════════════════════════

_cache_busquedas: Dict[tuple, Tuple[float, list]] = {}
_cache_lock = threading.Lock()


def _invalidar_cache() -> None:
    with _cache_lock:
        _cache_busquedas.clear()


def registrar_ruta(ruta: str) -> bool:
    """Alta/actualización puntual (la usa file_manager tras crear/mover/copiar)."""
    if not ruta or not os.path.exists(ruta):
        return False
    try:
        es_carpeta = os.path.isdir(ruta)
        st = os.stat(ruta)
        nombre = os.path.basename(ruta.rstrip("\\/")) or ruta
        with conectar() as conn:
            conn.execute("""
                INSERT INTO indice_archivos
                    (nombre, nombre_normalizado, ruta, extension, es_carpeta, tamanio_kb, ultima_modificacion)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ruta) DO UPDATE SET
                    tamanio_kb = excluded.tamanio_kb,
                    ultima_modificacion = excluded.ultima_modificacion,
                    ultimo_indexado = CURRENT_TIMESTAMP
            """, (nombre, normalizar_texto(nombre), ruta,
                  None if es_carpeta else os.path.splitext(ruta)[1].lower(),
                  1 if es_carpeta else 0, 0 if es_carpeta else st.st_size // 1024, st.st_mtime))
            conn.commit()
        _invalidar_cache()
        return True
    except Exception as e:
        _log_error("file_index", "registrar_ruta", e)
        return False


def registrar_acceso(ruta: str) -> bool:
    """Una apertura exitosa sube la relevancia: la próxima búsqueda ambigua favorece lo que sí usas."""
    if not ruta:
        return False
    try:
        with conectar() as conn:
            conn.execute("""
                UPDATE indice_archivos
                SET veces_accedido = COALESCE(veces_accedido, 0) + 1,
                    ultimo_acceso = CURRENT_TIMESTAMP,
                    relevancia = MIN(1.0, COALESCE(relevancia, 0.5) + 0.05)
                WHERE ruta = ?""", (ruta,))
            conn.commit()
        _invalidar_cache()
        return True
    except Exception as e:
        _log_error("file_index", "registrar_acceso", e)
        return False


def carpeta_conocida(nombre_carpeta: str) -> bool:
    if not nombre_carpeta:
        return False
    try:
        with conectar() as conn:
            fila = conn.execute(
                "SELECT 1 FROM indice_archivos WHERE es_carpeta = 1 AND nombre_normalizado = ? LIMIT 1",
                (normalizar_texto(nombre_carpeta),)).fetchone()
        return fila is not None
    except Exception as e:
        _log_error("file_index", "carpeta_conocida", e)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# BÚSQUEDA CON PUNTUACIÓN
# ═══════════════════════════════════════════════════════════════════════════

def _sin_extension(nombre_norm: str, extension: Optional[str]) -> str:
    if extension:
        ext = normalizar_texto(extension)
        if ext and nombre_norm.endswith(ext):
            return nombre_norm[: -len(ext)].strip()
    return nombre_norm


def _puntuar(consulta: str, tokens: List[str], fila) -> float:
    nombre_norm = fila["nombre_normalizado"] or ""
    base = _sin_extension(nombre_norm, fila["extension"])
    if consulta == nombre_norm or consulta == base:
        return 1.0
    if tokens and all(t in nombre_norm for t in tokens):
        cobertura = min(1.0, len(consulta) / max(1, len(base)))
        score = 0.80 + 0.15 * cobertura
    else:
        score = max(similitud(consulta, base), similitud(consulta, nombre_norm))
        score = min(score, 0.79)      # sin todos los tokens nunca compite con un match completo
    bonus = min(0.02, (fila["veces_accedido"] or 0) * 0.005 + ((fila["relevancia"] or 0.5) - 0.5) * 0.02)
    return min(0.99, score + max(0.0, bonus))


def _resolver_carpeta_objetivo(carpeta_objetivo: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Retorna (prefijo_ruta, nombre_normalizado). Acepta ruta absoluta o nombre/alias."""
    if not carpeta_objetivo:
        return None, None
    if os.path.isabs(carpeta_objetivo) and os.path.isdir(carpeta_objetivo):
        return os.path.normcase(os.path.abspath(carpeta_objetivo)), None
    return None, normalizar_texto(carpeta_objetivo)


def _en_carpeta(ruta: str, prefijo: Optional[str], nombre_norm: Optional[str]) -> bool:
    if prefijo:
        return os.path.normcase(os.path.abspath(ruta)).startswith(prefijo + os.sep) or \
               os.path.normcase(os.path.abspath(ruta)) == prefijo
    if nombre_norm:
        partes = [normalizar_texto(p) for p in os.path.normpath(os.path.dirname(ruta)).replace("\\", "/").split("/") if p]
        return any(p == nombre_norm or nombre_norm in p for p in partes)
    return True


def buscar_archivo(consulta: str, limite: int = 10, carpeta_objetivo: Optional[str] = None,
                   solo_carpetas: Optional[bool] = None) -> List[Dict]:
    """
    Busca por nombre. Cada resultado trae `score` ∈ [0,1]:
      1.0 nombre exacto · 0.80–0.97 contiene todas las palabras · ≤0.79 parecido difuso.
    solo_carpetas: True → solo carpetas, False → solo archivos, None → ambos.
    """
    consulta_norm = normalizar_texto(consulta or "")
    tokens = [t for t in consulta_norm.split() if t]
    if not tokens:
        return []

    clave = (consulta_norm, limite, carpeta_objetivo, solo_carpetas)
    with _cache_lock:
        hit = _cache_busquedas.get(clave)
        if hit and time.time() - hit[0] < TTL_CACHE_BUSQUEDAS_ARCHIVOS_SEGUNDOS:
            return [dict(r) for r in hit[1]]

    prefijo, nombre_carpeta = _resolver_carpeta_objetivo(carpeta_objetivo)
    filtro_tipo = "" if solo_carpetas is None else f" AND es_carpeta = {1 if solo_carpetas else 0}"

    try:
        with conectar() as conn:
            cond = " AND ".join(["nombre_normalizado LIKE ?"] * len(tokens))
            filas = conn.execute(
                f"SELECT * FROM indice_archivos WHERE {cond}{filtro_tipo} "
                "ORDER BY COALESCE(relevancia,0.5) DESC, COALESCE(veces_accedido,0) DESC LIMIT 400",
                [f"%{t}%" for t in tokens]).fetchall()
            if not filas:
                # Tolerancia a errores de escritura: comparación difusa sobre los más relevantes.
                filas = conn.execute(
                    f"SELECT * FROM indice_archivos WHERE 1=1{filtro_tipo} "
                    "ORDER BY COALESCE(relevancia,0.5) DESC, COALESCE(veces_accedido,0) DESC LIMIT 6000").fetchall()

            resultados = []
            for f in filas:
                if (prefijo or nombre_carpeta) and not _en_carpeta(f["ruta"], prefijo, nombre_carpeta):
                    continue
                score = _puntuar(consulta_norm, tokens, f)
                if score < 0.55:
                    continue
                resultados.append({
                    "nombre": f["nombre"], "ruta": f["ruta"], "es_carpeta": bool(f["es_carpeta"]),
                    "extension": f["extension"], "tamanio_kb": f["tamanio_kb"] or 0,
                    "score": round(score, 4),
                })
            resultados.sort(key=lambda r: (-r["score"], len(r["nombre"])))
            resultados = resultados[:max(1, limite)]

            if resultados:
                conn.execute("INSERT INTO historial_busquedas_archivo (termino_busqueda, archivo_encontrado, fue_exitosa) "
                             "VALUES (?, ?, 1)", (consulta_norm, resultados[0]["ruta"]))
                conn.commit()
    except Exception as e:
        _log_error("file_index", "buscar_archivo", e)
        return []

    with _cache_lock:
        _cache_busquedas[clave] = (time.time(), [dict(r) for r in resultados])
    return resultados


# ═══════════════════════════════════════════════════════════════════════════
# SEPARAR "QUÉ" Y "DÓNDE" EN UNA FRASE
# ═══════════════════════════════════════════════════════════════════════════

# Fuertes: siempre indican ubicación. Débiles: solo si lo que sigue es una carpeta real/alias.
_SEPARADORES_FUERTES = (
    " dentro de la carpeta ", " dentro del directorio ", " dentro de ", " en la carpeta ", " en el directorio ",
    " de la carpeta ", " que esta en la carpeta ", " que esta dentro de ",
)
_SEPARADORES_DEBILES = (" que esta en ", " en ", " desde ", " del ", " de la ", " de ")
_ARTICULOS = {"el", "la", "los", "las", "mi", "mis", "tu", "un", "una", "de", "del"}


def _alias_conocidos() -> set:
    try:
        from file_actions import ALIAS_CARPETA_ESPECIAL
        return set(ALIAS_CARPETA_ESPECIAL) | {"papelera"}
    except Exception:
        return {"escritorio", "descargas", "documentos", "imagenes", "musica", "videos", "peliculas"}


def _sin_articulos(texto: str) -> str:
    palabras = texto.split()
    while palabras and palabras[0] in _ARTICULOS:
        palabras.pop(0)
    return " ".join(palabras)


def _dividir_consulta_y_carpeta(base: str) -> Tuple[str, Optional[str]]:
    texto = f" {base.strip()} "
    for separadores, estricto in ((_SEPARADORES_FUERTES, False), (_SEPARADORES_DEBILES, True)):
        candidatos = [(texto.rfind(s), s) for s in separadores if texto.rfind(s) != -1]
        candidatos.sort(key=lambda x: -x[0])
        for idx, sep in candidatos:
            consulta = texto[:idx].strip()
            carpeta = _sin_articulos(texto[idx + len(sep):].strip())
            if not carpeta:
                continue
            if estricto:
                # Débil: exige que sea un alias fijo o una carpeta del índice.
                if not (carpeta in _alias_conocidos() or carpeta_conocida(carpeta)):
                    continue
            return consulta, carpeta
    return base.strip(), None


def _extraer_via_llm(texto: str) -> Optional[Tuple[str, Optional[str]]]:
    """Respaldo local (Ollama) cuando el parser determinista no encontró nada. Nunca lanza."""
    try:
        import requests
        prompt = (
            "Separa la frase en el NOMBRE del archivo/carpeta buscado y la CARPETA donde está (si la menciona). "
            "No inventes nada.\n"
            f"Frase: \"{texto}\"\n"
            "Responde SOLO JSON: {\"consulta\": \"<nombre>\", \"carpeta\": \"<carpeta o null>\"}")
        r = requests.post(
            _c("OLLAMA_URL", "http://localhost:11434/api/generate"),
            json={"model": _c("QWEN_MODEL", "llama3.2:3b"), "prompt": prompt, "stream": False,
                  "format": "json", "think": False,
                  "options": {"temperature": 0.0, "num_predict": 80, "num_ctx": 1024}},
            timeout=_c("QWEN_TIMEOUT_SEGUNDOS", 45))
        r.raise_for_status()
        datos = json.loads(r.json().get("response", "{}"))
        consulta = normalizar_texto(str(datos.get("consulta") or ""))
        carpeta = datos.get("carpeta")
        carpeta = normalizar_texto(str(carpeta)) if carpeta and str(carpeta).lower() != "null" else None
        return (consulta, carpeta) if consulta else None
    except Exception as e:
        _log_error("file_index", "_extraer_via_llm", e)
        return None


def resolver_consulta_y_carpeta(texto: str, patron: Optional[str] = None, palabras_ruido: Optional[set] = None,
                                forzar_respaldo_llm: bool = False) -> Tuple[str, Optional[str]]:
    """
    'abre el archivo informe dentro de descargas' → ('informe', 'descargas')
    'busca notas en el escritorio'               → ('notas', 'escritorio')
    'crea una carpeta proyectos'                 → ('proyectos', None)
    """
    texto_norm = normalizar_texto(texto or "")
    if not texto_norm:
        return "", None
    palabras_ruido = palabras_ruido or set()

    if forzar_respaldo_llm:
        via_llm = _extraer_via_llm(texto_norm)
        if via_llm:
            consulta, carpeta = via_llm
            return limpiar_ruido(consulta, palabras_ruido), (limpiar_ruido(carpeta, palabras_ruido) or None) if carpeta else None

    base = texto_norm
    if patron:
        resto = extraer_resto(texto_norm, normalizar_texto(patron))
        base = resto if resto else texto_norm.replace(normalizar_texto(patron), " ", 1).strip() or texto_norm

    consulta, carpeta = _dividir_consulta_y_carpeta(base)
    consulta = limpiar_ruido(consulta, palabras_ruido)
    carpeta = limpiar_ruido(carpeta, palabras_ruido) if carpeta else None
    return consulta, (carpeta or None)


def extraer_consulta_y_carpeta(texto: str, patron: Optional[str] = None, palabras_ruido: Optional[set] = None):
    """Compat con file_actions (import histórico)."""
    return resolver_consulta_y_carpeta(texto, patron, palabras_ruido)


# ═══════════════════════════════════════════════════════════════════════════
# REINDEXADO
# ═══════════════════════════════════════════════════════════════════════════

_reindex_lock = threading.Lock()
_hilo_auto: Optional[threading.Thread] = None


def ejecutar_reindexado() -> Dict:
    """Escanea las carpetas conocidas. Retorna {"indexados": n, "cortado_por": None|'limite'|'tiempo'|'en_progreso'}."""
    if not _reindex_lock.acquire(blocking=False):
        return {"indexados": 0, "cortado_por": "en_progreso"}
    inicio, total, cortado = time.time(), 0, None
    marca = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(inicio - 1))
    try:
        crear_tablas()
        raices = _raices_a_indexar()
        proyecto = os.path.dirname(os.path.abspath(__file__))
        extra = [proyecto]
        lote: list = []

        def _volcar(conn, filas):
            conn.executemany("""
                INSERT INTO indice_archivos
                    (nombre, nombre_normalizado, ruta, extension, es_carpeta, tamanio_kb, ultima_modificacion, ultimo_indexado)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(ruta) DO UPDATE SET
                    tamanio_kb = excluded.tamanio_kb, ultima_modificacion = excluded.ultima_modificacion,
                    ultimo_indexado = CURRENT_TIMESTAMP""", filas)

        with conectar() as conn:
            # La carpeta del proyecto se registra a sí misma (no cuelga de ninguna carpeta conocida).
            for p in extra:
                lote.append((os.path.basename(p), normalizar_texto(os.path.basename(p)), p, None, 1, 0, os.path.getmtime(p)))
            for raiz in raices:
                if cortado:
                    break
                lote.append((os.path.basename(raiz), normalizar_texto(os.path.basename(raiz)), raiz, None, 1, 0,
                             os.path.getmtime(raiz)))
                for dirpath, dirnames, filenames in os.walk(raiz, onerror=lambda e: None):
                    dirnames[:] = [d for d in dirnames if d.lower() not in _DIRS_IGNORADOS and not d.startswith(".")]
                    for nombre, es_dir in [(d, True) for d in dirnames] + [(f, False) for f in filenames]:
                        if not es_dir and nombre.startswith("."):
                            continue
                        ruta = os.path.join(dirpath, nombre)
                        try:
                            st = os.stat(ruta)
                        except OSError:
                            continue
                        lote.append((nombre, normalizar_texto(nombre), ruta,
                                     None if es_dir else os.path.splitext(nombre)[1].lower(),
                                     1 if es_dir else 0, 0 if es_dir else st.st_size // 1024, st.st_mtime))
                        total += 1
                        if len(lote) >= 2000:
                            _volcar(conn, lote)
                            conn.commit()
                            lote = []
                        if total >= MAX_ARCHIVOS_INDEXADOS:
                            cortado = "limite"
                            break
                        if time.time() - inicio > MAX_SEGUNDOS_REINDEXADO:
                            cortado = "tiempo"
                            break
                    if cortado:
                        break
            if lote:
                _volcar(conn, lote)
            conn.commit()
            # Solo si el escaneo terminó completo se puede afirmar que lo no visto ya no existe.
            if not cortado:
                for raiz in raices:
                    conn.execute("DELETE FROM indice_archivos WHERE ruta LIKE ? AND ultimo_indexado < ?",
                                 (raiz.rstrip("\\/") + os.sep + "%", marca))
                conn.commit()
        _invalidar_cache()
        return {"indexados": total, "cortado_por": cortado}
    except Exception as e:
        _log_error("file_index", "ejecutar_reindexado", e)
        return {"indexados": total, "cortado_por": "error"}
    finally:
        _reindex_lock.release()


def iniciar_reindexado_automatico() -> Optional[threading.Thread]:
    """Hilo demonio: repite el escaneo cada INTERVALO_REINDEXADO_SEGUNDOS. Idempotente."""
    global _hilo_auto
    if _hilo_auto is not None and _hilo_auto.is_alive():
        return _hilo_auto

    def _bucle():
        while True:
            time.sleep(INTERVALO_REINDEXADO_SEGUNDOS)
            try:
                ejecutar_reindexado()
            except Exception as e:
                _log_error("file_index", "reindexado automático", e)

    _hilo_auto = threading.Thread(target=_bucle, name="sara-reindex-archivos", daemon=True)
    _hilo_auto.start()
    return _hilo_auto


# ═══════════════════════════════════════════════════════════════════════════
# RESPUESTA A LA CATEGORÍA ARCHIVO (buscar sin abrir)
# ═══════════════════════════════════════════════════════════════════════════

def responder_desde_clasificacion(resultado: dict) -> Optional[dict]:
    if resultado.get("categoria") != "ARCHIVO":
        return None
    try:
        from file_actions import _PALABRAS_RUIDO_ARCHIVOS as ruido, _filtro_tipo_objeto
    except Exception:
        ruido, _filtro_tipo_objeto = set(), (lambda t: None)

    texto = resultado.get("texto_norm", "")
    consulta, carpeta = resolver_consulta_y_carpeta(texto, resultado.get("patron"), ruido)
    if not consulta:
        return {"tipo": "buscar_archivo", "texto": "¿Qué archivo o carpeta quieres que busque?", "exito": False}

    coincidencias = buscar_archivo(consulta, limite=5, carpeta_objetivo=carpeta, solo_carpetas=_filtro_tipo_objeto(texto))
    if not coincidencias:
        pista = f" dentro de '{carpeta}'" if carpeta else ""
        return {"tipo": "buscar_archivo", "exito": False,
                "texto": f"No encontré nada parecido a '{consulta}'{pista}. Prueba /reindexar si lo creaste hace poco."}

    lineas = [f"{'📁' if c['es_carpeta'] else '📄'} {c['nombre']} — {c['ruta']}" for c in coincidencias]
    return {"tipo": "buscar_archivo", "exito": True, "_mejor_coincidencia": coincidencias[0],
            "resultados": coincidencias,
            "texto": f"Encontré {len(coincidencias)} coincidencia(s) para '{consulta}':\n" + "\n".join(lineas)}


# ═══════════════════════════════════════════════════════════════════════════
# CONSULTAS Y MANTENIMIENTO
# ═══════════════════════════════════════════════════════════════════════════

def obtener_archivo_por_ruta(ruta: str) -> Optional[Dict]:
    try:
        with conectar() as conn:
            fila = conn.execute("SELECT * FROM indice_archivos WHERE ruta = ?", (ruta,)).fetchone()
        return dict(fila) if fila else None
    except Exception as e:
        _log_error("file_index", "obtener_archivo_por_ruta", e)
        return None


def obtener_carpetas_conocidas() -> List[Dict]:
    try:
        with conectar() as conn:
            filas = conn.execute(
                "SELECT nombre, ruta, veces_accedido FROM indice_archivos WHERE es_carpeta = 1 "
                "ORDER BY COALESCE(veces_accedido,0) DESC LIMIT 500").fetchall()
        return [dict(f) for f in filas]
    except Exception as e:
        _log_error("file_index", "obtener_carpetas_conocidas", e)
        return []


def limpiar_indice_archivos_inexistentes() -> int:
    try:
        with conectar() as conn:
            ids = [f["id"] for f in conn.execute("SELECT id, ruta FROM indice_archivos").fetchall()
                   if not os.path.exists(f["ruta"])]
            for i in range(0, len(ids), 500):
                bloque = ids[i:i + 500]
                conn.execute(f"DELETE FROM indice_archivos WHERE id IN ({','.join('?' * len(bloque))})", bloque)
            conn.commit()
        _invalidar_cache()
        return len(ids)
    except Exception as e:
        _log_error("file_index", "limpiar_indice_archivos_inexistentes", e)
        return 0


def obtener_estadisticas_indice() -> Dict:
    try:
        with conectar() as conn:
            total = conn.execute("SELECT COUNT(*) FROM indice_archivos").fetchone()[0]
            carpetas = conn.execute("SELECT COUNT(*) FROM indice_archivos WHERE es_carpeta = 1").fetchone()[0]
            kb = conn.execute("SELECT COALESCE(SUM(tamanio_kb), 0) FROM indice_archivos").fetchone()[0]
        return {"total": total, "archivos": total - carpetas, "carpetas": carpetas, "tamanio_total_mb": kb // 1024}
    except Exception as e:
        _log_error("file_index", "obtener_estadisticas_indice", e)
        return {}


def vaciar_historial_busquedas(dias: int = 30) -> int:
    try:
        with conectar() as conn:
            cur = conn.execute("DELETE FROM historial_busquedas_archivo WHERE fecha < datetime('now', '-' || ? || ' days')",
                               (int(dias),))
            conn.commit()
            return cur.rowcount
    except Exception as e:
        _log_error("file_index", "vaciar_historial_busquedas", e)
        return 0
