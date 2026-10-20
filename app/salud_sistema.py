# 📁 salud_sistema.py — Pilar 3: Conciencia de estado y salud del hardware (CORREGIDO)
#
# Evalúa la salud del sistema (RAM, batería, disco) de forma cacheada.
# Bloquea o advierte sobre sugerencias pesadas si los recursos son críticos.
#
# BUG FIX #2 (CRÍTICA): _evaluar_salud_sistema_real() AHORA LEE HARDWARE REAL con psutil
# BUG FIX #3 (CRÍTICA): Eliminada duplicación de evaluar_salud_sistema() (estaba definida 2 veces)
# BUG FIX #7: Soporta estructura JSON nueva de miembros (dominio + identificador)
# NO interfiere con confirmaciones del usuario — nunca mata procesos automáticamente.
#
# Degradación elegante:
#   - Si psutil no disponible → asume todo ok (no bloquea nada)
#   - Si error en lectura de hardware → registra y usa último caché
#   - Si entrada inválida → retorna None (proceder sin guardrails)

import time
import os
from typing import Optional, List, Union, Dict
from datetime import datetime

try:
    from config import (
        REFRESCO_SALUD_SISTEMA_SEGUNDOS,
        UMBRAL_RAM_LIBRE_CRITICO_PCT,
        UMBRAL_BATERIA_CRITICA_PCT,
        UMBRAL_DISCO_CRITICO_PCT,
        REPETICION_ALERTA_SALUD_SEGUNDOS,
    )
except ImportError:
    REFRESCO_SALUD_SISTEMA_SEGUNDOS = 30
    UMBRAL_RAM_LIBRE_CRITICO_PCT = 10
    UMBRAL_BATERIA_CRITICA_PCT = 20
    UMBRAL_DISCO_CRITICO_PCT = 5
    REPETICION_ALERTA_SALUD_SEGUNDOS = 1800

try:
    import psutil
    _PSUTIL_DISPONIBLE = True
