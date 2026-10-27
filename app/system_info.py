# 📁 system_info.py — Dominio de información del sistema de SARA v2.0
#
# A diferencia de productivity.py, este dominio es de SOLO LECTURA —
# no escribe nada en la base de datos, solo consulta el sistema
# operativo y devuelve una respuesta. Por eso no tiene su propio
# crear_tablas(): no hay nada que persistir aquí.
#
# Cada función retorna None (nunca truena) si el dato no está
# disponible — por ejemplo, una laptop sin sensor de batería, o sin
# conexión a internet para la IP pública. Ausencia de dato != error,
# así que se distingue de un fallo real en los logs.

import os
import socket
from typing import Optional

try:
    import psutil
except Exception:
    psutil = None

try:
    import requests
except Exception:
    requests = None


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        import logger as _logger
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[SYSTEM_INFO ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


# ── CONSULTAS INDIVIDUALES ────────────────────────────────────────────────

def obtener_ip_local() -> Optional[str]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))  # no envía datos, solo fuerza la elección de interfaz
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception as e:
        _log_error("system_info", "obtener_ip_local", e)
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception as e2:
            _log_error("system_info", "obtener_ip_local (fallback gethostbyname)", e2)
            return None


def obtener_ip_publica(timeout: int = 5) -> Optional[str]:
    if requests is None:
        return None  # sin la libreria instalada, no es un error, es una capacidad ausente
    try:
        respuesta = requests.get("https://api.ipify.org", timeout=timeout)
        respuesta.raise_for_status()
        return respuesta.text.strip()
    except Exception as e:
        _log_error("system_info", "obtener_ip_publica", e)  # sin internet, timeout, etc.
        return None


def obtener_nombre_pc() -> Optional[str]:
    try:
        return socket.gethostname()
    except Exception as e:
        _log_error("system_info", "obtener_nombre_pc", e)
        return None


def obtener_bateria() -> Optional[dict]:
    """None si no hay sensor de batería (ej. equipo de escritorio) — no es un error."""
    if psutil is None:
        return None
    try:
        info = psutil.sensors_battery()
        if info is None:
            return None
        tiempo_restante = None
        segundos = info.secsleft
        if segundos not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN, None):
            horas, resto = divmod(int(segundos), 3600)
            minutos = resto // 60
            tiempo_restante = f"{horas}h {minutos}m"
        return {
            "porcentaje": round(info.percent, 1),
            "cargando": bool(info.power_plugged),
            "tiempo_restante": tiempo_restante,
        }
    except Exception as e:
        _log_error("system_info", "obtener_bateria", e)
        return None


def obtener_recursos() -> Optional[dict]:
    if psutil is None:
        return None
    try:
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory().percent
        disco = psutil.disk_usage(os.getcwd()).percent
        return {"cpu": round(cpu, 1), "ram": round(ram, 1), "disco": round(disco, 1)}
    except Exception as e:
        _log_error("system_info", "obtener_recursos", e)
        return None


# ── PUENTE CON EL MOTOR DE CLASIFICACIÓN ─────────────────────────────────

def responder_desde_clasificacion(resultado: dict) -> Optional[dict]:
    """
    Espera el dict completo de intent_engine.clasificar(). Retorna
    {"tipo": ..., "texto": ...} con la respuesta ya formateada, o None
    si la categoría no pertenece a este dominio.
    """
    categoria = resultado.get("categoria")

    if categoria == "INFO_IP":
        local = obtener_ip_local()
        publica = obtener_ip_publica()
        partes = []
        if local:
            partes.append(f"IP local: {local}")
        if publica:
            partes.append(f"IP pública: {publica}")
        texto = " | ".join(partes) if partes else "No se pudo obtener ninguna IP en este momento."
        return {"tipo": "info_ip", "texto": texto, "exito": True}

    if categoria == "INFO_BATERIA":
        info = obtener_bateria()
        if info is None:
            texto = "Este equipo no reporta batería (¿es de escritorio, o psutil no está instalado?)."
        else:
            estado = "cargando" if info["cargando"] else "sin cargar"
            tiempo = f", tiempo restante ~{info['tiempo_restante']}" if info["tiempo_restante"] else ""
            texto = f"Batería: {info['porcentaje']}% ({estado}){tiempo}"
        return {"tipo": "info_bateria", "texto": texto, "exito": True}

    if categoria == "INFO_RECURSOS":
        info = obtener_recursos()
        if info is None:
            texto = "No se pudieron obtener los recursos del sistema (¿psutil no está instalado?)."
        else:
            texto = f"CPU: {info['cpu']}% | RAM: {info['ram']}% | Disco: {info['disco']}%"
        return {"tipo": "info_recursos", "texto": texto, "exito": True}

    if categoria == "INFO_NOMBRE":
        nombre = obtener_nombre_pc()
        texto = f"El nombre de este equipo es {nombre}." if nombre else "No se pudo obtener el nombre del equipo."
        return {"tipo": "info_nombre", "texto": texto, "exito": True}

    return None
