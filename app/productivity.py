# 📁 productivity.py — Dominio de productividad: tareas, recordatorios, notas (OPTIMIZADO)

import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from dataclasses import dataclass

from database import conectar
from utils import normalizar_texto

try:
    import logger as _logger
except ImportError:
    _logger = None

try:
    from config import (
        MINUTOS_DEFECTO_SIN_FECHA, REPETICIONES_RECORDATORIO,
        VERBOS_TAREA, VERBOS_RECORDATORIO, VERBOS_NOTA,
        VERBOS_TAREA_LISTAR, VERBOS_TAREA_COMPLETAR, VERBOS_TAREA_ELIMINAR,
        VERBOS_RECORDATORIO_LISTAR, VERBOS_RECORDATORIO_ELIMINAR,
        VERBOS_NOTA_LISTAR, VERBOS_NOTA_BUSCAR, VERBOS_NOTA_ELIMINAR,
    )
except ImportError:
    MINUTOS_DEFECTO_SIN_FECHA = 60
    REPETICIONES_RECORDATORIO = {}
    VERBOS_TAREA = VERBOS_RECORDATORIO = VERBOS_NOTA = frozenset()
    VERBOS_TAREA_LISTAR = VERBOS_TAREA_COMPLETAR = VERBOS_TAREA_ELIMINAR = frozenset()
    VERBOS_RECORDATORIO_LISTAR = VERBOS_RECORDATORIO_ELIMINAR = frozenset()
    VERBOS_NOTA_LISTAR = VERBOS_NOTA_BUSCAR = VERBOS_NOTA_ELIMINAR = frozenset()


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        if _logger is None:
            raise ImportError("logger.py no disponible")
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        pass


@dataclass
class FechaExtraida:
    """Estructura de salida del parser de fechas."""
    fecha_hora: datetime
    texto_sin_fecha: str


class _FechaParser:
    """
    Parser simple de fechas sin dependencias.
    OPTIMIZADO: patrones compilados, detección rápida.
    """
    
    # OPTIMIZACIÓN: Patrones precompilados
    _PATRONES_COMPILADOS = None
    
    @classmethod
    def _obtener_patrones(cls):
        """Lazy compilation de patrones regex."""
        if cls._PATRONES_COMPILADOS is None:
            cls._PATRONES_COMPILADOS = [
                (re.compile(r"en\s+(\d+)\s+minutos?", re.IGNORECASE), "minutos"),
                (re.compile(r"en\s+(\d+)\s+horas?", re.IGNORECASE), "horas"),
                # "pasado mañana" ANTES que "mañana"; [ñn] porque texto_norm ya no tiene tildes/ñ
                (re.compile(r"pasado\s+ma[ñn]ana", re.IGNORECASE), "pasado_mañana"),
                (re.compile(r"ma[ñn]ana", re.IGNORECASE), "mañana"),
                (re.compile(r"\bhoy\b", re.IGNORECASE), "hoy"),
            ]
        return cls._PATRONES_COMPILADOS
    
    @staticmethod
    def extraer_fecha_y_texto_limpio(texto_original: str) -> FechaExtraida:
        """
        Extrae fecha/hora + limpia el texto.
        OPTIMIZADO: una sola pasada por regex.
        """
        texto_norm = normalizar_texto(texto_original).lower()
        texto_sin_fecha = texto_original
        fecha_hora = None

        for regex, tipo in _FechaParser._obtener_patrones():
            match = regex.search(texto_norm)
            if match:
                try:
                    ahora = datetime.now()
                    if tipo == "minutos":
                        fecha_hora = ahora + timedelta(minutes=int(match.group(1)))
                    elif tipo == "horas":
                        fecha_hora = ahora + timedelta(hours=int(match.group(1)))
                    elif tipo == "mañana":
                        fecha_hora = ahora + timedelta(days=1)
                    elif tipo == "pasado_mañana":
                        fecha_hora = ahora + timedelta(days=2)
                    elif tipo == "hoy":
                        fecha_hora = ahora
                    
                    if fecha_hora is not None:
                        # Limpiar el texto
                        texto_sin_fecha = regex.sub("", texto_original).strip()   # el patrón ya es IGNORECASE
                        break
                except (ValueError, IndexError):
                    continue
        
        if fecha_hora is None:
            fecha_hora = datetime.now() + timedelta(minutes=MINUTOS_DEFECTO_SIN_FECHA)
        
        return FechaExtraida(
            fecha_hora=fecha_hora,
            texto_sin_fecha=texto_sin_fecha or texto_original
        )