except ImportError:
    psutil = None
    _PSUTIL_DISPONIBLE = False


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    """Log centralizado."""
    try:
        import logger as _logger
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[SALUD_SISTEMA ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


# ── CACHÉ EN MEMORIA (no persistencia, por sesión) ──────────────────────────

_CACHE = {
    "timestamp_ultimo_refresco": 0.0,
    "timestamp_creacion": 0.0,
    "salud": {
        "ram_disponible_pct": 100.0,
        "bateria_pct": None,
        "cargando": False,
        "disco_disponible_pct": 100.0,
        "cpu_pct": 0.0,
    }
}

# BUG FIX #3: Límite máximo de vida del caché (48 horas)
MAX_EDAD_CACHE_SEGUNDOS = 48 * 3600


def _evaluar_salud_sistema_real() -> dict:
    """
    BUG FIX #2 (CRÍTICA): Lectura REAL de hardware usando psutil.
    Se llama solo cuando el caché ha expirado.
    
    Retorna:
    {
        "ram_disponible_pct": float (0-100) — % de RAM DISPONIBLE (no usada),
        "bateria_pct": float (0-100) o None si no hay batería,
        "cargando": bool — True si la batería se está cargando,
        "disco_disponible_pct": float (0-100) — % de espacio DISPONIBLE (libre),
        "cpu_pct": float (0-100) — % de CPU en uso,
    }
    
    DEGRADACIÓN ELEGANTE: Si psutil no está disponible, retorna el caché anterior.
    """
    # Empezar con el caché anterior como base (degradación elegante)
    salud_nueva = _CACHE["salud"].copy()
    
    # Si psutil no está disponible, retornar caché viejo sin actualizar
    if not _PSUTIL_DISPONIBLE or psutil is None:
        _log_error("salud_sistema", "_evaluar_salud_sistema_real", 
                  Exception("psutil no disponible — usando caché anterior"))
        return salud_nueva
    
    try:
        # ── RAM: usamos memoria virtual (RAM física) ──────────────────────
        try:
            mem = psutil.virtual_memory()
            # mem.available es RAM disponible para reservar SIN swapear
            # mem.percent es % usado (nosotros reportamos % disponible)
            salud_nueva["ram_disponible_pct"] = mem.available / mem.total * 100
        except Exception as e:
            _log_error("salud_sistema", "_evaluar_salud_sistema_real (RAM)", e)
            # Mantener caché anterior para RAM si falla la lectura
        
        # ── BATERÍA ────────────────────────────────────────────────────────
        try:
            bateria = psutil.sensors_battery()
            if bateria is not None:
                salud_nueva["bateria_pct"] = bateria.percent
                salud_nueva["cargando"] = bateria.power_plugged
            else:
                # No hay batería (equipo de escritorio)
                salud_nueva["bateria_pct"] = None
                salud_nueva["cargando"] = False
        except Exception as e:
            _log_error("salud_sistema", "_evaluar_salud_sistema_real (BATERÍA)", e)
            # Mantener caché anterior para batería si falla
        
        # ── DISCO: usamos la unidad del SO (típicamente C:\ en Windows) ─────
        try:
            # Intentar obtener la ruta raíz de la unidad del SO
            if os.name == 'nt':  # Windows
                # Disco C: (o el que tenga el OS)
                disco_path = os.environ.get('SystemDrive', 'C:') + '\\'
            else:
                # Unix/Linux
                disco_path = '/'
            
            disco = psutil.disk_usage(disco_path)
            # disco.free es espacio DISPONIBLE en bytes
            # disco.total es espacio TOTAL en bytes
            salud_nueva["disco_disponible_pct"] = disco.free / disco.total * 100
        except Exception as e:
            _log_error("salud_sistema", "_evaluar_salud_sistema_real (DISCO)", e)
            # Mantener caché anterior para disco si falla
        
        # ── CPU: carga promedio en últimos 1 segundo ────────────────────────
        try:
            # cpu_percent(interval) retorna % de CPU usado en los últimos N segundos
            # interval=None usa un valor precalculado que no bloquea
            cpu_pct = psutil.cpu_percent(interval=None)
            salud_nueva["cpu_pct"] = cpu_pct
        except Exception as e:
            _log_error("salud_sistema", "_evaluar_salud_sistema_real (CPU)", e)
            # Mantener caché anterior para CPU si falla
        
        return salud_nueva
        
    except Exception as e:
        # Captura final: si algo inesperado ocurre, retornar caché anterior
        _log_error("salud_sistema", "_evaluar_salud_sistema_real (GENERAL)", e)
        return _CACHE["salud"].copy()


# BUG FIX #3 (CRÍTICA): ÚNICA definición de evaluar_salud_sistema()
# Antes estaba duplicada (líneas 82-105 y 119-142) — Python mantenía la segunda.

def evaluar_salud_sistema() -> dict:
    """
    Retorna el estado actual del hardware. CACHEADO: solo se recalcula si han pasado
    >= REFRESCO_SALUD_SISTEMA_SEGUNDOS desde el último refresco.
    
    BUG FIX #3: El caché se descarta y se recalcula de cero si tiene
    más de MAX_EDAD_CACHE_SEGUNDOS (48 horas).
    
    Retorna dict:
    {
        "ram_disponible_pct": float (0-100),
        "bateria_pct": float (0-100) o None si no hay batería,
        "cargando": bool,
        "disco_disponible_pct": float (0-100),
        "cpu_pct": float (0-100),
    }
    
    Degradación elegante: retorna caché anterior si la lectura falla.
    """
    ahora = time.time()
    
    # BUG FIX #3: Verificar antigüedad total del caché
    edad_cache = ahora - _CACHE["timestamp_creacion"]
    if edad_cache > MAX_EDAD_CACHE_SEGUNDOS:
        # Caché demasiado viejo (>48 horas) — resetear timestamps para forzar recálculo
        _CACHE["timestamp_creacion"] = ahora
        _CACHE["timestamp_ultimo_refresco"] = ahora - REFRESCO_SALUD_SISTEMA_SEGUNDOS
    
    # Refresco del caché si ha expirado
    if ahora - _CACHE["timestamp_ultimo_refresco"] >= REFRESCO_SALUD_SISTEMA_SEGUNDOS:
        _CACHE["salud"] = _evaluar_salud_sistema_real()
        _CACHE["timestamp_ultimo_refresco"] = ahora
    
    return _CACHE["salud"].copy()


def iniciar_cache() -> None:
    """
    Se llama UNA VEZ al arrancar main.py (sección 7.1).
    Inicializa timestamp_creacion y hace una lectura inicial no bloqueante.
    """
    try:
        _CACHE["timestamp_creacion"] = time.time()
        evaluar_salud_sistema()
    except Exception as e:
        _log_error("salud_sistema", "iniciar_cache", e)
        # No bloquea — el caché tiene valores por defecto (todo ok)


# ── VALIDACIÓN DE ESTRUCTURA DE MIEMBROS (BUG FIX #7) ────────────────────────

def _normalizar_miembros(paquete_miembros: List) -> tuple:
    """
    Convierte miembros a estructura normalizada y detecta tipos de archivos.
    
    Soporta dos formatos:
    1. NUEVO (JSON): [{"dominio": "aplicacion", "identificador": "..."}, ...]
    2. VIEJO (string): ["C:\\app.exe", "C:\\file.pdf", ...]
    
    Retorna: (miembros_normalizados, tiene_archivos, es_valido)
      - miembros_normalizados: lista de dicts con dominio + identificador
      - tiene_archivos: bool, si hay al menos un miembro con dominio="archivo"
      - es_valido: bool, si la estructura se pudo procesar
    
    BUG FIX #7: Evita la heurística falsa de "tiene punto = archivo"
    """
    if not paquete_miembros or not isinstance(paquete_miembros, (list, tuple)):
        return [], False, False
    
    miembros_norm = []
    tiene_archivos = False
    
    for m in paquete_miembros:
        # Formato JSON nuevo (estructura dict)
        if isinstance(m, dict):
            dominio = (m.get("dominio") or "").strip().lower()
            identificador = (m.get("identificador") or "").strip()
            
            if not identificador or dominio not in ("aplicacion", "archivo"):
                continue  # Ignorar miembros mal formados
            
            miembros_norm.append({"dominio": dominio, "identificador": identificador})
            
            if dominio == "archivo":
                tiene_archivos = True
        
        # Formato string antiguo (compatible hacia atrás)
        elif isinstance(m, str):
            identificador = m.strip()
            if not identificador:
                continue
            
            # Adivinar dominio por extensión (fallback, no ideal)
            es_archivo = identificador.lower().endswith((
                ".txt", ".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".xls",
                ".jpg", ".jpeg", ".png", ".gif", ".mp3", ".mp4", ".zip", ".rar"
            ))
            
            dominio = "archivo" if es_archivo else "aplicacion"
            miembros_norm.append({"dominio": dominio, "identificador": identificador})
            
            if dominio == "archivo":
                tiene_archivos = True
    
    es_valido = len(miembros_norm) > 0 and len(miembros_norm) == len(paquete_miembros)
    return miembros_norm, tiene_archivos, es_valido


# ── GUARDRAILS (bloqueo o advertencia) ────────────────────────────────────

def evaluar_guardias_para_paquete(paquete_miembros: List) -> Optional[dict]:
    """
    Verifica si el sistema puede soportar ejecutar este paquete.
    
    Retorna:
      - None si todo está bien → proceder
      - dict con "tipo": "advertencia" | "bloqueado" y "texto": mensaje
    
    Parámetros:
      paquete_miembros: lista de identificadores O lista de dicts con estructura JSON
                        (soporta ambos formatos por retrocompatibilidad)
    
    BUG FIX #7: Distingue exactamente archivos vs apps, no por heurística de "."
    
    ⚠️ → advertencia (bloqueante, pero requiere confirmación)
    🔴 → bloqueado (se rechaza sin oportunidad)
    """
    # Validar entrada y normalizar
    miembros_norm, tiene_archivos, es_valido = _normalizar_miembros(paquete_miembros)
    
    if not es_valido or len(miembros_norm) == 0:
        # Si la estructura es inválida o vacía, proceder sin restricciones
        # (mejor errar del lado permisivo que bloqueador)
        return None
    
    es_paquete_pesado = len(miembros_norm) > 2
    
    # Obtener estado del hardware
    salud = evaluar_salud_sistema()
    
    # ── GUARDRAIL 1: RAM CRÍTICA (🔴 BLOQUEADO para paquetes pesados) ────
    ram_critica = salud["ram_disponible_pct"] < UMBRAL_RAM_LIBRE_CRITICO_PCT
    
    if es_paquete_pesado and ram_critica:
        ram_usado_pct = 100 - salud["ram_disponible_pct"]
        return {
            "tipo": "bloqueado",
            "texto": f"🔴 RAM crítica ({ram_usado_pct:.1f}% en uso) — no puedo sugerir un paquete pesado ahora. "
                     f"Cierra algunas aplicaciones y vuelve a intentar.",
        }
    
    # ── GUARDRAIL 2: BATERÍA CRÍTICA (⚠️ ADVERTENCIA para paquetes pesados) ─
    si_hay_bateria = salud["bateria_pct"] is not None
    si_bateria_baja = si_hay_bateria and salud["bateria_pct"] < UMBRAL_BATERIA_CRITICA_PCT
    si_sin_cargador = si_hay_bateria and not salud["cargando"]
    
    if es_paquete_pesado and si_bateria_baja and si_sin_cargador:
        return {
            "tipo": "advertencia",
            "texto": f"⚠️ Batería al {salud['bateria_pct']:.1f}% sin cargador — "
                     f"ejecutar un paquete pesado ahora podría apagarte. ¿Realmente quieres continuar?",
        }
    
    # ── GUARDRAIL 3: DISCO CRÍTICO (🔴 BLOQUEADO para operaciones de archivo) ─
    disco_critico = salud["disco_disponible_pct"] < UMBRAL_DISCO_CRITICO_PCT
    
    if tiene_archivos and disco_critico:
        disco_usado_pct = 100 - salud["disco_disponible_pct"]
        return {
            "tipo": "bloqueado",
            "texto": f"🔴 Disco crítico ({disco_usado_pct:.1f}% en uso) — "
                     f"no puedo sugerir operaciones que impliquen archivos. Libera espacio primero.",
        }
    
    # ── GUARDRAIL 4: RAM BAJA PERO NO CRÍTICA (⚠️ ADVERTENCIA para paquetes pesados) ─
    ram_baja = salud["ram_disponible_pct"] < 20  # Entre 20% y CRÍTICO (10%)
    
    if es_paquete_pesado and ram_baja and not ram_critica:
        ram_usado_pct = 100 - salud["ram_disponible_pct"]
        return {
            "tipo": "advertencia",
            "texto": f"⚠️ RAM baja ({ram_usado_pct:.1f}% en uso) — "
                     f"ejecutar un paquete pesado podría ralentizar el sistema. ¿Continuar?",
        }
    
    # ── GUARDRAIL 5: DISCO BAJO PERO NO CRÍTICO (⚠️ ADVERTENCIA para archivos) ──
    disco_bajo = salud["disco_disponible_pct"] < 10  # Entre 10% y CRÍTICO (5%)
    
    if tiene_archivos and disco_bajo and not disco_critico:
        disco_usado_pct = 100 - salud["disco_disponible_pct"]
        return {
            "tipo": "advertencia",
            "texto": f"⚠️ Disco bajo ({disco_usado_pct:.1f}% en uso) — "
                     f"operaciones de archivo podrían fallar. ¿Continuar de todas formas?",
        }
    
    # Todo ok
    return None


# ── SUGERENCIAS INTELIGENTES ──────────────────────────────────────────────

def sugerir_liberacion_recursos() -> Optional[str]:
    """
    Si la RAM está baja, sugiere cerrar procesos específicos.
    Se reutiliza cerrar_procesos() de app_actions.py con confirmación explícita.
    
    Retorna el texto de la sugerencia, o None si la salud es adecuada.
    """
    salud = evaluar_salud_sistema()
    
    if salud["ram_disponible_pct"] < UMBRAL_RAM_LIBRE_CRITICO_PCT:
        ram_usado = 100 - salud["ram_disponible_pct"]
        return (
            f"💾 RAM crítica: {ram_usado:.1f}% en uso. "
            f"¿Quieres que cierre algunas aplicaciones pesadas?"
        )
    
    # RAM baja pero no crítica: sugerencia leve
    if salud["ram_disponible_pct"] < 20:
        ram_usado = 100 - salud["ram_disponible_pct"]
        return (
            f"💾 RAM baja: {ram_usado:.1f}% en uso. "
            f"Considera cerrar aplicaciones pesadas si notas lentitud."
        )
    
    return None


def reporte_salud_para_usuario() -> str:
    """
    Retorna un reporte textual de la salud actual, amigable para mostrar al usuario.
    Incluye emojis visuales para estado rápido (✓ = ok, ⚠️ = bajo, 🔴 = crítico).
    """
    salud = evaluar_salud_sistema()
    
    partes = []
    
    # ── RAM ────────────────────────────────────────────────────────────────
    ram_usado = 100 - salud["ram_disponible_pct"]
    if ram_usado > (100 - UMBRAL_RAM_LIBRE_CRITICO_PCT):
        estado_ram = "🔴"
    elif ram_usado > 80:
        estado_ram = "⚠️"
    else:
        estado_ram = "✓"
    partes.append(f"{estado_ram} RAM: {ram_usado:.1f}% en uso")
    
    # ── BATERÍA ────────────────────────────────────────────────────────────
    if salud["bateria_pct"] is not None:
        bateria_pct = salud["bateria_pct"]
        carga_estado = "🔌 cargando" if salud["cargando"] else "sin cargador"
        
        if bateria_pct < UMBRAL_BATERIA_CRITICA_PCT:
            estado_bat = "🔴"
        elif bateria_pct < 30:
            estado_bat = "⚠️"
        else:
            estado_bat = "✓"
        
        partes.append(f"{estado_bat} Batería: {bateria_pct:.1f}% ({carga_estado})")
    
    # ── DISCO ──────────────────────────────────────────────────────────────
    disco_usado = 100 - salud["disco_disponible_pct"]
    
    if disco_usado > (100 - UMBRAL_DISCO_CRITICO_PCT):
        estado_disco = "🔴"
    elif disco_usado > 90:
        estado_disco = "⚠️"
    else:
        estado_disco = "✓"
    
    partes.append(f"{estado_disco} Disco: {disco_usado:.1f}% en uso")
    
    # ── CPU ────────────────────────────────────────────────────────────────
    cpu_pct = salud["cpu_pct"]
    
    if cpu_pct > 80:
        estado_cpu = "🔴"
    elif cpu_pct > 60:
        estado_cpu = "⚠️"
    else:
        estado_cpu = "✓"
    
    partes.append(f"{estado_cpu} CPU: {cpu_pct:.1f}% en uso")
    
    return " | ".join(partes)

# NUEVO — agregar al final del archivo, después de reporte_salud_para_usuario():

# ── ALERTAS PROACTIVAS (Pilar 3 → usuario, sin que el usuario pregunte) ───

_ULTIMO_NIVEL_ALERTADO: Dict[str, Optional[str]] = {"ram": None, "bateria": None, "disco": None}
_ULTIMA_ALERTA_TIMESTAMP: Dict[str, float] = {"ram": 0.0, "bateria": 0.0, "disco": 0.0}
_ORDEN_NIVEL = {"advertencia": 1, "critico": 2}


def _nivel_ram(ram_disponible_pct: float) -> Optional[str]:
    if ram_disponible_pct < UMBRAL_RAM_LIBRE_CRITICO_PCT:
        return "critico"
    if ram_disponible_pct < 20:
        return "advertencia"
    return None


def _nivel_bateria(bateria_pct: Optional[float], cargando: bool) -> Optional[str]:
    # Con cargador conectado nunca alerta — la advertencia solo tiene
    # sentido si de verdad se está agotando.
    if bateria_pct is None or cargando:
        return None
    if bateria_pct < UMBRAL_BATERIA_CRITICA_PCT:
        return "critico"
    if bateria_pct < 30:
        return "advertencia"
    return None


def _nivel_disco(disco_disponible_pct: float) -> Optional[str]:
    if disco_disponible_pct < UMBRAL_DISCO_CRITICO_PCT:
        return "critico"
    if disco_disponible_pct < 10:
        return "advertencia"
    return None


def _mensaje_ram(nivel: str, ram_disponible_pct: float) -> str:
    ram_usado = 100 - ram_disponible_pct
    if nivel == "critico":
        return f"🔴 RAM crítica: {ram_usado:.1f}% en uso. ¿Quieres que cierre algunas aplicaciones pesadas?"
    return f"⚠️ RAM baja: {ram_usado:.1f}% en uso. Considera cerrar aplicaciones pesadas si notas lentitud."


def _mensaje_bateria(nivel: str, bateria_pct: float) -> str:
    if nivel == "critico":
        return f"🔴 Batería al {bateria_pct:.1f}% sin cargador — conecta el cargador pronto."
    return f"⚠️ Batería al {bateria_pct:.1f}% sin cargador."


def _mensaje_disco(nivel: str, disco_disponible_pct: float) -> str:
    disco_usado = 100 - disco_disponible_pct
    if nivel == "critico":
        return f"🔴 Disco crítico: {disco_usado:.1f}% en uso. Libera espacio — operaciones de archivo podrían fallar."
    return f"⚠️ Disco bajo: {disco_usado:.1f}% en uso."


def evaluar_alertas_proactivas() -> List[dict]:
    """
    Evalúa RAM/batería/disco y decide qué alertas mostrar AHORA, sin
    repetir el mismo aviso en cada turno. Reglas:
    - Recurso vuelve a estar ok -> se olvida el último nivel avisado
      (la próxima vez que empeore, avisa de inmediato, sin esperar).
    - Nivel empeora respecto al último avisado (o nunca se avisó) ->
      avisa de inmediato.
    - Nivel igual al último avisado -> solo repite tras
      REPETICION_ALERTA_SALUD_SEGUNDOS.
    - Nivel mejora pero sigue mal (crítico -> advertencia) -> se
      actualiza el registro en silencio, sin avisar "mejoró" (no
      pedido).
    Nunca lanza — cualquier error se loguea y retorna [] (degradación
    elegante, Principio 3: una alerta que falla no debe tumbar el REPL).
    Retorna [{"recurso": "ram"|"bateria"|"disco", "nivel": "advertencia"|"critico", "texto": str}, ...]
    """
    try:
        salud = evaluar_salud_sistema()
        ahora = time.time()
        alertas = []

        candidatos = (
            ("ram", _nivel_ram(salud["ram_disponible_pct"]),
             lambda n: _mensaje_ram(n, salud["ram_disponible_pct"])),
            ("bateria", _nivel_bateria(salud["bateria_pct"], salud["cargando"]),
             lambda n: _mensaje_bateria(n, salud["bateria_pct"])),
            ("disco", _nivel_disco(salud["disco_disponible_pct"]),
             lambda n: _mensaje_disco(n, salud["disco_disponible_pct"])),
        )

        for recurso, nivel_actual, generar_mensaje in candidatos:
            nivel_previo = _ULTIMO_NIVEL_ALERTADO.get(recurso)

            if nivel_actual is None:
                _ULTIMO_NIVEL_ALERTADO[recurso] = None
                continue

            empeoro = (
                nivel_previo is None
                or _ORDEN_NIVEL[nivel_actual] > _ORDEN_NIVEL.get(nivel_previo, 0)
            )
            igual_y_vencio_timer = (
                nivel_actual == nivel_previo
                and (ahora - _ULTIMA_ALERTA_TIMESTAMP.get(recurso, 0.0)) >= REPETICION_ALERTA_SALUD_SEGUNDOS
            )

            if empeoro or igual_y_vencio_timer:
                alertas.append({"recurso": recurso, "nivel": nivel_actual, "texto": generar_mensaje(nivel_actual)})
                _ULTIMO_NIVEL_ALERTADO[recurso] = nivel_actual
                _ULTIMA_ALERTA_TIMESTAMP[recurso] = ahora
            elif _ORDEN_NIVEL[nivel_actual] < _ORDEN_NIVEL.get(nivel_previo, 0):
                _ULTIMO_NIVEL_ALERTADO[recurso] = nivel_actual

        return alertas
    except Exception as e:
        _log_error("salud_sistema", "evaluar_alertas_proactivas", e)
        return []
# ── UTILITIES PARA DEBUGGING ──────────────────────────────────────────────

# DESPUÉS:
def debug_info() -> dict:
    """
    Exporta toda la información de salud para debugging. No usar en producción.
    """
    salud = evaluar_salud_sistema()
    return {
        "timestamp": datetime.now().isoformat(),
        "salud": salud,
        "cache_info": {
            "timestamp_ultimo_refresco": _CACHE["timestamp_ultimo_refresco"],
            "segundos_desde_refresco": time.time() - _CACHE["timestamp_ultimo_refresco"],
        }
    }
