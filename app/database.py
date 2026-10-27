# 📁 database.py — Núcleo de clasificación de SARA v2.0
#
# Alcance de este módulo: SOLO el motor de clasificación de intención
# (patrones, aprendizaje vectorial por dominio, caché, correcciones,
# historial). Otros dominios (productividad, shell, archivos, etc.)
# tienen su propia base de datos/tablas — no se mezclan aquí a propósito.
#
# Principio de diseño no negociable: ningún error se traga en silencio.
# Todo `except` que no relanza la excepción debe pasar por _log_error(),
# aunque el valor de retorno sea un fallback seguro (lista vacía, None,
# False). Un fallback silencioso es indistinguible de "no hay resultado"
# y fue la causa raíz de los falsos negativos en la versión anterior.
#
# Import circular: logger.py importa funciones de este módulo a nivel de
# módulo (guardar_log, etc.). Por eso este archivo NUNCA importa logger
# a nivel de módulo — solo de forma perezosa, dentro de cada función,
# vía _log_error().

import sqlite3
import json
import threading
import time
from datetime import datetime
from typing import Optional

from config import DB_NAME

SCHEMA_VERSION_ACTUAL = 3


# ── CONEXIÓN Y ESQUEMA ──────────────────────────────────────────────────

class _ConexionSARA(sqlite3.Connection):
    """Conexion cuyo bloque ``with`` tambien libera el recurso SQLite."""

    def __exit__(self, tipo, valor, traza):
        try:
            return super().__exit__(tipo, valor, traza)
        finally:
            self.close()

_WAL_CONFIRMADO = False      # el modo WAL queda grabado en el archivo: basta pedirlo UNA vez por proceso


