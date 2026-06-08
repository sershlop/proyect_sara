# 📁 database.py
import sqlite3
import json
from datetime import datetime
from config import DB_NAME


def conectar():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def crear_tablas():
    with conectar() as conn:
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS conocimientos (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            pregunta         TEXT NOT NULL,
            respuesta        TEXT NOT NULL,
            estado           TEXT DEFAULT 'nuevo',
            veces_consultada INTEGER DEFAULT 0,
            vector           TEXT,
            fecha            DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS comandos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre          TEXT NOT NULL,
            palabras_clave  TEXT,
            accion          TEXT NOT NULL,
            tipo            TEXT NOT NULL,
            descripcion     TEXT,
            prioridad       INTEGER DEFAULT 1,
            activo          INTEGER DEFAULT 1,
            veces_usado     INTEGER DEFAULT 0,
            vector          TEXT,
            fecha_creacion  DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS acciones_compuestas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            id_comando  INTEGER NOT NULL,
            orden       INTEGER DEFAULT 1,
            accion      TEXT NOT NULL,
            tipo        TEXT NOT NULL,
            descripcion TEXT,
            fecha       DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (id_comando) REFERENCES comandos(id)
        )""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS historial (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            entrada_original TEXT NOT NULL,
            entrada_limpia   TEXT NOT NULL,
            respuesta        TEXT,
            tipo             TEXT,
            confianza        REAL DEFAULT 0.0,
            fecha            DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS intenciones (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            texto_original  TEXT NOT NULL,
            texto_limpio    TEXT NOT NULL,
            tipo            TEXT NOT NULL,
            confianza       REAL DEFAULT 0.0,
            resultado       TEXT DEFAULT 'pendiente',
            fecha           DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS correcciones (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            pregunta_usuario    TEXT NOT NULL,
            pregunta_confundida TEXT,
            respuesta_antigua   TEXT,
            respuesta_nueva     TEXT NOT NULL,
            tipo_error          TEXT DEFAULT 'confusion',
            confianza_erronea   REAL DEFAULT 0.0,
            fecha               DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS interacciones_sociales (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            texto       TEXT NOT NULL,
            tipo_social TEXT NOT NULL,
            fecha       DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo    TEXT NOT NULL,
            mensaje TEXT NOT NULL,
            detalle TEXT,
            fecha   DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS resultados_externos (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            query   TEXT NOT NULL,
            resultado TEXT NOT NULL,
            fuente  TEXT DEFAULT 'web',
            fecha   DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre         TEXT NOT NULL,
            fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS indice_archivos (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre            TEXT NOT NULL,
            ruta              TEXT NOT NULL UNIQUE,
            tipo              TEXT NOT NULL,
            extension         TEXT,
            tamanio_kb        INTEGER DEFAULT 0,
            prioridad         INTEGER DEFAULT 5,
            veces_accedido    INTEGER DEFAULT 0,
            ultima_modificacion DATETIME,
            fecha_indexado    DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")

        conn.commit()
    migrar_bd()


def migrar_bd():
    with conectar() as conn:
        cursor = conn.cursor()
        migraciones = [
            "ALTER TABLE intenciones ADD COLUMN resultado TEXT DEFAULT 'pendiente'",
            "ALTER TABLE conocimientos ADD COLUMN vector TEXT",
            "ALTER TABLE comandos ADD COLUMN vector TEXT",
            """CREATE TABLE IF NOT EXISTS acciones_compuestas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_comando INTEGER NOT NULL,
                orden INTEGER DEFAULT 1,
                accion TEXT NOT NULL,
                tipo TEXT NOT NULL,
                descripcion TEXT,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (id_comando) REFERENCES comandos(id)
            )""",
            """CREATE TABLE IF NOT EXISTS interacciones_sociales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                texto TEXT NOT NULL,
                tipo_social TEXT NOT NULL,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP
            )""",
            # Nuevas columnas para la tabla correcciones
            "ALTER TABLE correcciones ADD COLUMN pregunta_usuario TEXT",
            "ALTER TABLE correcciones ADD COLUMN pregunta_confundida TEXT",
            "ALTER TABLE correcciones ADD COLUMN tipo_error TEXT DEFAULT 'confusion'",
            "ALTER TABLE correcciones ADD COLUMN confianza_erronea REAL DEFAULT 0.0"
        ]
        
        for sql in migraciones:
            try:
                cursor.execute(sql)
            except Exception:
                pass  # Ignora si la columna o tabla ya existe
        
        conn.commit()
def guardar_correccion_completa(pregunta_usuario, pregunta_confundida,
                                 respuesta_antigua, respuesta_nueva,
                                 tipo_error="confusion", confianza_erronea=0.0):
    from utils import normalizar_texto
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO correcciones
                (pregunta_usuario, pregunta_confundida, respuesta_antigua,
                 respuesta_nueva, tipo_error, confianza_erronea)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            normalizar_texto(pregunta_usuario),
            normalizar_texto(pregunta_confundida),
            respuesta_antigua,
            respuesta_nueva,
            tipo_error,
            confianza_erronea
        ))
        conn.commit()

def agregar_comando_si_no_existe(cmd):
    from utils import normalizar_texto, similitud
    comandos = obtener_comandos()
    nombre_norm = normalizar_texto(cmd["nombre"])
    for c in comandos:
        if similitud(nombre_norm, normalizar_texto(c["nombre"])) >= 0.85:
            return
    agregar_comando(
        cmd["nombre"],
        cmd.get("palabras_clave", ""),
        cmd["accion"],
        cmd["tipo"],
        cmd.get("descripcion", "")
    )
    # Vectorizar
    import embeddings
    if embeddings.esta_disponible():
        vector = embeddings.vector_desde_texto(normalizar_texto(cmd["nombre"]))
        if vector:
            guardar_vector_comando(cmd["nombre"], vector)

def obtener_correccion_por_pregunta(texto_limpio):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT respuesta_nueva FROM correcciones
            WHERE lower(trim(pregunta_usuario)) = lower(trim(?))
            OR lower(trim(pregunta_confundida)) = lower(trim(?))
            ORDER BY fecha DESC LIMIT 1
        """, (texto_limpio, texto_limpio))
        return cursor.fetchone()
# ── CONOCIMIENTOS ─────────────────────────────

def obtener_conocimientos():
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT pregunta, respuesta FROM conocimientos")
        return cursor.fetchall()


def guardar_conocimiento(pregunta, respuesta):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO conocimientos (pregunta, respuesta) VALUES (?, ?)",
            (pregunta, respuesta)
        )
        conn.commit()


def agregar_pregunta(pregunta, respuesta):
    guardar_conocimiento(pregunta, respuesta)


def actualizar_respuesta(pregunta, respuesta_nueva):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE conocimientos
            SET respuesta = ?, estado = 'corregido', vector = NULL
            WHERE pregunta = ?
        """, (respuesta_nueva, pregunta))
        conn.commit()


def marcar_pregunta_incorrecta(pregunta):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE conocimientos SET estado = 'incorrecto' WHERE pregunta = ?",
            (pregunta,)
        )
        conn.commit()


def incrementar_consulta(pregunta):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE conocimientos
            SET veces_consultada = veces_consultada + 1
            WHERE pregunta = ?
        """, (pregunta,))
        conn.commit()


# ── COMANDOS ──────────────────────────────────

def obtener_comandos():
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, nombre, palabras_clave, accion,
                   tipo, descripcion, prioridad, activo,
                   veces_usado, fecha_creacion
            FROM comandos
            WHERE activo = 1
            ORDER BY prioridad DESC
        """)
        return cursor.fetchall()


def agregar_comando(nombre, palabras_clave, accion, tipo, descripcion=""):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO comandos
                (nombre, palabras_clave, accion, tipo, descripcion)
            VALUES (?, ?, ?, ?, ?)
        """, (nombre, palabras_clave, accion, tipo, descripcion))
        conn.commit()


def marcar_comando_incorrecto(nombre):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE comandos SET activo = 0 WHERE nombre = ?",
            (nombre,)
        )
        conn.commit()


def incrementar_uso_comando(id_comando):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE comandos
            SET veces_usado = veces_usado + 1
            WHERE id = ?
        """, (id_comando,))
        conn.commit()


