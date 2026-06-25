# 📁 database.py
import sqlite3
import json
from datetime import datetime
from typing import Optional
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
            accesos           INTEGER DEFAULT 0,
            ultima_modificacion DATETIME,
            ultimo_acceso     DATETIME,
            score_relevancia  REAL DEFAULT 0.0,
            fecha_indexado    DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        # ── PRODUCTIVIDAD ─────────────────────────────────────────────────
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tareas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo      TEXT NOT NULL,
            descripcion TEXT DEFAULT '',
            estado      TEXT DEFAULT 'pendiente',
            prioridad   INTEGER DEFAULT 1,
            fecha_vencimiento DATETIME,
            fecha_completada  DATETIME,
            etiquetas   TEXT DEFAULT '',
            fecha       DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS recordatorios (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            mensaje     TEXT NOT NULL,
            fecha_hora  DATETIME NOT NULL,
            repeticion  TEXT DEFAULT 'ninguna',
            activo      INTEGER DEFAULT 1,
            disparado   INTEGER DEFAULT 0,
            fecha       DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS notas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo      TEXT DEFAULT '',
            contenido   TEXT NOT NULL,
            etiquetas   TEXT DEFAULT '',
            fijada      INTEGER DEFAULT 0,
            fecha       DATETIME DEFAULT CURRENT_TIMESTAMP,
            fecha_edicion DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        # Aprendizaje de intenciones shell desde lenguaje natural
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS intenciones_shell_aprendidas (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            texto_limpio TEXT NOT NULL UNIQUE,
            funcion_shell TEXT NOT NULL,
            vector       TEXT,
            confianza    REAL DEFAULT 1.0,
            veces_usada  INTEGER DEFAULT 1,
            fuente       TEXT DEFAULT 'qwen',
            fecha        DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        # Aprendizaje GENERAL de intenciones por dominio — generalización de
        # intenciones_shell_aprendidas a cualquier categoría (shell_accion,
        # tarea, recordatorio, nota, etc.). Se mantiene intenciones_shell_aprendidas
        # intacta para no romper shell_learner.py; esta tabla es la usada por
        # el nuevo intent_learner.py para el resto de dominios.
        # UNIQUE(categoria, texto_limpio): la misma frase puede significar cosas
        # distintas en dominios distintos sin pisarse entre sí.
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
            texto_limpio   TEXT PRIMARY KEY,
            resultado      TEXT,
            ts             DATETIME DEFAULT CURRENT_TIMESTAMP,
            ttl_segundos   INTEGER DEFAULT 30
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
            "ALTER TABLE indice_archivos ADD COLUMN accesos INTEGER DEFAULT 0",
            "ALTER TABLE indice_archivos ADD COLUMN ultimo_acceso DATETIME",
            "ALTER TABLE indice_archivos ADD COLUMN score_relevancia REAL DEFAULT 0.0",
            # Nuevas columnas para la tabla correcciones
            "ALTER TABLE correcciones ADD COLUMN pregunta_usuario TEXT",
            "ALTER TABLE correcciones ADD COLUMN pregunta_confundida TEXT",
            "ALTER TABLE correcciones ADD COLUMN tipo_error TEXT DEFAULT 'confusion'",
            "ALTER TABLE correcciones ADD COLUMN confianza_erronea REAL DEFAULT 0.0"
            # Tablas de productividad
            """CREATE TABLE IF NOT EXISTS tareas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo TEXT NOT NULL,
                descripcion TEXT DEFAULT '',
                estado TEXT DEFAULT 'pendiente',
                prioridad INTEGER DEFAULT 1,
                fecha_vencimiento DATETIME,
                fecha_completada DATETIME,
                etiquetas TEXT DEFAULT '',
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS recordatorios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mensaje TEXT NOT NULL,
                fecha_hora DATETIME NOT NULL,
                repeticion TEXT DEFAULT 'ninguna',
                activo INTEGER DEFAULT 1,
                disparado INTEGER DEFAULT 0,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS notas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo TEXT DEFAULT '',
                contenido TEXT NOT NULL,
                etiquetas TEXT DEFAULT '',
                fijada INTEGER DEFAULT 0,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
                fecha_edicion DATETIME DEFAULT CURRENT_TIMESTAMP
            )""",
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
    """
    Retorna todos los conocimientos con vector, en formato compatible con
    sqlite3.Row (indexable por nombre de columna: fila["pregunta"]).
    Incluye 'respuesta' porque brain.buscar_respuesta() la necesita para
    devolver la respuesta real, no solo encontrar la pregunta más similar.
    """
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pregunta, respuesta, estado, veces_consultada, vector
            FROM conocimientos
            WHERE vector IS NOT NULL
        """)
        return cursor.fetchall()  # sqlite3.Row — indexable por fila["pregunta"], fila["respuesta"], etc.


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
    guardar_log(registro.get('tipo', 'error'), mensaje, detalle)


def _calcular_score_relevancia(prioridad, accesos, ultima_modificacion=None):
    try:
        prioridad_val = float(prioridad or 0)
        accesos_val   = int(accesos or 0)
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
    """
    Retorna lista de (pregunta, vector) — formato de 2 elementos que
    espera embeddings.buscar_mas_similar().
    La respuesta no se incluye aquí: brain.buscar_respuesta() la busca
    por pregunta en obtener_conocimientos() tras encontrar el match.
    """
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pregunta, vector
            FROM conocimientos WHERE vector IS NOT NULL
        """)
        filas = cursor.fetchall()
    resultado = []
    for fila in filas:
        try:
            vector = json.loads(fila["vector"]) if isinstance(fila["vector"], str) else fila["vector"]
            if vector:
                resultado.append((fila["pregunta"], vector))
        except Exception:
            continue
    return resultado

def obtener_vectores_comandos():
    """
    Retorna los comandos que tienen vector semántico generado, en el
    formato (dict_comando, vector) que brain.buscar_comando() espera
    para comparar por similitud coseno.

    Análoga a obtener_vectores_conocimientos(), pero para la tabla
    comandos en lugar de conocimientos.
    """
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, nombre, palabras_clave, accion, tipo,
                   descripcion, prioridad, activo, veces_usado, vector
            FROM comandos
            WHERE activo = 1 AND vector IS NOT NULL
        """)
        filas = cursor.fetchall()

    resultado = []
    for fila in filas:
        try:
            vector = json.loads(fila["vector"]) if isinstance(fila["vector"], str) else fila["vector"]
            if vector:
                resultado.append((dict(fila), vector))
        except Exception:
            continue
    return resultado

def obtener_datos_vectores():
    with conectar() as conn:
        cursor = conn.cursor()
        
        # 1. Obtener vectores de comandos
        cursor.execute("""
            SELECT id, nombre, palabras_clave, accion, tipo, descripcion, prioridad, 
                   activo, veces_usado, vector
            FROM comandos 
            WHERE activo = 1 AND vector IS NOT NULL
        """)
        comandos_filas = cursor.fetchall()
        comandos = []
        for fila in comandos_filas:
            try:
                vector = json.loads(fila["vector"])
                comandos.append((dict(fila), vector))
            except Exception:
                continue

        # 2. Obtener vectores de conocimientos
        cursor.execute("SELECT pregunta, vector FROM conocimientos WHERE vector IS NOT NULL")
        conocimientos_filas = cursor.fetchall()
        conocimientos = [
            (row["pregunta"], json.loads(row["vector"]) if isinstance(row["vector"], str) else row["vector"])
            for row in conocimientos_filas if row["vector"]
        ]

    # Retorna ambos conjuntos de datos, puedes unirlos en una lista si lo prefieres
    return {
        "comandos": comandos,
        "conocimientos": conocimientos
    }


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

def insertar_archivo_indice(nombre, ruta, tipo, extension="", tamanio_kb=0, prioridad=5, ultima_modificacion=None, accesos=0, score_relevancia=None):
    try:
        if score_relevancia is None:
            score_relevancia = _calcular_score_relevancia(prioridad, accesos, ultima_modificacion)
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO indice_archivos
                    (nombre, ruta, tipo, extension, tamanio_kb, prioridad,
                     veces_accedido, accesos, ultima_modificacion, score_relevancia)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ruta) DO UPDATE SET
                    nombre = excluded.nombre,
                    tipo = excluded.tipo,
                    extension = excluded.extension,
                    tamanio_kb = excluded.tamanio_kb,
                    prioridad = excluded.prioridad,
                    ultima_modificacion = excluded.ultima_modificacion,
                    score_relevancia = excluded.score_relevancia
            """, (
                nombre, ruta, tipo, extension, tamanio_kb,
                prioridad, 0, accesos, ultima_modificacion, score_relevancia
            ))
            conn.commit()
    except Exception:
        pass

