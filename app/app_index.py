# 📁 app_index.py — Indexación de aplicaciones instaladas (OPTIMIZADO)

import os
import re
import subprocess
import json
from typing import Optional, List, Dict
import threading
import time

from database import conectar
from utils import normalizar_texto, similitud

try:
    import logger as _logger
except ImportError:
    _logger = None


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        if _logger is None:
            raise ImportError("logger.py no disponible")
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        pass


def _asegurar_columnas(conn, tabla: str, columnas: dict) -> list:
    """Migración ADITIVA: añade a una tabla YA existente las columnas que le falten.
    (CREATE TABLE IF NOT EXISTS no actualiza tablas creadas con un esquema anterior.)
    No borra ni reescribe datos. Devuelve los nombres de las columnas añadidas."""
    existentes = [f[1] for f in conn.execute(f"PRAGMA table_info({tabla})").fetchall()]
    if not existentes:
        return []
    agregadas = []
    for nombre, definicion in columnas.items():
        if nombre not in existentes:
            conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {definicion}")
            agregadas.append(nombre)
    if agregadas:
        import logging
        logging.getLogger("SARA-MIGRACION").warning(
            "%s: esquema antiguo (columnas que tenía: %s) -> añadidas: %s", tabla, existentes, agregadas)
    return agregadas