fecha_parser = _FechaParser()


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
    """Inicializa BD para tareas, recordatorios y notas."""
    try:
        with conectar() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS tareas (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                descripcion   TEXT NOT NULL,
                descripcion_n TEXT NOT NULL,
                completada    INTEGER DEFAULT 0,
                fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
                fecha_completada DATETIME DEFAULT NULL
            )""")
            
            conn.execute("""
            CREATE TABLE IF NOT EXISTS recordatorios (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                descripcion   TEXT NOT NULL,
                descripcion_n TEXT NOT NULL,
                fecha_hora    DATETIME NOT NULL,
                repeticion    TEXT DEFAULT 'ninguna',
                completado    INTEGER DEFAULT 0,
                fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
                fecha_completado DATETIME DEFAULT NULL
            )""")
            
            conn.execute("""
            CREATE TABLE IF NOT EXISTS notas (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo        TEXT,
                contenido     TEXT NOT NULL,
                contenido_n   TEXT NOT NULL,
                pinned        INTEGER DEFAULT 0,
                fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
                fecha_edicion DATETIME DEFAULT NULL
            )""")
            
            # MIGRACIÓN: tablas creadas con un esquema anterior (síntoma: 'no such column: descripcion').
            nuevas = _asegurar_columnas(conn, "recordatorios", {
                "descripcion": "TEXT NOT NULL DEFAULT ''", "descripcion_n": "TEXT NOT NULL DEFAULT ''",
                "fecha_hora": "DATETIME NOT NULL DEFAULT '1970-01-01 00:00:00'",
                "repeticion": "TEXT DEFAULT 'ninguna'", "completado": "INTEGER DEFAULT 0",
                "fecha_creacion": "DATETIME", "fecha_completado": "DATETIME"})
            if "descripcion" in nuevas or "fecha_hora" in nuevas:
                # Filas heredadas sin texto/fecha utilizables: se marcan como completadas para que no
                # disparen alertas vacías (no se borran).
                conn.execute("UPDATE recordatorios SET completado = 1 WHERE descripcion = '' OR fecha_hora = '1970-01-01 00:00:00'")
            _asegurar_columnas(conn, "tareas", {
                "descripcion": "TEXT NOT NULL DEFAULT ''", "descripcion_n": "TEXT NOT NULL DEFAULT ''",
                "completada": "INTEGER DEFAULT 0", "fecha_creacion": "DATETIME", "fecha_completada": "DATETIME"})
            _asegurar_columnas(conn, "notas", {
                "titulo": "TEXT", "contenido": "TEXT NOT NULL DEFAULT ''", "contenido_n": "TEXT NOT NULL DEFAULT ''",
                "pinned": "INTEGER DEFAULT 0", "fecha_creacion": "DATETIME", "fecha_edicion": "DATETIME"})
            
            conn.commit()
    except Exception as e:
        _log_error("productivity", "crear_tablas", e)


# ═══════════════════════════════════════════════════════════════════════════
# TAREAS
# ═══════════════════════════════════════════════════════════════════════════

def crear_tarea(descripcion: str) -> Optional[int]:
    """Crea una tarea. OPTIMIZADO: cálculo de descripcion_n."""
    if not descripcion or not descripcion.strip():
        return None
    
    descripcion = descripcion.strip()
    descripcion_n = normalizar_texto(descripcion)
    
    try:
        with conectar() as conn:
            cursor = conn.execute(
                "INSERT INTO tareas (descripcion, descripcion_n) VALUES (?, ?)",
                (descripcion, descripcion_n),
            )
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        _log_error("productivity", "crear_tarea", e)
        return None


def obtener_tareas(solo_pendientes: bool = True) -> List[Dict]:
    """Obtiene tareas con filtro. OPTIMIZADO: índice WHERE."""
    try:
        with conectar() as conn:
            filtro = "WHERE completada = 0" if solo_pendientes else ""
            filas = conn.execute(
                f"SELECT * FROM tareas {filtro} ORDER BY fecha_creacion DESC"
            ).fetchall()
            return [dict(f) for f in filas]
    except Exception as e:
        _log_error("productivity", "obtener_tareas", e)
        return []


def marcar_tarea_completada(id_tarea: int) -> bool:
    """Marca una tarea completada."""
    try:
        with conectar() as conn:
            conn.execute(
                "UPDATE tareas SET completada = 1, fecha_completada = CURRENT_TIMESTAMP WHERE id = ?",
                (id_tarea,),
            )
            conn.commit()
            return True
    except Exception as e:
        _log_error("productivity", "marcar_tarea_completada", e)
        return False


def eliminar_tarea(id_tarea: int) -> bool:
    """Elimina una tarea."""
    try:
        with conectar() as conn:
            conn.execute("DELETE FROM tareas WHERE id = ?", (id_tarea,))
            conn.commit()
            return True
    except Exception as e:
        _log_error("productivity", "eliminar_tarea", e)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# RECORDATORIOS
# ═══════════════════════════════════════════════════════════════════════════

def crear_recordatorio(descripcion: str, fecha_hora: Optional[datetime] = None,
                       repeticion: str = "ninguna") -> Optional[int]:
    """Crea un recordatorio con fecha/hora."""
    if not descripcion or not descripcion.strip():
        return None
    
    descripcion = descripcion.strip()
    descripcion_n = normalizar_texto(descripcion)
    
    if fecha_hora is None:
        fecha_hora = datetime.now() + timedelta(minutes=MINUTOS_DEFECTO_SIN_FECHA)
    
    repeticion = repeticion.lower().strip()
    if repeticion not in REPETICIONES_RECORDATORIO:
        repeticion = "ninguna"
    
    try:
        with conectar() as conn:
            cursor = conn.execute("""
                INSERT INTO recordatorios (descripcion, descripcion_n, fecha_hora, repeticion)
                VALUES (?, ?, ?, ?)
            """, (descripcion, descripcion_n, fecha_hora, repeticion))
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        _log_error("productivity", "crear_recordatorio", e)
        return None


def obtener_recordatorios(solo_pendientes: bool = True) -> List[Dict]:
    """Obtiene recordatorios. OPTIMIZADO: ORDER BY para ordenar próximos primero."""
    try:
        with conectar() as conn:
            filtro = "WHERE completado = 0" if solo_pendientes else ""
            filas = conn.execute(
                f"SELECT * FROM recordatorios {filtro} ORDER BY fecha_hora ASC"
            ).fetchall()
            return [dict(f) for f in filas]
    except Exception as e:
        _log_error("productivity", "obtener_recordatorios", e)
        return []


def obtener_recordatorios_vencidos() -> List[Dict]:
    """Recordatorios que ya pasaron su fecha_hora."""
    try:
        with conectar() as conn:
            filas = conn.execute("""
                SELECT * FROM recordatorios
                WHERE completado = 0 AND fecha_hora <= datetime('now', 'localtime')
                ORDER BY fecha_hora ASC
            """).fetchall()
            return [dict(f) for f in filas]
    except Exception as e:
        _log_error("productivity", "obtener_recordatorios_vencidos", e)
        return []


def marcar_recordatorio_completado(id_recordatorio: int) -> bool:
    """Marca un recordatorio completado."""
    try:
        with conectar() as conn:
            conn.execute("""
                UPDATE recordatorios
                SET completado = 1, fecha_completado = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (id_recordatorio,))
            conn.commit()
            return True
    except Exception as e:
        _log_error("productivity", "marcar_recordatorio_completado", e)
        return False


