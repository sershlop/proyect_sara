# 📁 file_watcher.py
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime
from database import insertar_archivo_indice, eliminar_archivo_indice, indice_vacio, conectar
from config import (
    WATCHDOG_ACTIVO, WATCHDOG_MODO,
    WATCHDOG_INTERVALO_MINUTOS, WATCHDOG_RUTAS_EXTRA
)
import logger

EXTENSIONES_RELEVANTES = {
    ".py", ".txt", ".pdf", ".docx", ".xlsx", ".pptx",
    ".jpg", ".jpeg", ".png", ".mp4", ".mp3", ".zip",
    ".csv", ".json", ".html", ".bat", ".exe", ".lnk"
}

_CACHE_LIMITE = 150
_CACHE_INDICE = OrderedDict()

PRIORIDAD_POR_RUTA = {
    "Desktop": 10,          "OneDrive\\Desktop": 10,
    "Documents": 9,         "OneDrive\\Documents": 9,
    "Downloads": 8,         "Pictures": 7,
    "Videos": 6,            "Music": 6,
    "Program Files": 3,
    "Program Files (x86)": 3,
    "AppData\\Local": 2,
    "AppData\\Roaming": 2,
}
RUTAS_EXCLUIDAS = {
    "temp", "tmp", "temporary", "cache", "caches",
    "logs", "log", "__pycache__", ".git", "node_modules",
    "crash reports", "crashreports", "crashpad",
    "gpucache", "shader cache", "shadercache",
    "code cache", "codecache", "jscache",
    "service worker", "serviceworker",
    "application cache", "applicationcache",
}


def _obtener_rutas_base():
    home = os.path.expanduser("~")
    rutas = {}
    for nombre, prioridad in PRIORIDAD_POR_RUTA.items():
        ruta = os.path.join(home, nombre)
        if os.path.exists(ruta):
            rutas[ruta] = prioridad
    for ruta_extra in WATCHDOG_RUTAS_EXTRA:
        if os.path.exists(ruta_extra):
            rutas[ruta_extra] = 5
    return rutas


