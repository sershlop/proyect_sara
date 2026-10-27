# 📁 memoria_core.py — Pilar 1: Memoria del usuario y restricciones duras (CORREGIDO)
#
# Dos tipos de dato separados con garantías distintas:
# 1. Hechos blandos (core_memory): se inyectan en el prompt de Qwen — influyen, no obligan
# 2. Restricciones duras (restricciones_usuario): validadas en código determinista — nunca se saltan
#
# BUG FIX #6: Cada hecho ahora es una fila independiente con su propio contador
# veces_mencionado. Se promociona a "confirmado" cuando repite >= UMBRAL_CONFIRMACION_HECHO.
#
# Reutiliza el patrón defensivo de context manager de contexto.py del compañero para
# evitar locks de archivo en Windows.

# DESPUÉS:
from typing import Optional

# Se reusa la conexión y el esquema de database.py — este archivo tenía su
# propio sqlite3.connect() + _crear_tablas() casi idéntico al de
# database.crear_tablas() (core_memory, restricciones_usuario), mismo
# patrón de esquema duplicado ya corregido en habitos.py.
from database import conectar as _conectar

# ── CONSTANTES ────────────────────────────────────────────────────────────
# Importar de config.py si está disponible; fallback si no
try:
    from config import (
        MAX_USUARIO_FACTS,
        UMBRAL_CONFIRMACION_HECHO,
    )