def buscar_en_indice(nombre_busqueda, limite=5):
    try:
        try:
            from file_watcher import buscar_en_cache
        except Exception:
            buscar_en_cache = None
        if buscar_en_cache:
            resultados_cache = buscar_en_cache(nombre_busqueda, limite)
            if resultados_cache:
                return resultados_cache
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT nombre, ruta, tipo, prioridad, accesos, score_relevancia
                FROM indice_archivos
                WHERE lower(nombre) LIKE lower(?)
                ORDER BY score_relevancia DESC, prioridad DESC, accesos DESC
                LIMIT ?
            """, (f"%{nombre_busqueda}%", limite))
            return cursor.fetchall()
    except Exception:
        return []

def incrementar_acceso_archivo(ruta):
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT nombre, tipo, extension, tamanio_kb, prioridad, accesos, ultima_modificacion FROM indice_archivos WHERE ruta = ?", (ruta,))
            fila = cursor.fetchone()
            if not fila:
                return
            accesos_actual = int(fila["accesos"] or 0) + 1
            score         = _calcular_score_relevancia(fila["prioridad"], accesos_actual, fila["ultima_modificacion"])
            cursor.execute("""
                UPDATE indice_archivos
                SET veces_accedido = veces_accedido + 1,
                    accesos = ?,
                    ultimo_acceso = CURRENT_TIMESTAMP,
                    score_relevancia = ?
                WHERE ruta = ?
            """, (accesos_actual, score, ruta))
            conn.commit()

            # Intentar actualizar cache en file_watcher si está cargado
            try:
                import file_watcher
                fila_actualizada = {
                    "nombre": fila.get("nombre"),
                    "ruta": ruta,
                    "tipo": fila.get("tipo"),
                    "extension": fila.get("extension"),
                    "tamanio_kb": fila.get("tamanio_kb"),
                    "prioridad": fila["prioridad"],
                    "accesos": accesos_actual,
                    "ultima_modificacion": fila["ultima_modificacion"],
                    "score_relevancia": score
                }
                # usa la función interna para mantener cache actualizada
                if hasattr(file_watcher, "_agregar_o_actualizar_cache"):
                    file_watcher._agregar_o_actualizar_cache(fila_actualizada)
            except Exception:
                pass
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


# ── CACHÉ PERSISTENTE DE INTENCIONES ─────────────────
from datetime import timedelta

def guardar_cache_intencion(texto_limpio, resultado, ttl_segundos=30):
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
    except Exception:
        pass


def obtener_cache_intencion(texto_limpio):
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT resultado, ts, ttl_segundos
                FROM cache_intenciones
                WHERE texto_limpio = ?
            """, (texto_limpio,))
            fila = cursor.fetchone()
            if not fila:
                return None
            try:
                ts = datetime.fromisoformat(fila["ts"]) if isinstance(fila["ts"], str) else fila["ts"]
            except Exception:
                ts = datetime.utcnow()
            ttl = int(fila["ttl_segundos"] or 0)
            if (datetime.utcnow() - ts).total_seconds() > ttl:
                # expirado
                cursor.execute("DELETE FROM cache_intenciones WHERE texto_limpio = ?", (texto_limpio,))
                conn.commit()
                return None
            try:
                return json.loads(fila["resultado"])
            except Exception:
                return None
    except Exception:
        return None