# ── ACCIONES COMPUESTAS ───────────────────────

def guardar_accion_compuesta(id_comando, orden, accion, tipo, descripcion=""):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO acciones_compuestas
                (id_comando, orden, accion, tipo, descripcion)
            VALUES (?, ?, ?, ?, ?)
        """, (id_comando, orden, accion, tipo, descripcion))
        conn.commit()


def obtener_acciones_compuestas(id_comando):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, orden, accion, tipo, descripcion
            FROM acciones_compuestas
            WHERE id_comando = ?
            ORDER BY orden ASC
        """, (id_comando,))
        return cursor.fetchall()


def es_comando_compuesto(id_comando):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) as total
            FROM acciones_compuestas
            WHERE id_comando = ?
        """, (id_comando,))
        fila = cursor.fetchone()
        return fila["total"] > 0
def agregar_palabras_clave_comando(id_comando, palabras_nuevas):
    """Agrega palabras clave a un comando existente sin duplicar."""
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT palabras_clave FROM comandos WHERE id = ?", (id_comando,))
            fila = cursor.fetchone()
            if not fila:
                return False
            
            existentes = fila["palabras_clave"] or ""
            set_existentes = {p.strip().lower() for p in existentes.split(",") if p.strip()}
            set_nuevas     = {p.strip().lower() for p in palabras_nuevas.split(",") if p.strip()}
            
            # Solo agregar las que no existen
            agregadas = set_nuevas - set_existentes
            if not agregadas:
                return False
            
            combinadas = ", ".join(sorted(set_existentes | agregadas))
            cursor.execute(
                "UPDATE comandos SET palabras_clave = ? WHERE id = ?",
                (combinadas, id_comando)
            )
            conn.commit()
            return True
    except Exception as e:
        logger.log_excepcion("database", "agregar_palabras_clave_comando", e)
        return False

# ── HISTORIAL ─────────────────────────────────

def guardar_historial(entrada_original, entrada_limpia, respuesta, tipo, confianza=0.0):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO historial
                (entrada_original, entrada_limpia, respuesta, tipo, confianza)
            VALUES (?, ?, ?, ?, ?)
        """, (entrada_original, entrada_limpia, respuesta, tipo, confianza))
        conn.commit()