def eliminar_recordatorio(id_recordatorio: int) -> bool:
    """Elimina un recordatorio."""
    try:
        with conectar() as conn:
            conn.execute("DELETE FROM recordatorios WHERE id = ?", (id_recordatorio,))
            conn.commit()
            return True
    except Exception as e:
        _log_error("productivity", "eliminar_recordatorio", e)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# NOTAS
# ═══════════════════════════════════════════════════════════════════════════

def crear_nota(contenido: str, titulo: str = "") -> Optional[int]:
    """Crea una nota con título opcional."""
    if not contenido or not contenido.strip():
        return None
    
    contenido = contenido.strip()
    contenido_n = normalizar_texto(contenido)
    titulo = (titulo or "").strip()[:100]
    
    try:
        with conectar() as conn:
            cursor = conn.execute(
                "INSERT INTO notas (titulo, contenido, contenido_n) VALUES (?, ?, ?)",
                (titulo, contenido, contenido_n),
            )
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        _log_error("productivity", "crear_nota", e)
        return None


def obtener_notas(pinned_solo: bool = False) -> List[Dict]:
    """Obtiene notas. OPTIMIZADO: pinned primero."""
    try:
        with conectar() as conn:
            filtro = "WHERE pinned = 1" if pinned_solo else ""
            orden = "pinned DESC, fecha_edicion DESC, fecha_creacion DESC"
            filas = conn.execute(
                f"SELECT * FROM notas {filtro} ORDER BY {orden}"
            ).fetchall()
            return [dict(f) for f in filas]
    except Exception as e:
        _log_error("productivity", "obtener_notas", e)
        return []


