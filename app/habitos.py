# 📁 habitos.py — Pilar 2: hábitos, combos y paquetes (v8, compatible con GUI)
#
# Usa EXACTAMENTE las tablas que crea database.crear_tablas():
#   registro_ejecuciones(dominio ∈ aplicacion|archivo|navegador, identificador, nombre_mostrar, fecha)
#   habitos_paquetes(miembros JSON, nombre_paquete, ventana_horaria_ini/fin, veces_detectado,
#                    veces_confirmado, veces_rechazado, racha_rechazos,
#                    estado ∈ candidato|promovido|descartado|fijo, ultima_sugerencia, ...)
#
# La versión anterior (dominio "OPTIMIZADO") definía tablas propias con otro esquema
# (`detecciones_habito`, `sugerencias_habitos`) que nunca se leían y un INSERT que no llenaba
# `nombre_mostrar NOT NULL`, por lo que ninguna ejecución quedaba registrada.
#
# API que consumen main.py y core_bridge.py:
#   inicializar, registrar_ejecucion, detectar_y_registrar_combo, sugerencia_pendiente,
#   marcar_sugerencia_mostrada, confirmar_sugerencia, rechazar_sugerencia, obtener_paquete,
#   crear_comando_desde_paquete, buscar_paquete_fijo_por_nombre, ejecutar_paquete,
#   evaluar_guardia_paquete, navegador_preferido, aplicacion_preferida, listar_paquetes,
#   obtener_estadisticas_habitos, limpiar_registro_ejecuciones_antiguo.

import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import database as db
from database import conectar
from utils import normalizar_texto

try:
    import logger as _logger
except ImportError:
    _logger = None

try:
    import memoria_core
except ImportError:
    memoria_core = None

try:
    import salud_sistema
except ImportError:
    salud_sistema = None

try:
    from config import (
        VENTANA_HABITOS_SEGUNDOS, RETENCION_HABITOS_DIAS,
        UMBRAL_DETECCIONES_HABITO, UMBRAL_RECHAZO_DESCARTE,
        TOLERANCIA_VENTANA_HORARIA_MINUTOS,
    )
except Exception:
    VENTANA_HABITOS_SEGUNDOS = 60
    RETENCION_HABITOS_DIAS = 30
    UMBRAL_DETECCIONES_HABITO = 3
    UMBRAL_RECHAZO_DESCARTE = 3
    TOLERANCIA_VENTANA_HORARIA_MINUTOS = 15

try:
    from config import UMBRAL_CONFIRMACION_HECHO as _UMBRAL_FIJO_SUGERIDO
except Exception:
    _UMBRAL_FIJO_SUGERIDO = 2

DOMINIOS_VALIDOS = ("aplicacion", "archivo", "navegador")
COOLDOWN_SUGERENCIA_SEGUNDOS = 4 * 3600   # una sugerencia por paquete cada 4 h como máximo
MAX_MIEMBROS_PAQUETE = 8

