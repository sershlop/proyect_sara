# -*- coding: utf-8 -*-
"""
intent_learner.py — Aprendizaje de intenciones MULTI-DOMINIO para SARA.

Responsabilidad:
    Generalización de shell_learner.py (que solo cubre CAT_SHELL_INFO) a
    cualquier dominio/categoría: shell_accion, tarea, recordatorio, nota,
    y los que se agreguen en el futuro. Mismo ciclo de aprendizaje, pero
    AISLADO por categoría — la búsqueda vectorial de 'tarea' nunca compara
    contra vectores de 'shell_accion' ni de ningún otro dominio. Esto evita
    que SARA tenga que comparar contra "5 módulos en uno": cada dominio solo
    ve su propio espacio de aprendizaje.

    Ciclo (idéntico al de shell_learner.py, parametrizado por categoría):
        1. Buscar en BD si ya aprendió esta intención (exacta, dentro de
           la categoría dada)
        2. Si no → buscar por similitud vectorial (solo dentro de la
           misma categoría)
        3. Si no → preguntar a Qwen / usar el resolutor que le pases
        4. Guardar el resultado en BD con su vector semántico
        5. La próxima vez, el paso 1 o 2 lo resuelve sin tocar Qwen

Por qué existe separado de shell_learner.py:
    shell_learner.py queda intacto y sigue funcionando exactamente igual
    para CAT_SHELL_INFO (no se toca para no arriesgar lo que ya funciona).
    Este módulo es el camino para TODO lo demás, evitando duplicar la
    misma lógica una vez por cada dominio nuevo.

Salvaguarda anti-refuerzo-erróneo (bug observado: "batería" aprendiendo
mal como "RAM" por un falso positivo vectorial que luego se reforzaba a
sí mismo sin validación):
    - UMBRAL_AUTO_ACEPTAR (0.92): si el match vectorial supera esto, se usa
      y se refuerza sin preguntar — virtualmente seguro de ser correcto.
    - UMBRAL_VECTORIAL (0.80): igual que antes, es el piso para considerar
      un match válido, pero entre 0.80 y 0.92 el match se USA igual (no
      bloquea la respuesta), pero NO se refuerza (no incrementa veces_usada
      ni sube su confianza) hasta que se repita varias veces de forma
      consistente o el usuario lo corrija explícitamente.
    - Esto rompe el ciclo vicioso de "un acierto dudoso se refuerza a sí
      mismo hasta volverse permanente sin que nadie lo valide".

Integración en brain.py:
    Mismo patrón que shell_learner.resolver_intencion_shell(), pero con
    un parámetro extra `categoria` y un `resolver_fn` que el módulo
    llamante pasa (la función que sabe ejecutar esa categoría — ej. el
    dispatcher de keywords de CAT_SHELL_ACCION ya existente en brain.py,
    o el propio gestionar_tarea/gestionar_recordatorio/gestionar_nota de
    productivity.py).

Convenciones SARA:
    - Retorno estándar {"exito": bool, "mensaje": str, "tipo": str} o lo que
      retorne resolver_fn — este módulo no impone forma, solo orquesta.
    - Try/except en toda operación — nunca bloquea el pipeline.
    - Degradación elegante: si algo falla, retorna None para que el llamante
      siga su flujo normal (igual que shell_learner.py).
"""

from __future__ import annotations
from typing import Optional, Callable

# Umbral para considerar un match vectorial válido y usarlo en la respuesta.
UMBRAL_VECTORIAL = 0.80

# Umbral para refonzar el aprendizaje sin supervisión. Por debajo de esto
# (pero por encima de UMBRAL_VECTORIAL) el match se usa pero NO se refuerza
# — evita que un acierto dudoso se vuelva permanente sin validación.
UMBRAL_AUTO_ACEPTAR = 0.92


def _log(nivel: str, mensaje: str, detalle: str = "") -> None:
    try:
        import logger
        getattr(logger, nivel, logger.info)("intent_learner", mensaje, detalle) \
            if nivel in ("debug", "warning", "error") \
            else logger.info("intent_learner", mensaje)
    except Exception:
        pass