def buscar_notas(texto_busqueda: str) -> List[Dict]:
    """Busca notas por contenido. OPTIMIZADO: índice FTS si está disponible."""
    if not texto_busqueda or not texto_busqueda.strip():
        return []
    
    texto_n = normalizar_texto(texto_busqueda)
    try:
        with conectar() as conn:
            filas = conn.execute("""
                SELECT * FROM notas
                WHERE contenido_n LIKE ? OR titulo LIKE ?
                ORDER BY fecha_edicion DESC, fecha_creacion DESC
            """, (f"%{texto_n}%", f"%{texto_n}%")).fetchall()
            return [dict(f) for f in filas]
    except Exception as e:
        _log_error("productivity", "buscar_notas", e)
        return []


def fijar_nota(id_nota: int) -> bool:
    """Fija una nota."""
    try:
        with conectar() as conn:
            conn.execute("UPDATE notas SET pinned = 1 WHERE id = ?", (id_nota,))
            conn.commit()
            return True
    except Exception as e:
        _log_error("productivity", "fijar_nota", e)
        return False


def desfijar_nota(id_nota: int) -> bool:
    """Desfija una nota."""
    try:
        with conectar() as conn:
            conn.execute("UPDATE notas SET pinned = 0 WHERE id = ?", (id_nota,))
            conn.commit()
            return True
    except Exception as e:
        _log_error("productivity", "desfijar_nota", e)
        return False


def eliminar_nota(id_nota: int) -> bool:
    """Elimina una nota."""
    try:
        with conectar() as conn:
            conn.execute("DELETE FROM notas WHERE id = ?", (id_nota,))
            conn.commit()
            return True
    except Exception as e:
        _log_error("productivity", "eliminar_nota", e)
        return False




def _quitar_verbo_inicial(texto: str, verbos) -> str:
    """'añade una tarea comprar leche' -> 'comprar leche' (respeta acentos del original)."""
    palabras = (texto or "").strip().split()
    norm = normalizar_texto(" ".join(palabras)).split()
    mejor = 0
    for v in verbos:
        vn = normalizar_texto(v).split()
        if vn and norm[:len(vn)] == vn:
            mejor = max(mejor, len(vn))
    return " ".join(palabras[mejor:]).strip() or (texto or "").strip()

# ═══════════════════════════════════════════════════════════════════════════
# ORQUESTACIÓN DE ACCIONES
# ═══════════════════════════════════════════════════════════════════════════