# ── INTENCIONES ───────────────────────────────

def guardar_intencion(texto_original, texto_limpio, tipo, confianza=0.0):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO intenciones
                (texto_original, texto_limpio, tipo, confianza)
            VALUES (?, ?, ?, ?)
        """, (texto_original, texto_limpio, tipo, confianza))
        conn.commit()


def actualizar_resultado_intencion(texto_limpio, resultado):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE intenciones
            SET resultado = ?
            WHERE texto_limpio = ?
            AND fecha = (
                SELECT MAX(fecha) FROM intenciones
                WHERE texto_limpio = ?
            )
        """, (resultado, texto_limpio, texto_limpio))
        conn.commit()


# ── LOGS ──────────────────────────────────────

def guardar_log(tipo, mensaje, detalle=""):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO logs (tipo, mensaje, detalle)
            VALUES (?, ?, ?)
        """, (tipo, mensaje, detalle))
        conn.commit()


def guardar_log_comando(registro):
    mensaje = f"Comando ejecutado: {registro.get('nombre', '?')}"
    detalle = f"id={registro.get('id_comando')} exito={registro.get('exito')}"
    guardar_log("comando", mensaje, detalle)


def guardar_log_pregunta(registro):
    mensaje = f"Pregunta procesada: {registro.get('pregunta', '?')}"
    detalle = f"correcta={registro.get('correcta')}"
    guardar_log("pregunta", mensaje, detalle)


def guardar_log_error(registro):
    mensaje = f"Error en {registro.get('tipo', '?')}: {registro.get('item', '?')}"
    detalle = registro.get('descripcion', '')
    guardar_log("error", mensaje, detalle)


# ── CORRECCIONES ──────────────────────────────




# ── VECTORES ──────────────────────────────────

def guardar_vector_conocimiento(pregunta, vector):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE conocimientos SET vector = ? WHERE pregunta = ?
        """, (json.dumps(vector), pregunta))
        conn.commit()


