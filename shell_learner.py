# -*- coding: utf-8 -*-
"""
shell_learner.py — Sistema de aprendizaje de intenciones shell para SARA.

Responsabilidad:
    Orquesta el ciclo completo de clasificación + aprendizaje de intenciones
    que corresponden a funciones de shell.py:

    1. Buscar en BD si ya aprendió esta intención (exacta o vectorial)
    2. Si no → preguntar a Qwen
    3. Ejecutar la función de shell.py correspondiente
    4. Guardar el resultado en BD con su vector semántico
    5. La próxima vez, el paso 1 lo resuelve sin Qwen

    Con el tiempo, Qwen se llama cada vez menos para las mismas categorías
    de peticiones porque los vectores acumulados aumentan la cobertura.

Integración:
    brain.py lo llama cuando:
        - intent_router clasificó CAT_SHELL_INFO
        - No hubo match en MAPA (substring) ni en mapa semántico
        - _despachar_shell_info_por_keywords() retornó None

Convenciones SARA:
    - Retorno estándar {"exito": bool, "mensaje": str, "tipo": str}
    - Try/except en toda operación — nunca bloquea el pipeline
    - Degradación elegante: si Qwen falla, retorna None para que
      brain.py continúe al flujo normal
"""

from __future__ import annotations
from typing import Optional

# Umbral de similitud vectorial para considerar un match aprendido
# 0.80 es más alto que el MAPA (0.65) porque las intenciones shell
# son más específicas y queremos evitar falsos positivos
UMBRAL_VECTORIAL_SHELL = 0.80

# Umbral mínimo de confianza de Qwen para aceptar su clasificación
UMBRAL_CONFIANZA_QWEN = 0.65


def _log(nivel: str, mensaje: str, detalle: str = "") -> None:
    try:
        import logger
        getattr(logger, nivel, logger.info)("shell_learner", mensaje, detalle) \
            if nivel in ("debug", "warning", "error") \
            else logger.info("shell_learner", mensaje)
    except Exception:
        pass


def _ejecutar_funcion_shell(funcion: str, argumento: str = "") -> Optional[dict]:
    """
    Ejecuta la función de shell.py correspondiente al nombre dado.
    Retorna el resultado o None si falla.
    """
    try:
        import shell as _shell
        DISPATCH = {
            "info_ram":                lambda: _shell.info_ram(),
            "info_cpu":                lambda: _shell.info_cpu(),
            "info_disco":              lambda: _shell.info_disco(),
            "info_ip":                 lambda: _shell.info_ip(),
            "info_procesos":           lambda: _shell.info_procesos(),
            "info_bateria":            lambda: _shell.info_bateria(),
            "info_gpu":                lambda: _shell.info_gpu(),
            "info_pantalla":           lambda: _shell.info_pantalla(),
            "info_temperatura":        lambda: _shell.info_temperatura(),
            "info_usb":                lambda: _shell.info_usb(),
            "info_servicios":          lambda: _shell.info_servicios(),
            "info_variables_entorno":  lambda: _shell.info_variables_entorno(argumento or ""),
            "info_red_extendida":      lambda: _shell.info_red_extendida(),
            "info_dns":                lambda: _shell.info_dns(),
            "info_conexiones_activas": lambda: _shell.info_conexiones_activas(),
            "info_tabla_rutas":        lambda: _shell.info_tabla_rutas(),
            "info_arp":                lambda: _shell.info_arp(),
            "info_estadisticas_red":   lambda: _shell.info_estadisticas_red(),
            "version_herramienta":     lambda: _shell.version_herramienta(argumento or "python"),
            "diagnostico_sistema":     lambda: _shell.diagnostico_sistema(),
            "ping_host":               lambda: _shell.ping_host(argumento or ""),
        }
        fn = DISPATCH.get(funcion)
        if fn:
            return fn()
        _log("warning", f"Función desconocida: '{funcion}'")
        return None
    except Exception as e:
        _log("error", f"Error ejecutando '{funcion}'", str(e))
        return None