def _guardar_aprendizaje(categoria: str, texto_limpio: str, accion: str,
                          confianza: float, fuente: str = "qwen") -> None:
    """Guarda la intención aprendida en BD con su vector semántico, aislada por categoria."""
    try:
        import database as db
        import embeddings

        vector = None
        if embeddings.esta_disponible():
            vector = embeddings.generar_vector(texto_limpio)

        guardado = db.guardar_intencion_aprendida(
            categoria=categoria,
            texto_limpio=texto_limpio,
            accion=accion,
            vector=vector,
            confianza=confianza,
            fuente=fuente
        )
        if guardado:
            _log("info",
                 f"[{categoria}] Aprendizaje guardado: '{texto_limpio[:40]}' → {accion} "
                 f"(conf={confianza:.2f}, fuente={fuente})")
    except Exception as e:
        _log("warning", f"[{categoria}] No se pudo guardar aprendizaje", str(e))


def _buscar_en_bd_exacto(categoria: str, texto_limpio: str) -> Optional[dict]:
    """Búsqueda exacta dentro de la categoría dada. Más rápida — se intenta primero."""
    try:
        import database as db
        resultado = db.buscar_intencion_aprendida_exacta(categoria, texto_limpio)
        if resultado:
            _log("debug",
                 f"[{categoria}] Match exacto BD: '{texto_limpio[:40]}' "
                 f"→ {resultado['accion']} (usado {resultado['veces_usada']}x)")
            return resultado
        return None
    except Exception:
        return None


def _buscar_en_bd_vectorial(categoria: str, texto_limpio: str) -> Optional[tuple[str, float]]:
    """
    Búsqueda por similitud vectorial DENTRO de la categoría dada únicamente.
    Aislamiento real: jamás compara contra vectores de otros dominios, así
    que el costo de esta búsqueda no crece con el tamaño total del
    aprendizaje de SARA, solo con el de ESE dominio específico.

    Retorna (accion, score) o None.
    """
    try:
        import database as db
        import embeddings

        if not embeddings.esta_disponible():
            return None

        vectores = db.obtener_intenciones_vectores(categoria)
        if not vectores:
            return None

        mejor_id, score = embeddings.buscar_mas_similar(texto_limpio, vectores)

        if score < UMBRAL_VECTORIAL or not mejor_id:
            return None

        partes = mejor_id.split("||", 1)
        if len(partes) != 2:
            return None

        texto_aprendido, accion = partes
        _log("debug",
             f"[{categoria}] Match vectorial: '{texto_limpio[:40]}' "
             f"≈ '{texto_aprendido[:40]}' → {accion} (score={score:.3f})")

        # Solo reforzar el aprendizaje si el score es suficientemente alto
        # para considerarlo confiable. Entre UMBRAL_VECTORIAL y
        # UMBRAL_AUTO_ACEPTAR el match se devuelve pero NO se refuerza —
        # evita el ciclo vicioso de un acierto dudoso auto-confirmándose.
        if score >= UMBRAL_AUTO_ACEPTAR:
            _guardar_aprendizaje(categoria, texto_limpio, accion,
                                  confianza=score, fuente="vectorial")
        else:
            _log("debug",
                 f"[{categoria}] Match entre umbrales ({score:.3f}) — "
                 f"se usa pero no se refuerza aún")

        return accion, score

    except Exception as e:
        _log("warning", f"[{categoria}] Error en búsqueda vectorial", str(e))
        return None