def crear_tablas() -> None:
    """Crea tablas de indexación de aplicaciones."""
    try:
        with conectar() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS indice_aplicaciones (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre              TEXT NOT NULL,
                nombre_normalizado  TEXT NOT NULL,
                nombre_mostrar      TEXT,
                exe                 TEXT,
                ruta_instalacion    TEXT,
                version             TEXT,
                veces_abierta       INTEGER DEFAULT 0,
                ultimo_abierta      DATETIME,
                ultimo_indexado     DATETIME DEFAULT CURRENT_TIMESTAMP,
                relevancia          REAL DEFAULT 0.5
            )""")
            
            # MIGRACIÓN: bases de datos creadas con un esquema anterior no tenían estas columnas
            # (síntoma: 'no column named nombre_mostrar' / 'no such column: relevancia').
            nuevas = _asegurar_columnas(conn, "indice_aplicaciones", {
                "nombre": "TEXT NOT NULL DEFAULT ''", "nombre_normalizado": "TEXT NOT NULL DEFAULT ''",
                "nombre_mostrar": "TEXT", "exe": "TEXT", "ruta_instalacion": "TEXT", "version": "TEXT",
                "veces_abierta": "INTEGER DEFAULT 0", "ultimo_abierta": "DATETIME",
                "ultimo_indexado": "DATETIME", "relevancia": "REAL DEFAULT 0.5"})
            if "nombre_normalizado" in nuevas:      # rellenar para que la deduplicación no funda todas las filas
                for fila in conn.execute("SELECT id, nombre FROM indice_aplicaciones").fetchall():
                    conn.execute("UPDATE indice_aplicaciones SET nombre_normalizado = ? WHERE id = ?",
                                 (normalizar_texto(fila[1] or "") or f"app_{fila[0]}", fila[0]))
            if "ultimo_indexado" in nuevas:
                conn.execute("UPDATE indice_aplicaciones SET ultimo_indexado = CURRENT_TIMESTAMP")
            
            # OPTIMIZACIÓN: Índices para búsquedas frecuentes
            # UNIQUE: sin esto el ON CONFLICT(nombre_normalizado) de registrar_aplicacion falla siempre
            conn.execute("DELETE FROM indice_aplicaciones WHERE id NOT IN (SELECT MIN(id) FROM indice_aplicaciones GROUP BY nombre_normalizado)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_app_nombre_norm ON indice_aplicaciones(nombre_normalizado)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_app_relevancia ON indice_aplicaciones(relevancia DESC)")
            
            conn.execute("""
            CREATE TABLE IF NOT EXISTS historial_aperturas_app (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                id_aplicacion       INTEGER,
                fecha_apertura      DATETIME DEFAULT CURRENT_TIMESTAMP,
                duracion_segundos   INTEGER,
                FOREIGN KEY(id_aplicacion) REFERENCES indice_aplicaciones(id)
            )""")
            
            _asegurar_columnas(conn, "historial_aperturas_app", {
                "id_aplicacion": "INTEGER", "fecha_apertura": "DATETIME", "duracion_segundos": "INTEGER"})
            conn.execute("CREATE INDEX IF NOT EXISTS idx_apertura_app ON historial_aperturas_app(id_aplicacion)")
            
            conn.commit()
    except Exception as e:
        _log_error("app_index", "crear_tablas", e)


def registrar_aplicacion(nombre: str, exe: str = "", ruta_instalacion: str = "",
                        version: str = "", nombre_mostrar: str = "") -> Optional[int]:
    """Registra una aplicación en el índice. OPTIMIZADO: deduplicación."""
    if not nombre:
        return None
    
    nombre_norm = normalizar_texto(nombre)
    
    try:
        with conectar() as conn:
            cursor = conn.execute("""
                INSERT INTO indice_aplicaciones
                    (nombre, nombre_normalizado, nombre_mostrar, exe, ruta_instalacion, version)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(nombre_normalizado) DO UPDATE SET
                    nombre_mostrar = COALESCE(excluded.nombre_mostrar, nombre_mostrar),
                    exe = COALESCE(excluded.exe, exe),
                    ruta_instalacion = COALESCE(excluded.ruta_instalacion, ruta_instalacion),
                    version = COALESCE(excluded.version, version),
                    ultimo_indexado = CURRENT_TIMESTAMP
            """, (
                nombre, nombre_norm,
                nombre_mostrar or nombre,
                exe, ruta_instalacion, version
            ))
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        _log_error("app_index", "registrar_aplicacion", e)
        return None


def registrar_apertura(id_app: int, duracion_segundos: int = 0) -> bool:
    """Registra apertura de aplicación y actualiza relevancia."""
    if not id_app:
        return False
    
    try:
        with conectar() as conn:
            conn.execute("""
                INSERT INTO historial_aperturas_app (id_aplicacion, duracion_segundos)
                VALUES (?, ?)
            """, (id_app, duracion_segundos))
            
            # OPTIMIZACIÓN: actualizar stats en una pasada
            conn.execute("""
                UPDATE indice_aplicaciones
                SET veces_abierta = veces_abierta + 1,
                    ultimo_abierta = CURRENT_TIMESTAMP,
                    relevancia = MIN(1.0, relevancia + 0.15)
                WHERE id = ?
            """, (id_app,))
            conn.commit()
            return True
    except Exception as e:
        _log_error("app_index", "registrar_apertura", e)
        return False

def buscar_aplicacion(termino: str, limite: int = 10) -> List[Dict]:
    if not termino or not termino.strip(): return []
    try:
        from utils import normalizar_texto
        termino_norm = normalizar_texto(termino)
    except Exception:
        termino_norm = termino.lower()
        
    tokens = [t for t in termino_norm.split() if t]
    if not tokens: return []
    
    try:
        from database import conectar
        with conectar() as conn:
            condiciones = ["(nombre_normalizado LIKE ? OR nombre LIKE ? OR exe LIKE ?)"]
            parametros = [f"%{termino_norm}%", f"%{termino}%", f"%{termino_norm}%"]
            
            for t in tokens:
                if len(t) > 2:
                    condiciones.append("(nombre_normalizado LIKE ? OR exe LIKE ?)")
                    parametros.extend([f"%{t}%", f"%{t}%"])
                    
            placeholders = " OR ".join(condiciones)
            
            try:
                # Intento 1: con ordenamiento completo
                filas = conn.execute(f"""
                    SELECT * FROM indice_aplicaciones WHERE {placeholders}
                    ORDER BY relevancia DESC, veces_abierta DESC LIMIT ?
                """, parametros + [limite]).fetchall()
            except Exception as sql_err:
                print(f"\n[SARA DEBUG] Falló SQL con ordenamiento: {sql_err}. Usando fallback simple...")
                # Intento 2: ultra simple, sobrevive aunque falten columnas en la BD
                filas = conn.execute(f"""
                    SELECT * FROM indice_aplicaciones WHERE {placeholders} LIMIT ?
                """, parametros + [limite]).fetchall()
                
            print(f"[SARA DEBUG] La BD encontró {len(filas)} coincidencias para '{termino}'.")
            return [dict(f) for f in filas]
    except Exception as e:
        print(f"\n[SARA DEBUG CRÍTICO] app_index colapsó: {e}")
        return []
def obtener_aplicacion_por_id(id_app: int) -> Optional[Dict]:
    """Obtiene información de una aplicación por ID."""
    if not id_app:
        return None
    
    try:
        with conectar() as conn:
            fila = conn.execute(
                "SELECT * FROM indice_aplicaciones WHERE id = ?",
                (id_app,),
            ).fetchone()
            return dict(fila) if fila else None
    except Exception as e:
        _log_error("app_index", "obtener_aplicacion_por_id", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PROCESOS EN EJECUCIÓN (Windows)
# ═══════════════════════════════════════════════════════════════════════════

# Caché de la lista de procesos con caducidad REAL (3 s). Antes había un @lru_cache que nadie llamaba (cada búsqueda
# lanzaba `tasklist` de nuevo) y, de haberse conectado, habría guardado la lista para siempre (lru_cache no caduca).
# Una orden suele consultar los procesos varias veces seguidas (buscar → exacto → comprobar): con esto es UN tasklist.
_TASKLIST_TTL_S = 3.0
_tasklist_cache = {"t": 0.0, "datos": None}
_tasklist_lock = threading.Lock()


def _cache_tasklist() -> List[Dict]:
    """Lista de procesos (copias) con caché de _TASKLIST_TTL_S segundos. Un fallo (lista vacía) no se guarda."""
    with _tasklist_lock:                      # varios hilos a la vez (ventana, Telegram, WhatsApp): un solo tasklist
        vigente = (_tasklist_cache["datos"] is not None
                   and time.monotonic() - _tasklist_cache["t"] < _TASKLIST_TTL_S)
        if not vigente:
            datos = _obtener_tasklist_bruto()
            if not datos:
                return []
            _tasklist_cache["t"], _tasklist_cache["datos"] = time.monotonic(), datos
        return [dict(p) for p in _tasklist_cache["datos"]]      # copias: quien reciba la lista no ensucia la caché


def _invalidar_cache_tasklist() -> None:
    with _tasklist_lock:
        _tasklist_cache["t"], _tasklist_cache["datos"] = 0.0, None

def _obtener_tasklist_bruto() -> List[Dict]:
    """Lista de procesos vía tasklist. Sin /V (lento y con títulos que rompían la decodificación)."""
    import csv, io
    try:
        crudo = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=10,
        )
        salida = crudo.decode("cp850", errors="replace")
        procesos = []
        for partes in csv.reader(io.StringIO(salida)):
            if len(partes) >= 5:
                digitos = re.sub(r"\D", "", partes[4])
                procesos.append({
                    "nombre_proceso": partes[0].strip(),
                    "pid": int(partes[1]) if partes[1].strip().isdigit() else 0,
                    "memoria_mb": (float(digitos) / 1024) if digitos else 0,
                })
        return procesos
    except Exception as e:
        _log_error("app_index", "_obtener_tasklist_bruto", e)
        return []


def listar_procesos_en_ejecucion() -> Dict[str, List[Dict]]:
    """
    Retorna procesos agrupados por tipo (sistema, usuario, otros).
    OPTIMIZADO: clasificación automática.
    """
    procesos_raw = _cache_tasklist()
    
    procesos_sistema = set({
        "svchost.exe", "dwm.exe", "explorer.exe", "taskhostw.exe",
        "csrss.exe", "lsass.exe", "services.exe", "wininit.exe",
        "smss.exe", "conhost.exe", "dllhost.exe", "svchost.exe",
    })
    
    procesos_user = {}
    procesos_sistema_lista = []
    procesos_otros = []
    
    for p in procesos_raw:
        nombre = p["nombre_proceso"].lower()
        
        if nombre in procesos_sistema:
            procesos_sistema_lista.append(p)
        else:
            # Agrupar por base de ejecutable para deduplicar
            exe_base = nombre.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
            if exe_base not in procesos_user:
                procesos_user[exe_base] = []
            procesos_user[exe_base].append(p)
    
    # Convertir agrupación en lista
    procesos_usuario = []
    for exe_base, lista in procesos_user.items():
        procesos_usuario.append({
            "nombre_proceso": exe_base,
            "nombre_mostrar": exe_base.replace(".exe", "").title(),
            "pids": [p["pid"] for p in lista],
            "memoria_total_mb": sum(p["memoria_mb"] for p in lista),
            "instancias": len(lista),
        })
    
    return {
        "usuario": procesos_usuario,
        "sistema": procesos_sistema_lista,
        "otros": procesos_otros,
    }


def buscar_proceso_en_ejecucion(nombre: str, limite: int = 5) -> List[Dict]:
    """
    Busca procesos activos por nombre.
    OPTIMIZADO: fuzzy matching + scoring inteligente.
    """
    consulta = normalizar_texto(nombre)
    if not consulta:
        return []
    
    tokens = [t for t in consulta.split() if t]
    if not tokens:
        return []
    
    procesos = listar_procesos_en_ejecucion()
    if not procesos:
        return []
    
    # Búsqueda en procesos de usuario
    grupos = {}
    for _, items in procesos.items():
        # items puede ser lista de dicts simples o lista de dicts agrupados
        items_iterar = items if isinstance(items, list) else [items]
        
        for item in items_iterar:
            nombre_proceso = (item.get('nombre_proceso') or '').strip().lower()
            nombre_mostrar_item = (item.get('nombre_mostrar') or nombre_proceso).strip()
            
            # Normalizar para búsqueda
            nombre_proceso_key = normalizar_texto(nombre_proceso)
            nombre_mostrar_key = normalizar_texto(nombre_mostrar_item)
            
            # Calcular scores
            score_proceso = 1.0 if consulta == nombre_proceso_key else similitud(consulta, nombre_proceso_key)
            score_mostrar = 1.0 if consulta == nombre_mostrar_key else similitud(consulta, nombre_mostrar_key)
            score = max(score_proceso, score_mostrar)
            
            # Boost si todos los tokens están contenidos
            if (nombre_proceso_key and all(tok in nombre_proceso_key for tok in tokens)) or \
               (nombre_mostrar_key and all(tok in nombre_mostrar_key for tok in tokens)):
                score = max(score, 0.85)
            
            # Si tiene score, agrupar
            if score > 0.0:
                exe = item.get('exe', '')
                grupo_key = (exe, nombre_mostrar_item)
                
                if grupo_key not in grupos:
                    grupos[grupo_key] = {
                        "nombre_mostrar": nombre_mostrar_item,
                        "nombre_proceso": nombre_proceso,
                        "pids": [],
                        "score": score,
                    }
                
                # Agregar PID si existe
                if 'pid' in item:
                    grupos[grupo_key]["pids"].append(item["pid"])
                elif 'pids' in item:
                    grupos[grupo_key]["pids"].extend(item["pids"])
                
                # Actualizar score máximo
                grupos[grupo_key]["score"] = max(grupos[grupo_key]["score"], score)
    
    # Ordenar por score y contar
    resultados = list(grupos.values())
    resultados.sort(key=lambda r: (r["score"], len(r["pids"])), reverse=True)
    
    return resultados[:limite]


def obtener_procesos_by_nombre_exacto(nombre: str) -> List[Dict]:
    """Obtiene procesos que coinciden exactamente con un nombre."""
    nombre_norm = normalizar_texto(nombre)
    procesos = listar_procesos_en_ejecucion()
    
    resultados = []
    for _, items in procesos.items():
        items_iterar = items if isinstance(items, list) else [items]
        for item in items_iterar:
            item_norm = normalizar_texto(item.get('nombre_proceso', ''))
            if item_norm == nombre_norm:
                resultados.append(item)
    
    return resultados


def terminar_proceso(pid: int) -> bool:
    """Termina un proceso por PID. CRÍTICO: solo procesos de usuario."""
    if not pid or pid <= 0:
        return False
    
    procesos_sistema = {
        "svchost.exe", "dwm.exe", "explorer.exe", "csrss.exe", "lsass.exe",
        "services.exe", "taskhostw.exe", "wininit.exe", "smss.exe",
    }
    
    try:
        # Verificar que no sea proceso sistema
        _sin_ventana = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        salida = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
            stderr=subprocess.DEVNULL, text=True, timeout=5, creationflags=_sin_ventana,
        )
        if any(sys_proc in salida.lower() for sys_proc in procesos_sistema):
            return False
        
        # Terminar. Antes devolvía True aunque taskkill fallara (sin permisos, PID inexistente): ahora se mira el código.
        r = subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=5, creationflags=_sin_ventana)
        _invalidar_cache_tasklist()             # el proceso ya no debe aparecer en la próxima consulta
        return r.returncode == 0
    except Exception as e:
        _log_error("app_index", "terminar_proceso", e)
        return False


def obtener_estadisticas_apps() -> Dict:
    """Retorna estadísticas del índice de aplicaciones."""
    try:
        with conectar() as conn:
            total = conn.execute("SELECT COUNT(*) FROM indice_aplicaciones").fetchone()[0]
            aperturas_total = conn.execute("SELECT SUM(veces_abierta) FROM indice_aplicaciones").fetchone()[0] or 0
            
            return {
                "total_aplicaciones": total,
                "total_aperturas": aperturas_total,
            }
    except Exception as e:
        _log_error("app_index", "obtener_estadisticas_apps", e)
        return {}


def recalcular_relevancia_apps() -> None:
    """Recalcula scores de relevancia basados en aperturas."""
    try:
        with conectar() as conn:
            total_aperturas = conn.execute("SELECT MAX(veces_abierta) FROM indice_aplicaciones").fetchone()[0]
            
            if total_aperturas is None or total_aperturas == 0:
                return
            
            conn.execute(f"""
                UPDATE indice_aplicaciones
                SET relevancia = MIN(1.0, (veces_abierta / {total_aperturas}) * 0.7 + 0.3)
            """)
            conn.commit()
    except Exception as e:
        _log_error("app_index", "recalcular_relevancia_apps", e)


def limpiar_cache_procesos() -> None:
    """Limpia el caché de procesos."""
    _invalidar_cache_tasklist()


_cache_tasklist.cache_clear = _invalidar_cache_tasklist      # compatibilidad con quien llamara a la API de lru_cache


# ═══════════════════════════════════════════════════════════════════════════
# DESCUBRIMIENTO DE APLICACIONES (Menú Inicio) — NUEVO
# ═══════════════════════════════════════════════════════════════════════════

def _carpetas_menu_inicio() -> List[str]:
    import os
    bases = [os.environ.get("ProgramData", r"C:\ProgramData"), os.environ.get("APPDATA", "")]
    return [os.path.join(b, "Microsoft", "Windows", "Start Menu", "Programs") for b in bases if b]


def indexar_aplicaciones() -> Dict:
    """
    Recorre los accesos directos (.lnk) del Menú Inicio y los registra.
    En 'exe' guarda la ruta del .lnk: os.startfile() lo abre sin resolver el destino.
    """
    import os
    indexados = 0
    for base in _carpetas_menu_inicio():
        if not os.path.isdir(base):
            continue
        for raiz, _dirs, archivos in os.walk(base):
            for a in archivos:
                if not a.lower().endswith(".lnk"):
                    continue
                nombre = a[:-4]
                bajo = nombre.lower()
                if any(x in bajo for x in ("uninstall", "desinstal", "readme", "leeme", "license", "help", "ayuda")):
                    continue
                if registrar_aplicacion(nombre, exe=os.path.join(raiz, a), ruta_instalacion=raiz,
                                        nombre_mostrar=nombre):
                    indexados += 1
    return {"indexados": indexados}


def ejecutar_reindexado_aplicaciones() -> Dict:
    return indexar_aplicaciones()


def iniciar_reindexado_automatico_aplicaciones() -> None:
    """Reindexa cada INTERVALO_REINDEXADO_APLICACIONES_SEGUNDOS en un hilo daemon."""
    import threading, time
    try:
        from config import INTERVALO_REINDEXADO_APLICACIONES_SEGUNDOS as intervalo
    except Exception:
        intervalo = 3600

    def _bucle():
        while True:
            time.sleep(intervalo)
            try:
                indexar_aplicaciones()
            except Exception as e:
                _log_error("app_index", "reindexado automático", e)

    threading.Thread(target=_bucle, name="reindexado-apps", daemon=True).start()