def _guardar_aprendizaje(texto_limpio: str, funcion: str,
                          argumento: str, confianza: float,
                          fuente: str = "qwen") -> None:
    """
    Guarda la intención aprendida en BD con su vector semántico.
    El vector permite que futuras búsquedas similares la encuentren
    sin necesitar a Qwen.
    """
    try:
        import database as db
        import embeddings

        # Generar vector semántico del texto
        vector = None
        if embeddings.esta_disponible():
            vector = embeddings.generar_vector(texto_limpio)

        # Guardar en BD: texto → función (con argumento embebido si existe)
        funcion_completa = f"{funcion}:{argumento}" if argumento else funcion
        guardado = db.guardar_intencion_shell(
            texto_limpio=texto_limpio,
            funcion_shell=funcion_completa,
            vector=vector,
            confianza=confianza,
            fuente=fuente
        )
        if guardado:
            _log("info",
                 f"Aprendizaje guardado: '{texto_limpio[:40]}' → {funcion_completa} "
                 f"(conf={confianza:.2f}, fuente={fuente})")
    except Exception as e:
        _log("warning", "No se pudo guardar aprendizaje", str(e))


def _buscar_en_bd_exacto(texto_limpio: str) -> Optional[dict]:
    """
    Búsqueda exacta en la tabla de intenciones aprendidas.
    Más rápida que la vectorial — se intenta primero.
    """
    try:
        import database as db
        resultado = db.buscar_intencion_shell_exacta(texto_limpio)
        if resultado:
            _log("debug",
                 f"Match exacto BD: '{texto_limpio[:40]}' "
                 f"→ {resultado['funcion_shell']} "
                 f"(usado {resultado['veces_usada']}x)")
            return resultado
        return None
    except Exception:
        return None


def _buscar_en_bd_vectorial(texto_limpio: str) -> Optional[tuple[str, float]]:
    """
    Búsqueda por similitud vectorial en intenciones aprendidas.
    Se usa cuando la búsqueda exacta falla pero los vectores
    pueden encontrar una intención semánticamente equivalente.

    Retorna (funcion_shell, score) o None.
    """
    try:
        import database as db
        import embeddings

        if not embeddings.esta_disponible():
            return None

        vectores = db.obtener_intenciones_shell_vectores()
        if not vectores:
            return None

        # buscar_mas_similar retorna (identificador, score)
        # El identificador tiene formato "texto_limpio||funcion_shell"
        mejor_id, score = embeddings.buscar_mas_similar(texto_limpio, vectores)

        if score < UMBRAL_VECTORIAL_SHELL or not mejor_id:
            return None

        # Extraer la función del identificador compuesto
        partes = mejor_id.split("||", 1)
        if len(partes) != 2:
            return None

        texto_aprendido, funcion_shell = partes
        _log("debug",
             f"Match vectorial: '{texto_limpio[:40]}' "
             f"≈ '{texto_aprendido[:40]}' "
             f"→ {funcion_shell} (score={score:.3f})")

        # Incrementar uso del texto aprendido para mejorar su ranking
        _guardar_aprendizaje(
            texto_limpio=texto_limpio,
            funcion=funcion_shell.split(":")[0],
            argumento=funcion_shell.split(":")[1] if ":" in funcion_shell else "",
            confianza=score,
            fuente="vectorial"
        )

        return funcion_shell, score

    except Exception as e:
        _log("warning", "Error en búsqueda vectorial shell", str(e))
        return None