def limpiar_cache_vencido():
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            # eliminar manualmente comprobando diferencia en segundos
            cursor.execute("SELECT texto_limpio, ts, ttl_segundos FROM cache_intenciones")
            filas = cursor.fetchall()
            ahora = datetime.utcnow()
            borrados = 0
            for f in filas:
                try:
                    ts = datetime.fromisoformat(f["ts"]) if isinstance(f["ts"], str) else f["ts"]
                    ttl = int(f["ttl_segundos"] or 0)
                    if (ahora - ts).total_seconds() > ttl:
                        cursor.execute("DELETE FROM cache_intenciones WHERE texto_limpio = ?", (f["texto_limpio"],))
                        borrados += 1
                except Exception:
                    continue
            if borrados:
                conn.commit()
    except Exception:
        pass
# ── TAREAS ────────────────────────────────────────────────────────────────

def agregar_tarea(titulo: str, descripcion: str = "", prioridad: int = 1,
                  fecha_vencimiento=None, etiquetas: str = "") -> int:
    """Inserta una tarea nueva. Retorna el id generado."""
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tareas (titulo, descripcion, prioridad, fecha_vencimiento, etiquetas)
            VALUES (?, ?, ?, ?, ?)
        """, (titulo, descripcion, prioridad, fecha_vencimiento, etiquetas))
        conn.commit()
        return cursor.lastrowid


def obtener_tareas(estado: str = "pendiente", limite: int = 20) -> list:
    """Retorna tareas filtradas por estado, ordenadas por prioridad y vencimiento."""
    with conectar() as conn:
        cursor = conn.cursor()
        if estado == "todas":
            cursor.execute("""
                SELECT * FROM tareas
                ORDER BY prioridad DESC, fecha_vencimiento ASC, fecha DESC
                LIMIT ?
            """, (limite,))
        else:
            cursor.execute("""
                SELECT * FROM tareas
                WHERE estado = ?
                ORDER BY prioridad DESC, fecha_vencimiento ASC, fecha DESC
                LIMIT ?
            """, (estado, limite))
        return cursor.fetchall()


def completar_tarea(id_tarea: int) -> bool:
    """Marca una tarea como completada con timestamp."""
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tareas
            SET estado = 'completada', fecha_completada = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (id_tarea,))
        conn.commit()
        return cursor.rowcount > 0


def eliminar_tarea(id_tarea: int) -> bool:
    """Elimina una tarea definitivamente."""
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tareas WHERE id = ?", (id_tarea,))
        conn.commit()
        return cursor.rowcount > 0


def buscar_tareas(texto: str) -> list:
    """Busca tareas por título o descripción."""
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM tareas
            WHERE lower(titulo) LIKE lower(?) OR lower(descripcion) LIKE lower(?)
            ORDER BY prioridad DESC, fecha DESC
            LIMIT 10
        """, (f"%{texto}%", f"%{texto}%"))
        return cursor.fetchall()