def _accion_tareas(resultado: dict) -> Optional[dict]:
    """Orquesta acciones sobre tareas."""
    texto = resultado.get("texto_original", "").lower()
    
    if any(v in texto for v in VERBOS_TAREA_LISTAR):
        tareas = obtener_tareas(solo_pendientes=True)
        if not tareas:
            return {"estado": "ok", "mensaje": "No tienes tareas pendientes."}
        lista = "\n".join(f"  • {t['descripcion']}" for t in tareas)
        return {"estado": "ok", "mensaje": f"Tus tareas:\n{lista}"}
    
    if any(v in texto for v in VERBOS_TAREA_COMPLETAR):
        tareas = obtener_tareas()
        if not tareas:
            return {"estado": "ok", "mensaje": "No hay tareas."}
        tarea = tareas[0]
        marcar_tarea_completada(tarea["id"])
        return {"estado": "ok", "mensaje": f"Tarea completada: '{tarea['descripcion']}'"}
    
    if any(v in texto for v in VERBOS_TAREA_ELIMINAR):
        tareas = obtener_tareas()
        if not tareas:
            return {"estado": "ok", "mensaje": "No hay tareas."}
        tarea = tareas[0]
        eliminar_tarea(tarea["id"])
        return {"estado": "ok", "mensaje": f"Tarea eliminada: '{tarea['descripcion']}'"}
    
    # Crear tarea
    descripcion = _quitar_verbo_inicial(resultado.get("texto_original", ""), VERBOS_TAREA)
    id_tarea = crear_tarea(descripcion)
    if id_tarea:
        return {"estado": "ok", "mensaje": f"Tarea creada: '{descripcion}'"}
    return {"estado": "error", "mensaje": "No pude crear la tarea."}


def _accion_recordatorios(resultado: dict) -> Optional[dict]:
    """Orquesta acciones sobre recordatorios."""
    texto = resultado.get("texto_original", "").lower()
    
    if any(v in texto for v in VERBOS_RECORDATORIO_LISTAR):
        recordatorios = obtener_recordatorios()
        if not recordatorios:
            return {"estado": "ok", "mensaje": "No tienes recordatorios."}
        lista = "\n".join(f"  • {r['descripcion']} ({r['fecha_hora']})" for r in recordatorios)
        return {"estado": "ok", "mensaje": f"Tus recordatorios:\n{lista}"}
    
    if any(v in texto for v in VERBOS_RECORDATORIO_ELIMINAR):
        recordatorios = obtener_recordatorios()
        if not recordatorios:
            return {"estado": "ok", "mensaje": "No hay recordatorios."}
        recordatorio = recordatorios[0]
        eliminar_recordatorio(recordatorio["id"])
        return {"estado": "ok", "mensaje": f"Recordatorio eliminado: '{recordatorio['descripcion']}'"}
    
    # Crear recordatorio
    descripcion = _quitar_verbo_inicial(resultado.get("texto_original", ""), VERBOS_RECORDATORIO)
    info_fecha = fecha_parser.extraer_fecha_y_texto_limpio(descripcion)
    id_recordatorio = crear_recordatorio(info_fecha.texto_sin_fecha, info_fecha.fecha_hora)
    if id_recordatorio:
        return {"estado": "ok", "mensaje": f"Recordatorio creado: '{info_fecha.texto_sin_fecha}'"}
    return {"estado": "error", "mensaje": "No pude crear el recordatorio."}