except ImportError:
    MAX_USUARIO_FACTS = 25
    UMBRAL_CONFIRMACION_HECHO = 2


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    """Log centralizado para errores de memoria_core."""
    try:
        import logger as _logger
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[MEMORIA_CORE ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


# ── CONTEXT MANAGER DEFENSIVO (reutilizado de contexto.py del compañero) ───

# DESPUÉS: (bloque eliminado por completo — database.crear_tablas() ya
# crea estas dos tablas como parte del esquema único del proyecto)


# ── PILAR 1: HECHOS BLANDOS (inyectable en prompt) ────────────────────────

def get_name() -> str:
    """Retorna el nombre del usuario, o "" si no se ha guardado."""
    try:
        with _conectar() as conn:
            row = conn.execute(
                "SELECT content FROM core_memory WHERE block = 'nombre'"
            ).fetchone()
        return row[0].strip() if row else ""
    except Exception as e:
        _log_error("memoria_core", "get_name", e)
        return ""


def set_name(nombre: str) -> None:
    """Guarda el nombre del usuario."""
    nombre = (nombre or "").strip()
    if not nombre:
        return
    try:
        with _conectar() as conn:
            conn.execute(
                """
                INSERT INTO core_memory (block, content, veces_mencionado)
                VALUES ('nombre', ?, 1)
                ON CONFLICT(block, content) DO UPDATE SET veces_mencionado = veces_mencionado + 1
                """,
                (nombre,),
            )
    except Exception as e:
        _log_error("memoria_core", "set_name", e)


def append_usuario_fact(dato: str) -> bool:
    """
    Agrega un dato nuevo a la memoria core del usuario como candidato.
    Se promociona a "confirmado" solo tras repetirse UMBRAL_CONFIRMACION_HECHO veces.
    Retorna True si se agregó o incrementó, False si no.
    
    BUG FIX #6: Cada hecho es una fila independiente en core_memory con su propio
    contador veces_mencionado. Si el usuario dice "prefiero Python" dos veces
    exactamente, ese hecho llega a veces=2 y se promociona. Si dice algo parecido
    pero no idéntico, entra como fila separada.
    
    Limitación aceptada (por ahora): no deduplica semánticamente. "Prefiero Python"
    y "me gusta mucho Python" se cuentan como dos hechos distintos. Refinamiento futuro.
    """
    dato = (dato or "").strip()
    if not dato or len(dato) > 500:  # límite sensato de caracteres
        return False
    
    try:
        with _conectar() as conn:
            cursor = conn.cursor()
            
            # Buscar si este hecho exacto ya existe
            row = cursor.execute(
                """
                SELECT id, veces_mencionado FROM core_memory
                WHERE block = 'usuario_hechos' AND content = ?
                """,
                (dato,),
            ).fetchone()
            
            if row:
                # Hecho ya existe — incrementar su contador
                hecho_id, veces_actuales = row
                nueva_veces = veces_actuales + 1
                cursor.execute(
                    """
                    UPDATE core_memory
                    SET veces_mencionado = ?, fecha_actualizacion = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (nueva_veces, hecho_id),
                )
            else:
                # Hecho completamente nuevo — insertarlo con veces=1 (candidato)
                # Antes de insertar, verificar que no excedamos MAX_USUARIO_FACTS
                count = cursor.execute(
                    "SELECT COUNT(*) FROM core_memory WHERE block = 'usuario_hechos'"
                ).fetchone()[0]
                
                if count >= MAX_USUARIO_FACTS:
                    # Borrar el hecho más antiguo (lowest veces_mencionado, oldest fecha)
                    cursor.execute(
                        """
                        DELETE FROM core_memory
                        WHERE block = 'usuario_hechos'
                        AND id = (
                            SELECT id FROM core_memory
                            WHERE block = 'usuario_hechos'
                            ORDER BY veces_mencionado ASC, fecha_actualizacion ASC
                            LIMIT 1
                        )
                        """
                    )
                
                # Ahora insertar el nuevo hecho
                cursor.execute(
                    """
                    INSERT INTO core_memory (block, content, veces_mencionado)
                    VALUES ('usuario_hechos', ?, 1)
                    """,
                    (dato,),
                )
            
            conn.commit()
        return True
    except Exception as e:
        _log_error("memoria_core", "append_usuario_fact", e)
        return False


def get_core_memory_text() -> str:
    """
    Retorna el texto listo para inyectar en el prompt de Qwen (Capa 3 solamente).
    Vacío ("") si aún no hay nada confirmado.
    
    BUG FIX #6: Solo incluye hechos que se han mencionado >= UMBRAL_CONFIRMACION_HECHO veces.
    El nombre siempre se incluye si existe.
    
    Formato retornado:
    ```
    El usuario se llama [nombre].

    Datos confirmados del usuario:
    - hecho 1
    - hecho 2
    - ...
    ```
    """
    try:
        with _conectar() as conn:
            # Obtener nombre si existe
            nombre_row = conn.execute(
                "SELECT content FROM core_memory WHERE block = 'nombre'"
            ).fetchone()
            
            # Obtener SOLO hechos confirmados (veces >= UMBRAL)
            hechos_rows = conn.execute(
                """
                SELECT content, veces_mencionado
                FROM core_memory
                WHERE block = 'usuario_hechos' AND veces_mencionado >= ?
                ORDER BY veces_mencionado DESC, fecha_actualizacion DESC
                """,
                (UMBRAL_CONFIRMACION_HECHO,),
            ).fetchall()
        
        partes = []
        
        # Agregar nombre si existe
        if nombre_row and nombre_row[0].strip():
            partes.append(f"El usuario se llama {nombre_row[0].strip()}.")
        
        # Agregar hechos confirmados si hay
        if hechos_rows:
            hechos_confirmados = [
                f"- {hecho[0]}" for hecho in hechos_rows
            ]
            partes.append("Datos confirmados del usuario:\n" + "\n".join(hechos_confirmados))
        
        # Retornar vacío si no hay nada confirmado
        if not partes:
            return ""
        
        return "\n\n".join(partes)
    except Exception as e:
        _log_error("memoria_core", "get_core_memory_text", e)
        return ""


def listar_hechos(incluir_no_confirmados: bool = False) -> list:
    """
    Lista todos los hechos guardados, útil para auditoría.
    
    Parámetros:
      incluir_no_confirmados: si False (default), solo muestra hechos ya confirmados
    
    Retorna: [{"contenido": str, "veces": int, "estado": "confirmado"|"candidato"}, ...]
    """
    try:
        with _conectar() as conn:
            if incluir_no_confirmados:
                rows = conn.execute(
                    """
                    SELECT content, veces_mencionado
                    FROM core_memory
                    WHERE block = 'usuario_hechos'
                    ORDER BY veces_mencionado DESC, fecha_actualizacion DESC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT content, veces_mencionado
                    FROM core_memory
                    WHERE block = 'usuario_hechos' AND veces_mencionado >= ?
                    ORDER BY veces_mencionado DESC
                    """,
                    (UMBRAL_CONFIRMACION_HECHO,),
                ).fetchall()
        
        resultado = []
        for content, veces in rows:
            estado = "confirmado" if veces >= UMBRAL_CONFIRMACION_HECHO else "candidato"
            resultado.append({
                "contenido": content,
                "veces": veces,
                "estado": estado
            })
        return resultado
    except Exception as e:
        _log_error("memoria_core", "listar_hechos", e)
        return []


def limpiar_hechos_candidatos() -> int:
    """
    Elimina todos los hechos que siguen siendo candidatos (veces < UMBRAL_CONFIRMACION_HECHO).
    Útil para limpiar memoria después de un período de prueba.
    Retorna cuántos se borraron.
    """
    try:
        with _conectar() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM core_memory
                WHERE block = 'usuario_hechos' AND veces_mencionado < ?
                """,
                (UMBRAL_CONFIRMACION_HECHO,),
            )
            return cursor.rowcount
    except Exception as e:
        _log_error("memoria_core", "limpiar_hechos_candidatos", e)
        return 0


# ── PILAR 1: RESTRICCIONES DURAS (validadas en código, nunca en LLM) ──────
def agregar_restriccion(dominio: str, identificador: str, regla: str) -> bool:
    """
    Agrega una restricción sobre una app o archivo.
    
    AMARILLO 9 FIX: Valida entrada completamente.
    """
    # AMARILLO 9 FIX: Validación defensiva
    dominio = (dominio or "").strip().lower()
    identificador = (identificador or "").strip()
    regla = (regla or "").strip()
    
    # AMARILLO 9 FIX: Rechazar si algún campo está vacío
    if not dominio or not identificador or not regla:
        _log_error("memoria_core", "agregar_restriccion",
                  Exception(f"Entrada vacía: dominio='{dominio}', id='{identificador}', regla='{regla}'"))
        return False
    
    # AMARILLO 9 FIX: Validar que dominio sea conocido
    if dominio not in ("aplicacion", "archivo"):
        _log_error("memoria_core", "agregar_restriccion",
                  Exception(f"Dominio desconocido: {dominio}"))
        return False
    
    # AMARILLO 9 FIX: Validar longitud de entrada
    if len(identificador) > 500 or len(regla) > 500:
        _log_error("memoria_core", "agregar_restriccion",
                  Exception(f"Entrada demasiado larga"))
        return False
    
    try:
        with _conectar() as conn:
            conn.execute(
                """
                INSERT INTO restricciones_usuario (tipo, identificador, regla)
                VALUES (?, ?, ?)
                """,
                (dominio, identificador, regla),
            )
        return True
    except Exception as e:
        _log_error("memoria_core", "agregar_restriccion", e)
        return False


def verificar_restriccion(tipo: str, identificador: str) -> Optional[str]:
    """
    Verifica si existe una restricción para este tipo + identificador.
    
    Retorna:
      - str (la regla original) si existe restricción → BLOQUEA la acción
      - None si no hay restricción → la acción puede proceder
    
    SE LLAMA DETERMINISTICAMENTE en cada punto donde algo se ejecutaría
    automáticamente (antes de app_actions.abrir_aplicacion(),
    antes de habitos.ejecutar_paquete(), etc.).
    """
    tipo = (tipo or "").strip().lower()
    identificador = (identificador or "").strip()
    
    if not tipo or not identificador:
        return None
    
    if tipo not in ("aplicacion", "archivo"):
        return None
    
    try:
        with _conectar() as conn:
            row = conn.execute(
                "SELECT regla FROM restricciones_usuario WHERE tipo = ? AND identificador = ?",
                (tipo, identificador),
            ).fetchone()
        
        return row[0] if row else None
    except Exception as e:
        _log_error("memoria_core", "verificar_restriccion", e)
        return None


def listar_restricciones(tipo: Optional[str] = None) -> list:
    """
    Lista todas las restricciones, opcionalmente filtradas por tipo.
    Retorna lista de dicts: [{"tipo": ..., "identificador": ..., "regla": ...}, ...]
    Uso: para auditoría, no para verificación en tiempo real (que es verificar_restriccion()).
    """
    try:
        with _conectar() as conn:
            if tipo:
                tipo = tipo.strip().lower()
                if tipo not in ("aplicacion", "archivo"):
                    return []
                rows = conn.execute(
                    "SELECT tipo, identificador, regla FROM restricciones_usuario WHERE tipo = ? ORDER BY fecha_creacion DESC",
                    (tipo,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT tipo, identificador, regla FROM restricciones_usuario ORDER BY fecha_creacion DESC"
                ).fetchall()
        
        return [
            {"tipo": r[0], "identificador": r[1], "regla": r[2]}
            for r in rows
        ]
    except Exception as e:
        _log_error("memoria_core", "listar_restricciones", e)
        return []


def eliminar_restriccion(tipo: str, identificador: str) -> bool:
    """Elimina una restricción específica."""
    tipo = (tipo or "").strip().lower()
    identificador = (identificador or "").strip()
    
    if not tipo or not identificador:
        return False
    
    try:
        with _conectar() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM restricciones_usuario WHERE tipo = ? AND identificador = ?",
                (tipo, identificador),
            )
            return cursor.rowcount > 0
    except Exception as e:
        _log_error("memoria_core", "eliminar_restriccion", e)
        return False


# ── UTILITIES PARA DEBUG Y TESTING ──────────────────────────────────────

def debug_dump() -> dict:
    """
    Dump completo de la memoria para debugging. NUNCA usar en producción.
    Retorna: {"nombre": str, "hechos": [...], "restricciones": [...]}
    """
    try:
        nombre = get_name()
        hechos = listar_hechos(incluir_no_confirmados=True)
        restricciones = listar_restricciones()
        return {"nombre": nombre, "hechos": hechos, "restricciones": restricciones}
    except Exception as e:
        _log_error("memoria_core", "debug_dump", e)
        return {}


def reset_total() -> bool:
    """
    Borra TODO. Úsalo SOLO si sabes qué haces. Para testing, no producción.
    """
    try:
        with _conectar() as conn:
            conn.execute("DELETE FROM core_memory")
            conn.execute("DELETE FROM restricciones_usuario")
        return True
    except Exception as e:
        _log_error("memoria_core", "reset_total", e)
        return False