_ultimo_combo_procesado: Dict[str, Any] = {"id_ejecucion": None}


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        if _logger is None:
            raise ImportError("logger.py no disponible")
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[HABITOS ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


# ═══════════════════════════════════════════════════════════════════════════
# INICIALIZACIÓN
# ═══════════════════════════════════════════════════════════════════════════

def crear_tablas() -> None:
    """Mismo DDL que database.crear_tablas() (idempotente). Se conserva porque
    core_bridge/main lo invocan por convención; no crea tablas paralelas."""
    try:
        with conectar() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS registro_ejecuciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dominio TEXT NOT NULL CHECK(dominio IN ('aplicacion','archivo','navegador')),
                identificador TEXT NOT NULL,
                nombre_mostrar TEXT NOT NULL,
                fecha DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("""
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
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reg_ejec_fecha ON registro_ejecuciones(fecha)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_paquetes_estado ON habitos_paquetes(estado)")
            conn.commit()
    except Exception as e:
        _log_error("habitos", "crear_tablas", e)


def inicializar() -> None:
    """Poda registros viejos y re-registra como patrón de Capa 1 cada comando fijo
    (así "mi rutina" sigue funcionando tras reiniciar aunque la tabla patrones se limpie)."""
    crear_tablas()
    limpiar_registro_ejecuciones_antiguo(RETENCION_HABITOS_DIAS)
    try:
        with conectar() as conn:
            filas = conn.execute(
                "SELECT nombre_paquete FROM habitos_paquetes WHERE estado = 'fijo' AND nombre_paquete IS NOT NULL"
            ).fetchall()
        for f in filas:
            _registrar_patron_comando(f[0])
    except Exception as e:
        _log_error("habitos", "inicializar (patrones fijos)", e)


def _registrar_patron_comando(nombre: str) -> None:
    try:
        nombre_norm = normalizar_texto(nombre.replace("_", " ").replace("-", " "))
        if nombre_norm:
            db.agregar_patron("EJECUTAR_PAQUETE", "keyword", nombre_norm)
    except Exception as e:
        _log_error("habitos", "_registrar_patron_comando", e)


# ═══════════════════════════════════════════════════════════════════════════
# REGISTRO DE EJECUCIONES Y DETECCIÓN DE COMBOS
# ═══════════════════════════════════════════════════════════════════════════

def registrar_ejecucion(dominio: str, identificador: str, nombre_mostrar: Optional[str] = None) -> bool:
    """
    Registra que el usuario abrió algo. Acepta 2 o 3 argumentos:
      registrar_ejecucion("aplicacion", ruta_exe, "Chrome")   (forma de main.py)
      registrar_ejecucion("aplicacion", "Chrome")             (forma abreviada: id == nombre)
    """
    dominio = (dominio or "").strip().lower()
    identificador = (identificador or "").strip()
    if dominio not in DOMINIOS_VALIDOS:
        return False
    if not identificador and nombre_mostrar:
        identificador = nombre_mostrar.strip()
    if not identificador:
        return False
    nombre = (nombre_mostrar or "").strip() or os.path.basename(identificador) or identificador
    try:
        with conectar() as conn:
            cur = conn.execute(
                "INSERT INTO registro_ejecuciones (dominio, identificador, nombre_mostrar) VALUES (?, ?, ?)",
                (dominio, identificador, nombre),
            )
            conn.commit()
            _ultimo_combo_procesado["ultimo_insertado"] = cur.lastrowid
        return True
    except Exception as e:
        _log_error("habitos", "registrar_ejecucion", e)
        return False


def _parsear_fecha(valor: Any) -> Optional[datetime]:
    if isinstance(valor, datetime):
        return valor
    try:
        return datetime.fromisoformat(str(valor))
    except Exception:
        try:
            return datetime.strptime(str(valor)[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None


def _clave_canonica(miembros: List[dict]) -> str:
    return json.dumps(sorted((m["dominio"], m["identificador"].lower()) for m in miembros))


def _ventana_reciente() -> List[dict]:
    """Ejecuciones encadenadas: cada acción nueva extiende la ventana (VENTANA_HABITOS_SEGUNDOS)."""
    try:
        with conectar() as conn:
            filas = conn.execute(
                "SELECT id, dominio, identificador, nombre_mostrar, fecha FROM registro_ejecuciones "
                "ORDER BY id DESC LIMIT 30"
            ).fetchall()
    except Exception as e:
        _log_error("habitos", "_ventana_reciente", e)
        return []
    if not filas:
        return []
    cadena = [dict(filas[0])]
    previa = _parsear_fecha(filas[0]["fecha"])
    for f in filas[1:]:
        actual = _parsear_fecha(f["fecha"])
        if previa is None or actual is None or (previa - actual).total_seconds() > VENTANA_HABITOS_SEGUNDOS:
            break
        cadena.append(dict(f))
        previa = actual
    cadena.reverse()
    return cadena


def detectar_y_registrar_combo() -> Optional[dict]:
    """
    Si en la ventana reciente hay ≥2 elementos distintos, cuenta esa combinación.
    Al alcanzar UMBRAL_DETECCIONES_HABITO el paquete pasa de 'candidato' a 'promovido'
    (elegible para sugerirse). Retorna None si no hubo combo.
    """
    cadena = _ventana_reciente()
    if len(cadena) < 2:
        return None
    ultimo_id = cadena[-1]["id"]
    if _ultimo_combo_procesado.get("id_ejecucion") == ultimo_id:
        return None   # ya contado (evita doble conteo si se llama dos veces por la misma acción)

    vistos, miembros = set(), []
    for e in cadena:
        clave = (e["dominio"], e["identificador"].lower())
        if clave in vistos:
            continue
        vistos.add(clave)
        miembros.append({"dominio": e["dominio"], "identificador": e["identificador"],
                         "nombre_mostrar": e["nombre_mostrar"]})
    if len(miembros) < 2:
        return None
    miembros = miembros[:MAX_MIEMBROS_PAQUETE]
    clave = _clave_canonica(miembros)

    ini_hhmm = (_parsear_fecha(cadena[0]["fecha"]) or datetime.now()).strftime("%H:%M")
    fin_hhmm = (_parsear_fecha(cadena[-1]["fecha"]) or datetime.now()).strftime("%H:%M")

    try:
        with conectar() as conn:
            existente = None
            for f in conn.execute("SELECT * FROM habitos_paquetes ORDER BY id DESC LIMIT 500").fetchall():
                try:
                    if _clave_canonica(json.loads(f["miembros"])) == clave:
                        existente = f
                        break
                except Exception:
                    continue
            if existente is None:
                cur = conn.execute(
                    "INSERT INTO habitos_paquetes (miembros, ventana_horaria_ini, ventana_horaria_fin) VALUES (?, ?, ?)",
                    (json.dumps(miembros, ensure_ascii=False), ini_hhmm, fin_hhmm),
                )
                conn.commit()
                _ultimo_combo_procesado["id_ejecucion"] = ultimo_id
                return {"id": cur.lastrowid, "estado": "candidato", "veces_detectado": 1, "promovido": False}

            veces = (existente["veces_detectado"] or 0) + 1
            estado = existente["estado"]
            promovido = False
            if estado == "candidato" and veces >= UMBRAL_DETECCIONES_HABITO:
                estado, promovido = "promovido", True
            ini = min(existente["ventana_horaria_ini"] or ini_hhmm, ini_hhmm)
            fin = max(existente["ventana_horaria_fin"] or fin_hhmm, fin_hhmm)
            conn.execute(
                "UPDATE habitos_paquetes SET veces_detectado = ?, estado = ?, ventana_horaria_ini = ?, "
                "ventana_horaria_fin = ?, ultima_deteccion = CURRENT_TIMESTAMP, ultima_actividad = CURRENT_TIMESTAMP "
                "WHERE id = ?", (veces, estado, ini, fin, existente["id"]),
            )
            conn.commit()
            _ultimo_combo_procesado["id_ejecucion"] = ultimo_id
            return {"id": existente["id"], "estado": estado, "veces_detectado": veces, "promovido": promovido}
    except Exception as e:
        _log_error("habitos", "detectar_y_registrar_combo", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PAQUETES
# ═══════════════════════════════════════════════════════════════════════════

def _nombre_automatico(miembros: List[dict]) -> str:
    partes = []
    for m in miembros[:3]:
        base = os.path.splitext(m.get("nombre_mostrar") or m.get("identificador") or "")[0]
        base = re.sub(r"[^a-zA-Z0-9]+", "_", normalizar_texto(base)).strip("_")
        if base:
            partes.append(base)
    return ("_".join(partes) or "paquete")[:50]


def _fila_a_paquete(fila) -> dict:
    try:
        miembros = json.loads(fila["miembros"])
    except Exception:
        miembros = []
    d = dict(fila)
    d["miembros"] = miembros
    if not d.get("nombre_paquete"):
        d["nombre_paquete"] = _nombre_automatico(miembros)
        d["nombre_automatico"] = True
    return d


def obtener_paquete(id_paquete: int) -> Optional[dict]:
    try:
        with conectar() as conn:
            fila = conn.execute("SELECT * FROM habitos_paquetes WHERE id = ?", (id_paquete,)).fetchone()
        return _fila_a_paquete(fila) if fila else None
    except Exception as e:
        _log_error("habitos", "obtener_paquete", e)
        return None


def listar_paquetes(estado: Optional[str] = None) -> List[dict]:
    try:
        with conectar() as conn:
            if estado:
                filas = conn.execute("SELECT * FROM habitos_paquetes WHERE estado = ? ORDER BY id DESC", (estado,)).fetchall()
            else:
                filas = conn.execute("SELECT * FROM habitos_paquetes ORDER BY id DESC").fetchall()
        return [_fila_a_paquete(f) for f in filas]
    except Exception as e:
        _log_error("habitos", "listar_paquetes", e)
        return []


def _hora_en_ventana(ini: Optional[str], fin: Optional[str]) -> bool:
    """Ahora está dentro de [ini, fin] ± TOLERANCIA_VENTANA_HORARIA_MINUTOS (maneja cruce de medianoche)."""
    if not ini or not fin:
        return True
    try:
        h_ini = datetime.strptime(ini, "%H:%M")
        h_fin = datetime.strptime(fin, "%H:%M")
    except ValueError:
        return True
    ahora = datetime.utcnow()   # ini/fin vienen de CURRENT_TIMESTAMP (UTC)
    a = ahora.hour * 60 + ahora.minute
    i = h_ini.hour * 60 + h_ini.minute - TOLERANCIA_VENTANA_HORARIA_MINUTOS
    f = h_fin.hour * 60 + h_fin.minute + TOLERANCIA_VENTANA_HORARIA_MINUTOS
    if i <= f:
        return i <= a <= f
    return a >= i or a <= f   # ventana que cruza la medianoche


def _frase_miembros(miembros: List[dict]) -> str:
    nombres = [m.get("nombre_mostrar") or m.get("identificador") for m in miembros]
    if len(nombres) <= 1:
        return "".join(nombres)
    return ", ".join(nombres[:-1]) + " y " + nombres[-1]


def sugerencia_pendiente() -> Optional[dict]:
    """
    Paquete 'promovido' cuya hora habitual coincide con la actual y que no se sugirió hace poco.
    Retorna dict {id, nombre, texto, miembros:[{dominio, identificador, nombre_mostrar}]} o None.
    """
    try:
        with conectar() as conn:
            filas = conn.execute(
                "SELECT * FROM habitos_paquetes WHERE estado = 'promovido' ORDER BY veces_detectado DESC, id DESC"
            ).fetchall()
    except Exception as e:
        _log_error("habitos", "sugerencia_pendiente", e)
        return None

    ahora = datetime.utcnow()
    recientes = {(e["dominio"], e["identificador"].lower()) for e in _ventana_reciente()
                 if (_parsear_fecha(e["fecha"]) or ahora) >= ahora - timedelta(seconds=VENTANA_HABITOS_SEGUNDOS)}
    for f in filas:
        p = _fila_a_paquete(f)
        ultima = _parsear_fecha(f["ultima_sugerencia"]) if f["ultima_sugerencia"] else None
        if ultima and (ahora - ultima).total_seconds() < COOLDOWN_SUGERENCIA_SEGUNDOS:
            continue
        if not _hora_en_ventana(f["ventana_horaria_ini"], f["ventana_horaria_fin"]):
            continue
        if not p["miembros"]:
            continue
        claves = {(m["dominio"], m["identificador"].lower()) for m in p["miembros"]}
        if claves & recientes:      # el usuario ya está haciendo esto ahora mismo
            continue
        return {
            "id": p["id"],
            "nombre": p["nombre_paquete"],
            "miembros": p["miembros"],
            "texto": f"¿Quieres que abra {_frase_miembros(p['miembros'])}, como sueles hacer a esta hora?",
        }
    return None


def marcar_sugerencia_mostrada(id_paquete: int) -> bool:
    try:
        with conectar() as conn:
            conn.execute("UPDATE habitos_paquetes SET ultima_sugerencia = CURRENT_TIMESTAMP WHERE id = ?", (id_paquete,))
            conn.commit()
        return True
    except Exception as e:
        _log_error("habitos", "marcar_sugerencia_mostrada", e)
        return False


def confirmar_sugerencia(id_paquete: int) -> bool:
    try:
        with conectar() as conn:
            conn.execute(
                "UPDATE habitos_paquetes SET veces_confirmado = veces_confirmado + 1, racha_rechazos = 0, "
                "ultima_actividad = CURRENT_TIMESTAMP WHERE id = ?", (id_paquete,),
            )
            conn.commit()
        return True
    except Exception as e:
        _log_error("habitos", "confirmar_sugerencia", e)
        return False


def rechazar_sugerencia(id_paquete: int) -> bool:
    """UMBRAL_RECHAZO_DESCARTE rechazos seguidos → 'descartado' (deja de sugerirse)."""
    try:
        with conectar() as conn:
            conn.execute(
                "UPDATE habitos_paquetes SET veces_rechazado = veces_rechazado + 1, "
                "racha_rechazos = racha_rechazos + 1, ultima_actividad = CURRENT_TIMESTAMP WHERE id = ?", (id_paquete,),
            )
            conn.execute(
                "UPDATE habitos_paquetes SET estado = 'descartado' "
                "WHERE id = ? AND estado != 'fijo' AND racha_rechazos >= ?", (id_paquete, UMBRAL_RECHAZO_DESCARTE),
            )
            conn.commit()
        return True
    except Exception as e:
        _log_error("habitos", "rechazar_sugerencia", e)
        return False


def crear_comando_desde_paquete(id_paquete: int, nombre: str) -> bool:
    """Convierte un paquete en comando permanente: 'nombre' → abre todos sus miembros."""
    nombre = (nombre or "").strip()
    if not nombre:
        return False
    nombre_norm = normalizar_texto(nombre.replace("_", " ").replace("-", " "))
    if not nombre_norm:
        return False
    try:
        with conectar() as conn:
            choque = conn.execute(
                "SELECT id FROM habitos_paquetes WHERE estado = 'fijo' AND nombre_paquete = ? AND id != ?",
                (nombre, id_paquete),
            ).fetchone()
            if choque:
                return False
            cur = conn.execute(
                "UPDATE habitos_paquetes SET nombre_paquete = ?, estado = 'fijo', ultima_actividad = CURRENT_TIMESTAMP "
                "WHERE id = ?", (nombre, id_paquete),
            )
            conn.commit()
            if cur.rowcount == 0:
                return False
        _registrar_patron_comando(nombre)
        return True
    except Exception as e:
        _log_error("habitos", "crear_comando_desde_paquete", e)
        return False


def buscar_paquete_fijo_por_nombre(texto_norm: str) -> Optional[int]:
    """Id del paquete fijo cuyo nombre aparece (como palabras completas) en el texto; gana el más largo."""
    texto = f" {normalizar_texto(texto_norm)} "
    if not texto.strip():
        return None
    mejor_id, mejor_len = None, 0
    try:
        with conectar() as conn:
            filas = conn.execute(
                "SELECT id, nombre_paquete FROM habitos_paquetes WHERE estado = 'fijo' AND nombre_paquete IS NOT NULL"
            ).fetchall()
        for f in filas:
            nombre = normalizar_texto(f["nombre_paquete"].replace("_", " ").replace("-", " "))
            if nombre and f" {nombre} " in texto and len(nombre) > mejor_len:
                mejor_id, mejor_len = f["id"], len(nombre)
    except Exception as e:
        _log_error("habitos", "buscar_paquete_fijo_por_nombre", e)
    return mejor_id


def evaluar_guardia_paquete(id_paquete: int) -> Optional[dict]:
    """None si todo bien; {"tipo": "advertencia"|"bloqueado", "texto"} según Pilar 3."""
    if salud_sistema is None:
        return None
    p = obtener_paquete(id_paquete)
    if not p:
        return None
    try:
        return salud_sistema.evaluar_guardias_para_paquete(p["miembros"])
    except Exception as e:
        _log_error("habitos", "evaluar_guardia_paquete", e)
        return None


def _abrir_miembro(m: dict) -> Optional[str]:
    """Abre un miembro. Retorna None si OK, o el motivo del fallo."""
    dominio, ident = m.get("dominio"), m.get("identificador", "")
    nombre = m.get("nombre_mostrar") or ident
    if memoria_core is not None and dominio in ("aplicacion", "archivo"):
        try:
            regla = memoria_core.verificar_restriccion(dominio, ident)
        except Exception as e:
            _log_error("habitos", "_abrir_miembro (restriccion)", e)
            regla = None
        if regla:
            return f"{nombre} (restricción activa: {regla})"
    try:
        if dominio == "aplicacion":
            import app_actions
            ok = app_actions.abrir_aplicacion({"ruta_ejecutable": ident, "argumentos": ""})
        elif dominio == "archivo":
            import file_actions
            ok = file_actions.abrir_ruta(ident)
        elif dominio == "navegador":
            import web_navigator
            ok = web_navigator.abrir_home_navegador(ident)
        else:
            ok = False
    except Exception as e:
        _log_error("habitos", "_abrir_miembro", e)
        ok = False
    return None if ok else f"{nombre} (no se pudo abrir)"


def ejecutar_paquete(id_paquete: int) -> dict:
    """
    Abre todos los miembros del paquete. Retorna {"exitoso": bool, "texto": str}.
    Solo el nivel 🔴 (bloqueado) de Pilar 3 detiene la ejecución; la advertencia ⚠️ se
    confirma ANTES (main.py: el usuario ya dijo sí; core_bridge: modal de confirmación).
    No registra estas aperturas como ejecuciones nuevas (evita que un paquete se "auto-refuerce").
    """
    p = obtener_paquete(id_paquete)
    if not p or not p["miembros"]:
        return {"exitoso": False, "texto": "No encontré ese paquete."}

    guardia = evaluar_guardia_paquete(id_paquete)
    if guardia and guardia.get("tipo") == "bloqueado":
        return {"exitoso": False, "texto": guardia["texto"]}

    abiertos, fallidos = [], []
    for m in p["miembros"]:
        error = _abrir_miembro(m)
        (fallidos if error else abiertos).append(error or (m.get("nombre_mostrar") or m.get("identificador")))

    if not abiertos:
        return {"exitoso": False, "texto": "No pude abrir nada del paquete: " + "; ".join(fallidos)}
    texto = f"Abrí {', '.join(abiertos)}."
    if fallidos:
        texto += " No pude: " + "; ".join(fallidos) + "."
    return {"exitoso": True, "texto": texto}


# ═══════════════════════════════════════════════════════════════════════════
# CONSULTAS Y MANTENIMIENTO
# ═══════════════════════════════════════════════════════════════════════════

def _mas_usado(dominio: str) -> Optional[str]:
    try:
        with conectar() as conn:
            fila = conn.execute(
                "SELECT identificador, COUNT(*) AS veces FROM registro_ejecuciones WHERE dominio = ? "
                "GROUP BY identificador ORDER BY veces DESC, MAX(id) DESC LIMIT 1", (dominio,),
            ).fetchone()
        return fila["identificador"] if fila else None
    except Exception as e:
        _log_error("habitos", f"_mas_usado({dominio})", e)
        return None


def navegador_preferido() -> Optional[str]:
    """Ruta del navegador que más se ha usado (la consume web_navigator.seleccionar_navegador)."""
    return _mas_usado("navegador")


def aplicacion_preferida() -> Optional[str]:
    return _mas_usado("aplicacion")


def limpiar_registro_ejecuciones_antiguo(dias: int = 7) -> int:
    try:
        with conectar() as conn:
            cur = conn.execute(
                "DELETE FROM registro_ejecuciones WHERE fecha < datetime('now', '-' || ? || ' days')", (int(dias),)
            )
            conn.commit()
            return cur.rowcount
    except Exception as e:
        _log_error("habitos", "limpiar_registro_ejecuciones_antiguo", e)
        return 0


def obtener_estadisticas_habitos() -> Dict[str, int]:
    try:
        with conectar() as conn:
            def n(sql: str) -> int:
                return conn.execute(sql).fetchone()[0]
            return {
                "ejecuciones_registradas": n("SELECT COUNT(*) FROM registro_ejecuciones"),
                "paquetes_candidatos": n("SELECT COUNT(*) FROM habitos_paquetes WHERE estado = 'candidato'"),
                "paquetes_promovidos": n("SELECT COUNT(*) FROM habitos_paquetes WHERE estado = 'promovido'"),
                "comandos_fijos": n("SELECT COUNT(*) FROM habitos_paquetes WHERE estado = 'fijo'"),
                "paquetes_descartados": n("SELECT COUNT(*) FROM habitos_paquetes WHERE estado = 'descartado'"),
            }
    except Exception as e:
        _log_error("habitos", "obtener_estadisticas_habitos", e)
        return {}