def conectar() -> sqlite3.Connection:
    global _WAL_CONFIRMADO
    conn = sqlite3.connect(DB_NAME, timeout=10, factory=_ConexionSARA)
    conn.row_factory = sqlite3.Row
    if _WAL_CONFIRMADO:
        # v2: sin volver a negociar el journal en cada conexión. synchronous=NORMAL es el modo recomendado
        # para WAL (no corrompe la BD; solo en un corte de luz podría perderse el último commit): evita un
        # fsync por cada historial/caché que se guarda en cada orden.
        try:
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    try:
        modo = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        if modo and str(modo[0]).lower() == "wal":
            _WAL_CONFIRMADO = True
    except sqlite3.OperationalError as e:
        # WAL necesita locking de archivos compartidos que carpetas
        # sincronizadas en la nube (OneDrive, Google Drive, Dropbox) no
        # soportan bien — produce "disk I/O error". Se degrada a un modo
        # de journal compatible en vez de tumbar la conexión completa.
        _log_error("database", "conectar (PRAGMA WAL no disponible, degradando)", e)
        try:
            conn.execute("PRAGMA journal_mode=DELETE")
        except Exception as e2:
            _log_error("database", "conectar (fallback DELETE tambien fallo)", e2)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def inicializar_db() -> bool:
    """
    Inicializa el esquema de clasificación de SARA.
    Otros módulos (shell, productivity, file_index, app_index, habitos)
    crean sus propias tablas cuando se importan.
    """
    try:
        crear_tablas()
        return True
    except BaseDatosInaccesible as e:
        _log_error("database", "inicializar_db", e)
        raise  # Las nubes son un problema serio, deja que core_bridge lo maneje
    except Exception as e:
        _log_error("database", "inicializar_db", e)
        return False


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    """
    Registra un error real. Import perezoso de logger para evitar el
    ciclo de imports (ver cabecera del archivo). Si logger también
    falla, se imprime en consola como último recurso — el error nunca
    desaparece sin dejar rastro en algún lado.
    """
    try:
        import logger as _logger
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[DATABASE ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


def _version_esquema(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _fijar_version_esquema(conn: sqlite3.Connection, version: int) -> None:
    # PRAGMA no acepta parámetros con '?', se interpola directo (version es int controlado por nosotros, no input externo)
    conn.execute(f"PRAGMA user_version = {int(version)}")


class BaseDatosInaccesible(Exception):
    """Se lanza cuando ni siquiera crear una tabla funciona — típicamente
    porque la carpeta del proyecto está en OneDrive/Google Drive/Dropbox
    y el archivo .db está bloqueado o no descargado localmente."""
    pass


def crear_tablas() -> None:
    """
    Crea el esquema completo si no existe. Usa CREATE TABLE IF NOT EXISTS
    para instalaciones nuevas; las instalaciones existentes se actualizan
    vía migrar_bd() usando PRAGMA user_version, no por ensayo-error.
    """
    try:
        with conectar() as conn:
            cursor = conn.cursor()

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS patrones (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                categoria           TEXT NOT NULL,
                tipo                TEXT NOT NULL CHECK(tipo IN ('exacto', 'regex', 'keyword')),
                patron              TEXT NOT NULL,
                peso_especificidad  INTEGER NOT NULL DEFAULT 1,
                activo              INTEGER NOT NULL DEFAULT 1,
                fecha_creacion      DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(categoria, tipo, patron)
            )""")
            # ── PILAR 1: MEMORIA CORE ─────────────────────────────────────────
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS core_memory (
                id INTEGER PRIMARY KEY,
                block TEXT NOT NULL,
                content TEXT NOT NULL,
                veces_mencionado INTEGER DEFAULT 1,
                fecha_actualizacion DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(block, content)
            )
                """)        
            
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS restricciones_usuario (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo TEXT NOT NULL,
                identificador TEXT NOT NULL,
                regla TEXT NOT NULL,
                fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tipo, identificador)
            )
            """)
            
            # ── PILAR 2: HÁBITOS ───────────────────────────────────────────
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS registro_ejecuciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dominio TEXT NOT NULL CHECK(dominio IN ('aplicacion','archivo','navegador')),
                identificador TEXT NOT NULL,
                nombre_mostrar TEXT NOT NULL,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
            
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS habitos_paquetes (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             miembros TEXT NOT NULL,
            nombre_paquete TEXT,
            ventana_horaria_ini TEXT,
            ventana_horaria_fin TEXT,
            veces_detectado INTEGER DEFAULT 1,
            veces_confirmado INTEGER DEFAULT 0,
            veces_rechazado INTEGER DEFAULT 0,
            racha_rechazos INTEGER DEFAULT 0,
            estado TEXT NOT NULL CHECK(estado IN ('candidato','promovido','descartado','fijo')) DEFAULT 'candidato',
            ultima_sugerencia DATETIME,
            primera_deteccion DATETIME DEFAULT CURRENT_TIMESTAMP,
            ultima_deteccion DATETIME DEFAULT CURRENT_TIMESTAMP,
            ultima_actividad DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(miembros)
            )
            """)
            # Aprendizaje vectorial aislado por dominio. UNIQUE(categoria, texto_limpio)
            # garantiza que la misma frase pueda significar cosas distintas en
            # dominios distintos sin pisarse entre sí.
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS intenciones_aprendidas (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                categoria     TEXT NOT NULL,
                texto_limpio  TEXT NOT NULL,
                accion        TEXT NOT NULL,
                vector        TEXT,
                confianza     REAL DEFAULT 1.0,
                veces_usada   INTEGER DEFAULT 1,
                fuente        TEXT DEFAULT 'qwen',
                fecha         DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(categoria, texto_limpio)
            )""")

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache_intenciones (
                texto_limpio  TEXT PRIMARY KEY,
                resultado     TEXT,
                ts            DATETIME DEFAULT CURRENT_TIMESTAMP,
                ttl_segundos  INTEGER DEFAULT 30
            )""")

            # Cuando el usuario corrige a SARA. guardar_correccion() no solo
            # inserta aquí — también purga el aprendizaje erróneo en
            # intenciones_aprendidas para que el mismo error no se refuerce.
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS correcciones (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                texto_original      TEXT NOT NULL,
                texto_limpio        TEXT NOT NULL,
                categoria_erronea   TEXT NOT NULL,
                categoria_correcta  TEXT NOT NULL,
                confianza_erronea   REAL DEFAULT 0.0,
                fecha               DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")

            # Bitácora de cada clasificación. `correcto` queda NULL hasta que
            # se confirma (por uso normal o por corrección). Es la materia
            # prima de generar_fixture_regresion().
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS historial_clasificacion (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                texto_original      TEXT NOT NULL,
                texto_limpio        TEXT NOT NULL,
                categoria_asignada  TEXT NOT NULL,
                confianza           REAL DEFAULT 0.0,
                metodo              TEXT NOT NULL CHECK(metodo IN ('exacto', 'regex', 'keyword', 'vectorial', 'llm', 'ninguno', 'cache', 'llm_blindaje')),
                correcto            INTEGER,
                fecha               DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")

            conn.commit()
            
    except sqlite3.OperationalError as e:
        _log_error("database", "crear_tablas", e)
        mensaje_error = str(e).lower()
        # "disk i/o error", "database is locked" y "unable to open database
        # file" son las firmas reales de un archivo bloqueado por
        # sincronización en la nube. Cualquier otro OperationalError
        # (sintaxis SQL inválida, columna inexistente, etc.) NO es un
        # problema de nube — decirlo así manda a revisar una carpeta que
        # puede ni siquiera estar sincronizada, como pasó aquí con un "#"
        # colado dentro de un CREATE TABLE.
        indicadores_nube = ("disk i/o error", "database is locked", "unable to open database file")
        if any(indicador in mensaje_error for indicador in indicadores_nube):
            raise BaseDatosInaccesible(
                f"No se pudo crear/abrir la base de datos ({DB_NAME}). "
                "Causa mas comun: la carpeta del proyecto esta dentro de OneDrive/Google Drive/Dropbox "
                "y el archivo .db esta bloqueado o no descargado localmente. "
                "Mueve la carpeta completa a una ruta local sin sincronizacion en la nube (ej. C:\\SARA) "
                "y vuelve a intentarlo."
            ) from e
        raise BaseDatosInaccesible(
            f"No se pudo crear/abrir la base de datos ({DB_NAME}) por un error de SQL, no de acceso a "
            f"archivo: {e}. Revisa la sentencia CREATE TABLE/ALTER TABLE mas cercana al traceback — "
            "es probable un caracter o comentario mal colocado dentro del bloque SQL."
        ) from e

    if _version_esquema_segura() == 0:
        with conectar() as conn:
            _fijar_version_esquema(conn, SCHEMA_VERSION_ACTUAL)
            

    migrar_bd()


def _version_esquema_segura() -> int:
    try:
        with conectar() as conn:
            return _version_esquema(conn)
    except Exception as e:
        _log_error("database", "_version_esquema_segura", e)
        return 0


# Migraciones futuras: {version_destino: [sentencias SQL]}. crear_tablas()
# ya construye el esquema de SCHEMA_VERSION_ACTUAL completo para instalaciones
# nuevas, así que este diccionario solo se llena cuando una versión nueva
# del código introduce cambios de esquema para instalaciones EXISTENTES.
MIGRACIONES: dict = {
    2: [
        "ALTER TABLE registro_ejecuciones RENAME TO registro_ejecuciones_old_v1",
        """CREATE TABLE registro_ejecuciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dominio TEXT NOT NULL CHECK(dominio IN ('aplicacion','archivo','navegador')),
            identificador TEXT NOT NULL,
            nombre_mostrar TEXT NOT NULL,
            fecha DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        """INSERT INTO registro_ejecuciones (id, dominio, identificador, nombre_mostrar, fecha)
           SELECT id, dominio, identificador, nombre_mostrar, fecha FROM registro_ejecuciones_old_v1""",
        "DROP TABLE registro_ejecuciones_old_v1",

        "ALTER TABLE historial_clasificacion RENAME TO historial_clasificacion_old_v1",
        """CREATE TABLE historial_clasificacion (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            texto_original      TEXT NOT NULL,
            texto_limpio        TEXT NOT NULL,
            categoria_asignada  TEXT NOT NULL,
            confianza           REAL DEFAULT 0.0,
            metodo              TEXT NOT NULL CHECK(metodo IN ('exacto', 'regex', 'keyword', 'vectorial', 'llm', 'ninguno', 'cache')),
            correcto            INTEGER,
            fecha               DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        """INSERT INTO historial_clasificacion
               (id, texto_original, texto_limpio, categoria_asignada, confianza, metodo, correcto, fecha)
           SELECT id, texto_original, texto_limpio, categoria_asignada, confianza, metodo, correcto, fecha
           FROM historial_clasificacion_old_v1""",
        "DROP TABLE historial_clasificacion_old_v1",
    ],
    3: [
        "ALTER TABLE historial_clasificacion RENAME TO historial_clasificacion_old_v2",
        """CREATE TABLE historial_clasificacion (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            texto_original      TEXT NOT NULL,
            texto_limpio        TEXT NOT NULL,
            categoria_asignada  TEXT NOT NULL,
            confianza           REAL DEFAULT 0.0,
            metodo              TEXT NOT NULL CHECK(metodo IN ('exacto', 'regex', 'keyword', 'vectorial', 'llm', 'ninguno', 'cache', 'llm_blindaje')),
            correcto            INTEGER,
            fecha               DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        """INSERT INTO historial_clasificacion
               (id, texto_original, texto_limpio, categoria_asignada, confianza, metodo, correcto, fecha)
           SELECT id, texto_original, texto_limpio, categoria_asignada, confianza, metodo, correcto, fecha
           FROM historial_clasificacion_old_v2""",
        "DROP TABLE historial_clasificacion_old_v2",
    ],
}


def migrar_bd() -> None:
    try:
        with conectar() as conn:
            version_actual = _version_esquema(conn)
            if version_actual >= SCHEMA_VERSION_ACTUAL:
                return
            for version in range(version_actual + 1, SCHEMA_VERSION_ACTUAL + 1):
                for sql in MIGRACIONES.get(version, []):
                    try:
                        conn.execute(sql)
                    except Exception as e:
                        _log_error("database", f"migrar_bd v{version}", e)
                _fijar_version_esquema(conn, version)
            conn.commit()
    except Exception as e:
        _log_error("database", "migrar_bd", e)


# ── PATRONES (Capa 1 — exacto/regex/keyword) ────────────────────────────

def agregar_patron(categoria: str, tipo: str, patron: str,
                    peso_especificidad: Optional[int] = None) -> Optional[int]:
    """
    peso_especificidad por defecto = número de palabras del patrón, para
    que frases más largas/específicas (ej. 'crea una tarea') ganen
    automáticamente sobre verbos genéricos (ej. 'crea') sin tener que
    ordenar categorías a mano como en la v0.5.0.
    """
    if tipo in ("exacto", "keyword"):
        # Se normaliza aquí (no solo al clasificar) para que quede consistente
        # con normalizar_texto() aplicado al texto de entrada — si no, un
        # patrón con tildes o mayúsculas nunca coincide con nada.
        try:
            from utils import normalizar_texto
            patron = normalizar_texto(patron)
        except Exception as e:
            _log_error("database", "agregar_patron (normalizar)", e)
    if peso_especificidad is None:
        peso_especificidad = max(1, len(patron.split()))
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO patrones (categoria, tipo, patron, peso_especificidad)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(categoria, tipo, patron) DO UPDATE SET
                    peso_especificidad = excluded.peso_especificidad,
                    activo = 1
            """, (categoria, tipo, patron, peso_especificidad))
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        _log_error("database", "agregar_patron", e)
        return None


def obtener_patrones(categoria: Optional[str] = None) -> list:
    """Ordenados por especificidad descendente: el más específico se evalúa primero."""
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            if categoria:
                cursor.execute("""
                    SELECT * FROM patrones WHERE activo = 1 AND categoria = ?
                    ORDER BY peso_especificidad DESC
                """, (categoria,))
            else:
                cursor.execute("""
                    SELECT * FROM patrones WHERE activo = 1
                    ORDER BY peso_especificidad DESC
                """)
            return cursor.fetchall()
    except Exception as e:
        _log_error("database", "obtener_patrones", e)
        return []


def desactivar_patron(id_patron: int) -> bool:
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE patrones SET activo = 0 WHERE id = ?", (id_patron,))
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        _log_error("database", "desactivar_patron", e)
        return False



def guardar_intencion_aprendida(categoria: str, texto_limpio: str, accion: str,
                                 vector=None, confianza: float = 1.0,
                                 fuente: str = "qwen") -> bool:
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            vec_str = json.dumps(vector) if vector else None
            cursor.execute("""
                INSERT INTO intenciones_aprendidas
                    (categoria, texto_limpio, accion, vector, confianza, fuente)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(categoria, texto_limpio) DO UPDATE SET
                    veces_usada = veces_usada + 1,
                    confianza   = MAX(confianza, excluded.confianza),
                    accion      = excluded.accion,
                    vector      = COALESCE(excluded.vector, vector)
            """, (categoria, texto_limpio, accion, vec_str, confianza, fuente))
            conn.commit()
            return True
    except Exception as e:
        _log_error("database", "guardar_intencion_aprendida", e)
        return False


def obtener_intenciones_vectores(categoria: str) -> list:
    """
    Retorna [(texto_limpio||accion, vector), ...] SOLO de la categoría dada
    — formato compatible con embeddings.buscar_mas_similar().

    Filtro veces_usada >= 2: una intención recién aprendida de Qwen
    (Capa 3) entra aquí con veces_usada=1 desde el primer uso, pero
    Qwen a veces reporta confianza alta (ej. 0.9) en respuestas que en
    realidad son incorrectas o inseguras. Si se dejara disponible desde
    el primer uso, UNA respuesta mala de Qwen se volvería la respuesta
    "de memoria" para todas las veces siguientes que el usuario pida
    algo parecido — sin que Qwen vuelva a evaluarla nunca. Exigir un
    segundo uso (veces_usada >= 2) antes de ofrecerla como atajo de
    Capa 2 da una ventana de "periodo de prueba": si la respuesta era
    mala, /corregir la borra antes de llegar a un segundo uso; si era
    buena, se confirma sola la siguiente vez que aparezca una frase
    parecida.
    """
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT texto_limpio, accion, vector
                FROM intenciones_aprendidas
                WHERE categoria = ? AND vector IS NOT NULL AND veces_usada >= 2
                ORDER BY veces_usada DESC, confianza DESC
            """, (categoria,))
            filas = cursor.fetchall()
    except Exception as e:
        _log_error("database", "obtener_intenciones_vectores", e)
        return []

    resultado = []
    for fila in filas:
        try:
            vec = json.loads(fila["vector"]) if fila["vector"] else None
            if vec:
                resultado.append((f"{fila['texto_limpio']}||{fila['accion']}", vec))
        except Exception as e:
            _log_error("database", "obtener_intenciones_vectores (parseo fila)", e)
            continue
    return resultado


def buscar_intencion_aprendida_exacta(categoria: str, texto_limpio: str) -> Optional[dict]:
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT accion, confianza, veces_usada
                FROM intenciones_aprendidas
                WHERE categoria = ? AND texto_limpio = ?
            """, (categoria, texto_limpio))
            fila = cursor.fetchone()
        if fila:
            return {"accion": fila["accion"], "confianza": fila["confianza"], "veces_usada": fila["veces_usada"]}
        return None
    except Exception as e:
        _log_error("database", "buscar_intencion_aprendida_exacta", e)
        return None

def buscar_intencion_por_texto(texto_limpio: str) -> Optional[dict]:
    """Frase ya aprendida (≥2 usos, o sea probada con éxito) → su categoría, sin vector ni LLM."""
    try:
        with conectar() as conn:
            fila = conn.execute("""SELECT categoria, confianza, veces_usada FROM intenciones_aprendidas
                                   WHERE texto_limpio = ? AND veces_usada >= 2
                                   ORDER BY veces_usada DESC, confianza DESC LIMIT 1""", (texto_limpio,)).fetchone()
        return dict(fila) if fila else None
    except Exception as e:
        _log_error("database", "buscar_intencion_por_texto", e)
        return None
def eliminar_intencion_aprendida(categoria: str, texto_limpio: str) -> bool:
    """Usado cuando el usuario corrige a SARA, para que el match erróneo no se siga reforzando."""
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM intenciones_aprendidas
                WHERE categoria = ? AND texto_limpio = ?
            """, (categoria, texto_limpio))
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        _log_error("database", "eliminar_intencion_aprendida", e)
        return False


def eliminar_intenciones_por_texto(texto_limpio: str) -> bool:
    """Elimina cualquier aprendizaje asociado a una frase, sin importar categoría."""
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM intenciones_aprendidas WHERE texto_limpio = ?",
                (texto_limpio,),
            )
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        _log_error("database", "eliminar_intenciones_por_texto", e)
        return False


def obtener_stats_intenciones_aprendidas(categoria: Optional[str] = None) -> dict:
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            if categoria:
                cursor.execute("SELECT COUNT(*), SUM(veces_usada) FROM intenciones_aprendidas WHERE categoria = ?", (categoria,))
            else:
                cursor.execute("SELECT COUNT(*), SUM(veces_usada) FROM intenciones_aprendidas")
            total, usos = cursor.fetchone()
        return {"total": total or 0, "usos_totales": usos or 0}
    except Exception as e:
        _log_error("database", "obtener_stats_intenciones_aprendidas", e)
        return {"total": 0, "usos_totales": 0}


def obtener_categorias_registradas() -> list:
    """
    Categorías conocidas por el sistema, derivadas de los datos (patrones +
    aprendizaje), no hardcodeadas. Así el motor de clasificación puede
    presentarle a Qwen la lista real de categorías válidas sin que el
    código tenga que declarar cada una manualmente.
    """
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT categoria FROM patrones
                UNION
                SELECT categoria FROM intenciones_aprendidas
                ORDER BY categoria
            """)
            return [fila["categoria"] for fila in cursor.fetchall()]
    except Exception as e:
        _log_error("database", "obtener_categorias_registradas", e)
        return []


# ── CACHÉ DE CLASIFICACIÓN (TTL) ─────────────────────────────────────────

def guardar_cache_intencion(texto_limpio: str, resultado: dict, ttl_segundos: int = 30) -> None:
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO cache_intenciones (texto_limpio, resultado, ts, ttl_segundos)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(texto_limpio) DO UPDATE SET
                    resultado = excluded.resultado,
                    ts = CURRENT_TIMESTAMP,
                    ttl_segundos = excluded.ttl_segundos
            """, (texto_limpio, json.dumps(resultado), ttl_segundos))
            conn.commit()
    except Exception as e:
        _log_error("database", "guardar_cache_intencion", e)


def obtener_cache_intencion(texto_limpio: str) -> Optional[dict]:
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT resultado, ts, ttl_segundos FROM cache_intenciones WHERE texto_limpio = ?
            """, (texto_limpio,))
            fila = cursor.fetchone()
            if not fila:
                return None
            ts = datetime.fromisoformat(fila["ts"]) if isinstance(fila["ts"], str) else fila["ts"]
            if (datetime.utcnow() - ts).total_seconds() > int(fila["ttl_segundos"] or 0):
                cursor.execute("DELETE FROM cache_intenciones WHERE texto_limpio = ?", (texto_limpio,))
                conn.commit()
                return None
            return json.loads(fila["resultado"])
    except Exception as e:
        _log_error("database", "obtener_cache_intencion", e)
        return None


def eliminar_cache_intencion(texto_limpio: str) -> bool:
    """Borra una entrada de caché puntual, antes de que expire su TTL.
    Usado cuando se deshace un aprendizaje prematuro (ver intent_engine.ajustar_aprendizaje_por_resultado)."""
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM cache_intenciones WHERE texto_limpio = ?", (texto_limpio,))
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        _log_error("database", "eliminar_cache_intencion", e)
        return False


def limpiar_cache_vencido() -> int:
    """Retorna cuántas entradas se borraron (0 si no había o si falló)."""
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT texto_limpio, ts, ttl_segundos FROM cache_intenciones")
            filas = cursor.fetchall()
            ahora = datetime.utcnow()
            a_borrar = []
            for f in filas:
                try:
                    ts = datetime.fromisoformat(f["ts"]) if isinstance(f["ts"], str) else f["ts"]
                    if (ahora - ts).total_seconds() > int(f["ttl_segundos"] or 0):
                        a_borrar.append(f["texto_limpio"])
                except Exception as e:
                    _log_error("database", "limpiar_cache_vencido (parseo fila)", e)
            for texto in a_borrar:
                cursor.execute("DELETE FROM cache_intenciones WHERE texto_limpio = ?", (texto,))
            if a_borrar:
                conn.commit()
            return len(a_borrar)
    except Exception as e:
        _log_error("database", "limpiar_cache_vencido", e)
        return 0


# ── CORRECCIONES (retroalimentación del usuario) ────────────────────────
def guardar_correccion(texto_original: str, categoria_erronea: str,
                        categoria_correcta: str, confianza_erronea: float = 0.0) -> bool:
    """
    Registra la corrección y purga TODA huella de la respuesta mala,
    sin importar de qué capa vino:
      - Capa 2 (vectorial): borra la fila en intenciones_aprendidas.
      - Cualquier capa: borra la entrada de cache_intenciones.
      - Capa 1 (exacto/regex/keyword): un patrón de Capa 1 (ej. la
        keyword "recuerdame") es una regla genérica que puede seguir
        siendo correcta para OTRAS frases — no se puede simplemente
        desactivar sin romper casos que sí funcionan. En vez de eso,
        se registra un patrón 'exacto' nuevo para esta frase puntual,
        con prioridad garantizada (peso_especificidad muy alto), que
        SIEMPRE se evalúa antes que cualquier keyword/regex genérico
        en _por_patrones(). Así, la próxima vez que el usuario escriba
        exactamente esta misma frase, gana la corrección — sin tocar
        el patrón genérico que sigue sirviendo para el resto de casos.

    Esto resuelve el mismo problema para las 3 capas con un solo
    mecanismo: un patrón 'exacto' de máxima prioridad es, en la
    práctica, la forma más fuerte de "no vuelvas a responder esto mal".
    """
    try:
        from utils import normalizar_texto
        texto_limpio = normalizar_texto(texto_original)
    except Exception as e:
        _log_error("database", "guardar_correccion (normalizar_texto)", e)
        texto_limpio = texto_original.strip().lower()

    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO correcciones
                    (texto_original, texto_limpio, categoria_erronea, categoria_correcta, confianza_erronea)
                VALUES (?, ?, ?, ?, ?)
            """, (texto_original, texto_limpio, categoria_erronea, categoria_correcta, confianza_erronea))

            # Si una corrección previa dejó un override 'exacto' para esta
            # misma frase pero con OTRA categoría, se desactiva — evita que
            # dos patrones 'exacto' compitan por el mismo texto literal.
            cursor.execute("""
                UPDATE patrones SET activo = 0
                WHERE tipo = 'exacto' AND patron = ? AND categoria != ?
            """, (texto_limpio, categoria_correcta))
            conn.commit()
            
    except Exception as e:
        _log_error("database", "guardar_correccion (insert)", e)
        return False

    # Purga total de la respuesta errónea, sin importar la capa de origen:
    _eliminar_intenciones_de_texto(texto_limpio)                    # cualquier categoría previa
    eliminar_cache_intencion(texto_limpio)                          # respuesta cacheada de cualquier capa

    # Override de máxima prioridad para Capa 1 — cubre el caso de una
    # keyword/regex genérica que clasificó mal ESTA frase puntual.
    PESO_OVERRIDE_CORRECCION = 1000
    agregar_patron(
        categoria_correcta, "exacto", texto_limpio,
        peso_especificidad=PESO_OVERRIDE_CORRECCION + max(1, len(texto_limpio.split())),
    )
    return True


def guardar_validacion(texto_original: str, categoria: str) -> bool:
    """Guarda una validación explícita como patrón exacto persistente."""
    try:
        from utils import normalizar_texto
        texto_limpio = normalizar_texto(texto_original)
        with conectar() as conn:
            conn.execute(
                "UPDATE patrones SET activo = 0 WHERE tipo = 'exacto' AND patron = ? AND categoria != ?",
                (texto_limpio, categoria),
            )
            conn.commit()
        eliminar_cache_intencion(texto_limpio)
        _eliminar_intenciones_de_texto(texto_limpio)
        agregar_patron(
            categoria, "exacto", texto_limpio,
            peso_especificidad=1000 + max(1, len(texto_limpio.split())),
        )
        return True
    except Exception as e:
        _log_error("database", "guardar_validacion", e)
        return False


def _eliminar_intenciones_de_texto(texto_limpio: str) -> bool:
    """Elimina aprendizajes de todas las categorías para una frase corregida."""
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM intenciones_aprendidas WHERE texto_limpio = ?",
                (texto_limpio,),
            )
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        _log_error("database", "_eliminar_intenciones_de_texto", e)
        return False

def obtener_correcciones(categoria: Optional[str] = None) -> list:
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            if categoria:
                cursor.execute("""
                    SELECT * FROM correcciones
                    WHERE categoria_erronea = ? OR categoria_correcta = ?
                    ORDER BY fecha DESC
                """, (categoria, categoria))
            else:
                cursor.execute("SELECT * FROM correcciones ORDER BY fecha DESC")
            return cursor.fetchall()
    except Exception as e:
        _log_error("database", "obtener_correcciones", e)
        return []


# ── HISTORIAL DE CLASIFICACIÓN ───────────────────────────────────────────

def guardar_historial_clasificacion(texto_original: str, texto_limpio: str,
                                     categoria_asignada: str, confianza: float,
                                     metodo: str) -> Optional[int]:
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO historial_clasificacion
                    (texto_original, texto_limpio, categoria_asignada, confianza, metodo)
                VALUES (?, ?, ?, ?, ?)
            """, (texto_original, texto_limpio, categoria_asignada, confianza, metodo))
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        _log_error("database", "guardar_historial_clasificacion", e)
        return None


