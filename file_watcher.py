# 📁 file_watcher.py
import os
import threading
import time
from datetime import datetime
from database import insertar_archivo_indice, eliminar_archivo_indice, indice_vacio
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

PRIORIDAD_POR_RUTA = {
    # Usuario — alta prioridad
    "Desktop": 10,          "OneDrive\\Desktop": 10,
    "Documents": 9,         "OneDrive\\Documents": 9,
    "Downloads": 8,         "Pictures": 7,
    "Videos": 6,            "Music": 6,
    # Programas instalados — prioridad baja
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
                tipo   = "carpeta" if event.is_directory else "archivo"
                ext    = "" if event.is_directory else os.path.splitext(nombre)[1].lower()
                if not event.is_directory and ext not in EXTENSIONES_RELEVANTES:
                    return
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(event.src_path)).isoformat()
                    tamanio = 0 if event.is_directory else os.path.getsize(event.src_path) // 1024
                except Exception:
                    mtime, tamanio = None, 0
                insertar_archivo_indice(nombre, event.src_path, tipo, ext, tamanio, 5, mtime)

            def on_deleted(self, event):
                eliminar_archivo_indice(event.src_path)

            def on_moved(self, event):
                eliminar_archivo_indice(event.src_path)
                nombre = os.path.basename(event.dest_path)
                tipo   = "carpeta" if event.is_directory else "archivo"
                ext    = "" if event.is_directory else os.path.splitext(nombre)[1].lower()
                insertar_archivo_indice(nombre, event.dest_path, tipo, ext, 0, 5, None)

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
    def _run():
        if WATCHDOG_MODO == "eventos":
            _watcher_por_eventos()
        else:
            _watcher_por_tiempo()
    hilo = threading.Thread(target=_run, daemon=True)
    hilo.start()
    logger.info("file_watcher", f"Watchdog iniciado en modo: {WATCHDOG_MODO}")