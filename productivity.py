# -*- coding: utf-8 -*-
"""
productivity.py — Módulo de productividad para SARA.
Gestión de tareas, recordatorios y notas desde lenguaje natural.

Integración en el pipeline:
    brain.py lo llama cuando intent_router clasifica:
        CAT_TAREA        → gestionar_tarea(texto)
        CAT_RECORDATORIO → gestionar_recordatorio(texto)
        CAT_NOTA         → gestionar_nota(texto)

sentinel.py lo llama cada ciclo para verificar recordatorios próximos.

Dependencias:
    - database.py  (tablas tareas, recordatorios, notas)
    - utils.py     (normalizar_texto)
    - io_manager.py (confirmaciones)
    - logger.py

Convenciones:
    - Retorno estándar {"exito": bool, "mensaje": str, "tipo": str}
    - Try/except en toda operación de BD
    - Nunca lanza excepciones hacia arriba
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────
# UTILIDADES INTERNAS
# ──────────────────────────────────────────────────────────────────────────

def _log(nivel: str, mensaje: str, detalle: str = "") -> None:
    try:
        import logger
        getattr(logger, nivel, logger.info)("productivity", mensaje, detalle) \
            if nivel in ("debug","warning","error") else logger.info("productivity", mensaje)
    except Exception:
        pass


def _resultado(exito: bool, mensaje: str, tipo: str = "productividad",
               extra: dict = None) -> dict:
    base = {"exito": exito, "mensaje": mensaje, "tipo": tipo}
    if extra:
        base.update(extra)
    return base


def _normalizar(texto: str) -> str:
    try:
        from utils import normalizar_texto
        return normalizar_texto(texto)
    except Exception:
        return texto.strip().lower()


def _confirmar(descripcion: str) -> bool:
    """Solicita confirmación usando io_manager. Degrada a input() si no está."""
    try:
        import io_manager
        resp = io_manager.obtener_input(f"¿Confirmas {descripcion}? (sí/no): ")
    except Exception:
        try:
            resp = input(f"SARA: ¿Confirmas {descripcion}? (sí/no): ")
        except Exception:
            return False
    return (resp or "").strip().lower() in {"si", "sí", "yes", "s", "y", "ok", "dale"}


# ──────────────────────────────────────────────────────────────────────────
# PARSEO DE FECHAS Y HORAS DESDE LENGUAJE NATURAL
# ──────────────────────────────────────────────────────────────────────────

def _parsear_fecha_hora(texto_norm: str) -> Optional[datetime]:
    """
    Extrae fecha y hora de lenguaje natural en español.
    Retorna datetime o None si no se puede parsear.

    Soporta:
        "a las 3", "a las 15:30", "a las 3 de la tarde"
        "en 30 minutos", "en 2 horas", "en 1 hora"
        "mañana a las 9", "pasado mañana a las 10"
        "el lunes a las 8", "el viernes a las 17"
        "hoy a las 6 pm"
    """
    ahora = datetime.now()
    resultado = None

    # ── en N minutos / en N horas ────────────────────────────────────
    m = re.search(r"en\s+(\d+)\s+(minutos?|horas?)", texto_norm)
    if m:
        cantidad = int(m.group(1))
        unidad   = m.group(2)
        if "hora" in unidad:
            resultado = ahora + timedelta(hours=cantidad)
        else:
            resultado = ahora + timedelta(minutes=cantidad)
        return resultado

    # ── Base de día ───────────────────────────────────────────────────
    dia_base = ahora.date()
    DIAS_SEMANA = {
        "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2,
        "jueves": 3, "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6,
    }

    if "manana" in texto_norm or "mañana" in texto_norm:
        dia_base = (ahora + timedelta(days=1)).date()
    elif "pasado manana" in texto_norm or "pasado mañana" in texto_norm:
        dia_base = (ahora + timedelta(days=2)).date()
    else:
        for nombre_dia, num_dia in DIAS_SEMANA.items():
            if nombre_dia in texto_norm:
                dias_hasta = (num_dia - ahora.weekday()) % 7
                if dias_hasta == 0:
                    dias_hasta = 7   # si es hoy, ir al siguiente
                dia_base = (ahora + timedelta(days=dias_hasta)).date()
                break

    # ── Hora ─────────────────────────────────────────────────────────
    hora = None
    minuto = 0

    # "a las HH:MM"
    m = re.search(r"a las\s+(\d{1,2}):(\d{2})", texto_norm)
    if m:
        hora   = int(m.group(1))
        minuto = int(m.group(2))
    else:
        # "a las H" con posible "de la tarde/mañana/noche"
        m = re.search(r"a las\s+(\d{1,2})", texto_norm)
        if m:
            hora = int(m.group(1))
            if "tarde" in texto_norm or "pm" in texto_norm:
                if hora < 12:
                    hora += 12
            elif "noche" in texto_norm:
                if hora < 12:
                    hora += 12
            elif "manana" in texto_norm or "mañana" in texto_norm or "am" in texto_norm:
                if hora == 12:
                    hora = 0

    if hora is not None:
        try:
            resultado = datetime.combine(dia_base, datetime.min.time().replace(
                hour=hora % 24, minute=minuto
            ))
        except Exception:
            pass

    return resultado


def _parsear_prioridad(texto_norm: str) -> int:
    """Extrae nivel de prioridad del texto. Retorna 1 (baja), 2 (media), 3 (alta)."""
    if any(p in texto_norm for p in ("urgente", "alta prioridad", "importante", "critica", "crítica")):
        return 3
    if any(p in texto_norm for p in ("media prioridad", "normal", "moderada")):
        return 2
    return 1


def _parsear_repeticion(texto_norm: str) -> str:
    """Detecta patrón de repetición para recordatorios."""
    try:
        from config import REPETICIONES_RECORDATORIO
    except Exception:
        REPETICIONES_RECORDATORIO = {
            "diario": "diario", "cada dia": "diario", "cada día": "diario",
            "semanal": "semanal", "cada semana": "semanal",
            "mensual": "mensual", "cada mes": "mensual",
        }
    for patron, valor in REPETICIONES_RECORDATORIO.items():
        if patron in texto_norm:
            return valor
    return "ninguna"


# ──────────────────────────────────────────────────────────────────────────
# GESTIÓN DE TAREAS
# ──────────────────────────────────────────────────────────────────────────

def gestionar_tarea(texto_usuario: str) -> dict:
    """
    Punto de entrada para gestión de tareas desde lenguaje natural.
    brain.py lo llama cuando intent_router devuelve CAT_TAREA.

    Ejemplos manejados:
        "añade una tarea revisar el servidor"
        "mis tareas pendientes"
        "completar tarea 2"
        "eliminar tarea 1"
        "tareas de alta prioridad"
        "cuántas tareas tengo"
    """
    texto_norm = _normalizar(texto_usuario)

    # ── COMPLETAR ─────────────────────────────────────────────────────
    for patron in ("completar tarea", "marcar tarea", "tarea completada",
                   "marcar como completada", "completar la tarea"):
        if patron in texto_norm:
            m = re.search(r"(\d+)", texto_norm)
            if m:
                return _completar_tarea_por_id(int(m.group(1)))
            # Buscar por título si no hay número
            titulo = texto_norm.split(patron, 1)[-1].strip()
            if titulo:
                return _completar_tarea_por_titulo(titulo)
            return _resultado(False,
                              "Especifica el número o nombre de la tarea a completar. "
                              "Ej: 'completar tarea 2' o 'completar tarea revisar servidor'.",
                              "tarea")

    # ── ELIMINAR ──────────────────────────────────────────────────────
    for patron in ("eliminar tarea", "borrar tarea", "elimina la tarea", "borra la tarea"):
        if patron in texto_norm:
            m = re.search(r"(\d+)", texto_norm)
            if m:
                return _eliminar_tarea_por_id(int(m.group(1)))
            titulo = texto_norm.split(patron, 1)[-1].strip()
            if titulo:
                return _eliminar_tarea_por_titulo(titulo)
            return _resultado(False, "Especifica el número de tarea a eliminar.", "tarea")

    # ── VER TAREAS ────────────────────────────────────────────────────
    for patron in ("mis tareas", "ver tareas", "lista de tareas",
                   "que tareas tengo", "qué tareas tengo",
                   "tareas pendientes", "cuantas tareas", "cuántas tareas"):
        if patron in texto_norm:
            estado = "todas" if "todas" in texto_norm else "pendiente"
            if "completada" in texto_norm:
                estado = "completada"
            return _listar_tareas(estado)

    # ── AÑADIR TAREA ─────────────────────────────────────────────────
    for patron in ("añade una tarea", "añade tarea", "agrega una tarea",
                   "agrega tarea", "nueva tarea", "crea una tarea",
                   "crear tarea", "añadir tarea"):
        if patron in texto_norm:
            titulo = texto_norm.split(patron, 1)[-1].strip()
            for art in ("para ", "de ", "sobre "):
                if titulo.startswith(art):
                    titulo = titulo[len(art):]
            if not titulo:
                return _resultado(False,
                                  "¿Cuál es el título de la tarea? "
                                  "Ej: 'añade una tarea revisar el servidor'.",
                                  "tarea")
            return _agregar_tarea(titulo, texto_norm)

    # Fallback: si el texto empieza con tarea asumir que quiere añadir
    return _resultado(False,
                      "No entendí qué quieres hacer con tus tareas. "
                      "Prueba: 'añade una tarea X', 'mis tareas', 'completar tarea 1'.",
                      "tarea")


def _agregar_tarea(titulo: str, texto_norm: str) -> dict:
    try:
        import database as db
        prioridad = _parsear_prioridad(texto_norm)
        fecha_venc = _parsear_fecha_hora(texto_norm)
        id_tarea = db.agregar_tarea(
            titulo=titulo.strip(),
            prioridad=prioridad,
            fecha_vencimiento=fecha_venc.isoformat() if fecha_venc else None
        )
        msg = f"✅ Tarea #{id_tarea} añadida: '{titulo}'"
        if fecha_venc:
            msg += f" — vence {fecha_venc.strftime('%d/%m/%Y %H:%M')}"
        if prioridad == 3:
            msg += " 🔴 (alta prioridad)"
        return _resultado(True, msg, "tarea", {"id": id_tarea})
    except Exception as e:
        _log("error", "agregar_tarea", str(e))
        return _resultado(False, f"No pude guardar la tarea: {e}", "tarea")


def _listar_tareas(estado: str = "pendiente") -> dict:
    try:
        import database as db
        tareas = db.obtener_tareas(estado=estado, limite=15)
        if not tareas:
            label = {"pendiente": "pendientes", "completada": "completadas",
                     "todas": ""}.get(estado, estado)
            return _resultado(True, f"No tienes tareas {label} registradas.", "tarea")

        PRIORIDAD_LABEL = {1: "", 2: "🟡", 3: "🔴"}
        lineas = []
        for t in tareas:
            t = dict(t)
            prio  = PRIORIDAD_LABEL.get(t.get("prioridad", 1), "")
            vence = ""
            if t.get("fecha_vencimiento"):
                try:
                    fv = datetime.fromisoformat(t["fecha_vencimiento"])
                    vence = f" — vence {fv.strftime('%d/%m %H:%M')}"
                except Exception:
                    pass
            lineas.append(f"  [{t['id']}] {prio} {t['titulo']}{vence}")

        resumen = (f"Tareas {estado} ({len(lineas)}):\n" + "\n".join(lineas))
        return _resultado(True, resumen, "tarea", {"total": len(lineas)})
    except Exception as e:
        return _resultado(False, f"No pude obtener las tareas: {e}", "tarea")


def _completar_tarea_por_id(id_tarea: int) -> dict:
    try:
        import database as db
        ok = db.completar_tarea(id_tarea)
        if ok:
            return _resultado(True, f"✅ Tarea #{id_tarea} marcada como completada.", "tarea")
        return _resultado(False, f"No encontré la tarea #{id_tarea}.", "tarea")
    except Exception as e:
        return _resultado(False, f"Error al completar tarea: {e}", "tarea")


def _completar_tarea_por_titulo(titulo: str) -> dict:
    try:
        import database as db
        tareas = db.buscar_tareas(titulo)
        if not tareas:
            return _resultado(False, f"No encontré ninguna tarea con '{titulo}'.", "tarea")
        t = dict(tareas[0])
        ok = db.completar_tarea(t["id"])
        if ok:
            return _resultado(True, f"✅ Tarea '{t['titulo']}' marcada como completada.", "tarea")
        return _resultado(False, "No pude completar la tarea.", "tarea")
    except Exception as e:
        return _resultado(False, f"Error: {e}", "tarea")


def _eliminar_tarea_por_id(id_tarea: int) -> dict:
    try:
        import database as db
        if not _confirmar(f"eliminar la tarea #{id_tarea}"):
            return _resultado(False, "Eliminación cancelada.", "tarea")
        ok = db.eliminar_tarea(id_tarea)
        if ok:
            return _resultado(True, f"🗑 Tarea #{id_tarea} eliminada.", "tarea")
        return _resultado(False, f"No encontré la tarea #{id_tarea}.", "tarea")
    except Exception as e:
        return _resultado(False, f"Error: {e}", "tarea")


def _eliminar_tarea_por_titulo(titulo: str) -> dict:
    try:
        import database as db
        tareas = db.buscar_tareas(titulo)
        if not tareas:
            return _resultado(False, f"No encontré ninguna tarea con '{titulo}'.", "tarea")
        t = dict(tareas[0])
        if not _confirmar(f"eliminar la tarea '{t['titulo']}'"):
            return _resultado(False, "Eliminación cancelada.", "tarea")
        db.eliminar_tarea(t["id"])
        return _resultado(True, f"🗑 Tarea '{t['titulo']}' eliminada.", "tarea")
    except Exception as e:
        return _resultado(False, f"Error: {e}", "tarea")


# ──────────────────────────────────────────────────────────────────────────
# GESTIÓN DE RECORDATORIOS
# ──────────────────────────────────────────────────────────────────────────

def gestionar_recordatorio(texto_usuario: str) -> dict:
    """
    Punto de entrada para gestión de recordatorios desde lenguaje natural.

    Ejemplos manejados:
        "recuérdame tomar agua a las 3 de la tarde"
        "pon un recordatorio para mañana a las 9"
        "avísame en 30 minutos"
        "mis recordatorios"
        "eliminar recordatorio 2"
        "recordatorio diario a las 8"
    """
    texto_norm = _normalizar(texto_usuario)

    # ── VER RECORDATORIOS ────────────────────────────────────────────
    for patron in ("mis recordatorios", "ver recordatorios",
                   "que recordatorios tengo", "qué recordatorios tengo",
                   "recordatorios pendientes"):
        if patron in texto_norm:
            return _listar_recordatorios()

    # ── ELIMINAR ──────────────────────────────────────────────────────
    for patron in ("eliminar recordatorio", "borrar recordatorio",
                   "elimina el recordatorio", "borra el recordatorio"):
        if patron in texto_norm:
            m = re.search(r"(\d+)", texto_norm)
            if m:
                return _eliminar_recordatorio(int(m.group(1)))
            return _resultado(False, "Especifica el número de recordatorio. "
                              "Ej: 'eliminar recordatorio 2'.", "recordatorio")

    # ── CREAR RECORDATORIO ────────────────────────────────────────────
    fecha_hora = _parsear_fecha_hora(texto_norm)
    if not fecha_hora:
        return _resultado(False,
                          "No entendí la hora del recordatorio. "
                          "Prueba: 'recuérdame X a las 3 de la tarde', "
                          "'avísame en 30 minutos', 'recordatorio mañana a las 9'.",
                          "recordatorio")

    # Extraer mensaje eliminando verbos y referencias de tiempo
    mensaje = texto_norm
    for patron in ("recuerdame", "recuérdame", "avisame", "avísame",
                   "pon un recordatorio", "crea un recordatorio",
                   "nuevo recordatorio", "recordatorio para"):
        mensaje = mensaje.replace(patron, "").strip()

    # Quitar la referencia de tiempo del mensaje
    mensaje = re.sub(
        r"(a las\s+\d{1,2}(:\d{2})?(\s+(de la\s+)?(tarde|manana|mañana|noche|am|pm))?|"
        r"en\s+\d+\s+(minutos?|horas?)|"
        r"manana|mañana|pasado manana|hoy|el (lunes|martes|miercoles|miércoles|"
        r"jueves|viernes|sabado|sábado|domingo))",
        "", mensaje
    ).strip()

    # Quitar artículos sobrantes al inicio
    for art in ("que ", "para ", "de ", "sobre "):
        if mensaje.startswith(art):
            mensaje = mensaje[len(art):]

    if not mensaje:
        mensaje = "Recordatorio"

    repeticion = _parsear_repeticion(texto_norm)

    return _crear_recordatorio(mensaje.strip(), fecha_hora, repeticion)


def _crear_recordatorio(mensaje: str, fecha_hora: datetime,
                        repeticion: str = "ninguna") -> dict:
    try:
        import database as db
        id_rec = db.agregar_recordatorio(
            mensaje=mensaje,
            fecha_hora=fecha_hora.isoformat(),
            repeticion=repeticion
        )
        rep_str = f" — {repeticion}" if repeticion != "ninguna" else ""
        msg = (f"⏰ Recordatorio #{id_rec} guardado:\n"
               f"  '{mensaje}'\n"
               f"  📅 {fecha_hora.strftime('%d/%m/%Y a las %H:%M')}{rep_str}")
        _log("info", f"Recordatorio creado: {mensaje} @ {fecha_hora}")
        return _resultado(True, msg, "recordatorio", {"id": id_rec})
    except Exception as e:
        _log("error", "crear_recordatorio", str(e))
        return _resultado(False, f"No pude guardar el recordatorio: {e}", "recordatorio")


def _listar_recordatorios() -> dict:
    try:
        import database as db
        recs = db.obtener_recordatorios_pendientes()
        if not recs:
            return _resultado(True, "No tienes recordatorios pendientes.", "recordatorio")
        lineas = []
        for r in recs:
            r = dict(r)
            try:
                fh = datetime.fromisoformat(r["fecha_hora"])
                fh_str = fh.strftime("%d/%m/%Y %H:%M")
            except Exception:
                fh_str = r.get("fecha_hora", "?")
            rep = f" ({r['repeticion']})" if r.get("repeticion", "ninguna") != "ninguna" else ""
            lineas.append(f"  [{r['id']}] ⏰ {r['mensaje']} — {fh_str}{rep}")
        return _resultado(True,
                          f"Recordatorios pendientes ({len(lineas)}):\n" + "\n".join(lineas),
                          "recordatorio", {"total": len(lineas)})
    except Exception as e:
        return _resultado(False, f"No pude obtener recordatorios: {e}", "recordatorio")


def _eliminar_recordatorio(id_rec: int) -> dict:
    try:
        import database as db
        if not _confirmar(f"eliminar el recordatorio #{id_rec}"):
            return _resultado(False, "Eliminación cancelada.", "recordatorio")
        ok = db.eliminar_recordatorio(id_rec)
        if ok:
            return _resultado(True, f"🗑 Recordatorio #{id_rec} eliminado.", "recordatorio")
        return _resultado(False, f"No encontré el recordatorio #{id_rec}.", "recordatorio")
    except Exception as e:
        return _resultado(False, f"Error: {e}", "recordatorio")


def verificar_recordatorios_pendientes() -> list[dict]:
    """
    Verifica recordatorios que deben dispararse ahora mismo.
    sentinel.py llama esta función en cada ciclo de vigilancia.
    Retorna lista de recordatorios disparados (para que sentinel los emita).
    """
    disparados = []
    try:
        import database as db
        proximos = db.obtener_recordatorios_proximos(horas=0)  # solo los ya vencidos
        ahora = datetime.now()
        for rec in proximos:
            rec = dict(rec)
            try:
                fh = datetime.fromisoformat(rec["fecha_hora"])
                if fh <= ahora:
                    db.marcar_recordatorio_disparado(rec["id"])
                    disparados.append(rec)
                    # Si es repetición, reprogramar
                    if rec.get("repeticion", "ninguna") != "ninguna":
                        _reprogramar_recordatorio(rec)
            except Exception:
                continue
    except Exception as e:
        _log("error", "verificar_recordatorios_pendientes", str(e))
    return disparados


def _reprogramar_recordatorio(rec: dict) -> None:
    """Crea una copia del recordatorio con la siguiente fecha según repetición."""
    try:
        import database as db
        fh = datetime.fromisoformat(rec["fecha_hora"])
        repeticion = rec.get("repeticion", "ninguna")
        DELTA = {"diario": timedelta(days=1), "semanal": timedelta(weeks=1),
                 "mensual": timedelta(days=30)}
        delta = DELTA.get(repeticion)
        if delta:
            nueva_fh = fh + delta
            db.agregar_recordatorio(
                mensaje=rec["mensaje"],
                fecha_hora=nueva_fh.isoformat(),
                repeticion=repeticion
            )
    except Exception as e:
        _log("error", "reprogramar_recordatorio", str(e))


# ──────────────────────────────────────────────────────────────────────────
# GESTIÓN DE NOTAS
# ──────────────────────────────────────────────────────────────────────────

def gestionar_nota(texto_usuario: str) -> dict:
    """
    Punto de entrada para gestión de notas desde lenguaje natural.

    Ejemplos manejados:
        "anota que el cliente pidió cambiar el logo"
        "toma nota de esto: reunión el martes"
        "mis notas"
        "busca en mis notas sobre el proyecto"
        "fijar nota 3"
        "borrar nota 1"
    """
    texto_norm = _normalizar(texto_usuario)

    # ── VER NOTAS ─────────────────────────────────────────────────────
    for patron in ("mis notas", "ver notas", "mostrar notas",
                   "todas mis notas", "notas fijadas"):
        if patron in texto_norm:
            solo_fijadas = "fijada" in texto_norm
            return _listar_notas(solo_fijadas=solo_fijadas)

    # ── BUSCAR EN NOTAS ───────────────────────────────────────────────
    for patron in ("busca en mis notas", "buscar en notas", "encuentra en mis notas",
                   "nota sobre", "notas sobre", "notas de"):
        if patron in texto_norm:
            query = texto_norm.split(patron, 1)[-1].strip()
            if query:
                return _buscar_notas(query)
            return _listar_notas()

    # ── FIJAR NOTA ────────────────────────────────────────────────────
    for patron in ("fijar nota", "fija la nota", "fijar la nota", "anclar nota"):
        if patron in texto_norm:
            m = re.search(r"(\d+)", texto_norm)
            if m:
                return _fijar_nota(int(m.group(1)), True)
            return _resultado(False, "Especifica el número de nota. Ej: 'fijar nota 2'.", "nota")

    # ── ELIMINAR NOTA ─────────────────────────────────────────────────
    for patron in ("borrar nota", "eliminar nota", "borra la nota", "elimina la nota"):
        if patron in texto_norm:
            m = re.search(r"(\d+)", texto_norm)
            if m:
                return _eliminar_nota(int(m.group(1)))
            return _resultado(False, "Especifica el número de nota a borrar.", "nota")

    # ── CREAR NOTA ────────────────────────────────────────────────────
    for patron in ("anota", "toma nota", "toma una nota", "nueva nota",
                   "crea una nota", "guarda esto", "guarda una nota",
                   "escribe una nota", "guarda esta nota"):
        if patron in texto_norm:
            contenido = texto_norm.split(patron, 1)[-1].strip()
            # Quitar "que", "esto:", "lo siguiente:" al inicio
            for intro in ("que ", "esto ", "lo siguiente ", ": "):
                if contenido.startswith(intro):
                    contenido = contenido[len(intro):]
            if not contenido:
                return _resultado(False,
                                  "¿Qué quieres anotar? "
                                  "Ej: 'anota que la reunión es el martes a las 10'.",
                                  "nota")
            return _crear_nota(contenido, texto_norm)

    return _resultado(False,
                      "No entendí la acción con notas. "
                      "Prueba: 'anota X', 'mis notas', 'busca en mis notas sobre Y'.",
                      "nota")


def _crear_nota(contenido: str, texto_norm: str = "") -> dict:
    try:
        import database as db
        # Intentar extraer título de la primera frase
        titulo = contenido.split(".")[0][:60] if "." in contenido else contenido[:60]
        fijada = "fija" in texto_norm or "importante" in texto_norm or "urgente" in texto_norm
        id_nota = db.agregar_nota(
            contenido=contenido.strip(),
            titulo=titulo.strip(),
            fijada=fijada
        )
        msg = f"📝 Nota #{id_nota} guardada"
        if fijada:
            msg += " 📌 (fijada)"
        msg += f":\n  '{contenido[:80]}{'...' if len(contenido) > 80 else ''}'"
        return _resultado(True, msg, "nota", {"id": id_nota})
    except Exception as e:
        _log("error", "crear_nota", str(e))
        return _resultado(False, f"No pude guardar la nota: {e}", "nota")


def _listar_notas(solo_fijadas: bool = False) -> dict:
    try:
        import database as db
        notas = db.obtener_notas(limite=10, solo_fijadas=solo_fijadas)
        if not notas:
            label = "fijadas " if solo_fijadas else ""
            return _resultado(True, f"No tienes notas {label}guardadas.", "nota")
        lineas = []
        for n in notas:
            n = dict(n)
            pin   = "📌 " if n.get("fijada") else ""
            titulo = n.get("titulo") or n.get("contenido", "")[:40]
            try:
                fe = datetime.fromisoformat(n["fecha_edicion"]).strftime("%d/%m %H:%M")
            except Exception:
                fe = ""
            lineas.append(f"  [{n['id']}] {pin}{titulo}  ({fe})")
        return _resultado(True,
                          f"Notas ({len(lineas)}):\n" + "\n".join(lineas),
                          "nota", {"total": len(lineas)})
    except Exception as e:
        return _resultado(False, f"No pude obtener las notas: {e}", "nota")


def _buscar_notas(query: str) -> dict:
    try:
        import database as db
        notas = db.buscar_notas(query)
        if not notas:
            return _resultado(True, f"No encontré notas sobre '{query}'.", "nota")
        lineas = []
        for n in notas:
            n = dict(n)
            pin = "📌 " if n.get("fijada") else ""
            preview = n.get("contenido", "")[:60]
            lineas.append(f"  [{n['id']}] {pin}{preview}...")
        return _resultado(True,
                          f"Notas sobre '{query}' ({len(lineas)}):\n" + "\n".join(lineas),
                          "nota")
    except Exception as e:
        return _resultado(False, f"Error buscando notas: {e}", "nota")


def _fijar_nota(id_nota: int, fijada: bool) -> dict:
    try:
        import database as db
        ok = db.actualizar_nota(id_nota, fijada=fijada)
        if ok:
            accion = "fijada 📌" if fijada else "desfijada"
            return _resultado(True, f"Nota #{id_nota} {accion}.", "nota")
        return _resultado(False, f"No encontré la nota #{id_nota}.", "nota")
    except Exception as e:
        return _resultado(False, f"Error: {e}", "nota")


def _eliminar_nota(id_nota: int) -> dict:
    try:
        import database as db
        if not _confirmar(f"eliminar la nota #{id_nota}"):
            return _resultado(False, "Eliminación cancelada.", "nota")
        ok = db.eliminar_nota(id_nota)
        if ok:
            return _resultado(True, f"🗑 Nota #{id_nota} eliminada.", "nota")
        return _resultado(False, f"No encontré la nota #{id_nota}.", "nota")
    except Exception as e:
        return _resultado(False, f"Error: {e}", "nota")