def obtener_calibracion_confianza(paso: float = 0.05) -> list:
    """
    Agrupa historial_clasificacion (solo metodo IN ('llm','llm_blindaje'),
    los únicos que consultan UMBRAL_CONFIANZA_QWEN) en buckets de tamaño
    `paso`, y calcula el % de aciertos confirmados en cada uno — SOLO
    sobre filas ya confirmadas o corregidas (correcto IS NOT NULL); las
    nunca confirmadas se excluyen en vez de contar como "correctas" por
    omisión silenciosa.
    """
    try:
        with conectar() as conn:
            filas = conn.execute("""
                SELECT confianza, correcto FROM historial_clasificacion
                WHERE metodo IN ('llm', 'llm_blindaje') AND correcto IS NOT NULL
            """).fetchall()
    except Exception as e:
        _log_error("database", "obtener_calibracion_confianza", e)
        return []

    buckets: dict = {}
    for fila in filas:
        conf = fila["confianza"] or 0.0
        clave = round((conf // paso) * paso, 2)
        b = buckets.setdefault(clave, {"total": 0, "aciertos": 0})
        b["total"] += 1
        if fila["correcto"]:
            b["aciertos"] += 1

    return [
        {
            "bucket_desde": desde, "bucket_hasta": round(desde + paso, 2),
            "total_evaluado": buckets[desde]["total"], "aciertos": buckets[desde]["aciertos"],
            "pct_acierto": round(100 * buckets[desde]["aciertos"] / buckets[desde]["total"], 1),
        }
        for desde in sorted(buckets.keys())
    ]

def marcar_resultado_historial(id_historial: int, correcto: bool) -> bool:
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE historial_clasificacion SET correcto = ? WHERE id = ?
            """, (int(correcto), id_historial))
            conn.commit()
            return cursor.rowcount > 0
    except Exception as e:
        _log_error("database", "marcar_resultado_historial", e)
        return False


# ── GENERACIÓN DE FIXTURE DE REGRESIÓN ───────────────────────────────────
# Esto es lo que conecta el historial real de uso con los tests automáticos
# descritos en la idea conceptual: cada corrección y cada acierto confirmado
# se convierte en un caso de prueba, sin escribirlo a mano.

def generar_fixture_regresion() -> dict:
    """
    Retorna: { categoria: {"debe_capturar": [...], "no_debe_capturar": [...]} }

    - debe_capturar: textos confirmados correctos (historial.correcto=1) o
      la categoría correcta de una corrección.
    - no_debe_capturar: textos que SÍ fueron clasificados erróneamente como
      esa categoría según correcciones.categoria_erronea.
    """
    fixture: dict = {}

    def _asegurar(categoria):
        if categoria not in fixture:
            fixture[categoria] = {"debe_capturar": [], "no_debe_capturar": []}

    try:
        with conectar() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT DISTINCT texto_limpio, categoria_asignada
                FROM historial_clasificacion WHERE correcto = 1
            """)
            for fila in cursor.fetchall():
                _asegurar(fila["categoria_asignada"])
                fixture[fila["categoria_asignada"]]["debe_capturar"].append(fila["texto_limpio"])

            cursor.execute("SELECT texto_limpio, categoria_erronea, categoria_correcta FROM correcciones")
            for fila in cursor.fetchall():
                _asegurar(fila["categoria_erronea"])
                _asegurar(fila["categoria_correcta"])
                fixture[fila["categoria_erronea"]]["no_debe_capturar"].append(fila["texto_limpio"])
                fixture[fila["categoria_correcta"]]["debe_capturar"].append(fila["texto_limpio"])

    except Exception as e:
        _log_error("database", "generar_fixture_regresion", e)
        return fixture

    # Dedupe manteniendo orden
    for cat in fixture:
        for clave in ("debe_capturar", "no_debe_capturar"):
            fixture[cat][clave] = list(dict.fromkeys(fixture[cat][clave]))

    return fixture


if __name__ == "__main__":
    crear_tablas()
    print(f"Base de datos de clasificación creada/verificada. Esquema v{SCHEMA_VERSION_ACTUAL}.")


# ── CACHÉS EN MEMORIA DEL MOTOR (v2) ─────────────────────────────────────
# Cada orden leía de disco TODOS los patrones, las categorías y —por cada categoría— todos los vectores
# aprendidos (con json.loads de cada vector), abriendo una conexión SQLite nueva cada vez. Eso se repetía en
# reconocer_rapido Y en clasificar. Ahora se leen una vez y se sirven de memoria hasta que algo los cambia.
# Invalidación: toda función de este módulo que escribe patrones o intenciones limpia las cachés (ver el
# bloque de envoltorios al final) y, como red de seguridad ante escrituras externas, caducan en TTL_CACHES_S.

TTL_CACHES_S = 60.0
_lock_caches = threading.Lock()
_c_patrones = {"t": 0.0, "v": None}
_c_categorias = {"t": 0.0, "v": None}
_c_vectores: dict = {}


def invalidar_caches() -> None:
    with _lock_caches:
        _c_patrones.update(t=0.0, v=None)
        _c_categorias.update(t=0.0, v=None)
        _c_vectores.clear()


def _fresca(entrada: dict) -> bool:
    return entrada["v"] is not None and (time.monotonic() - entrada["t"]) < TTL_CACHES_S


_obtener_patrones_disco = obtener_patrones
_obtener_categorias_disco = obtener_categorias_registradas
_obtener_vectores_disco = obtener_intenciones_vectores


def obtener_patrones(categoria: Optional[str] = None) -> list:          # noqa: F811
    if categoria:
        return _obtener_patrones_disco(categoria)
    with _lock_caches:
        if _fresca(_c_patrones):
            return _c_patrones["v"]
    datos = _obtener_patrones_disco()
    if datos:                                    # una lectura fallida ([]) nunca se cachea
        with _lock_caches:
            _c_patrones.update(t=time.monotonic(), v=datos)
    return datos


def obtener_categorias_registradas() -> list:                            # noqa: F811
    with _lock_caches:
        if _fresca(_c_categorias):
            return list(_c_categorias["v"])
    datos = _obtener_categorias_disco()
    if datos:
        with _lock_caches:
            _c_categorias.update(t=time.monotonic(), v=list(datos))
    return datos


def obtener_intenciones_vectores(categoria: str) -> list:                # noqa: F811
    with _lock_caches:
        e = _c_vectores.get(categoria)
        if e and (time.monotonic() - e["t"]) < TTL_CACHES_S:
            return e["v"]
    datos = _obtener_vectores_disco(categoria)
    with _lock_caches:
        _c_vectores[categoria] = {"t": time.monotonic(), "v": datos}
    return datos


def _invalida_tras(fn):
    def _envuelta(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            invalidar_caches()
    _envuelta.__name__ = fn.__name__
    _envuelta.__doc__ = fn.__doc__
    return _envuelta


for _n in ("agregar_patron", "desactivar_patron", "guardar_intencion_aprendida", "eliminar_intencion_aprendida",
           "eliminar_intenciones_por_texto", "guardar_correccion", "guardar_validacion",
           "_eliminar_intenciones_de_texto"):
    globals()[_n] = _invalida_tras(globals()[_n])