def guardar_vector_comando(nombre, vector):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE comandos SET vector = ? WHERE nombre = ?
        """, (json.dumps(vector), nombre))
        conn.commit()


def obtener_vectores_conocimientos():
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pregunta, respuesta, vector
            FROM conocimientos WHERE vector IS NOT NULL
        """)
        filas = cursor.fetchall()
    resultado = []
    for fila in filas:
        try:
            vector = json.loads(fila["vector"])
            resultado.append((fila["pregunta"], fila["respuesta"], vector))
        except Exception:
            continue
    return resultado


def obtener_vectores_comandos():
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, nombre, palabras_clave, accion,
                   tipo, descripcion, prioridad, activo,
                   veces_usado, vector
            FROM comandos WHERE activo = 1 AND vector IS NOT NULL
        """)
        filas = cursor.fetchall()
    resultado = []
    for fila in filas:
        try:
            vector = json.loads(fila["vector"])
            resultado.append((dict(fila), vector))
        except Exception:
            continue
    return resultado


# ── SOCIALES ──────────────────────────────────

def guardar_interaccion_social(texto, tipo_social):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO interacciones_sociales (texto, tipo_social)
            VALUES (?, ?)
        """, (texto, tipo_social))
        conn.commit()


def obtener_stats_sociales():
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT tipo_social, COUNT(*) as total
            FROM interacciones_sociales
            GROUP BY tipo_social
            ORDER BY total DESC
        """)
        return cursor.fetchall()


# ── EXTERNOS ──────────────────────────────────

def agregar_respuesta_externa(query, resultado, fuente="web"):
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO resultados_externos (query, resultado, fuente)
            VALUES (?, ?, ?)
        """, (query, str(resultado), fuente))
        conn.commit()
# ── ÍNDICE DE ARCHIVOS ────────────────────────

def insertar_archivo_indice(nombre, ruta, tipo, extension="", tamanio_kb=0, prioridad=5, ultima_modificacion=None):
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO indice_archivos
                    (nombre, ruta, tipo, extension, tamanio_kb, prioridad, ultima_modificacion)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (nombre, ruta, tipo, extension, tamanio_kb, prioridad, ultima_modificacion))
            conn.commit()
    except Exception:
        pass

def buscar_en_indice(nombre_busqueda, limite=5):
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT nombre, ruta, tipo, prioridad, veces_accedido
                FROM indice_archivos
                WHERE lower(nombre) LIKE lower(?)
                ORDER BY prioridad DESC, veces_accedido DESC
                LIMIT ?
            """, (f"%{nombre_busqueda}%", limite))
            return cursor.fetchall()
    except Exception:
        return []

def incrementar_acceso_archivo(ruta):
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE indice_archivos
                SET veces_accedido = veces_accedido + 1, prioridad = MIN(10, prioridad + 1)
                WHERE ruta = ?
            """, (ruta,))
            conn.commit()
    except Exception:
        pass

def eliminar_archivo_indice(ruta):
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM indice_archivos WHERE ruta = ?", (ruta,))
            conn.commit()
    except Exception:
        pass

def contar_archivos_indice():
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total FROM indice_archivos")
            return cursor.fetchone()["total"]
    except Exception:
        return 0

def indice_vacio():
    return contar_archivos_indice() == 0

if __name__ == "__main__":
    crear_tablas()
    print("Base de datos creada correctamente.")