def _accion_notas(resultado: dict) -> Optional[dict]:
    """Orquesta acciones sobre notas."""
    texto = resultado.get("texto_original", "").lower()
    
    if any(v in texto for v in VERBOS_NOTA_LISTAR):
        notas = obtener_notas()
        if not notas:
            return {"estado": "ok", "mensaje": "No tienes notas."}
        lista = "\n".join(f"  • {n['titulo'] or 'Sin título'}" for n in notas)
        return {"estado": "ok", "mensaje": f"Tus notas:\n{lista}"}
    
    if any(v in texto for v in VERBOS_NOTA_BUSCAR):
        termino = texto.split("busca en mis notas", 1)[-1].strip()
        notas = buscar_notas(termino)
        if not notas:
            return {"estado": "ok", "mensaje": f"No encontré notas con '{termino}'."}
        lista = "\n".join(f"  • {n['titulo'] or 'Sin título'}" for n in notas)
        return {"estado": "ok", "mensaje": f"Notas encontradas:\n{lista}"}
    
    if any(v in texto for v in VERBOS_NOTA_ELIMINAR):
        notas = obtener_notas()
        if not notas:
            return {"estado": "ok", "mensaje": "No hay notas."}
        nota = notas[0]
        eliminar_nota(nota["id"])
        return {"estado": "ok", "mensaje": f"Nota eliminada: '{nota['titulo'] or 'Sin título'}'"}
    
    # Crear nota
    contenido = _quitar_verbo_inicial(resultado.get("texto_original", ""), VERBOS_NOTA)
    id_nota = crear_nota(contenido)
    if id_nota:
        return {"estado": "ok", "mensaje": f"Nota creada."}
    return {"estado": "error", "mensaje": "No pude crear la nota."}


def resolver_productividad(resultado: dict) -> Optional[dict]:
    """Distribuye a subtareas de productividad."""
    categoria = resultado.get("categoria", "").upper()
    
    if categoria == "TAREA":
        return _accion_tareas(resultado)
    elif categoria == "RECORDATORIO":
        return _accion_recordatorios(resultado)
    elif categoria == "NOTA":
        return _accion_notas(resultado)
    
    return None

# ═══════════════════════════════════════════════════════════════════════════
# AÑADIDO (integración GUI): contrato que main.py y core_bridge esperan
#   registrar_desde_clasificacion(res) -> {"estado", "texto", ...} | None
#   confirmar_eliminacion(tipo, id)    -> {"exito", "texto"}
# Estados: creado | completado | listado | no_encontrado | ambiguo | candidato_eliminar
# Eliminar NUNCA borra directo: devuelve candidato_eliminar y espera confirmación.
# ═══════════════════════════════════════════════════════════════════════════

from utils import similitud as _similitud, quitar_verbos_y_relleno as _quitar_prefijo

_RELLENO_PROD = frozenset({
    "la", "el", "los", "las", "un", "una", "mi", "mis", "de", "del", "que", "a",
    "tarea", "tareas", "recordatorio", "recordatorios", "nota", "notas",
    "elimina", "eliminar", "borra", "borrar", "quita", "quitar",
    "completa", "completar", "marca", "marcar", "como", "completada", "hecha", "hecho",
    "termina", "terminar", "finaliza", "finalizar", "busca", "buscar", "en", "ver", "muestra",
})

_RE_ELIMINAR = re.compile(r"\b(elimin\w*|borr\w*|quit\w*|suprim\w*)\b")
_RE_COMPLETAR = re.compile(r"\b(complet\w*|marc\w*|termin\w*|finaliz\w*)\b")
_RE_LISTAR = re.compile(r"^(ver|muestra\w*|lista\w*|dime|cuales|que)\b.*\b(tareas|recordatorios|notas)\b")


def _contiene_frase(tn: str, verbos) -> bool:
    return any(normalizar_texto(v) in tn for v in verbos)


def _texto_item(tipo: str, it: dict) -> str:
    if tipo == "nota":
        return normalizar_texto(f"{it.get('titulo') or ''} {it.get('contenido') or ''}")
    return normalizar_texto(it.get("descripcion") or "")


def _etiqueta_item(tipo: str, it: dict) -> str:
    if tipo == "nota":
        return (it.get("titulo") or (it.get("contenido") or "")[:40] or "Sin título").strip()
    return (it.get("descripcion") or "").strip()