# ── RECORDATORIOS ─────────────────────────────────────────────────────────

def agregar_recordatorio(mensaje: str, fecha_hora,
                         repeticion: str = "ninguna") -> int:
    """Inserta un recordatorio. Retorna el id generado."""
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO recordatorios (mensaje, fecha_hora, repeticion)
            VALUES (?, ?, ?)
        """, (mensaje, fecha_hora, repeticion))
        conn.commit()
        return cursor.lastrowid


def obtener_recordatorios_pendientes() -> list:
    """Retorna recordatorios activos no disparados aún, ordenados por fecha."""
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM recordatorios
            WHERE activo = 1 AND disparado = 0
            ORDER BY fecha_hora ASC
        """)
        return cursor.fetchall()


def obtener_recordatorios_proximos(horas: int = 24) -> list:
    """Retorna recordatorios que disparan en las próximas N horas."""
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM recordatorios
            WHERE activo = 1 AND disparado = 0
              AND fecha_hora <= datetime('now', ? || ' hours')
              AND fecha_hora >= datetime('now')
            ORDER BY fecha_hora ASC
        """, (str(horas),))
        return cursor.fetchall()


def marcar_recordatorio_disparado(id_rec: int) -> None:
    """Marca un recordatorio como ya disparado."""
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE recordatorios SET disparado = 1 WHERE id = ?
        """, (id_rec,))
        conn.commit()


def eliminar_recordatorio(id_rec: int) -> bool:
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM recordatorios WHERE id = ?", (id_rec,))
        conn.commit()
        return cursor.rowcount > 0


# ── NOTAS ─────────────────────────────────────────────────────────────────

def agregar_nota(contenido: str, titulo: str = "",
                 etiquetas: str = "", fijada: bool = False) -> int:
    """Inserta una nota nueva. Retorna el id generado."""
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO notas (titulo, contenido, etiquetas, fijada)
            VALUES (?, ?, ?, ?)
        """, (titulo, contenido, etiquetas, int(fijada)))
        conn.commit()
        return cursor.lastrowid


def obtener_notas(limite: int = 20, solo_fijadas: bool = False) -> list:
    """Retorna notas ordenadas: fijadas primero, luego por fecha."""
    with conectar() as conn:
        cursor = conn.cursor()
        if solo_fijadas:
            cursor.execute("""
                SELECT * FROM notas WHERE fijada = 1
                ORDER BY fecha_edicion DESC LIMIT ?
            """, (limite,))
        else:
            cursor.execute("""
                SELECT * FROM notas
                ORDER BY fijada DESC, fecha_edicion DESC LIMIT ?
            """, (limite,))
        return cursor.fetchall()


def buscar_notas(texto: str) -> list:
    """Busca notas por título, contenido o etiquetas."""
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM notas
            WHERE lower(titulo) LIKE lower(?)
               OR lower(contenido) LIKE lower(?)
               OR lower(etiquetas) LIKE lower(?)
            ORDER BY fijada DESC, fecha_edicion DESC
            LIMIT 10
        """, (f"%{texto}%", f"%{texto}%", f"%{texto}%"))
        return cursor.fetchall()