def resolver_intencion_shell(texto_limpio: str,
                              texto_original: str = "") -> Optional[dict]:
    """
    Punto de entrada principal del sistema de aprendizaje shell.

    Flujo:
        1. Búsqueda exacta en BD → respuesta instantánea
        2. Búsqueda vectorial en BD → respuesta rápida (~5ms)
        3. Clasificación por Qwen → respuesta lenta (~300-500ms)
           + guardar aprendizaje para que las próximas sean rápidas
        4. Si Qwen falla → retornar None (brain.py sigue su flujo)

    Args:
        texto_limpio:   Texto normalizado del usuario.
        texto_original: Texto original (para logs y contexto).

    Returns:
        dict resultado de shell.py con {"exito", "mensaje", "tipo"}
        None si no se pudo resolver
    """
    texto_ref = texto_original or texto_limpio

    # ── PASO 1: Búsqueda exacta en BD ─────────────────────────────────
    match_exacto = _buscar_en_bd_exacto(texto_limpio)
    if match_exacto:
        funcion_completa = match_exacto["funcion_shell"]
        funcion  = funcion_completa.split(":")[0]
        argumento = funcion_completa.split(":")[1] if ":" in funcion_completa else ""
        resultado = _ejecutar_funcion_shell(funcion, argumento)
        if resultado:
            # Actualizar contador de uso
            _guardar_aprendizaje(texto_limpio, funcion, argumento,
                                  match_exacto["confianza"], fuente="bd_exacto")
            return resultado

    # ── PASO 2: Búsqueda vectorial en BD ──────────────────────────────
    match_vectorial = _buscar_en_bd_vectorial(texto_limpio)
    if match_vectorial:
        funcion_completa, score = match_vectorial
        funcion   = funcion_completa.split(":")[0]
        argumento = funcion_completa.split(":")[1] if ":" in funcion_completa else ""
        resultado = _ejecutar_funcion_shell(funcion, argumento)
        if resultado:
            return resultado

    # ── PASO 3: Clasificación por Qwen ────────────────────────────────
    _log("info", f"Consultando Qwen para clasificar: '{texto_limpio[:50]}'")
    try:
        from external_service import clasificar_intencion_shell_qwen
        clasificacion = clasificar_intencion_shell_qwen(texto_limpio)
    except Exception as e:
        _log("warning", "external_service no disponible para clasificar shell", str(e))
        return None

    if not clasificacion:
        _log("debug", f"Qwen no clasificó: '{texto_limpio[:40]}'")
        return None

    funcion   = clasificacion["funcion"]
    argumento = clasificacion.get("argumento", "")
    confianza = clasificacion["confianza"]

    # Ejecutar la función clasificada por Qwen
    resultado = _ejecutar_funcion_shell(funcion, argumento)
    if not resultado:
        return None

    # ── PASO 4: Guardar aprendizaje ────────────────────────────────────
    # Solo guardar si el resultado fue exitoso
    if resultado.get("exito"):
        _guardar_aprendizaje(
            texto_limpio=texto_limpio,
            funcion=funcion,
            argumento=argumento,
            confianza=confianza,
            fuente="qwen"
        )
        stats = _obtener_stats()
        _log("info",
             f"Sistema aprendió: '{texto_limpio[:40]}' → {funcion} | "
             f"Total aprendido: {stats.get('total', '?')} intenciones "
             f"({stats.get('con_vector', '?')} vectorizadas)")

    return resultado


def _obtener_stats() -> dict:
    """Stats del sistema de aprendizaje para logging."""
    try:
        import database as db
        return db.obtener_stats_intenciones_shell()
    except Exception:
        return {}


def estado_aprendizaje() -> dict:
    """
    Retorna el estado del sistema de aprendizaje.
    Útil para que SARA responda '¿cuánto has aprendido?'
    o para el diagnóstico de sentinel.
    """
    try:
        import database as db
        stats = db.obtener_stats_intenciones_shell()
        return {
            "exito": True,
            "total_aprendido":    stats.get("total", 0),
            "con_vector":         stats.get("con_vector", 0),
            "usos_totales":       stats.get("usos_totales", 0),
            "mensaje": (
                f"He aprendido {stats.get('total', 0)} intenciones de sistema "
                f"({stats.get('con_vector', 0)} vectorizadas). "
                f"Total de usos: {stats.get('usos_totales', 0)}."
            )
        }
    except Exception as e:
        return {"exito": False, "mensaje": str(e)}