def _elegir_item(tipo: str, items: List[Dict], consulta_n: str) -> tuple:
    """-> ('ok', item) | ('no_encontrado', texto) | ('ambiguo', texto)"""
    plural = {"tarea": "tareas", "recordatorio": "recordatorios", "nota": "notas"}[tipo]
    if not items:
        return "no_encontrado", f"No tienes {plural}."
    if not consulta_n:
        if len(items) == 1:
            return "ok", items[0]
        lista = "\n".join(f"  • {_etiqueta_item(tipo, i)}" for i in items[:5])
        return "ambiguo", f"¿Cuál? Dime el nombre de la {tipo}:\n{lista}"
    toks = consulta_n.split()
    puntuados = []
    for it in items:
        cand = _texto_item(tipo, it)
        if consulta_n == cand:
            s = 1.0
        elif cand and all(t in cand for t in toks):
            s = 0.85 + 0.10 * min(1.0, len(consulta_n) / max(1, len(cand)))
        else:
            s = _similitud(consulta_n, cand)
            if s < 0.75:
                continue
        puntuados.append((s, it))
    if not puntuados:
        return "no_encontrado", f"No encontré ninguna {tipo} que coincida con '{consulta_n}'."
    puntuados.sort(key=lambda p: p[0], reverse=True)
    if len(puntuados) > 1 and puntuados[0][0] < 1.0 and puntuados[0][0] - puntuados[1][0] < 0.05:
        lista = "\n".join(f"  • {_etiqueta_item(tipo, i)}" for _s, i in puntuados[:5])
        return "ambiguo", f"Hay varias coincidencias, sé más específico:\n{lista}"
    return "ok", puntuados[0][1]


def _lista_texto(cabecera: str, tipo: str, items: List[Dict], vacio: str) -> str:
    if not items:
        return vacio
    return cabecera + "\n" + "\n".join(f"  • {_etiqueta_item(tipo, i)}" for i in items)


def registrar_desde_clasificacion(resultado: dict) -> Optional[dict]:
    cat = (resultado.get("categoria") or "").upper()
    if cat not in ("TAREA", "RECORDATORIO", "NOTA"):
        return None
    original = resultado.get("texto_original") or ""
    tn = resultado.get("texto_norm") or normalizar_texto(original)
    tipo = {"TAREA": "tarea", "RECORDATORIO": "recordatorio", "NOTA": "nota"}[cat]
    verbos_crear = {"TAREA": VERBOS_TAREA, "RECORDATORIO": VERBOS_RECORDATORIO, "NOTA": VERBOS_NOTA}[cat]
    verbos_listar = {"TAREA": VERBOS_TAREA_LISTAR, "RECORDATORIO": VERBOS_RECORDATORIO_LISTAR,
                     "NOTA": VERBOS_NOTA_LISTAR}[cat]
    consulta = _quitar_prefijo(tn, verbos_crear, _RELLENO_PROD)

    # ── ELIMINAR (pide confirmación) ──────────────────────────────────────
    if _RE_ELIMINAR.search(tn):
        items = {"tarea": lambda: obtener_tareas(solo_pendientes=False),
                 "recordatorio": lambda: obtener_recordatorios(solo_pendientes=False),
                 "nota": obtener_notas}[tipo]()
        est, dato = _elegir_item(tipo, items, consulta)
        if est != "ok":
            return {"estado": est, "texto": dato}
        return {"estado": "candidato_eliminar", "tipo": tipo, "id": dato["id"],
                "texto": f"¿Eliminar la {tipo} '{_etiqueta_item(tipo, dato)}'?"}

    # ── COMPLETAR (tareas y recordatorios) ────────────────────────────────
    if cat in ("TAREA", "RECORDATORIO") and _RE_COMPLETAR.search(tn):
        if cat == "TAREA":
            items = obtener_tareas(solo_pendientes=True)
        else:
            items = obtener_recordatorios(solo_pendientes=True)
        est, dato = _elegir_item(tipo, items, consulta)
        if est != "ok":
            return {"estado": est, "texto": dato}
        ok = marcar_tarea_completada(dato["id"]) if cat == "TAREA" else marcar_recordatorio_completado(dato["id"])
        if not ok:
            return {"estado": "no_encontrado", "texto": f"No pude completar la {tipo}."}
        return {"estado": "completado", "texto": f"Listo, completé la {tipo}: '{_etiqueta_item(tipo, dato)}'."}

    # ── BUSCAR EN NOTAS ───────────────────────────────────────────────────
    if cat == "NOTA" and _contiene_frase(tn, VERBOS_NOTA_BUSCAR):
        termino = _quitar_prefijo(tn, VERBOS_NOTA_BUSCAR, _RELLENO_PROD)
        notas = buscar_notas(termino)
        return {"estado": "listado" if notas else "no_encontrado",
                "texto": _lista_texto(f"Notas con '{termino}':", "nota", notas,
                                      f"No encontré notas con '{termino}'.")}

    # ── LISTAR ────────────────────────────────────────────────────────────
    if _contiene_frase(tn, verbos_listar) or _RE_LISTAR.search(tn):
        if cat == "TAREA":
            items = obtener_tareas(solo_pendientes=True)
            txt = _lista_texto("Tus tareas pendientes:", "tarea", items, "No tienes tareas pendientes.")
        elif cat == "RECORDATORIO":
            items = obtener_recordatorios(solo_pendientes=True)
            txt = ("No tienes recordatorios pendientes." if not items else
                   "Tus recordatorios:\n" + "\n".join(
                       f"  • {i['descripcion']} ({str(i.get('fecha_hora'))[:16]})" for i in items))
        else:
            items = obtener_notas()
            txt = _lista_texto("Tus notas:", "nota", items, "No tienes notas.")
        return {"estado": "listado", "texto": txt}

    # ── CREAR ─────────────────────────────────────────────────────────────
    descripcion = _quitar_verbo_inicial(original, verbos_crear)
    if cat == "TAREA":
        nuevo = crear_tarea(descripcion)
        return ({"estado": "creado", "texto": f"Tarea creada: '{descripcion}'."} if nuevo
                else {"estado": "no_encontrado", "texto": "No pude crear la tarea."})
    if cat == "RECORDATORIO":
        info = fecha_parser.extraer_fecha_y_texto_limpio(descripcion)
        nuevo = crear_recordatorio(info.texto_sin_fecha, info.fecha_hora)
        if not nuevo:
            return {"estado": "no_encontrado", "texto": "No pude crear el recordatorio."}
        return {"estado": "creado",
                "texto": f"Recordatorio creado: '{info.texto_sin_fecha}' para {info.fecha_hora:%d/%m %H:%M}."}
    nuevo = crear_nota(descripcion)
    return ({"estado": "creado", "texto": "Nota guardada."} if nuevo
            else {"estado": "no_encontrado", "texto": "No pude guardar la nota."})