def actualizar_nota(id_nota: int, contenido: str = None,
                    titulo: str = None, etiquetas: str = None,
                    fijada: bool = None) -> bool:
    """Actualiza campos de una nota existente."""
    with conectar() as conn:
        cursor = conn.cursor()
        campos, valores = [], []
        if contenido is not None:
            campos.append("contenido = ?"); valores.append(contenido)
        if titulo is not None:
            campos.append("titulo = ?"); valores.append(titulo)
        if etiquetas is not None:
            campos.append("etiquetas = ?"); valores.append(etiquetas)
        if fijada is not None:
            campos.append("fijada = ?"); valores.append(int(fijada))
        if not campos:
            return False
        campos.append("fecha_edicion = CURRENT_TIMESTAMP")
        valores.append(id_nota)
        cursor.execute(
            f"UPDATE notas SET {', '.join(campos)} WHERE id = ?",
            valores
        )
        conn.commit()
        return cursor.rowcount > 0


def eliminar_nota(id_nota: int) -> bool:
    with conectar() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM notas WHERE id = ?", (id_nota,))
        conn.commit()
        return cursor.rowcount > 0
# ── INTENCIONES SHELL APRENDIDAS ──────────────────────────────────────────

def guardar_intencion_shell(texto_limpio: str, funcion_shell: str,
                             vector=None, confianza: float = 1.0,
                             fuente: str = "qwen") -> bool:
    """
    Guarda o actualiza una intención shell aprendida.
    Si el texto ya existe, incrementa veces_usada y actualiza confianza.
    vector: lista de floats serializada como JSON, o None.
    """
    import json as _json
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            vec_str = _json.dumps(vector) if vector else None
            cursor.execute("""
                INSERT INTO intenciones_shell_aprendidas
                    (texto_limpio, funcion_shell, vector, confianza, fuente)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(texto_limpio) DO UPDATE SET
                    veces_usada  = veces_usada + 1,
                    confianza    = MAX(confianza, excluded.confianza),
                    funcion_shell = excluded.funcion_shell,
                    vector       = COALESCE(excluded.vector, vector)
            """, (texto_limpio, funcion_shell, vec_str, confianza, fuente))
            conn.commit()
            return True
    except Exception as e:
        try:
            import logger
            logger.error("database", "guardar_intencion_shell", str(e))
        except Exception:
            pass
        return False


def obtener_intenciones_shell_vectores() -> list:
    """
    Retorna lista de (texto_limpio, funcion_shell, vector) para búsqueda semántica.
    Solo las que tienen vector guardado.
    Formato compatible con embeddings.buscar_mas_similar().
    """
    import json as _json
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT texto_limpio, funcion_shell, vector
                FROM intenciones_shell_aprendidas
                WHERE vector IS NOT NULL
                ORDER BY veces_usada DESC, confianza DESC
            """)
            filas = cursor.fetchall()
        resultado = []
        for fila in filas:
            try:
                vec = _json.loads(fila[2]) if fila[2] else None
                if vec:
                    # Formato: (identificador, vector) para buscar_mas_similar
                    # El identificador incluye la funcion para recuperarla después
                    resultado.append((f"{fila[0]}||{fila[1]}", vec))
            except Exception:
                continue
        return resultado
    except Exception:
        return []


def buscar_intencion_shell_exacta(texto_limpio: str):
    """
    Búsqueda exacta por texto_limpio.
    Retorna funcion_shell o None.
    """
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT funcion_shell, confianza, veces_usada
                FROM intenciones_shell_aprendidas
                WHERE texto_limpio = ?
            """, (texto_limpio,))
            fila = cursor.fetchone()
        if fila:
            return {"funcion_shell": fila[0], "confianza": fila[1], "veces_usada": fila[2]}
        return None
    except Exception:
        return None


def obtener_stats_intenciones_shell() -> dict:
    """Estadísticas del sistema de aprendizaje shell."""
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM intenciones_shell_aprendidas")
            total = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM intenciones_shell_aprendidas WHERE vector IS NOT NULL")
            con_vector = cursor.fetchone()[0]
            cursor.execute("SELECT SUM(veces_usada) FROM intenciones_shell_aprendidas")
            usos = cursor.fetchone()[0] or 0
        return {"total": total, "con_vector": con_vector, "usos_totales": usos}
    except Exception:
        return {"total": 0, "con_vector": 0, "usos_totales": 0}