def _calcular_prioridad(ruta_completa, prioridad_base):
    profundidad = ruta_completa.count(os.sep)
    return max(1, prioridad_base - (profundidad // 3))


def _calcular_score_relevancia(prioridad, accesos, ultima_modificacion=None):
    try:
        prioridad_val = float(prioridad or 0)
        accesos_val = int(accesos or 0)
        score = prioridad_val * 2.0 + min(accesos_val, 50) * 1.2
        if ultima_modificacion:
            if isinstance(ultima_modificacion, str):
                ultima_modificacion = datetime.fromisoformat(ultima_modificacion)
            if isinstance(ultima_modificacion, datetime):
                edad_horas = (datetime.now() - ultima_modificacion).total_seconds() / 3600.0
                score += max(0.0, 5.0 - min(edad_horas / 24.0, 5.0))
        return round(score, 4)
    except Exception:
        return float(prioridad or 0) * 2.0


def _agregar_o_actualizar_cache(fila):
    ruta = fila.get("ruta")
    if not ruta:
        return
    _CACHE_INDICE.pop(ruta, None)
    if len(_CACHE_INDICE) >= _CACHE_LIMITE:
        _CACHE_INDICE.popitem(last=False)
    _CACHE_INDICE[ruta] = fila


def cargar_cache_indice():
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT nombre, ruta, tipo, extension, tamanio_kb,
                       prioridad, accesos, ultima_modificacion,
                       ultimo_acceso, score_relevancia
                FROM indice_archivos
                ORDER BY score_relevancia DESC, prioridad DESC, accesos DESC
                LIMIT ?
            """, (_CACHE_LIMITE,))
            filas = cursor.fetchall()
        _CACHE_INDICE.clear()
        for fila in filas:
            _CACHE_INDICE[fila["ruta"]] = dict(fila)
        logger.info("file_watcher", f"Cache de índice cargada ({len(_CACHE_INDICE)} elementos).")
    except Exception as e:
        logger.log_excepcion("file_watcher", "cargar_cache_indice", e)


def buscar_en_cache(nombre_busqueda, limite=5):
    try:
        texto_busqueda = nombre_busqueda.lower().strip()
        resultados = []
        for fila in _CACHE_INDICE.values():
            nombre_val = fila.get("nombre") or ""
            ruta_val = fila.get("ruta") or ""
            # Asegurar que son strings antes de lower()
            try:
                nombre_lower = str(nombre_val).lower()
                ruta_lower = str(ruta_val).lower()
            except Exception:
                continue
            if texto_busqueda in nombre_lower or texto_busqueda in ruta_lower:
                resultados.append(fila)
                if len(resultados) >= limite:
                    break
        return resultados
    except Exception:
        return []


def escanear_rutas(callback_progreso=None):
    try:
        rutas_base = _obtener_rutas_base()
        total = 0
        for ruta_base, prioridad_base in rutas_base.items():
            for root, dirs, files in os.walk(ruta_base):
                nivel = root.replace(ruta_base, "").count(os.sep)
                if nivel >= 4:
                    dirs.clear()
                    continue
                root_lower = root.lower()
                if any(excluida in root_lower for excluida in RUTAS_EXCLUIDAS):
                    dirs.clear()
                    continue
                for nombre in dirs:
                    ruta = os.path.join(root, nombre)
                    prioridad = _calcular_prioridad(ruta, prioridad_base)
                    try:
                        mtime = datetime.fromtimestamp(os.path.getmtime(ruta)).isoformat()
                    except Exception:
                        mtime = None
                    insertar_archivo_indice(nombre, ruta, "carpeta", "", 0, prioridad, mtime)
                    total += 1
                for nombre in files:
                    ext = os.path.splitext(nombre)[1].lower()
                    if ext not in EXTENSIONES_RELEVANTES:
                        continue
                    ruta = os.path.join(root, nombre)
                    prioridad = _calcular_prioridad(ruta, prioridad_base)
                    try:
                        tamanio = os.path.getsize(ruta) // 1024
                        mtime = datetime.fromtimestamp(os.path.getmtime(ruta)).isoformat()
                    except Exception:
                        tamanio, mtime = 0, None
                    insertar_archivo_indice(nombre, ruta, "archivo", ext, tamanio, prioridad, mtime)
                    total += 1
                if callback_progreso and total % 50 == 0:
                    callback_progreso(total, root)
        logger.info("file_watcher", f"Escaneo completado: {total} elementos indexados.")
        cargar_cache_indice()
        return total
    except Exception as e:
        logger.log_excepcion("file_watcher", "escanear_rutas", e)
        return 0


def escanear_en_hilo(callback_progreso=None, callback_fin=None):
    def _tarea():
        total = escanear_rutas(callback_progreso)
        if callback_fin:
            callback_fin(total)
    hilo = threading.Thread(target=_tarea, daemon=True)
    hilo.start()
    return hilo


def _watcher_por_tiempo():
    intervalo = WATCHDOG_INTERVALO_MINUTOS * 60
    while True:
        time.sleep(intervalo)
        logger.info("file_watcher", "Actualizando índice por intervalo de tiempo...")
        escanear_rutas()


def _watcher_por_eventos():
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        class _Handler(FileSystemEventHandler):
            def on_created(self, event):
                nombre = os.path.basename(event.src_path)
                tipo = "carpeta" if event.is_directory else "archivo"
                ext = "" if event.is_directory else os.path.splitext(nombre)[1].lower()
                if not event.is_directory and ext not in EXTENSIONES_RELEVANTES:
                    return
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(event.src_path)).isoformat()
                    tamanio = 0 if event.is_directory else os.path.getsize(event.src_path) // 1024
                except Exception:
                    mtime, tamanio = None, 0
                insertar_archivo_indice(nombre, event.src_path, tipo, ext, tamanio, 5, mtime)
                _agregar_o_actualizar_cache({
                    "nombre": nombre,
                    "ruta": event.src_path,
                    "tipo": tipo,
                    "extension": ext,
                    "tamanio_kb": tamanio,
                    "prioridad": 5,
                    "accesos": 0,
                    "ultima_modificacion": mtime,
                    "score_relevancia": _calcular_score_relevancia(5, 0, mtime)
                })

            def on_modified(self, event):
                # Actualizar mtime y score en DB y cache
                if event.is_directory:
                    return
                nombre = os.path.basename(event.src_path)
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(event.src_path)).isoformat()
                    tamanio = os.path.getsize(event.src_path) // 1024
                except Exception:
                    mtime, tamanio = None, 0
                # actualizar índice
                try:
                    insertar_archivo_indice(nombre, event.src_path, "archivo", os.path.splitext(nombre)[1].lower(), tamanio, 5, mtime)
                except Exception:
                    pass
                _agregar_o_actualizar_cache({
                    "nombre": nombre,
                    "ruta": event.src_path,
                    "tipo": "archivo",
                    "extension": os.path.splitext(nombre)[1].lower(),
                    "tamanio_kb": tamanio,
                    "prioridad": 5,
                    "accesos": 0,
                    "ultima_modificacion": mtime,
                    "score_relevancia": _calcular_score_relevancia(5, 0, mtime)
                })

            def on_deleted(self, event):
                eliminar_archivo_indice(event.src_path)
                _CACHE_INDICE.pop(event.src_path, None)

            def on_moved(self, event):
                eliminar_archivo_indice(event.src_path)
                _CACHE_INDICE.pop(event.src_path, None)
                nombre = os.path.basename(event.dest_path)
                tipo = "carpeta" if event.is_directory else "archivo"
                ext = "" if event.is_directory else os.path.splitext(nombre)[1].lower()
                insertar_archivo_indice(nombre, event.dest_path, tipo, ext, 0, 5, None)
                _agregar_o_actualizar_cache({
                    "nombre": nombre,
                    "ruta": event.dest_path,
                    "tipo": tipo,
                    "extension": ext,
                    "tamanio_kb": 0,
                    "prioridad": 5,
                    "accesos": 0,
                    "ultima_modificacion": None,
                    "score_relevancia": _calcular_score_relevancia(5, 0, None)
                })

        observer = Observer()
        for ruta in _obtener_rutas_base().keys():
            observer.schedule(_Handler(), ruta, recursive=True)
        observer.start()
        logger.info("file_watcher", "Watchdog por eventos activo.")
        observer.join()
    except ImportError:
        logger.warning("file_watcher", "watchdog no instalado — usando modo tiempo.")
        _watcher_por_tiempo()
    except Exception as e:
        logger.log_excepcion("file_watcher", "_watcher_por_eventos", e)


def iniciar_watchdog():
    if not WATCHDOG_ACTIVO:
        return
    cargar_cache_indice()
    def _run():
        if WATCHDOG_MODO == "eventos":
            _watcher_por_eventos()
        else:
            _watcher_por_tiempo()
    hilo = threading.Thread(target=_run, daemon=True)
    hilo.start()
    logger.info("file_watcher", f"Watchdog iniciado en modo: {WATCHDOG_MODO}")