def confirmar_eliminacion(tipo: str, id_item: int) -> dict:
    """Ejecuta la eliminación ya confirmada por el usuario."""
    borrar = {"tarea": eliminar_tarea, "recordatorio": eliminar_recordatorio, "nota": eliminar_nota}.get(tipo)
    if borrar is None:
        return {"exito": False, "texto": f"Tipo desconocido: {tipo}."}
    ok = bool(borrar(id_item))
    return {"exito": ok, "texto": f"{tipo.capitalize()} eliminada." if ok else f"No pude eliminar la {tipo}."}


def obtener_alertas_recordatorios_pendientes() -> List[str]:
    """
    Recordatorios vencidos -> lista de textos de alerta. Cada uno se marca como
    completado al avisarlo (una sola notificación). Lo consume la GUI (core_bridge)
    y el REPL de main.py. fecha_hora se guarda en hora LOCAL, así que se compara
    contra datetime.now() (antes contra CURRENT_TIMESTAMP, que es UTC).
    """
    try:
        ahora = datetime.now().isoformat(sep=" ")
        with conectar() as conn:
            filas = conn.execute(
                "SELECT id, descripcion FROM recordatorios WHERE completado = 0 AND fecha_hora <= ? "
                "ORDER BY fecha_hora ASC", (ahora,)).fetchall()
            for f in filas:
                conn.execute("UPDATE recordatorios SET completado = 1, fecha_completado = CURRENT_TIMESTAMP "
                             "WHERE id = ?", (f["id"],))
            conn.commit()
        return [f"⏰ Recordatorio: {f['descripcion']}" for f in filas]
    except Exception as e:
        _log_error("productivity", "obtener_alertas_recordatorios_pendientes", e)
        return []