# ── INTENCIONES APRENDIDAS (GENERAL, MULTI-DOMINIO) ───────────────────────
# Generalización de las 4 funciones de "INTENCIONES SHELL APRENDIDAS" arriba.
# Misma lógica exacta, parametrizada por categoria para que cada dominio
# (shell_accion, tarea, recordatorio, nota...) tenga su espacio aislado
# dentro de la misma tabla, sin compararse entre sí jamás.

def guardar_intencion_aprendida(categoria: str, texto_limpio: str, accion: str,
                                 vector=None, confianza: float = 1.0,
                                 fuente: str = "qwen") -> bool:
    """
    Guarda o actualiza una intención aprendida dentro de una categoría/dominio.
    Si (categoria, texto_limpio) ya existe, incrementa veces_usada y actualiza
    confianza (igual que guardar_intencion_shell, pero aislado por categoria).
    vector: lista de floats, o None.
    """
    import json as _json
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            vec_str = _json.dumps(vector) if vector else None
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
        try:
            import logger
            logger.error("database", "guardar_intencion_aprendida", str(e))
        except Exception:
            pass
        return False


def obtener_intenciones_vectores(categoria: str) -> list:
    """
    Retorna [(texto_limpio||accion, vector), ...] SOLO de la categoria dada.
    Aislamiento real: la búsqueda vectorial de 'tarea' nunca compara contra
    vectores de 'shell_accion' ni de ningún otro dominio.
    Formato compatible con embeddings.buscar_mas_similar().
    """
    import json as _json
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT texto_limpio, accion, vector
                FROM intenciones_aprendidas
                WHERE categoria = ? AND vector IS NOT NULL
                ORDER BY veces_usada DESC, confianza DESC
            """, (categoria,))
            filas = cursor.fetchall()
        resultado = []
        for fila in filas:
            try:
                vec = _json.loads(fila[2]) if fila[2] else None
                if vec:
                    resultado.append((f"{fila[0]}||{fila[1]}", vec))
            except Exception:
                continue
        return resultado
    except Exception:
        return []


def buscar_intencion_aprendida_exacta(categoria: str, texto_limpio: str):
    """Búsqueda exacta dentro de una categoria. Retorna dict o None."""
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
            return {"accion": fila[0], "confianza": fila[1], "veces_usada": fila[2]}
        return None
    except Exception:
        return None


def obtener_stats_intenciones_aprendidas(categoria: Optional[str] = None) -> dict:
    """
    Estadísticas del aprendizaje general. Si categoria es None, agrega todas
    las categorías; si se especifica, solo esa.
    """
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            if categoria:
                cursor.execute("SELECT COUNT(*) FROM intenciones_aprendidas WHERE categoria = ?", (categoria,))
                total = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM intenciones_aprendidas WHERE categoria = ? AND vector IS NOT NULL", (categoria,))
                con_vector = cursor.fetchone()[0]
                cursor.execute("SELECT SUM(veces_usada) FROM intenciones_aprendidas WHERE categoria = ?", (categoria,))
                usos = cursor.fetchone()[0] or 0
            else:
                cursor.execute("SELECT COUNT(*) FROM intenciones_aprendidas")
                total = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM intenciones_aprendidas WHERE vector IS NOT NULL")
                con_vector = cursor.fetchone()[0]
                cursor.execute("SELECT SUM(veces_usada) FROM intenciones_aprendidas")
                usos = cursor.fetchone()[0] or 0
        return {"total": total, "con_vector": con_vector, "usos_totales": usos}
    except Exception:
        return {"total": 0, "con_vector": 0, "usos_totales": 0}


def eliminar_intencion_aprendida(categoria: str, texto_limpio: str) -> bool:
    """
    Borra una intención aprendida específica. Usado cuando el usuario corrige
    a SARA (ej. 'batería' aprendió mal como RAM) para que el match erróneo
    no siga reforzándose en futuras búsquedas vectoriales.
    """
    try:
        with conectar() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM intenciones_aprendidas
                WHERE categoria = ? AND texto_limpio = ?
            """, (categoria, texto_limpio))
            conn.commit()
            return cursor.rowcount > 0
    except Exception:
        return False


if __name__ == "__main__":
    crear_tablas()
    print("Base de datos creada correctamente.")