def resolver_intencion(categoria: str, texto_limpio: str,
                        resolver_fn: Callable[[str], Optional[dict]],
                        texto_original: str = "") -> Optional[dict]:
    """
    Punto de entrada principal — generaliza shell_learner.resolver_intencion_shell()
    a cualquier categoría/dominio.

    Flujo:
        1. Búsqueda exacta en BD (dentro de la categoría) → respuesta instantánea
        2. Búsqueda vectorial en BD (dentro de la categoría) → respuesta rápida
        3. resolver_fn(texto_limpio) — la función real que sabe ejecutar esa
           categoría (puede ser un dispatcher de keywords, o una llamada a
           Qwen para clasificar y ejecutar). Si tiene éxito, se guarda el
           aprendizaje para la próxima vez.
        4. Si resolver_fn falla o no resuelve → retorna None (el llamante
           sigue su flujo normal, igual que con shell_learner.py)

    Args:
        categoria:      Nombre del dominio (ej. "shell_accion", "tarea",
                         "recordatorio", "nota"). Aísla el espacio de
                         aprendizaje — nunca se compara entre categorías.
        texto_limpio:    Texto normalizado del usuario.
        resolver_fn:     Función que recibe texto_limpio y retorna un dict
                         resultado (o None si no pudo resolver). Es quien
                         realmente ejecuta la acción la primera vez (antes
                         de que haya aprendizaje guardado).
        texto_original:  Texto original (para logs).

    Returns:
        dict resultado, o None si no se pudo resolver.
    """
    texto_ref = texto_original or texto_limpio

    # ── PASO 1: Búsqueda exacta en BD ─────────────────────────────────
    match_exacto = _buscar_en_bd_exacto(categoria, texto_limpio)
    if match_exacto:
        accion = match_exacto["accion"]
        resultado = resolver_fn(accion)
        if resultado:
            _guardar_aprendizaje(categoria, texto_limpio, accion,
                                  match_exacto["confianza"], fuente="bd_exacto")
            return resultado

    # ── PASO 2: Búsqueda vectorial en BD ──────────────────────────────
    match_vectorial = _buscar_en_bd_vectorial(categoria, texto_limpio)
    if match_vectorial:
        accion, score = match_vectorial
        resultado = resolver_fn(accion)
        if resultado:
            return resultado

    # ── PASO 3: Resolver con la función real (ej. Qwen, o keywords) ───
    _log("info", f"[{categoria}] Resolviendo sin aprendizaje previo: '{texto_limpio[:50]}'")
    try:
        resultado = resolver_fn(texto_limpio)
    except Exception as e:
        _log("warning", f"[{categoria}] resolver_fn falló", str(e))
        return None

    if not resultado:
        return None

    # ── PASO 4: Guardar aprendizaje (solo si fue exitoso) ─────────────
    if resultado.get("exito", True):
        accion_aprendida = resultado.get("accion_aprendida", texto_limpio)
        _guardar_aprendizaje(categoria, texto_limpio, accion_aprendida,
                              confianza=resultado.get("confianza", 0.75),
                              fuente="resolver_fn")

    return resultado


def corregir_aprendizaje(categoria: str, texto_limpio: str) -> bool:
    """
    Borra una intención aprendida que resultó ser un error (ej. el usuario
    corrigió a SARA porque 'batería' devolvía info de RAM). Rompe el ciclo
    de refuerzo de un match vectorial incorrecto.

    Usar desde el flujo de corrección de social.py / brain.py cuando el
    usuario indica que la respuesta fue equivocada.
    """
    try:
        import database as db
        eliminado = db.eliminar_intencion_aprendida(categoria, texto_limpio)
        if eliminado:
            _log("info", f"[{categoria}] Aprendizaje erróneo eliminado: '{texto_limpio[:40]}'")
        return eliminado
    except Exception as e:
        _log("warning", f"[{categoria}] No se pudo corregir aprendizaje", str(e))
        return False


def estado_aprendizaje(categoria: Optional[str] = None) -> dict:
    """
    Retorna el estado del sistema de aprendizaje. Si categoria es None,
    agrega todos los dominios; si se especifica, solo ese.
    Útil para que SARA responda '¿cuánto has aprendido?' o para sentinel.py.
    """
    try:
        import database as db
        stats = db.obtener_stats_intenciones_aprendidas(categoria)
        etiqueta = f" en '{categoria}'" if categoria else " en total"
        return {
            "exito": True,
            "categoria": categoria,
            "total_aprendido": stats.get("total", 0),
            "con_vector": stats.get("con_vector", 0),
            "usos_totales": stats.get("usos_totales", 0),
            "mensaje": (
                f"He aprendido {stats.get('total', 0)} intenciones{etiqueta} "
                f"({stats.get('con_vector', 0)} vectorizadas). "
                f"Total de usos: {stats.get('usos_totales', 0)}."
            )
        }
    except Exception as e:
        return {"exito": False, "mensaje": str(e)}
