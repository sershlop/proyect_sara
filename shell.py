# -*- coding: utf-8 -*-
"""
shell.py — Motor de ejecución controlada de comandos para SARA v0.4.0.
Subsistema PRAXIS — módulo: Las Manos.

Responsabilidad principal:
    Ejecutar comandos del sistema operativo (CMD/PowerShell) de forma
    segura, controlada y auditada. No es un sustituto de commands.py —
    es su backend de bajo nivel para comandos de shell puro, y el motor
    que permite a SARA percibir, actuar y verificar sobre la máquina.

Filosofía:
    commands.py abre apps, URLs y carpetas (acciones de usuario).
    shell.py ejecuta comandos de sistema, extrae información del entorno
    y realiza instalaciones controladas (acciones de sistema).
    Nunca se solapan: commands.py enruta a shell.py cuando la acción
    es un comando de shell, no lo reemplaza.

Arquitectura de control de riesgo (3 zonas):
    🟢 LISTA BLANCA  → ejecución inmediata, sin confirmación.
                       Comandos de solo lectura / info del sistema.
    🔴 LISTA NEGRA   → bloqueados siempre, sin excepción posible.
                       Comandos destructivos irreversibles.
    🟡 ZONA AMARILLA → requieren confirmación del usuario antes de ejecutar.
                       Todo lo que modifica estado del sistema.

    Las listas viven en config.py como frozensets (cero peso en RAM,
    cero disco adicional, solo texto). Este módulo las consume y aplica.

Capacidades principales:
    1. Extracción de información del sistema (RAM, CPU, disco, IP, etc.)
    2. Control de procesos (tasklist, taskkill con confirmación)
    3. Instalaciones controladas (pip, winget) con verificación posterior
    4. Reproducción multimedia (Spotify URI, archivos locales)
    5. Automatización de desarrollo (pytest, pip, git) con confirmación
    6. Ejecución de scripts generados por SARA con preview + confirmación

Integración en la arquitectura SARA:
    - brain.py lo llama cuando intent_router clasifica CAT_SHELL_INFO
      o CAT_SHELL_ACCION.
    - commands.py llama a ejecutar_shell() para tipo='shell' en lugar
      de _ejecutar_sistema() cuando la acción empieza por patrón CLI.
    - sara.py llama a reproducir() cuando el router clasifica CAT_REPRODUCIR.
    - sentinel.py usará las funciones de info para monitoreo proactivo.

Dependencias:
    - config.py   (SHELL_LISTA_BLANCA, SHELL_LISTA_NEGRA — nuevas constantes)
    - logger.py   (logging por niveles — import local para evitar ciclos)
    - perceptor.py (verificación posterior a instalaciones)
    - io_manager.py (confirmaciones en modo texto/voz — import diferido)

Convenciones respetadas (Documento Maestro SARA v0.3.0):
    - Formato de retorno estándar {"exito": bool, "mensaje": str, "tipo": str}.
    - Try/except obligatorio en toda operación de sistema.
    - SARA arranca siempre: si un submódulo falla, shell.py degrada con gracia.
    - Nunca exec() ni eval(). Nunca shell=True en comandos con input de usuario
      sin sanitizar. Los comandos de lista blanca/negra son strings literales.
    - Timeout defensivo en toda llamada subprocess: nunca bloquea el pipeline.
"""

from __future__ import annotations

import os
import subprocess
import sys
import shutil
import time
import json
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────
# LISTAS DE CONTROL DE RIESGO
# ──────────────────────────────────────────────────────────────────────────
# Definidas aquí como fallback. La versión canónica debe estar en config.py.
# Si config.py ya las tiene, se importan desde allí (ver _cargar_listas()).
#
# IMPORTANTE: las claves de la lista blanca son PREFIJOS de comandos, no
# comandos exactos. "wmic" cubre "wmic cpu get name", "wmic os get", etc.
# Esto permite flexibilidad sin abrir la lista a cualquier argumento.

_LISTA_BLANCA_DEFAULT: frozenset[str] = frozenset({
    # Información del sistema
    "systeminfo", "wmic", "winver",
    # Procesos y rendimiento
    "tasklist", "query process",
    # Red
    "ipconfig", "nslookup", "ping", "netstat",
    # Disco y archivos (solo lectura)
    "dir", "tree", "vol", "diskpart /s",
    # Versiones de herramientas de desarrollo
    "python --version", "python -V",
    "node --version", "node -v",
    "npm --version", "npm -v",
    "git --version",
    "pip --version", "pip -V",
    "pip show", "pip list",
    # Sistema de archivos (info)
    "where", "echo",
    # PowerShell info (modo lectura)
    "powershell",
    # Pantalla y hardware
    "get-displayresolution", "get-wmiobject win32_videocontroller",
    "wmic path win32_videocontroller",
    # Red extendida
    "test-connection", "get-netadapter", "get-dnsclientserveraddress",
    "route print", "arp -a",
    # Sistema de archivos (info extendida)
    "get-childitem", "get-item", "measure-object",
    # Entorno
    "get-childitem env:", "[system.environment]::getenvironmentvariable",
    # Servicios (solo lectura)
    "get-service",
    # GPU y temperatura (lectura)
    "get-wmiobject win32_temperatureprobe",
})

_LISTA_NEGRA_DEFAULT: frozenset[str] = frozenset({
    # Destrucción de datos
    "format", "del /f /s /q", "rd /s /q",
    "rmdir /s /q", "erase /f /s /q",
    # Registro del sistema (destructivo)
    "reg delete", "reg add hklm", "reg add hkcu\\system",
    # Usuarios y privilegios
    "net user /add", "net localgroup administrators",
    "net user administrator",
    # Apagado/reinicio sin confirmación
    "shutdown /f", "shutdown /r /f",
    # Desactivar seguridad
    "netsh firewall set", "netsh advfirewall set allprofiles state off",
    "sc delete", "sc stop",
    # Scripts arbitrarios sin revisión
    "cmd /c del", "cmd /c rd", "cmd /c format",
    # PowerShell destructivo
    "powershell -command remove-item -recurse",
    "powershell -encodedcommand",  # ofuscación — siempre bloquear
    "powershell -enc",
    # Descarga y ejecución remota
    "iex (", "invoke-expression", "invoke-webrequest",
    "certutil -decode", "bitsadmin /transfer",
})

# Prefijos que SIEMPRE requieren confirmación (zona amarilla)
# aunque no estén en lista negra.
_ZONA_AMARILLA_PREFIJOS: frozenset[str] = frozenset({
    "taskkill", "shutdown", "restart",
    "net stop", "net start", "net user",
    "reg add", "reg export", "reg import",
    "schtasks /create", "schtasks /delete", "schtasks /run",
    "pip install", "pip uninstall",
    "npm install", "npm uninstall",
    "winget install", "winget uninstall",
    "choco install", "choco uninstall",
    "git push", "git reset", "git clean",
    "powershell -command stop-process",
    "powershell -command remove-item",
    "powershell -command set-executionpolicy",
    # Archivos con efecto
    "copy", "move", "rename", "robocopy", "xcopy",
    "mkdir", "md",
    # Red con efecto
    "netsh advfirewall firewall add",
    "netsh advfirewall firewall delete",
    # Servicios con efecto
    "start-service", "stop-service", "restart-service",
    # Limpieza
    "remove-item", "clear-recycleBin",
})

# Timeout por defecto para comandos de información (ms).
# Comandos de acción pueden necesitar más — se pasa como parámetro.
# Extensiones multimedia reconocidas (audio + video)
_EXTENSIONES_AUDIO: frozenset[str] = frozenset({
    ".mp3", ".wav", ".flac", ".m4a", ".ogg", ".wma", ".aac", ".opus"
})
_EXTENSIONES_VIDEO: frozenset[str] = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"
})
_TIMEOUT_INFO_SEG: float = 10.0
_TIMEOUT_INSTALACION_SEG: float = 120.0


# ──────────────────────────────────────────────────────────────────────────
# UTILIDAD INTERNA DE LOGGING
# ──────────────────────────────────────────────────────────────────────────

def _log(nivel: str, mensaje: str, detalle: str = "") -> None:
    """Wrapper de logging seguro — mismo patrón que perceptor.py / commands.py."""
    try:
        import logger
        if nivel == "debug":
            logger.debug("shell", mensaje)
        elif nivel == "warning":
            logger.warning("shell", mensaje, detalle)
        elif nivel == "error":
            logger.error("shell", mensaje, detalle)
        else:
            logger.info("shell", mensaje)
    except Exception:
        pass


def _resultado(exito: bool, mensaje: str, tipo: str = "shell", extra: dict = None) -> dict:
    """
    Formato de retorno estándar de shell.py — compatible con el formato
    de commands.py: {"exito": bool, "mensaje": str, "tipo": str}.
    El campo "tipo" siempre es "shell" o una subcategoría de shell.
    """
    base = {"exito": exito, "mensaje": mensaje, "tipo": tipo}
    if extra:
        base.update(extra)
    return base


# ──────────────────────────────────────────────────────────────────────────
# CARGA DE LISTAS DESDE config.py
# ──────────────────────────────────────────────────────────────────────────

def _cargar_listas() -> tuple[frozenset, frozenset, frozenset]:
    """
    Intenta cargar las listas de control desde config.py.
    Si no existen (versión antigua de config), usa los defaults de este módulo.
    Esto garantiza retrocompatibilidad: shell.py funciona desde el día 1
    aunque config.py aún no tenga las constantes nuevas.
    """
    try:
        import config
        blanca   = getattr(config, "SHELL_LISTA_BLANCA",   _LISTA_BLANCA_DEFAULT)
        negra    = getattr(config, "SHELL_LISTA_NEGRA",    _LISTA_NEGRA_DEFAULT)
        amarilla = getattr(config, "SHELL_ZONA_AMARILLA",  _ZONA_AMARILLA_PREFIJOS)
        return frozenset(blanca), frozenset(negra), frozenset(amarilla)
    except Exception:
        return _LISTA_BLANCA_DEFAULT, _LISTA_NEGRA_DEFAULT, _ZONA_AMARILLA_PREFIJOS


LISTA_BLANCA, LISTA_NEGRA, ZONA_AMARILLA = _cargar_listas()


# ──────────────────────────────────────────────────────────────────────────
# CLASIFICACIÓN DE RIESGO
# ──────────────────────────────────────────────────────────────────────────

def clasificar_riesgo(comando: str) -> str:
    """
    Determina la zona de riesgo de un comando antes de ejecutarlo.

    Returns:
        "blanca"   — ejecutar sin confirmación
        "negra"    — bloquear siempre
        "amarilla" — solicitar confirmación al usuario
    """
    if not comando or not isinstance(comando, str):
        return "negra"  # defensivo: input inválido → bloquear

    cmd_norm = comando.strip().lower()

    # Lista negra — verificar primero (más restrictivo)
    for patron in LISTA_NEGRA:
        if cmd_norm.startswith(patron) or patron in cmd_norm:
            return "negra"

    # Lista blanca — prefijos permitidos
    for patron in LISTA_BLANCA:
        if cmd_norm.startswith(patron):
            return "blanca"

    # Zona amarilla — requieren confirmación
    for patron in ZONA_AMARILLA:
        if cmd_norm.startswith(patron):
            return "amarilla"

    # Por defecto: zona amarilla (principio de menor privilegio)
    return "amarilla"


# ──────────────────────────────────────────────────────────────────────────
# EJECUTOR PRINCIPAL
# ──────────────────────────────────────────────────────────────────────────

def _ejecutar_subprocess(
    comando: str | list[str],
    timeout: float = _TIMEOUT_INFO_SEG,
    encoding: str = "utf-8",
) -> dict:
    """
    Núcleo de ejecución: subprocess.run con captura de output completa.
    Nunca lanza excepciones — siempre retorna dict de resultado.

    Importante: shell=True se usa SOLO con comandos de tipo string validados por
    clasificar_riesgo(). Si comando es una lista, se ejecuta de forma segura
    con shell=False para evitar problemas de escape de caracteres en Windows.
    """
    # Si viene como lista estructurada, forzamos shell=False para máxima estabilidad
    usar_shell = True if isinstance(comando, str) else False
    log_cmd = comando if isinstance(comando, str) else " ".join(comando)

    try:
        resultado = subprocess.run(
            comando,
            shell=usar_shell,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding=encoding,
            errors="replace",   # evita UnicodeDecodeError en salidas mixtas
        )

        salida = resultado.stdout.strip() if resultado.stdout else ""
        error  = resultado.stderr.strip() if resultado.stderr else ""

        if resultado.returncode == 0:
            mensaje = salida or "Comando ejecutado correctamente."
            _log("info", f"Shell OK: {log_cmd[:60]}")
            return _resultado(True, mensaje, "shell", {"salida": salida, "error": ""})
        else:
            mensaje_error = error or salida or f"El comando terminó con código {resultado.returncode}."
            _log("warning", f"Shell returncode={resultado.returncode}: {log_cmd[:60]}", mensaje_error[:100])
            return _resultado(False, mensaje_error, "shell", {"salida": salida, "error": error})

    except subprocess.TimeoutExpired:
        _log("warning", f"Timeout en comando shell: {log_cmd[:60]}")
        return _resultado(False, f"El comando tardó más de {timeout}s y fue cancelado.", "shell_timeout")

    except FileNotFoundError:
        cmd_error = log_cmd.split()[0] if log_cmd else "desconocido"
        _log("error", f"Comando no encontrado: {log_cmd[:60]}")
        return _resultado(False, f"Comando no encontrado en el sistema: '{cmd_error}'.", "shell_error")

    except Exception as e:
        _log("error", "Error inesperado en ejecución shell", str(e))
        return _resultado(False, f"Error al ejecutar el comando: {e}", "shell_error")


def _solicitar_confirmacion(descripcion_accion: str, preview: Optional[str] = None) -> bool:
    """
    Solicita confirmación al usuario antes de ejecutar un comando de zona amarilla.
    Usa io_manager si está disponible (respeta el modo voz); degrada a input() si no.

    Args:
        descripcion_accion: Descripción legible de lo que se va a ejecutar.
        preview: Contenido extra a mostrar (ej. contenido de un script).

    Returns:
        bool — True si el usuario confirma, False si cancela.
    """
    try:
        import io_manager
        mensaje = f"Voy a ejecutar: {descripcion_accion}"
        if preview:
            mensaje += f"\n\nPreview:\n{preview[:300]}"
        mensaje += "\n¿Confirmas? (sí/no): "
        respuesta = io_manager.obtener_input(mensaje)
    except Exception:
        # Fallback a input() directo si io_manager no está disponible
        try:
            if preview:
                print(f"\nPreview:\n{preview[:300]}\n")
            respuesta = input(f"⚠ SARA: Voy a ejecutar: {descripcion_accion}\n¿Confirmas? (sí/no): ")
        except Exception:
            return False

    respuesta_norm = (respuesta or "").strip().lower()
    return respuesta_norm in {"si", "sí", "yes", "s", "y", "ok", "dale", "adelante"}


# ──────────────────────────────────────────────────────────────────────────
# API PÚBLICA — EJECUCIÓN CONTROLADA
# ──────────────────────────────────────────────────────────────────────────

def ejecutar_controlado(comando: str, contexto: str = "") -> dict:
    """
    Punto de entrada principal para ejecución de comandos shell.
    Aplica el flujo completo: clasificar → confirmar (si aplica) → ejecutar.

    Este es el método que brain.py y commands.py deben usar para
    cualquier comando que no sea apertura de app/URL.

    Args:
        comando:  Comando de shell a ejecutar.
        contexto: Descripción legible del contexto (para mensajes al usuario).

    Returns:
        Formato estándar {"exito": bool, "mensaje": str, "tipo": str}
        + "salida" y "error" when hay output relevante.
        + "bloqueado": True cuando el comando es de lista negra.
        + "cancelado": True cuando el usuario rechazó la confirmación.
    """
    if not comando or not isinstance(comando, str) or not comando.strip():
        return _resultado(False, "No se proporcionó un comando válido.", "shell_error")

    zona = clasificar_riesgo(comando)
    descripcion = contexto or comando[:80]

    # ─── LISTA NEGRA: bloqueo absoluto ────────────────────────────────
    if zona == "negra":
        _log("warning", f"Comando bloqueado (lista negra): {comando[:60]}")
        return _resultado(
            False,
            f"No puedo ejecutar ese comando — está clasificado como peligroso: '{comando[:60]}'",
            "shell_bloqueado",
            {"bloqueado": True},
        )

    # ─── ZONA AMARILLA: confirmación obligatoria ──────────────────────
    if zona == "amarilla":
        confirmado = _solicitar_confirmacion(descripcion)
        if not confirmado:
            _log("info", f"Comando cancelado por el usuario: {comando[:60]}")
            return _resultado(
                False,
                "Entendido, cancelado.",
                "shell_cancelado",
                {"cancelado": True},
            )

    # ─── LISTA BLANCA o confirmado: ejecutar ──────────────────────────
    _log("info", f"Ejecutando ({zona}): {comando[:80]}")
    return _ejecutar_subprocess(comando)


def ejecutar_script(
    ruta_script: str,
    interprete: str = "python",
    contexto: str = "",
) -> dict:
    """
    Ejecuta un script generado por SARA (en scripts/) con preview obligatorio
    y confirmación del usuario, independientemente de las listas.

    Los scripts siempre son zona amarilla: SARA los genera, pero el usuario
    los aprueba explícitamente antes de que corran.

    Args:
        ruta_script: Ruta al archivo de script (.py, .ps1, .bat).
        interprete:  Intérprete a usar ("python", "powershell", "cmd").
        contexto:    Descripción del propósito del script.

    Returns:
        Formato estándar + "salida" con el output del script.
    """
    if not ruta_script or not isinstance(ruta_script, str):
        return _resultado(False, "Ruta de script inválida.", "shell_error")

    try:
        import perceptor
        check = perceptor.existe_archivo(ruta_script)
        if not check["exito"]:
            return _resultado(False, f"No encontré el script: {ruta_script}", "shell_error")
    except Exception:
        if not os.path.isfile(ruta_script):
            return _resultado(False, f"No encontré el script: {ruta_script}", "shell_error")

    # Leer preview del contenido
    preview = ""
    try:
        with open(ruta_script, "r", encoding="utf-8", errors="replace") as f:
            preview = f.read(500)   # primeras 500 chars para el preview
    except Exception:
        preview = "(no se pudo leer el contenido del script)"

    descripcion = contexto or f"script: {os.path.basename(ruta_script)}"
    confirmado = _solicitar_confirmacion(descripcion, preview=preview)

    if not confirmado:
        return _resultado(False, "Script cancelado por el usuario.", "shell_cancelado", {"cancelado": True})

    comando = f'{interprete} "{ruta_script}"'
    _log("info", f"Ejecutando script: {ruta_script}")
    return _ejecutar_subprocess(comando, timeout=60.0)


# ──────────────────────────────────────────────────────────────────────────
# EXTRACCIÓN DE INFORMACIÓN DEL SISTEMA (Capa SHELL_INFO)
# ──────────────────────────────────────────────────────────────────────────
# Estas funciones son las que brain.py llama cuando intent_router clasifica
# una entrada como CAT_SHELL_INFO. Todas son lista blanca: ejecución inmediata.
# Cada una usa argumentos estructurados en listas para erradicar errores de sintaxis 255.

def info_ram() -> dict:
    """RAM disponible y total. Equivale a 'cuánta RAM tengo'."""
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "$mem = Get-CimInstance Win32_OperatingSystem; "
        "$total = [math]::Round($mem.TotalVisibleMemorySize/1MB,2); "
        "$libre = [math]::Round($mem.FreePhysicalMemory/1MB,2); "
        "$usado = [math]::Round(($mem.TotalVisibleMemorySize-$mem.FreePhysicalMemory)/1MB,2); "
        "Write-Output \"Total:$total GB|Libre:$libre GB|Usado:$usado GB\""
    ]
    resultado = _ejecutar_subprocess(cmd)
    if resultado["exito"]:
        try:
            partes = resultado["salida"].split("|")
            datos = {p.split(":")[0].strip(): p.split(":")[1].strip() for p in partes if ":" in p}
            resumen = (
                f"RAM total: {datos.get('Total','?')} | "
                f"Usada: {datos.get('Usado','?')} | "
                f"Libre: {datos.get('Libre','?')}"
            )
            return _resultado(True, resumen, "shell_info", {"datos_ram": datos})
        except Exception:
            return resultado  # retornar salida cruda si el parseo falla
    return resultado


def info_cpu() -> dict:
    """Nombre, núcleos y uso actual del CPU."""
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1; "
        "$uso = (Get-Counter '\\Processor(_Total)\\% Processor Time' -SampleInterval 1 -MaxSamples 1).CounterSamples.CookedValue; "
        "Write-Output \"$($cpu.Name)|Nucleos:$($cpu.NumberOfCores)|Uso:$([math]::Round($uso,1))%\""
    ]
    resultado = _ejecutar_subprocess(cmd, timeout=8.0)
    if resultado["exito"] and "|" in resultado.get("salida", ""):
        try:
            partes = resultado["salida"].split("|")
            nombre = partes[0].strip()
            extra = {p.split(":")[0].strip(): p.split(":")[1].strip() for p in partes[1:] if ":" in p}
            resumen = f"CPU: {nombre} | Núcleos: {extra.get('Nucleos','?')} | Uso actual: {extra.get('Uso','?')}"
            return _resultado(True, resumen, "shell_info", {"datos_cpu": extra, "nombre_cpu": nombre})
        except Exception:
            return resultado
    return resultado


def info_disco(unidad: str = "C:") -> dict:
    """Espacio libre y total en una unidad."""
    unidad_limpia = unidad.rstrip("\\").rstrip("/").upper()
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        f"$d = Get-CimInstance Win32_LogicalDisk -Filter 'DeviceID=\"{unidad_limpia}\"'; "
        f"$libre = [math]::Round($d.FreeSpace/1GB,2); "
        f"$total = [math]::Round($d.Size/1GB,2); "
        f"$usado = [math]::Round(($d.Size-$d.FreeSpace)/1GB,2); "
        f"$pct = [math]::Round($d.FreeSpace/$d.Size*100,1); "
        f"Write-Output \"Libre:$libre GB|Total:$total GB|Usado:$usado GB|Porcentaje libre:$pct%\""
    ]
    resultado = _ejecutar_subprocess(cmd)
    if resultado["exito"]:
        try:
            datos = {p.split(":")[0].strip(): p.split(":")[1].strip()
                     for p in resultado["salida"].split("|") if ":" in p}
            resumen = (
                f"Disco {unidad_limpia}: "
                f"{datos.get('Libre','?')} libres de {datos.get('Total','?')} "
                f"({datos.get('Porcentaje libre','?')} disponible)"
            )
            alerta = float(datos.get("Porcentaje libre","100%").replace("%","") or 100) < 10
            return _resultado(True, resumen + (" ⚠ Espacio crítico." if alerta else ""),
                              "shell_info", {"datos_disco": datos, "alerta_disco": alerta})
        except Exception:
            return resultado
    return resultado


def info_ip() -> dict:
    """Dirección IP local y nombre del equipo."""
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "$ip = (Get-NetIPAddress -AddressFamily IPv4 | "
        "Where-Object {$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.*'} | "
        "Select-Object -First 1).IPAddress; "
        "$hostName = [System.Environment]::MachineName; "
        "Write-Output \"IP:$ip|Equipo:$hostName\""
    ]
    resultado = _ejecutar_subprocess(cmd)
    if resultado["exito"]:
        try:
            datos = {p.split(":")[0].strip(): p.split(":")[1].strip()
                     for p in resultado["salida"].split("|") if ":" in p}
            resumen = f"IP local: {datos.get('IP','?')} | Equipo: {datos.get('Equipo','?')}"
            return _resultado(True, resumen, "shell_info", {"datos_red": datos})
        except Exception:
            return resultado
    return resultado
def info_procesos(limite: int = 10) -> dict:
    """Lista los procesos que más CPU/RAM consumen en este momento."""
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        f"Get-Process | Sort-Object CPU -Descending | Select-Object -First {limite} | "
        f"Format-Table Name,CPU,WorkingSet -AutoSize | Out-String"
    ]
    resultado = _ejecutar_subprocess(cmd)
    if resultado["exito"]:
        salida_limpia = resultado["salida"].strip()
        return _resultado(True, f"Procesos activos (top {limite} por CPU):\n{salida_limpia}",
                          "shell_info", {"salida_procesos": salida_limpia})
    return resultado


def info_bateria() -> dict:
    """Estado de la batería (laptops). Retorna aviso si no hay batería."""
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance -ClassName Win32_Battery | Select-Object EstimatedChargeRemaining, BatteryStatus | ConvertTo-Json"
    ]
    resultado = _ejecutar_subprocess(cmd)
    if resultado["exito"]:
        salida = resultado["salida"].strip()
        if not salida:
            return _resultado(True, "Este equipo no tiene batería (escritorio o sin batería detectada).",
                              "shell_info")
        try:
            datos = json.loads(salida)
            if isinstance(datos, list):
                datos = datos[0]
                
            carga = datos.get("EstimatedChargeRemaining", 100)
            status = datos.get("BatteryStatus", 2)
            
            # 2 significa 'Cargando' o 'Conectado totalmente cargado' en CIM_Battery
            estado_str = "Cargando" if status == 2 else "Descargando"
            alerta = carga < 20 and status != 2

            resumen = f"Batería: {carga}% — {estado_str}" + (" ⚠ Batería baja." if alerta else "")
            return _resultado(True, resumen, "shell_info",
                              {"datos_bateria": {"Carga": str(carga), "Estado": estado_str}, "alerta_bateria": alerta})
        except Exception:
            return resultado
    return resultado

# ──────────────────────────────────────────────────────────────────────────
# CONTROL AVANZADO DEL SISTEMA (pantalla, red, GPU, entorno, servicios)
# ──────────────────────────────────────────────────────────────────────────

def info_pantalla() -> dict:
    """
    Resolución actual y configuración de monitores conectados.
    Lista blanca — sin confirmación.
    Útil para: 'qué resolución tengo', 'cuántos monitores hay'.
    """
    cmd = [
        "powershell", "-NoProfile", "-Command",
        "Get-CimInstance -ClassName Win32_VideoController | "
        "Select-Object Name, CurrentHorizontalResolution, CurrentVerticalResolution, "
        "CurrentRefreshRate, AdapterRAM | "
        "ForEach-Object { "
        "  $vram = if ($_.AdapterRAM) { [math]::Round($_.AdapterRAM/1MB) } else { '?' }; "
        "  \"$($_.Name)|$($_.CurrentHorizontalResolution)x$($_.CurrentVerticalResolution)"
        "@$($_.CurrentRefreshRate)Hz|VRAM:${vram}MB\" "
        "} | Out-String"
    ]
    resultado = _ejecutar_subprocess(cmd)
    if resultado["exito"]:
        lineas = [l.strip() for l in resultado["salida"].split("\n") if l.strip()]
        monitores = []
        for i, linea in enumerate(lineas, 1):
            partes = linea.split("|")
            if len(partes) >= 2:
                monitores.append(f"Monitor {i}: {partes[0].strip()} — {partes[1].strip()}")
                if len(partes) >= 3:
                    monitores[-1] += f" ({partes[2].strip()})"
        if monitores:
            return _resultado(True, "\n".join(monitores), "shell_info",
                              {"monitores": monitores, "cantidad": len(monitores)})
    return resultado


def info_gpu() -> dict:
    """
    Información detallada de la GPU: nombre, VRAM, driver, estado.
    Lista blanca — sin confirmación.
    Útil para: 'qué tarjeta gráfica tengo', 'cuánta VRAM tengo'.
    NOTA: temperatura requiere OpenHardwareMonitor externo (ver info_temperatura).
    """
    cmd = [
        "powershell", "-NoProfile", "-Command",
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name, AdapterRAM, DriverVersion, VideoProcessor, Status | "
        "ForEach-Object { "
        "  $vram = if ($_.AdapterRAM) { [math]::Round($_.AdapterRAM/1GB, 2) } else { '?' }; "
        "  Write-Output \"GPU:$($_.Name)|VRAM:${vram}GB|Driver:$($_.DriverVersion)|Estado:$($_.Status)\" "
        "}"
    ]
    resultado = _ejecutar_subprocess(cmd)
    if resultado["exito"]:
        try:
            lineas = [l.strip() for l in resultado["salida"].split("\n") if "GPU:" in l]
            gpus = []
            for linea in lineas:
                datos = {p.split(":")[0]: ":".join(p.split(":")[1:])
                         for p in linea.split("|") if ":" in p}
                resumen = (f"GPU: {datos.get('GPU','?')} | "
                           f"VRAM: {datos.get('VRAM','?')} | "
                           f"Driver: {datos.get('Driver','?')}")
                gpus.append(resumen)
            if gpus:
                return _resultado(True, "\n".join(gpus), "shell_info", {"gpus": gpus})
        except Exception:
            return resultado
    return resultado


def info_temperatura() -> dict:
    """
    Intenta obtener temperatura del CPU/GPU.
    Usa WMI MSAcpi_ThermalZoneTemperature si está disponible.
    En la mayoría de equipos requiere permisos de admin o HWiNFO/OHM.
    Degrada con aviso si no hay datos disponibles — nunca lanza excepción.
    Útil para: 'a qué temperatura está el CPU', 'está caliente la PC'.
    """
    cmd = [
        "powershell", "-NoProfile", "-Command",
        "try { "
        "  $zonas = Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi -EA Stop; "
        "  $zonas | ForEach-Object { "
        "    $temp = [math]::Round($_.CurrentTemperature/10 - 273.15, 1); "
        "    Write-Output \"Zona:$($_.InstanceName)|Temp:${temp}°C\" "
        "  } "
        "} catch { Write-Output 'NO_DATA' }"
    ]
    resultado = _ejecutar_subprocess(cmd)
    if resultado["exito"]:
        salida = resultado["salida"].strip()
        if "NO_DATA" in salida or not salida:
            return _resultado(
                False,
                "No pude leer la temperatura directamente. "
                "Para datos de temperatura precisos instala HWiNFO64 o OpenHardwareMonitor.",
                "shell_info"
            )
        lineas = [l.strip() for l in salida.split("\n") if "Temp:" in l]
        if lineas:
            temps = []
            for l in lineas:
                partes = {p.split(":")[0]: p.split(":")[1] for p in l.split("|") if ":" in p}
                zona_raw  = partes.get("Zona", "?")
                temp_val  = partes.get("Temp", "?")
                # Simplificar nombre técnico: ACPI\ThermalZone\TZ01_0 → Zona Térmica 1
                zona_limpia = zona_raw
                try:
                    import re as _re
                    m = _re.search(r'TZ(\w+)_?(\d*)', zona_raw, _re.IGNORECASE)
                    if m:
                        zona_limpia = f"Zona Térmica {m.group(1)}"
                except Exception:
                    pass
                # Indicador de estado según temperatura
                try:
                    temp_num = float(temp_val.replace("°C","").strip())
                    if temp_num < 50:
                        estado = "✅ Normal"
                    elif temp_num < 70:
                        estado = "⚠️ Elevada"
                    elif temp_num < 85:
                        estado = "🔴 Alta"
                    else:
                        estado = "🚨 Crítica"
                except Exception:
                    estado = ""
                temps.append(f"  🌡 {zona_limpia}: {temp_val}°C — {estado}")
            return _resultado(True,
                              "Temperatura del sistema:\n" + "\n".join(temps),
                              "shell_info", {"temperaturas": temps})
    return resultado


def info_red_extendida() -> dict:
    """
    Estado de adaptadores de red: velocidad, tipo, MAC, estado.
    Lista blanca — sin confirmación.
    Útil para: 'cómo está mi red', 'velocidad de conexión', 'mi MAC address'.
    """
    cmd = [
        "powershell", "-NoProfile", "-Command",
        "Get-NetAdapter | Where-Object Status -eq 'Up' | "
        "Select-Object Name, InterfaceDescription, LinkSpeed, MacAddress, Status | "
        "ForEach-Object { "
        "  Write-Output \"$($_.Name)|$($_.InterfaceDescription)|"
        "Velocidad:$($_.LinkSpeed)|MAC:$($_.MacAddress)\" "
        "} | Out-String"
    ]
    resultado = _ejecutar_subprocess(cmd)
    if resultado["exito"]:
        lineas = [l.strip() for l in resultado["salida"].split("\n") if "|" in l]
        adaptadores = []
        for linea in lineas:
            partes = linea.split("|")
            if len(partes) >= 3:
                adaptadores.append(
                    f"🌐 {partes[0].strip()} ({partes[1].strip()}) — {partes[2].strip()}"
                )
        if adaptadores:
            return _resultado(True, "\n".join(adaptadores), "shell_info",
                              {"adaptadores": adaptadores})
    return resultado


def info_conexiones_activas(limite: int = 15) -> dict:
    """
    Conexiones TCP activas con proceso asociado.
    Lista blanca — sin confirmación.
    Útil para: 'qué conexiones tengo abiertas', 'qué apps usan internet'.
    """
    cmd = [
        "powershell", "-NoProfile", "-Command",
        f"Get-NetTCPConnection -State Established | "
        f"Select-Object -First {limite} LocalAddress, LocalPort, RemoteAddress, "
        f"RemotePort, OwningProcess | "
        f"ForEach-Object {{ "
        f"  $proc = (Get-Process -Id $_.OwningProcess -EA SilentlyContinue).Name; "
        f"  Write-Output \"$proc|$($_.LocalAddress):$($_.LocalPort) → "
        f"$($_.RemoteAddress):$($_.RemotePort)\" "
        f"}} | Out-String"
    ]
    resultado = _ejecutar_subprocess(cmd, timeout=12.0)
    if resultado["exito"]:
        return _resultado(True,
                          f"Conexiones TCP activas (top {limite}):\n{resultado['salida'].strip()}",
                          "shell_info")
    return resultado


def info_servicios(filtro: str = "") -> dict:
    """
    Lista servicios de Windows con su estado.
    Lista blanca — sin confirmación.
    Útil para: 'qué servicios están corriendo', 'está activo el servicio X'.

    Args:
        filtro: Nombre parcial para filtrar (ej. 'sql', 'audio'). Vacío = todos en Running.
    """
    if filtro:
        where = f"Where-Object {{ $_.DisplayName -like '*{filtro}*' -or $_.Name -like '*{filtro}*' }}"
    else:
        where = "Where-Object Status -eq 'Running'"

    cmd = [
        "powershell", "-NoProfile", "-Command",
        f"Get-Service | {where} | "
        "Select-Object DisplayName, Name, Status | "
        "Sort-Object DisplayName | "
        "Format-Table -AutoSize | Out-String"
    ]
    resultado = _ejecutar_subprocess(cmd, timeout=12.0)
    if resultado["exito"]:
        label = f"(filtro: '{filtro}')" if filtro else "(activos)"
        return _resultado(True,
                          f"Servicios de Windows {label}:\n{resultado['salida'].strip()}",
                          "shell_info")
    return resultado


def info_variables_entorno(nombre: str = "") -> dict:
    """
    Consulta variables de entorno del sistema.
    Lista blanca — sin confirmación.
    Útil para: 'cuál es mi PATH', 'qué vale la variable X'.

    Args:
        nombre: Nombre de la variable (ej. 'PATH', 'JAVA_HOME'). Vacío = las más útiles.
    """
    if nombre:
        cmd = [
            "powershell", "-NoProfile", "-Command",
            f"$val = [System.Environment]::GetEnvironmentVariable('{nombre}', 'Machine'); "
            f"$valU = [System.Environment]::GetEnvironmentVariable('{nombre}', 'User'); "
            f"Write-Output \"Sistema: $val\"; Write-Output \"Usuario: $valU\""
        ]
    else:
        # Variables útiles predefinidas si no se especifica ninguna
        cmd = [
            "powershell", "-NoProfile", "-Command",
            "('COMPUTERNAME','USERNAME','OS','PROCESSOR_ARCHITECTURE',"
            "'NUMBER_OF_PROCESSORS','TEMP','APPDATA','USERPROFILE') | "
            "ForEach-Object { \"$_=$([System.Environment]::GetEnvironmentVariable($_))\" } | "
            "Out-String"
        ]
    resultado = _ejecutar_subprocess(cmd)
    if resultado["exito"]:
        label = f"Variable '{nombre}'" if nombre else "Variables de entorno del sistema"
        return _resultado(True, f"{label}:\n{resultado['salida'].strip()}", "shell_info")
    return resultado


def limpiar_temporales(confirmar: bool = True) -> dict:
    """
    Limpia archivos temporales del usuario (%TEMP%).
    Zona amarilla — solicita confirmación por defecto.
    Nunca toca archivos de sistema ni otras carpetas.
    Útil para: 'limpia los temporales', 'borra los archivos temp'.

    Args:
        confirmar: Si False, omite la confirmación (para uso interno de sentinel).
    """
    ruta_temp = os.environ.get("TEMP", os.path.join(os.environ.get("USERPROFILE", "C:\\"), "AppData", "Local", "Temp"))

    if confirmar:
        confirmado = _solicitar_confirmacion(
            f"limpiar archivos temporales en {ruta_temp}",
        )
        if not confirmado:
            return _resultado(False, "Limpieza cancelada.", "shell_cancelado", {"cancelado": True})

    cmd = [
        "powershell", "-NoProfile", "-Command",
        f"$temp = '{ruta_temp}'; "
        "$antes = (Get-ChildItem $temp -Recurse -Force -EA SilentlyContinue | "
        "Measure-Object -Property Length -Sum).Sum; "
        "Get-ChildItem $temp -Force -EA SilentlyContinue | "
        "Remove-Item -Recurse -Force -EA SilentlyContinue; "
        "$despues = (Get-ChildItem $temp -Recurse -Force -EA SilentlyContinue | "
        "Measure-Object -Property Length -Sum).Sum; "
        "$liberado = [math]::Round(($antes - $despues)/1MB, 2); "
        "Write-Output \"Liberado: ${liberado} MB\""
    ]
    resultado = _ejecutar_subprocess(cmd, timeout=30.0)
    if resultado["exito"]:
        return _resultado(True,
                          f"✅ Temporales limpiados. {resultado['salida'].strip()}",
                          "shell_accion")
    return resultado
def info_usb() -> dict:
    """
    Dispositivos USB conectados actualmente.
    Usa Win32_PnPEntity (WMI) en vez de Get-PnpDevice — más rápido y
    sin timeout. Detecta unidades de almacenamiento, teléfonos (WPD)
    y hubs USB. Retorna mensaje claro si no hay nada conectado.
    """
    cmd = [
        "powershell", "-NoProfile", "-Command",
        # Dispositivos USB genéricos y almacenamiento extraíble
        "$usb = Get-WmiObject Win32_PnPEntity -EA SilentlyContinue | "
        "Where-Object { ($_.PNPClass -eq 'USB' -or $_.PNPClass -eq 'WPD' "
        "-or $_.PNPClass -eq 'DiskDrive') -and $_.Status -eq 'OK' } | "
        "Select-Object Name; "
        # Unidades extraíbles (DriveType=2)
        "$drives = Get-WmiObject Win32_LogicalDisk -EA SilentlyContinue | "
        "Where-Object DriveType -eq 2 | "
        "Select-Object DeviceID, VolumeName; "
        # Combinar y emitir
        "$total = @($usb).Count; "
        "if ($total -eq 0 -and @($drives).Count -eq 0) { "
        "  Write-Output 'NINGUNO' "
        "} else { "
        "  $usb | ForEach-Object { Write-Output \"USB: $($_.Name)\" }; "
        "  $drives | ForEach-Object { "
        "    $vol = if ($_.VolumeName) { $_.VolumeName } else { 'Sin etiqueta' }; "
        "    Write-Output \"UNIDAD: $($_.DeviceID) ($vol)\" "
        "  } "
        "}"
    ]
    resultado = _ejecutar_subprocess(cmd, timeout=10.0)

    if not resultado["exito"]:
        return _resultado(False,
                          "No pude consultar los dispositivos USB.",
                          "shell_info")

    salida = resultado["salida"].strip()

    if not salida or salida == "NINGUNO":
        return _resultado(True,
                          "No tienes ningún dispositivo USB conectado en este momento.",
                          "shell_info")

    lineas = [l.strip() for l in salida.split("\n") if l.strip()]
    dispositivos = []
    unidades     = []

    for linea in lineas:
        if linea.startswith("UNIDAD:"):
            unidades.append(f"  💾 {linea.replace('UNIDAD:', '').strip()}")
        elif linea.startswith("USB:"):
            nombre = linea.replace("USB:", "").strip()
            # Filtrar entradas genéricas de Windows que no son hardware real del usuario
            IGNORAR = {
                "usb root hub", "usb composite device", "generic usb hub",
                "usb hub", "intel usb", "amd usb", "xhci", "ehci"
            }
            if not any(ig in nombre.lower() for ig in IGNORAR):
                dispositivos.append(f"  🔌 {nombre}")

    if not dispositivos and not unidades:
        return _resultado(True,
                          "No tienes ningún dispositivo USB externo conectado "
                          "(solo controladores internos del sistema).",
                          "shell_info")

    partes = []
    if dispositivos:
        partes.append(f"Dispositivos USB ({len(dispositivos)}):\n" +
                      "\n".join(dispositivos))
    if unidades:
        partes.append(f"Unidades extraíbles ({len(unidades)}):\n" +
                      "\n".join(unidades))

    return _resultado(True, "\n".join(partes), "shell_info",
                      {"dispositivos": dispositivos, "unidades": unidades})


def version_herramienta(nombre: str) -> dict:
    """
    Consulta la versión de una herramienta de desarrollo instalada.
    Ej: version_herramienta("python") → "Python 3.11.4"
    """
    # Mapa de herramientas a su flag de versión
    MAPA_VERSION: dict[str, str] = {
        "python": "python --version",
        "node":   "node --version",
        "npm":    "npm --version",
        "git":    "git --version",
        "pip":    "pip --version",
        "cargo":  "cargo --version",
        "go":     "go version",
        "java":   "java -version",
        "dotnet": "dotnet --version",
        "ruby":    "ruby --version",
        "php":     "php --version",
        "rust":    "rustc --version",
        "cargo":   "cargo --version",
        "docker":  "docker --version",
        "kubectl": "kubectl version --client --short",
        "ffmpeg":  "ffmpeg -version",
        "ollama":  "ollama --version",
        "7z":      "7z i",
        "conda":   "conda --version",
    }
    nombre_norm = nombre.strip().lower()
    cmd = MAPA_VERSION.get(nombre_norm, f"{nombre_norm} --version")

    # Verificar que la herramienta existe antes de ejecutar
    if not shutil.which(nombre_norm):
        return _resultado(False, f"'{nombre}' no está instalado o no está en el PATH.", "shell_info")

    resultado = _ejecutar_subprocess(cmd)
    if resultado["exito"]:
        return _resultado(True, resultado["salida"].split("\n")[0].strip(), "shell_info")
    # Muchas herramientas mandan la versión a stderr (ej. java)
    if resultado.get("error"):
        return _resultado(True, resultado["error"].split("\n")[0].strip(), "shell_info")
    return resultado


# ──────────────────────────────────────────────────────────────────────────
# CONTROL DE PROCESOS (zona amarilla — requieren confirmación)
# ──────────────────────────────────────────────────────────────────────────

def matar_proceso(nombre_proceso: str) -> dict:
    """
    Termina un proceso por nombre. Siempre requiere confirmación.

    Args:
        nombre_proceso: Nombre del proceso, con o sin .exe (ej. "chrome", "chrome.exe")
    """
    if not nombre_proceso or not isinstance(nombre_proceso, str):
        return _resultado(False, "Nombre de proceso inválido.", "shell_error")

    nombre_limpio = nombre_proceso.strip()
    if not nombre_limpio.lower().endswith(".exe"):
        nombre_limpio += ".exe"

    # Verificar que el proceso exista antes de pedir confirmación
    try:
        import perceptor
        check = perceptor.app_esta_corriendo(nombre_limpio)
        if not check["exito"]:
            return _resultado(False, f"'{nombre_proceso}' no está corriendo actualmente.", "shell_info")
    except Exception:
        pass  # Si perceptor no está, continuar de todas formas

    return ejecutar_controlado(
        f"taskkill /im {nombre_limpio} /f",
        contexto=f"cerrar el proceso '{nombre_proceso}'",
    )


def apagar_equipo(minutos: int = 0) -> dict:
    """Apaga el equipo con confirmación obligatoria. minutos=0 → inmediato."""
    if minutos == 0:
        # /p apaga el equipo inmediatamente sin mostrar advertencias
        comando = "shutdown /s /p"
    else:
        segundos = minutos * 60
        comando = f"shutdown /s /t {segundos}"
        
    return ejecutar_controlado(
        comando,
        contexto=f"apagar el equipo" + (f" en {minutos} minuto(s)" if minutos > 0 else " ahora"),
    )



def reiniciar_equipo(minutos: int = 0) -> dict:
    """Reinicia el equipo con confirmación obligatoria."""
    segundos = minutos * 60
    return ejecutar_controlado(
        f"shutdown /r /t {segundos}",
        contexto=f"reiniciar el equipo" + (f" en {minutos} minuto(s)" if minutos > 0 else " ahora"),
    )


# ──────────────────────────────────────────────────────────────────────────
# INSTALACIONES CONTROLADAS
# ──────────────────────────────────────────────────────────────────────────

def instalar_pip(paquete: str) -> dict:
    """
    Instala un paquete Python con pip, con verificación previa y posterior.

    Flujo completo:
        1. Verificar si ya está instalado (perceptor.paquete_pip_instalado)
        2. Solicitar confirmación al usuario
        3. Ejecutar pip install con captura de output en tiempo real (simulado)
        4. Verificar que se instaló correctamente
        5. Retornar versión instalada

    Args:
        paquete: Nombre del paquete (ej. "requests", "pandas==2.0.0")
    """
    if not paquete or not isinstance(paquete, str):
        return _resultado(False, "Nombre de paquete inválido.", "shell_error")

    nombre_base = paquete.split("==")[0].split(">=")[0].split("<=")[0].strip()

    # 1. Verificar si ya está instalado
    try:
        import perceptor
        check_previo = perceptor.paquete_pip_instalado(nombre_base)
        if check_previo["exito"]:
            return _resultado(
                True,
                f"'{nombre_base}' ya está instalado (versión {check_previo.get('version','?')}). No es necesario instalarlo.",
                "shell_info",
            )
    except Exception:
        pass

    # 2. Confirmación + 3. Instalación
    resultado = ejecutar_controlado(
        f"pip install {paquete}",
        contexto=f"instalar el paquete Python '{paquete}' con pip",
    )

    if resultado.get("cancelado"):
        return resultado

    if not resultado["exito"]:
        return resultado

    # 4. Verificar que quedó instalado
    try:
        import perceptor
        check_post = perceptor.paquete_pip_instalado(nombre_base)
        if check_post["exito"]:
            return _resultado(
                True,
                f"'{nombre_base}' instalado correctamente (versión {check_post.get('version','?')}).",
                "shell_instalacion",
            )
    except Exception:
        pass

    # Si perceptor no está disponible, confiar en el returncode de pip
    return _resultado(True, f"'{paquete}' instalado. (No pude verificar la versión.)", "shell_instalacion")


def instalar_winget(app: str) -> dict:
    """
    Instala una aplicación usando Windows Package Manager (winget).
    Requiere confirmación. Verifica que winget esté disponible.

    Args:
        app: Nombre o ID del paquete en winget (ej. "Microsoft.VSCode")
    """
    if not shutil.which("winget"):
        return _resultado(
            False,
            "winget no está disponible en este sistema. Considera instalar manualmente.",
            "shell_error",
        )

    return ejecutar_controlado(
        f"winget install --id {app} -e --accept-source-agreements --accept-package-agreements",
        contexto=f"instalar '{app}' con Windows Package Manager (winget)",
    )


# ──────────────────────────────────────────────────────────────────────────
# REPRODUCCIÓN MULTIMEDIA (CAT_REPRODUCIR desde intent_router)
# ──────────────────────────────────────────────────────────────────────────

def reproducir_spotify() -> dict:
    """
    Abre Spotify o lo lleva al frente si ya está corriendo.
    Usa el protocolo URI spotify: que commands.py ya conoce (_ejecutar_sistema).
    """
    try:
        # Si ya está corriendo, activar la ventana vía PowerShell
        import perceptor
        check = perceptor.app_esta_corriendo("Spotify.exe")
        if check["exito"]:
            # Traer al frente en lugar de abrir una segunda instancia
            cmd = [
                "powershell",
                "-NoProfile",
                "-Command",
                "$p = Get-Process Spotify -ErrorAction SilentlyContinue | Select-Object -First 1; "
                "if ($p) { "
                "  Add-Type -AssemblyName Microsoft.VisualBasic; "
                "  [Microsoft.VisualBasic.Interaction]::AppActivate($p.Id) "
                "}"
            ]
            _ejecutar_subprocess(cmd)
            return _resultado(True, "Spotify está abierto.", "shell_reproduccion")
    except Exception:
        pass

    # Abrir Spotify por protocolo URI (mismo que commands._ejecutar_sistema)
    try:
        subprocess.Popen("start spotify:", shell=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _log("info", "Spotify abierto vía URI spotify:")
        return _resultado(True, "Abriendo Spotify...", "shell_reproduccion")
    except Exception as e:
        # Fallback: buscar spotify.exe en rutas conocidas
        rutas_spotify = [
            os.path.join(os.environ.get("APPDATA", ""), "Spotify", "Spotify.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "Spotify.exe"),
        ]
        for ruta in rutas_spotify:
            if os.path.isfile(ruta):
                try:
                    subprocess.Popen([ruta], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return _resultado(True, "Abriendo Spotify...", "shell_reproduccion")
                except Exception:
                    continue
        return _resultado(False, f"No pude abrir Spotify: {e}", "shell_error")


def reproducir_archivo_audio(ruta: str) -> dict:
    """
    Reproduce un archivo de audio local con el reproductor predeterminado del sistema.
    Verifica que el archivo existe y es de audio antes de intentar abrirlo.

    Args:
        ruta: Ruta completa al archivo de audio.
    """
    if not ruta or not isinstance(ruta, str):
        return _resultado(False, "Ruta de audio inválida.", "shell_error")

    try:
        import perceptor
        check_audio = perceptor.es_archivo_audio(ruta)
        if not check_audio["exito"]:
            return _resultado(False, f"'{os.path.basename(ruta)}' no es un archivo de audio reconocido.", "shell_error")

        check_existe = perceptor.existe_archivo(ruta)
        if not check_existe["exito"]:
            return _resultado(False, f"No encontré el archivo: '{ruta}'", "shell_error")
    except Exception:
        # Sin perceptor, verificación básica
        if not os.path.isfile(ruta):
            return _resultado(False, f"No encontré el archivo: '{ruta}'", "shell_error")

    try:
        subprocess.Popen(["start", "", ruta], shell=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        nombre = os.path.basename(ruta)
        _log("info", f"Reproduciendo: {nombre}")
        return _resultado(True, f"Reproduciendo '{nombre}'...", "shell_reproduccion")
    except Exception as e:
        return _resultado(False, f"No pude reproducir el archivo: {e}", "shell_error")


def reproducir(plataforma: Optional[str] = None, ruta_local: Optional[str] = None) -> dict:
    """
    Punto de entrada unificado para reproducción.
    Decide qué reproducir según lo que tenga disponible:
        1. Si se indica una plataforma de streaming → abrirla
        2. Si se indica una ruta local → reproducir ese archivo
        3. Default → Spotify

    Este es el método que sara.py llama cuando intent_router devuelve CAT_REPRODUCIR.

    Args:
        plataforma:  Nombre de la plataforma (de intent_router.obtener_plataforma_streaming)
        ruta_local:  Ruta a un archivo de audio específico del índice local.
    """
    PLATAFORMAS_URI: dict[str, str] = {
        "spotify":       "spotify:",
        "youtube":       "https://music.youtube.com",
        "youtube music": "https://music.youtube.com",
        "amazon music":  "https://music.amazon.com",
        "soundcloud":    "https://soundcloud.com",
        "deezer":        "https://www.deezer.com",
        "tidal":         "https://tidal.com",
    }

    if ruta_local:
        return reproducir_archivo_audio(ruta_local)

    if plataforma:
        plataforma_norm = plataforma.strip().lower()
        if plataforma_norm == "spotify":
            return reproducir_spotify()
        uri = PLATAFORMAS_URI.get(plataforma_norm)
        if uri:
            try:
                subprocess.Popen(f"start {uri}", shell=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return _resultado(True, f"Abriendo {plataforma.title()}...", "shell_reproduccion")
            except Exception as e:
                return _resultado(False, f"No pude abrir {plataforma}: {e}", "shell_error")

    # Default: Spotify
    return reproducir_spotify()


# ──────────────────────────────────────────────────────────────────────────
# UTILIDADES PARA AUTOMATIZACIÓN DE DESARROLLO
# ──────────────────────────────────────────────────────────────────────────

def ejecutar_tests(directorio: str = ".") -> dict:
    """
    Ejecuta pytest en el directorio indicado con confirmación.
    Parsea el resumen de pytest para retornar un mensaje limpio.
    """
    if not shutil.which("pytest"):
        return _resultado(False, "pytest no está instalado. Ejecuta: pip install pytest", "shell_error")

    resultado = ejecutar_controlado(
        f"pytest {directorio} --tb=short -q",
        contexto=f"ejecutar los tests en '{directorio}'",
    )

    if resultado["exito"] and resultado.get("salida"):
        # Extraer la línea de resumen de pytest (última línea relevante)
        lineas = [l.strip() for l in resultado["salida"].split("\n") if l.strip()]
        resumen_pytest = lineas[-1] if lineas else resultado["salida"]
        resultado["mensaje"] = resumen_pytest

    return resultado


def listar_paquetes_pip() -> dict:
    """Lista los paquetes pip instalados. Lista blanca — sin confirmación."""
    return ejecutar_controlado("pip list", contexto="listar paquetes pip instalados")


def info_git(directorio: str = ".") -> dict:
    """
    Muestra el estado del repositorio git en el directorio actual.
    Lista blanca — sin confirmación.
    """
    if not shutil.which("git"):
        return _resultado(False, "git no está disponible en este sistema.", "shell_error")

    resultado = ejecutar_controlado(
        f"git -C \"{directorio}\" status --short",
        contexto="ver estado del repositorio git",
    )
    return resultado
# ──────────────────────────────────────────────────────────────────────────
# AUTOMATIZACIÓN DE DESARROLLO
# ──────────────────────────────────────────────────────────────────────────
# Funciones orientadas al flujo de trabajo del desarrollador: ejecutar scripts,
# levantar servidores locales, correr tests, gestionar entornos virtuales,
# inspeccionar logs y trabajar con git.
# Zona blanca: info (log git, rama, dependencias, errores log).
# Zona amarilla: ejecución (scripts, servidores, compilación, venv).
# Toda ejecución de script externo pasa por ejecutar_script() con preview.

def _detectar_interprete(ruta_script: str) -> str:
    """
    Detecta el intérprete correcto para un script según su extensión.
    Fallback a 'python' si no se puede determinar.
    """
    ext = os.path.splitext(ruta_script)[1].lower()
    MAPA: dict[str, str] = {
        ".py":  "python",
        ".js":  "node",
        ".ts":  "npx ts-node",
        ".rb":  "ruby",
        ".php": "php",
        ".sh":  "bash",
        ".ps1": "powershell -File",
        ".bat": "cmd /c",
        ".go":  "go run",
        ".rs":  "cargo run --manifest-path",
    }
    return MAPA.get(ext, "python")


def ejecutar_script_dev(ruta_script: str, args: str = "",
                        directorio: str = "") -> dict:
    """
    Ejecuta un script de desarrollo con preview obligatorio y confirmación.
    Detecta el intérprete automáticamente por extensión.
    Valida sintaxis Python antes de ejecutar si aplica.

    Args:
        ruta_script: Ruta al script. Acepta nombres naturales de carpeta base.
        args:        Argumentos adicionales a pasar al script.
        directorio:  Directorio de trabajo. Vacío = carpeta del script.

    Ejemplos:
        ejecutar_script_dev("scripts/procesar_datos.py")
        ejecutar_script_dev("tests/test_brain.py", directorio="C:/sara")
        ejecutar_script_dev("build.bat")
    """
    ruta_real = _resolver_ruta_natural(ruta_script)

    if not os.path.isfile(ruta_real):
        return _resultado(False, f"No encontré el script: '{ruta_real}'", "shell_error")

    interprete = _detectar_interprete(ruta_real)
    dir_trabajo = directorio or os.path.dirname(ruta_real) or "."

    # Validación de sintaxis para Python antes de mostrar preview
    if ruta_real.endswith(".py"):
        try:
            import perceptor
            check = perceptor.verificar_script_generado(open(ruta_real, encoding="utf-8").read())
            if not check["exito"]:
                return _resultado(False,
                                  f"El script tiene errores de sintaxis: {check['mensaje']}",
                                  "shell_error")
        except Exception:
            pass

    # Preview del contenido (primeras 20 líneas)
    try:
        with open(ruta_real, encoding="utf-8", errors="replace") as f:
            lineas_preview = f.readlines()[:20]
        preview = "".join(lineas_preview)
        if len(lineas_preview) == 20:
            preview += "\n... (archivo continúa)"
    except Exception:
        preview = "(no se pudo leer el contenido)"

    cmd_completo = f'{interprete} "{ruta_real}"' + (f" {args}" if args else "")
    confirmado = _solicitar_confirmacion(
        f"ejecutar '{os.path.basename(ruta_real)}' con {interprete}",
        preview=preview
    )
    if not confirmado:
        return _resultado(False, "Ejecución cancelada.", "shell_cancelado", {"cancelado": True})

    _log("info", f"Ejecutando script dev: {cmd_completo}")

    # Ejecutar con cwd correcto
    try:
        proc = subprocess.run(
            cmd_completo, shell=True,
            capture_output=True, text=True,
            timeout=120.0, cwd=dir_trabajo,
            encoding="utf-8", errors="replace"
        )
        salida = proc.stdout.strip()
        error  = proc.stderr.strip()

        if proc.returncode == 0:
            resumen = salida[:800] if salida else "Script ejecutado correctamente (sin output)."
            return _resultado(True, resumen, "shell_dev", {"salida": salida, "error": error})
        else:
            msg_error = error[:500] or salida[:500] or f"Código de salida: {proc.returncode}"
            return _resultado(False, f"El script terminó con errores:\n{msg_error}",
                              "shell_dev", {"salida": salida, "error": error})
    except subprocess.TimeoutExpired:
        return _resultado(False, "El script tardó más de 120s y fue cancelado.", "shell_timeout")
    except Exception as e:
        return _resultado(False, f"No pude ejecutar el script: {e}", "shell_error")


def levantar_servidor(framework: str = "", puerto: int = 0,
                      directorio: str = ".") -> dict:
    """
    Levanta un servidor de desarrollo en una ventana de terminal nueva.
    Detecta el comando correcto según el framework o busca manage.py/app.py/main.py.
    Zona amarilla — requiere confirmación.

    Args:
        framework:  Nombre del framework (django, flask, fastapi, react, etc.)
                    Vacío = autodetectar por archivos en el directorio.
        puerto:     Puerto a usar. 0 = usar el puerto por defecto del framework.
        directorio: Directorio del proyecto. Default = directorio actual.

    Ejemplos:
        levantar_servidor("django")
        levantar_servidor("flask", puerto=5001)
        levantar_servidor(directorio="C:/mis_proyectos/mi_app")
    """
    try:
        from config import PUERTOS_DEV_CONOCIDOS
    except Exception:
        PUERTOS_DEV_CONOCIDOS = {
            "django": 8000, "flask": 5000, "fastapi": 8000,
            "react": 3000, "vue": 5173, "node": 3000,
        }

    dir_real = os.path.abspath(directorio)
    if not os.path.isdir(dir_real):
        return _resultado(False, f"No encontré el directorio: '{dir_real}'", "shell_error")

    fw = framework.strip().lower()

    # Autodetección por archivos del proyecto si no se especifica framework
    if not fw:
        if os.path.isfile(os.path.join(dir_real, "manage.py")):
            fw = "django"
        elif os.path.isfile(os.path.join(dir_real, "package.json")):
            fw = "node"
        elif os.path.isfile(os.path.join(dir_real, "app.py")):
            fw = "flask"
        elif os.path.isfile(os.path.join(dir_real, "main.py")):
            fw = "fastapi"
        else:
            return _resultado(False,
                              "No pude detectar el framework automáticamente. "
                              "Especifica: 'django', 'flask', 'fastapi', 'react', 'node', etc.",
                              "shell_error")

    # Mapa de comandos por framework
    MAPA_COMANDOS: dict[str, str] = {
        "django":    f"python manage.py runserver {puerto or PUERTOS_DEV_CONOCIDOS.get('django', 8000)}",
        "flask":     f"python app.py",
        "fastapi":   f"uvicorn main:app --reload --port {puerto or PUERTOS_DEV_CONOCIDOS.get('fastapi', 8000)}",
        "uvicorn":   f"uvicorn main:app --reload --port {puerto or 8000}",
        "react":     "npm start",
        "vue":       "npm run dev",
        "vite":      "npm run dev",
        "angular":   "ng serve",
        "nextjs":    "npm run dev",
        "svelte":    "npm run dev",
        "node":      "node index.js",
        "express":   "node index.js",
        "streamlit": f"streamlit run app.py --server.port {puerto or 8501}",
        "jupyter":   f"jupyter notebook --port {puerto or 8888}",
    }

    cmd_servidor = MAPA_COMANDOS.get(fw)
    if not cmd_servidor:
        return _resultado(False,
                          f"Framework '{fw}' no reconocido. "
                          f"Frameworks soportados: {', '.join(MAPA_COMANDOS.keys())}",
                          "shell_error")

    puerto_display = puerto or PUERTOS_DEV_CONOCIDOS.get(fw, "?")
    confirmado = _solicitar_confirmacion(
        f"levantar servidor {fw.title()} en puerto {puerto_display} "
        f"(directorio: {dir_real})"
    )
    if not confirmado:
        return _resultado(False, "Servidor cancelado.", "shell_cancelado", {"cancelado": True})

    # Abrir en ventana nueva para que el servidor quede vivo
    try:
        subprocess.Popen(
            f'start cmd /k "cd /d "{dir_real}" && {cmd_servidor}"',
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _log("info", f"Servidor {fw} levantado en {dir_real}")
        return _resultado(True,
                          f"✅ Servidor {fw.title()} iniciado en puerto {puerto_display}.\n"
                          f"Se abrió una terminal nueva — ciérrala para detenerlo.",
                          "shell_dev",
                          {"framework": fw, "puerto": puerto_display, "directorio": dir_real})
    except Exception as e:
        return _resultado(False, f"No pude levantar el servidor: {e}", "shell_error")


def crear_entorno_virtual(directorio: str = ".", nombre: str = "venv") -> dict:
    """
    Crea un entorno virtual Python en el directorio indicado.
    Verifica que venv/virtualenv estén disponibles.
    Zona amarilla — requiere confirmación.

    Args:
        directorio: Directorio donde crear el entorno. Default = directorio actual.
        nombre:     Nombre de la carpeta del entorno (default 'venv').

    Ejemplos:
        crear_entorno_virtual()
        crear_entorno_virtual("C:/mis_proyectos/mi_app", nombre=".venv")
    """
    if not shutil.which("python") and not shutil.which("python3"):
        return _resultado(False, "Python no está disponible en el PATH.", "shell_error")

    dir_real   = os.path.abspath(directorio)
    ruta_venv  = os.path.join(dir_real, nombre)

    if os.path.isdir(ruta_venv):
        return _resultado(True,
                          f"El entorno virtual '{nombre}' ya existe en '{dir_real}'.",
                          "shell_info")

    confirmado = _solicitar_confirmacion(
        f"crear entorno virtual '{nombre}' en '{dir_real}'"
    )
    if not confirmado:
        return _resultado(False, "Creación de entorno cancelada.",
                          "shell_cancelado", {"cancelado": True})

    resultado = _ejecutar_subprocess(
        f'python -m venv "{ruta_venv}"',
        timeout=30.0
    )
    if resultado["exito"] or os.path.isdir(ruta_venv):
        activar = os.path.join(ruta_venv, "Scripts", "activate.bat")
        return _resultado(True,
                          f"✅ Entorno virtual '{nombre}' creado en '{dir_real}'.\n"
                          f"Para activarlo: {activar}",
                          "shell_dev",
                          {"ruta_venv": ruta_venv, "activar": activar})
    return resultado


def info_git_log(directorio: str = ".", limite: int = 10) -> dict:
    """
    Muestra el historial de commits del repositorio de forma legible.
    Lista blanca — sin confirmación.

    Args:
        directorio: Directorio del repositorio git.
        limite:     Número de commits a mostrar (default 10, max 50).

    Ejemplos:
        info_git_log()
        info_git_log("C:/sara", limite=5)
    """
    if not shutil.which("git"):
        return _resultado(False, "git no está disponible en el PATH.", "shell_error")

    limite = min(max(1, limite), 50)
    dir_real = os.path.abspath(directorio)

    cmd = [
        "git", "-C", dir_real, "log",
        f"--max-count={limite}",
        "--pretty=format:%h | %an | %ar | %s",
        "--no-merges"
    ]
    resultado = _ejecutar_subprocess(cmd, timeout=10.0)
    if resultado["exito"] and resultado.get("salida"):
        lineas = resultado["salida"].strip().split("\n")
        formateadas = [f"  {i+1}. {l.strip()}" for i, l in enumerate(lineas)]
        return _resultado(True,
                          f"Últimos {len(formateadas)} commits:\n" + "\n".join(formateadas),
                          "shell_info",
                          {"commits": formateadas, "directorio": dir_real})
    if not resultado["exito"]:
        return _resultado(False,
                          f"No es un repositorio git o no hay commits en '{dir_real}'.",
                          "shell_info")
    return resultado


def info_git_rama(directorio: str = ".") -> dict:
    """
    Muestra la rama activa y las ramas disponibles del repositorio.
    Lista blanca — sin confirmación.

    Ejemplos:
        info_git_rama()
        info_git_rama("C:/mis_proyectos/mi_app")
    """
    if not shutil.which("git"):
        return _resultado(False, "git no está disponible.", "shell_error")

    dir_real = os.path.abspath(directorio)

    # Rama actual
    cmd_actual = ["git", "-C", dir_real, "branch", "--show-current"]
    r_actual = _ejecutar_subprocess(cmd_actual, timeout=5.0)

    # Todas las ramas
    cmd_ramas = ["git", "-C", dir_real, "branch", "-a", "--format=%(refname:short)"]
    r_ramas = _ejecutar_subprocess(cmd_ramas, timeout=5.0)

    if r_actual["exito"]:
        rama_actual = r_actual["salida"].strip() or "HEAD detached"
        ramas_lista = []
        if r_ramas["exito"]:
            ramas_lista = [l.strip() for l in r_ramas["salida"].split("\n") if l.strip()]
        resumen = f"Rama actual: 🌿 {rama_actual}"
        if ramas_lista:
            resumen += f"\nRamas disponibles ({len(ramas_lista)}): {', '.join(ramas_lista[:10])}"
            if len(ramas_lista) > 10:
                resumen += f" ... y {len(ramas_lista)-10} más"
        return _resultado(True, resumen, "shell_info",
                          {"rama_actual": rama_actual, "ramas": ramas_lista})
    return _resultado(False,
                      f"No es un repositorio git en '{dir_real}'.",
                      "shell_info")


def ver_errores_log(ruta_log: str = "", ultimas_lineas: int = 30) -> dict:
    """
    Muestra los errores y warnings más recientes de un archivo de log.
    Lista blanca — sin confirmación. Solo lectura.
    Si no se especifica ruta, busca sara.log o logs/ en el directorio actual.

    Args:
        ruta_log:      Ruta al archivo de log. Vacío = autodetectar.
        ultimas_lineas: Número de líneas del final a analizar (default 30).

    Ejemplos:
        ver_errores_log()
        ver_errores_log("C:/sara/sara.log", ultimas_lineas=50)
        ver_errores_log("logs/app.log")
    """
    # Autodetección de log si no se especifica
    if not ruta_log:
        candidatos = [
            "sara.log", "app.log", "error.log",
            os.path.join("logs", "sara.log"),
            os.path.join("logs", "app.log"),
            os.path.join("logs", "error.log"),
        ]
        for c in candidatos:
            if os.path.isfile(c):
                ruta_log = c
                break
        if not ruta_log:
            return _resultado(False,
                              "No encontré archivo de log. "
                              "Especifica la ruta: 'ver errores del log C:/sara/sara.log'",
                              "shell_info")

    if not os.path.isfile(ruta_log):
        return _resultado(False, f"No encontré el log: '{ruta_log}'", "shell_error")

    try:
        with open(ruta_log, encoding="utf-8", errors="replace") as f:
            todas = f.readlines()

        ultimas = todas[-ultimas_lineas:] if len(todas) > ultimas_lineas else todas

        # Filtrar solo líneas de error/warning
        PATRONES_ERROR = ("error", "exception", "traceback", "critical",
                          "warning", "warn", "failed", "fallo", "❌")
        errores = [l.rstrip() for l in ultimas
                   if any(p in l.lower() for p in PATRONES_ERROR)]

        if not errores:
            return _resultado(True,
                              f"Sin errores en las últimas {len(ultimas)} líneas de '{os.path.basename(ruta_log)}'.",
                              "shell_info")

        resumen = (f"{len(errores)} error(es)/warning(s) en "
                   f"'{os.path.basename(ruta_log)}':\n" +
                   "\n".join(errores[-15:]))   # mostrar máx 15 para no inundar
        return _resultado(True, resumen, "shell_info",
                          {"errores": errores, "total_lineas": len(todas)})

    except PermissionError:
        return _resultado(False, "Sin permisos para leer el log.", "shell_error")
    except Exception as e:
        return _resultado(False, f"No pude leer el log: {e}", "shell_error")


def listar_dependencias_proyecto(directorio: str = ".") -> dict:
    """
    Detecta el gestor de paquetes del proyecto y lista sus dependencias.
    Soporta: requirements.txt, pyproject.toml, package.json, Gemfile, go.mod.
    Lista blanca — sin confirmación.

    Args:
        directorio: Directorio del proyecto. Default = directorio actual.

    Ejemplos:
        listar_dependencias_proyecto()
        listar_dependencias_proyecto("C:/mis_proyectos/mi_app")
    """
    dir_real = os.path.abspath(directorio)

    # Detección por archivo de dependencias
    ARCHIVOS_DEP: dict[str, str] = {
        "requirements.txt": "pip",
        "pyproject.toml":   "pip/poetry",
        "Pipfile":          "pipenv",
        "package.json":     "npm",
        "Gemfile":          "bundler",
        "go.mod":           "go",
        "Cargo.toml":       "cargo",
        "composer.json":    "composer",
    }

    archivo_encontrado = None
    gestor = None
    for archivo, gest in ARCHIVOS_DEP.items():
        ruta_arch = os.path.join(dir_real, archivo)
        if os.path.isfile(ruta_arch):
            archivo_encontrado = ruta_arch
            gestor = gest
            break

    if not archivo_encontrado:
        return _resultado(False,
                          f"No encontré archivo de dependencias en '{dir_real}'. "
                          "Buscados: requirements.txt, package.json, pyproject.toml, etc.",
                          "shell_info")

    try:
        with open(archivo_encontrado, encoding="utf-8", errors="replace") as f:
            contenido = f.read()

        lineas = [l.strip() for l in contenido.split("\n")
                  if l.strip() and not l.strip().startswith("#")]
        total = len(lineas)
        muestra = lineas[:20]

        resumen = (f"Dependencias de '{os.path.basename(archivo_encontrado)}' "
                   f"(gestor: {gestor}) — {total} total:\n" +
                   "\n".join(f"  • {l}" for l in muestra))
        if total > 20:
            resumen += f"\n  ... y {total - 20} más."

        return _resultado(True, resumen, "shell_info",
                          {"gestor": gestor, "total": total,
                           "archivo": archivo_encontrado})
    except Exception as e:
        return _resultado(False, f"No pude leer las dependencias: {e}", "shell_error")


def gestionar_dev(texto_usuario: str, directorio: str = ".") -> dict:
    """
    Punto de entrada unificado para automatización de desarrollo desde
    lenguaje natural. brain.py lo llama cuando intent_router devuelve CAT_DEV.

    Args:
        texto_usuario: Entrada normalizada del usuario.
        directorio:    Directorio de trabajo actual (contexto del proyecto).

    Ejemplos manejados:
        "ejecuta el script procesar_datos.py"
        "levanta el servidor django"
        "levanta el servidor en el puerto 5001"
        "crea un entorno virtual"
        "ver los últimos commits"
        "en qué rama estoy"
        "ver errores del log"
        "listar dependencias del proyecto"
        "corre los tests"
    """
    try:
        from utils import normalizar_texto
        texto_norm = normalizar_texto(texto_usuario)
    except Exception:
        texto_norm = texto_usuario.lower().strip()

    # ── TESTS ─────────────────────────────────────────────────────────
    for patron in ("corre los tests", "ejecuta los tests", "lanza los tests",
                   "correr tests", "ejecutar tests", "run tests", "pytest"):
        if patron in texto_norm:
            dir_tests = directorio
            # Detectar si se especifica carpeta de tests
            for subcarpeta in ("tests", "test", "testing"):
                ruta_sub = os.path.join(os.path.abspath(directorio), subcarpeta)
                if os.path.isdir(ruta_sub):
                    dir_tests = ruta_sub
                    break
            return ejecutar_tests(dir_tests)

    # ── LEVANTAR SERVIDOR ─────────────────────────────────────────────
    for patron in ("levanta el servidor", "inicia el servidor", "arranca el servidor",
                   "levanta", "sube el servidor", "start server"):
        if patron in texto_norm:
            resto = texto_norm.split(patron, 1)[-1].strip()
            # Detectar puerto si viene: "en el puerto 5001"
            puerto = 0
            import re
            m = re.search(r"puerto\s+(\d+)|port\s+(\d+)|:(\d+)", resto)
            if m:
                puerto = int(next(g for g in m.groups() if g))
                resto = re.sub(r"(en el puerto|puerto|port)\s+\d+", "", resto).strip()
            # Detectar framework
            fw = ""
            try:
                from config import PUERTOS_DEV_CONOCIDOS
                for nombre_fw in PUERTOS_DEV_CONOCIDOS:
                    if nombre_fw in resto:
                        fw = nombre_fw
                        break
            except Exception:
                pass
            return levantar_servidor(fw, puerto, directorio)

    # ── ENTORNO VIRTUAL ───────────────────────────────────────────────
    for patron in ("crea el entorno virtual", "crea un entorno virtual",
                   "nuevo entorno", "crear venv", "crea el venv"):
        if patron in texto_norm:
            nombre_venv = "venv"
            for kw in (".venv", "env", "venv"):
                if kw in texto_norm:
                    nombre_venv = kw
                    break
            return crear_entorno_virtual(directorio, nombre_venv)

    # ── LOG DE GIT ────────────────────────────────────────────────────
    for patron in ("ver los commits", "log de git", "historial de git",
                   "historial git", "ultimos commits", "últimos commits",
                   "git log"):
        if patron in texto_norm:
            import re
            m = re.search(r"(\d+)\s*(commits?|ultimos?|últimos?)", texto_norm)
            limite = int(m.group(1)) if m else 10
            return info_git_log(directorio, limite)

    # ── RAMA GIT ──────────────────────────────────────────────────────
    for patron in ("que rama estoy", "qué rama estoy", "rama actual",
                   "en que rama", "en qué rama", "git branch"):
        if patron in texto_norm:
            return info_git_rama(directorio)

    # ── VER ERRORES LOG ───────────────────────────────────────────────
    for patron in ("ver errores del log", "errores del log", "ultimos errores",
                   "últimos errores", "ver el log", "revisar el log"):
        if patron in texto_norm:
            # Detectar ruta de log si se menciona
            ruta_log = ""
            partes = texto_norm.split(patron, 1)
            if len(partes) > 1 and partes[1].strip():
                ruta_log = partes[1].strip()
            return ver_errores_log(ruta_log)

    # ── DEPENDENCIAS ─────────────────────────────────────────────────
    for patron in ("listar dependencias", "dependencias del proyecto",
                   "qué dependencias", "que dependencias",
                   "requirements", "package json"):
        if patron in texto_norm:
            return listar_dependencias_proyecto(directorio)

    # ── EJECUTAR SCRIPT ───────────────────────────────────────────────
    for patron in ("ejecuta el script", "ejecuta mi script", "corre el script",
                   "ejecuta", "corre", "run"):
        if texto_norm.startswith(patron):
            resto = texto_norm[len(patron):].strip()
            for art in ("el script ", "mi script ", "el archivo ", "el "):
                if resto.startswith(art):
                    resto = resto[len(art):]
            # Separar argumentos si vienen: "script.py --debug"
            partes = resto.split(" ", 1)
            ruta_script = partes[0].strip()
            args        = partes[1].strip() if len(partes) > 1 else ""
            if ruta_script:
                return ejecutar_script_dev(ruta_script, args, directorio)
            break

    # ── COMPILAR / BUILD ──────────────────────────────────────────────
    for patron in ("compila el proyecto", "construye el proyecto",
                   "build", "compilar", "construir"):
        if patron in texto_norm:
            # Detectar tipo de build
            if os.path.isfile(os.path.join(directorio, "package.json")):
                return ejecutar_controlado("npm run build",
                                           contexto="compilar el proyecto con npm")
            if os.path.isfile(os.path.join(directorio, "Makefile")):
                return ejecutar_controlado("make",
                                           contexto="compilar el proyecto con make")
            if os.path.isfile(os.path.join(directorio, "Cargo.toml")):
                return ejecutar_controlado("cargo build",
                                           contexto="compilar el proyecto Rust con cargo")
            return _resultado(False,
                              "No detecté sistema de build (package.json, Makefile, Cargo.toml). "
                              "Especifica el comando manualmente.",
                              "shell_info")

    # ── No reconocido ─────────────────────────────────────────────────
    _log("warning", f"gestionar_dev no reconoció: '{texto_norm[:60]}'")
    return _resultado(False,
                      "No entendí qué acción de desarrollo quieres realizar. "
                      "Prueba: 'levanta el servidor django', 'corre los tests', "
                      "'ver los commits', 'ejecuta el script main.py'.",
                      "shell_info")
# ──────────────────────────────────────────────────────────────────────────
# RED Y CONECTIVIDAD
# ──────────────────────────────────────────────────────────────────────────
# Funciones de diagnóstico y control de red.
# Lectura (ping, dns, adaptadores, rutas, arp) → zona blanca, sin confirmación.
# Modificación (firewall, puertos) → zona amarilla, confirmación obligatoria.
# Todas usan PowerShell estructurado — sin shell=True con input del usuario.

def ping_host(host: str = "", cuenta: int = 3) -> dict:
    """
    Hace ping a un host y reporta latencia y pérdida de paquetes.
    Lista blanca — sin confirmación.
    Si no se indica host, prueba conectividad general con hosts por defecto.

    Args:
        host:   IP o dominio a hacer ping. Vacío = prueba conectividad general.
        cuenta: Número de paquetes a enviar (default 3, max 10 para no bloquear).

    Ejemplos:
        ping_host("google.com")
        ping_host("192.168.1.1")
        ping_host()  →  prueba internet con DNS públicos
    """
    try:
        from config import HOSTS_PING_DEFAULT
    except Exception:
        HOSTS_PING_DEFAULT = ["8.8.8.8", "1.1.1.1"]

    cuenta = min(max(1, cuenta), 10)   # clamp 1–10

    if not host:
        # Modo conectividad general — probar varios hosts
        resultados = []
        hay_internet = False
        for h in HOSTS_PING_DEFAULT[:3]:
            cmd = [
                "powershell", "-NoProfile", "-Command",
                f"$r = Test-Connection -ComputerName {h} -Count 1 -Quiet -EA SilentlyContinue; "
                f"Write-Output $r"
            ]
            r = _ejecutar_subprocess(cmd, timeout=5.0)
            conectado = r["exito"] and "True" in r.get("salida", "")
            resultados.append(f"{'✅' if conectado else '❌'} {h}")
            if conectado:
                hay_internet = True

        estado = "Conexión a internet activa." if hay_internet else "Sin conexión a internet."
        return _resultado(hay_internet, f"{estado}\n" + "\n".join(resultados),
                          "shell_info", {"hay_internet": hay_internet})

    # Ping a host específico con latencia
    cmd = [
        "powershell", "-NoProfile", "-Command",
        f"$r = Test-Connection -ComputerName '{host}' -Count {cuenta} -EA SilentlyContinue; "
        f"if ($r) {{ "
        f"  $avg = [math]::Round(($r | Measure-Object ResponseTime -Average).Average, 1); "
        f"  $min = ($r | Measure-Object ResponseTime -Minimum).Minimum; "
        f"  $max = ($r | Measure-Object ResponseTime -Maximum).Maximum; "
        f"  $perdida = [math]::Round((1 - $r.Count/{cuenta})*100); "
        f"  Write-Output \"OK|Latencia media:{avg}ms|Min:{min}ms|Max:{max}ms|Perdida:{perdida}%\" "
        f"}} else {{ Write-Output 'FALLO' }}"
    ]
    resultado = _ejecutar_subprocess(cmd, timeout=15.0)
    if resultado["exito"]:
        salida = resultado["salida"].strip()
        if salida.startswith("OK"):
            datos = {p.split(":")[0]: ":".join(p.split(":")[1:])
                     for p in salida.split("|") if ":" in p}
            resumen = (f"Ping a '{host}': "
                       f"latencia {datos.get('Latencia media','?')} | "
                       f"pérdida {datos.get('Perdida','?')}")
            return _resultado(True, resumen, "shell_info", {"datos_ping": datos, "host": host})
        else:
            return _resultado(False, f"No se pudo alcanzar '{host}'. Sin respuesta.", "shell_info")
    return resultado


def info_dns() -> dict:
    """
    Muestra los servidores DNS configurados en los adaptadores activos.
    Lista blanca — sin confirmación.
    Útil para: 'cuál es mi DNS', 'qué DNS tengo configurado'.
    """
    cmd = [
        "powershell", "-NoProfile", "-Command",
        "Get-DnsClientServerAddress -AddressFamily IPv4 | "
        "Where-Object { $_.ServerAddresses.Count -gt 0 } | "
        "Select-Object InterfaceAlias, ServerAddresses | "
        "ForEach-Object { "
        "  $dns = $_.ServerAddresses -join ', '; "
        "  Write-Output \"$($_.InterfaceAlias)|DNS:$dns\" "
        "} | Out-String"
    ]
    resultado = _ejecutar_subprocess(cmd)
    if resultado["exito"]:
        lineas = [l.strip() for l in resultado["salida"].split("\n") if "|" in l]
        if lineas:
            formateadas = []
            for l in lineas:
                partes = l.split("|", 1)
                formateadas.append(f"🌐 {partes[0].strip()}: {partes[1].replace('DNS:','').strip()}")
            return _resultado(True, "Servidores DNS configurados:\n" + "\n".join(formateadas),
                              "shell_info", {"lineas_dns": formateadas})
    return resultado


def info_tabla_rutas() -> dict:
    """
    Muestra la tabla de rutas de red activas (gateway, destino, métrica).
    Lista blanca — sin confirmación.
    Útil para: 'tabla de rutas', 'qué gateway tengo', 'rutas de red'.
    """
    cmd = [
        "powershell", "-NoProfile", "-Command",
        "Get-NetRoute -AddressFamily IPv4 | "
        "Where-Object { $_.RouteMetric -lt 500 } | "
        "Sort-Object RouteMetric | "
        "Select-Object -First 15 DestinationPrefix, NextHop, RouteMetric, InterfaceAlias | "
        "Format-Table -AutoSize | Out-String"
    ]
    resultado = _ejecutar_subprocess(cmd, timeout=8.0)
    if resultado["exito"]:
        return _resultado(True,
                          f"Tabla de rutas de red (principales):\n{resultado['salida'].strip()}",
                          "shell_info")
    return resultado


def info_arp() -> dict:
    """
    Muestra la tabla ARP — dispositivos detectados en la red local con su MAC.
    Lista blanca — sin confirmación.
    Útil para: 'qué dispositivos hay en mi red', 'tabla arp', 'MACs en la red'.
    """
    cmd = [
        "powershell", "-NoProfile", "-Command",
        "Get-NetNeighbor -AddressFamily IPv4 | "
        "Where-Object { $_.State -ne 'Unreachable' -and $_.IPAddress -notlike '224.*' "
        "-and $_.IPAddress -notlike '239.*' } | "
        "Select-Object IPAddress, LinkLayerAddress, State, InterfaceAlias | "
        "Sort-Object IPAddress | "
        "Format-Table -AutoSize | Out-String"
    ]
    resultado = _ejecutar_subprocess(cmd, timeout=8.0)
    if resultado["exito"]:
        return _resultado(True,
                          f"Dispositivos en la red local (tabla ARP):\n{resultado['salida'].strip()}",
                          "shell_info")
    return resultado


def verificar_puerto(host: str, puerto: int, timeout_seg: float = 3.0) -> dict:
    """
    Verifica si un puerto TCP está abierto en un host remoto o local.
    Lista blanca — sin confirmación (solo lectura).
    Útil para: 'está abierto el puerto 8080', 'el puerto 3306 responde'.

    Args:
        host:        IP o dominio a verificar. '127.0.0.1' para local.
        puerto:      Número de puerto TCP (1–65535).
        timeout_seg: Tiempo máximo de espera en segundos.
    """
    if not (1 <= puerto <= 65535):
        return _resultado(False, f"Puerto inválido: {puerto}. Debe estar entre 1 y 65535.", "shell_error")

    cmd = [
        "powershell", "-NoProfile", "-Command",
        f"$tcp = New-Object System.Net.Sockets.TcpClient; "
        f"try {{ "
        f"  $r = $tcp.BeginConnect('{host}', {puerto}, $null, $null); "
        f"  $ok = $r.AsyncWaitHandle.WaitOne({int(timeout_seg*1000)}, $false); "
        f"  if ($ok -and $tcp.Connected) {{ Write-Output 'ABIERTO' }} "
        f"  else {{ Write-Output 'CERRADO' }} "
        f"}} catch {{ Write-Output 'CERRADO' }} "
        f"finally {{ $tcp.Close() }}"
    ]
    resultado = _ejecutar_subprocess(cmd, timeout=timeout_seg + 2)
    if resultado["exito"]:
        abierto = "ABIERTO" in resultado.get("salida", "")
        icono = "✅" if abierto else "❌"
        msg = f"{icono} Puerto {puerto} en '{host}': {'abierto' if abierto else 'cerrado o sin respuesta'}."
        return _resultado(True, msg, "shell_info", {"abierto": abierto, "host": host, "puerto": puerto})
    return resultado


def info_estadisticas_red() -> dict:
    """
    Estadísticas de uso de red por adaptador: bytes enviados/recibidos.
    Lista blanca — sin confirmación.
    Útil para: 'cuánto he descargado', 'estadísticas de red', 'uso de ancho de banda'.
    """
    cmd = [
        "powershell", "-NoProfile", "-Command",
        "Get-NetAdapterStatistics | "
        "Where-Object { $_.ReceivedBytes -gt 0 } | "
        "Select-Object Name, ReceivedBytes, SentBytes | "
        "ForEach-Object { "
        "  $rx = [math]::Round($_.ReceivedBytes/1MB, 1); "
        "  $tx = [math]::Round($_.SentBytes/1MB, 1); "
        "  Write-Output \"$($_.Name)|RX:${rx}MB|TX:${tx}MB\" "
        "} | Out-String"
    ]
    resultado = _ejecutar_subprocess(cmd)
    if resultado["exito"]:
        lineas = [l.strip() for l in resultado["salida"].split("\n") if "|" in l]
        formateadas = []
        for l in lineas:
            partes = l.split("|")
            if len(partes) >= 3:
                formateadas.append(
                    f"📡 {partes[0].strip()} — "
                    f"↓ {partes[1].replace('RX:','').strip()} recibidos | "
                    f"↑ {partes[2].replace('TX:','').strip()} enviados"
                )
        if formateadas:
            return _resultado(True, "Uso de red por adaptador:\n" + "\n".join(formateadas),
                              "shell_info", {"adaptadores": formateadas})
    return resultado


def gestionar_firewall(accion: str, puerto: int,
                       protocolo: str = "TCP",
                       nombre_regla: str = "") -> dict:
    """
    Añade o elimina una regla de firewall de Windows para un puerto.
    ZONA AMARILLA — siempre requiere confirmación.
    Nunca modifica reglas existentes del sistema; solo crea/elimina reglas SARA.

    Args:
        accion:      'abrir' para permitir tráfico, 'cerrar' para bloquearlo/eliminarlo.
        puerto:      Número de puerto TCP/UDP (1–65535).
        protocolo:   'TCP' o 'UDP' (default TCP).
        nombre_regla: Nombre descriptivo para la regla. Auto-generado si vacío.

    Ejemplos:
        gestionar_firewall("abrir", 8080)
        gestionar_firewall("cerrar", 3306, protocolo="TCP")
        gestionar_firewall("abrir", 5432, nombre_regla="PostgreSQL SARA")
    """
    if not (1 <= puerto <= 65535):
        return _resultado(False, f"Puerto inválido: {puerto}.", "shell_error")

    protocolo = protocolo.upper()
    if protocolo not in ("TCP", "UDP"):
        return _resultado(False, "Protocolo debe ser TCP o UDP.", "shell_error")

    accion_norm = accion.strip().lower()
    if accion_norm not in ("abrir", "cerrar", "open", "close", "bloquear"):
        return _resultado(False, "Acción debe ser 'abrir' o 'cerrar'.", "shell_error")

    abrir = accion_norm in ("abrir", "open")
    nombre = nombre_regla or f"SARA_{protocolo}_{puerto}_{'IN' if abrir else 'BLOCK'}"

    if abrir:
        descripcion_accion = (f"crear regla de firewall para PERMITIR {protocolo} "
                              f"en puerto {puerto} (nombre: '{nombre}')")
        cmd_ps = (
            f"New-NetFirewallRule -DisplayName '{nombre}' "
            f"-Direction Inbound -Protocol {protocolo} "
            f"-LocalPort {puerto} -Action Allow -Profile Any"
        )
    else:
        descripcion_accion = (f"ELIMINAR regla de firewall '{nombre}' "
                              f"({protocolo} puerto {puerto})")
        cmd_ps = f"Remove-NetFirewallRule -DisplayName '{nombre}' -EA SilentlyContinue"

    confirmado = _solicitar_confirmacion(descripcion_accion)
    if not confirmado:
        return _resultado(False, "Operación de firewall cancelada.",
                          "shell_cancelado", {"cancelado": True})

    cmd = ["powershell", "-NoProfile", "-Command", cmd_ps]
    resultado = _ejecutar_subprocess(cmd, timeout=15.0)

    if resultado["exito"]:
        accion_str = "abierto" if abrir else "eliminada la regla de"
        return _resultado(True,
                          f"✅ Firewall: puerto {puerto}/{protocolo} {accion_str}.",
                          "shell_accion", {"puerto": puerto, "protocolo": protocolo})
    return resultado


def diagnostico_red() -> dict:
    """
    Diagnóstico completo de red: adaptadores, IP, DNS, conectividad a internet.
    Zona blanca — sin confirmación. Combina varias funciones en un solo reporte.
    Útil para: 'cómo está mi red', 'diagnóstico de red', 'problemas de conexión'.
    """
    lineas: list[str] = []
    alertas: list[str] = []

    # Adaptadores activos
    try:
        r = info_red_extendida()
        if r["exito"]:
            lineas.append(r["mensaje"])
    except Exception:
        pass

    # IP local
    try:
        r = info_ip()
        if r["exito"]:
            lineas.append(f"📍 {r['mensaje']}")
    except Exception:
        pass

    # DNS
    try:
        r = info_dns()
        if r["exito"]:
            primera_linea = r["mensaje"].split("\n")[1] if "\n" in r["mensaje"] else r["mensaje"]
            lineas.append(f"🔤 {primera_linea.strip()}")
    except Exception:
        pass

    # Conectividad a internet
    try:
        r = ping_host()
        lineas.append(f"{'✅' if r['exito'] else '❌'} {r['mensaje'].split(chr(10))[0]}")
        if not r["exito"]:
            alertas.append("sin conexión a internet")
    except Exception:
        pass

    if not lineas:
        return _resultado(False, "No pude obtener información de red.", "shell_diagnostico")

    resumen = "\n".join(lineas)
    if alertas:
        resumen += f"\n⚠ Alertas: {', '.join(alertas)}."

    return _resultado(True, resumen, "shell_diagnostico", {"alertas": alertas})

# ──────────────────────────────────────────────────────────────────────────
# GESTIÓN DE ARCHIVOS DESDE LENGUAJE NATURAL
# ──────────────────────────────────────────────────────────────────────────
# Estas funciones permiten a SARA crear, mover, copiar, renombrar, listar
# y medir archivos/carpetas usando lenguaje natural como entrada.
# Todas usan perceptor.py para verificación previa y posterior.
# Las operaciones destructivas (eliminar) siempre pasan por zona amarilla.
# Las operaciones de solo lectura (listar, medir) son zona blanca.

def _resolver_ruta_natural(ruta_texto: str) -> str:
    """
    Convierte nombres naturales de carpetas a rutas reales del sistema.
    Ej: "escritorio" → "C:\\Users\\Sergio\\Desktop"
        "descargas/informe.pdf" → "C:\\Users\\Sergio\\Downloads\\informe.pdf"

    Si la ruta ya es absoluta o relativa válida, la retorna sin cambios.
    Nunca lanza excepciones.
    """
    try:
        from config import RUTAS_NATURALES
    except Exception:
        RUTAS_NATURALES = {
            "escritorio": "Desktop", "desktop": "Desktop",
            "documentos": "Documents", "descargas": "Downloads",
            "downloads": "Downloads", "imagenes": "Pictures",
            "musica": "Music", "videos": "Videos",
        }

    if not ruta_texto or not isinstance(ruta_texto, str):
        return ruta_texto or ""

    ruta_strip = ruta_texto.strip().strip('"').strip("'")

    # Ya es ruta absoluta — no transformar
    if os.path.isabs(ruta_strip) or (len(ruta_strip) > 1 and ruta_strip[1] == ":"):
        return ruta_strip

    home = os.path.expanduser("~")
    partes = ruta_strip.replace("\\", "/").split("/", 1)
    nombre_base = partes[0].strip().lower()

    # Quitar artículos comunes antes de buscar
    for articulo in ("la ", "el ", "los ", "las ", "mi ", "mis "):
        if nombre_base.startswith(articulo):
            nombre_base = nombre_base[len(articulo):]

    if nombre_base in RUTAS_NATURALES:
        carpeta_real = os.path.join(home, RUTAS_NATURALES[nombre_base])
        # Reincorporar el resto de la ruta si había subdirectorios
        if len(partes) > 1 and partes[1]:
            carpeta_real = os.path.join(carpeta_real, partes[1])
        return carpeta_real

    # Intentar como ruta relativa al home
    ruta_home = os.path.join(home, ruta_strip)
    if os.path.exists(ruta_home):
        return ruta_home

    return ruta_strip   # devolver como vino si no se pudo resolver


def crear_carpeta(ruta: str) -> dict:
    """
    Crea una carpeta en la ruta indicada. Crea directorios intermedios si
    no existen (makedirs). Zona amarilla — requiere confirmación.

    Args:
        ruta: Ruta destino. Acepta nombres naturales ("escritorio/Proyectos").

    Ejemplos de uso natural:
        "crea una carpeta Proyectos en el escritorio"
        "crea la carpeta backup en documentos"
    """
    ruta_real = _resolver_ruta_natural(ruta)

    if os.path.exists(ruta_real):
        return _resultado(True,
                          f"La carpeta '{os.path.basename(ruta_real)}' ya existe en {os.path.dirname(ruta_real)}.",
                          "shell_archivo")

    confirmado = _solicitar_confirmacion(
        f"crear la carpeta '{os.path.basename(ruta_real)}' en {os.path.dirname(ruta_real)}"
    )
    if not confirmado:
        return _resultado(False, "Creación cancelada.", "shell_cancelado", {"cancelado": True})

    try:
        os.makedirs(ruta_real, exist_ok=True)
        _log("info", f"Carpeta creada: {ruta_real}")
        return _resultado(True,
                          f"✅ Carpeta '{os.path.basename(ruta_real)}' creada correctamente.",
                          "shell_archivo")
    except PermissionError:
        return _resultado(False, f"Sin permisos para crear la carpeta en '{ruta_real}'.", "shell_error")
    except Exception as e:
        return _resultado(False, f"No pude crear la carpeta: {e}", "shell_error")


def mover_archivo(origen: str, destino: str) -> dict:
    """
    Mueve un archivo o carpeta de origen a destino. Zona amarilla.
    Si destino es una carpeta existente, mueve el archivo dentro de ella.
    Si destino no existe como carpeta, renombra/mueve a esa ruta exacta.

    Args:
        origen:  Ruta del archivo/carpeta a mover. Acepta nombres naturales.
        destino: Ruta de destino. Acepta nombres naturales.

    Ejemplos:
        mover_archivo("descargas/informe.pdf", "documentos")
        mover_archivo("escritorio/notas.txt", "documentos/archivados")
    """
    origen_real  = _resolver_ruta_natural(origen)
    destino_real = _resolver_ruta_natural(destino)

    if not os.path.exists(origen_real):
        return _resultado(False, f"No encontré el origen: '{origen_real}'", "shell_error")

    # Si destino es carpeta existente, mover dentro de ella
    if os.path.isdir(destino_real):
        destino_final = os.path.join(destino_real, os.path.basename(origen_real))
    else:
        destino_final = destino_real

    if os.path.exists(destino_final):
        return _resultado(False,
                          f"Ya existe '{os.path.basename(destino_final)}' en el destino. "
                          f"Renómbralo primero si quieres reemplazarlo.",
                          "shell_error")

    confirmado = _solicitar_confirmacion(
        f"mover '{os.path.basename(origen_real)}' → '{destino_real}'"
    )
    if not confirmado:
        return _resultado(False, "Movimiento cancelado.", "shell_cancelado", {"cancelado": True})

    try:
        shutil.move(origen_real, destino_final)
        _log("info", f"Movido: {origen_real} → {destino_final}")
        return _resultado(True,
                          f"✅ '{os.path.basename(origen_real)}' movido a '{destino_real}' correctamente.",
                          "shell_archivo")
    except PermissionError:
        return _resultado(False, "Sin permisos para mover ese archivo.", "shell_error")
    except Exception as e:
        return _resultado(False, f"No pude mover el archivo: {e}", "shell_error")


def copiar_archivo(origen: str, destino: str) -> dict:
    """
    Copia un archivo o carpeta completa a destino. Zona amarilla.
    Para carpetas usa shutil.copytree; para archivos, shutil.copy2
    (preserva metadatos de fecha/permisos).

    Args:
        origen:  Ruta del archivo/carpeta origen. Acepta nombres naturales.
        destino: Ruta destino. Acepta nombres naturales.

    Ejemplos:
        copiar_archivo("documentos/config.py", "escritorio/backup")
        copiar_archivo("descargas/proyecto", "documentos/proyectos")
    """
    origen_real  = _resolver_ruta_natural(origen)
    destino_real = _resolver_ruta_natural(destino)

    if not os.path.exists(origen_real):
        return _resultado(False, f"No encontré el origen: '{origen_real}'", "shell_error")

    es_carpeta = os.path.isdir(origen_real)

    if os.path.isdir(destino_real):
        destino_final = os.path.join(destino_real, os.path.basename(origen_real))
    else:
        destino_final = destino_real

    confirmado = _solicitar_confirmacion(
        f"copiar '{os.path.basename(origen_real)}' → '{destino_real}'"
    )
    if not confirmado:
        return _resultado(False, "Copia cancelada.", "shell_cancelado", {"cancelado": True})

    try:
        if es_carpeta:
            shutil.copytree(origen_real, destino_final)
        else:
            os.makedirs(os.path.dirname(destino_final), exist_ok=True)
            shutil.copy2(origen_real, destino_final)

        _log("info", f"Copiado: {origen_real} → {destino_final}")
        tipo_str = "Carpeta" if es_carpeta else "Archivo"
        return _resultado(True,
                          f"✅ {tipo_str} '{os.path.basename(origen_real)}' copiado correctamente.",
                          "shell_archivo")
    except PermissionError:
        return _resultado(False, "Sin permisos para copiar.", "shell_error")
    except Exception as e:
        return _resultado(False, f"No pude copiar: {e}", "shell_error")


def renombrar_archivo(ruta: str, nuevo_nombre: str) -> dict:
    """
    Renombra un archivo o carpeta. Zona amarilla.
    El nuevo nombre puede ser solo el nombre (sin ruta) o una ruta completa.
    El archivo renombrado queda en la misma carpeta que el original.

    Args:
        ruta:         Ruta actual del archivo/carpeta. Acepta nombres naturales.
        nuevo_nombre: Nuevo nombre (solo el nombre base, sin ruta completa).

    Ejemplos:
        renombrar_archivo("escritorio/informe.docx", "informe_final.docx")
        renombrar_archivo("documentos/proyecto viejo", "proyecto_2025")
    """
    ruta_real = _resolver_ruta_natural(ruta)

    if not os.path.exists(ruta_real):
        return _resultado(False, f"No encontré: '{ruta_real}'", "shell_error")

    # Si nuevo_nombre tiene separadores de ruta, tomar solo el nombre base
    nombre_base_nuevo = os.path.basename(nuevo_nombre.strip())
    if not nombre_base_nuevo:
        return _resultado(False, "El nuevo nombre no puede estar vacío.", "shell_error")

    directorio_padre = os.path.dirname(ruta_real)
    ruta_destino = os.path.join(directorio_padre, nombre_base_nuevo)

    if os.path.exists(ruta_destino):
        return _resultado(False,
                          f"Ya existe un archivo/carpeta llamado '{nombre_base_nuevo}' en esa ubicación.",
                          "shell_error")

    confirmado = _solicitar_confirmacion(
        f"renombrar '{os.path.basename(ruta_real)}' → '{nombre_base_nuevo}'"
    )
    if not confirmado:
        return _resultado(False, "Renombrado cancelado.", "shell_cancelado", {"cancelado": True})

    try:
        os.rename(ruta_real, ruta_destino)
        _log("info", f"Renombrado: {ruta_real} → {ruta_destino}")
        return _resultado(True,
                          f"✅ '{os.path.basename(ruta_real)}' renombrado a '{nombre_base_nuevo}'.",
                          "shell_archivo")
    except PermissionError:
        return _resultado(False, "Sin permisos para renombrar.", "shell_error")
    except Exception as e:
        return _resultado(False, f"No pude renombrar: {e}", "shell_error")


def eliminar_archivo(ruta: str, forzar: bool = False) -> dict:
    """
    Elimina un archivo o carpeta. SIEMPRE zona amarilla — requiere confirmación
    explícita. Para carpetas usa shutil.rmtree.
    Nunca elimina raíces de sistema (C:\\, C:\\Windows, C:\\Users, etc.)

    Args:
        ruta:   Ruta a eliminar. Acepta nombres naturales.
        forzar: Si True, omite la confirmación (solo para uso interno de tests).

    Ejemplos:
        eliminar_archivo("escritorio/borrador.txt")
        eliminar_archivo("descargas/carpeta_temp")
    """
    ruta_real = _resolver_ruta_natural(ruta)

    if not os.path.exists(ruta_real):
        return _resultado(False, f"No encontré: '{ruta_real}'", "shell_error")

    # Protección: nunca eliminar raíces críticas del sistema
    RUTAS_PROTEGIDAS = {
        os.path.expanduser("~"),
        "C:\\", "C:\\Windows", "C:\\Users",
        "C:\\Program Files", "C:\\Program Files (x86)",
        os.path.expanduser("~/Documents"),
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("~/Downloads"),
    }
    ruta_norm = os.path.normpath(ruta_real)
    if ruta_norm in {os.path.normpath(p) for p in RUTAS_PROTEGIDAS}:
        return _resultado(False,
                          f"No puedo eliminar '{ruta_norm}' — es una carpeta protegida del sistema.",
                          "shell_bloqueado", {"bloqueado": True})

    es_carpeta = os.path.isdir(ruta_real)
    tipo_str = "carpeta" if es_carpeta else "archivo"

    if not forzar:
        confirmado = _solicitar_confirmacion(
            f"ELIMINAR el {tipo_str} '{os.path.basename(ruta_real)}' de '{os.path.dirname(ruta_real)}'"
        )
        if not confirmado:
            return _resultado(False, "Eliminación cancelada.", "shell_cancelado", {"cancelado": True})

    try:
        if es_carpeta:
            shutil.rmtree(ruta_real)
        else:
            os.remove(ruta_real)

        _log("info", f"Eliminado: {ruta_real}")
        return _resultado(True,
                          f"✅ {tipo_str.capitalize()} '{os.path.basename(ruta_real)}' eliminado.",
                          "shell_archivo")
    except PermissionError:
        return _resultado(False,
                          f"Sin permisos para eliminar '{os.path.basename(ruta_real)}'. "
                          "Puede estar en uso o protegido.",
                          "shell_error")
    except Exception as e:
        return _resultado(False, f"No pude eliminar: {e}", "shell_error")


def listar_carpeta(ruta: str, mostrar_ocultos: bool = False, limite: int = 50) -> dict:
    """
    Lista el contenido de una carpeta con detalles básicos.
    Zona blanca — sin confirmación. Nunca modifica nada.

    Args:
        ruta:           Ruta de la carpeta. Acepta nombres naturales.
        mostrar_ocultos: Si True, incluye archivos que empiezan con punto.
        limite:         Máximo de elementos a listar (evita floods en carpetas grandes).

    Ejemplos:
        listar_carpeta("escritorio")
        listar_carpeta("documentos/proyectos")
        listar_carpeta("descargas", limite=20)
    """
    ruta_real = _resolver_ruta_natural(ruta)

    if not os.path.exists(ruta_real):
        return _resultado(False, f"No encontré la carpeta: '{ruta_real}'", "shell_error")

    if not os.path.isdir(ruta_real):
        return _resultado(False, f"'{ruta_real}' no es una carpeta.", "shell_error")

    try:
        entradas = os.listdir(ruta_real)
        if not mostrar_ocultos:
            entradas = [e for e in entradas if not e.startswith(".")]

        entradas.sort(key=lambda x: (not os.path.isdir(os.path.join(ruta_real, x)), x.lower()))

        carpetas, archivos = [], []
        for entrada in entradas[:limite]:
            ruta_entrada = os.path.join(ruta_real, entrada)
            if os.path.isdir(ruta_entrada):
                carpetas.append(f"📁 {entrada}/")
            else:
                try:
                    tam = os.path.getsize(ruta_entrada)
                    tam_str = (f"{tam/1024/1024:.1f} MB" if tam > 1024*1024
                               else f"{tam/1024:.1f} KB" if tam > 1024
                               else f"{tam} B")
                    ext = os.path.splitext(entrada)[1].lower()
                    icono = {"py": "🐍", "docx": "📝", "pdf": "📄", "xlsx": "📊",
                             "txt": "📃", "mp3": "🎵", "mp4": "🎬", "jpg": "🖼",
                             "png": "🖼", "zip": "📦", "exe": "⚙"}.get(ext.lstrip("."), "📄")
                    archivos.append(f"{icono} {entrada}  ({tam_str})")
                except Exception:
                    archivos.append(f"📄 {entrada}")

        total = len(os.listdir(ruta_real))
        truncado = total > limite
        lineas = (carpetas + archivos)
        resumen = (f"Contenido de '{os.path.basename(ruta_real)}' "
                   f"({len(carpetas)} carpetas, {len(archivos)} archivos"
                   f"{', mostrando primeros ' + str(limite) if truncado else ''}):\n"
                   + "\n".join(lineas))

        return _resultado(True, resumen, "shell_info",
                          {"carpetas": len(carpetas), "archivos": len(archivos),
                           "total": total, "truncado": truncado})

    except PermissionError:
        return _resultado(False, f"Sin permisos para leer '{ruta_real}'.", "shell_error")
    except Exception as e:
        return _resultado(False, f"No pude listar la carpeta: {e}", "shell_error")


def medir_tamanio(ruta: str) -> dict:
    """
    Calcula el tamaño de un archivo o carpeta (recursivo).
    Zona blanca — sin confirmación. Solo lectura.

    Args:
        ruta: Archivo o carpeta a medir. Acepta nombres naturales.

    Ejemplos:
        medir_tamanio("documentos")
        medir_tamanio("escritorio/proyecto.zip")
        medir_tamanio("descargas")
    """
    ruta_real = _resolver_ruta_natural(ruta)

    if not os.path.exists(ruta_real):
        return _resultado(False, f"No encontré: '{ruta_real}'", "shell_error")

    try:
        if os.path.isfile(ruta_real):
            tam = os.path.getsize(ruta_real)
            num_items = 1
        else:
            tam = 0
            num_items = 0
            for dirpath, dirnames, filenames in os.walk(ruta_real):
                for f in filenames:
                    try:
                        tam += os.path.getsize(os.path.join(dirpath, f))
                        num_items += 1
                    except Exception:
                        pass

        if tam >= 1024 ** 3:
            tam_str = f"{tam/1024**3:.2f} GB"
        elif tam >= 1024 ** 2:
            tam_str = f"{tam/1024**2:.1f} MB"
        elif tam >= 1024:
            tam_str = f"{tam/1024:.1f} KB"
        else:
            tam_str = f"{tam} bytes"

        nombre_display = os.path.basename(ruta_real) or ruta_real
        tipo_str = "archivo" if os.path.isfile(ruta_real) else f"carpeta ({num_items} archivos)"
        mensaje = f"'{nombre_display}' ({tipo_str}): {tam_str}"

        return _resultado(True, mensaje, "shell_info",
                          {"bytes": tam, "legible": tam_str, "items": num_items})

    except PermissionError:
        return _resultado(False, "Sin permisos para medir esa ruta.", "shell_error")
    except Exception as e:
        return _resultado(False, f"No pude calcular el tamaño: {e}", "shell_error")


def gestionar_archivo(texto_usuario: str) -> dict:
    """
    Punto de entrada unificado para gestión de archivos desde lenguaje natural.
    brain.py lo llama cuando intent_router devuelve CAT_GESTIONAR_ARCHIVO.

    Analiza el texto y despacha a la función correspondiente.
    Usa heurísticas simples de parseo — no NLP pesado.

    Args:
        texto_usuario: Entrada normalizada del usuario.

    Ejemplos manejados:
        "crea una carpeta Proyectos en el escritorio"
        "mueve informe.pdf de descargas a documentos"
        "renombra notas.txt como notas_2025.txt"
        "cuánto pesa la carpeta de videos"
        "lista los archivos del escritorio"
        "copia config.py a la carpeta backup"
        "elimina el archivo borrador.txt del escritorio"
    """
    try:
        from utils import normalizar_texto
        texto_norm = normalizar_texto(texto_usuario)
    except Exception:
        texto_norm = texto_usuario.lower().strip()

    # ── LISTAR ────────────────────────────────────────────────────────
    for patron in ("lista el contenido de", "lista los archivos de", "lista la carpeta",
                   "lista el contenido", "que hay en", "qué hay en",
                   "muestra el contenido de", "que contiene", "qué contiene"):
        if patron in texto_norm:
            ruta_raw = texto_norm.split(patron, 1)[-1].strip()
            return listar_carpeta(ruta_raw)

    # ── MEDIR TAMAÑO ──────────────────────────────────────────────────
    for patron in ("cuanto pesa", "cuánto pesa", "tamaño de", "peso de",
                   "que tan grande es", "qué tan grande es", "espacio ocupa"):
        if patron in texto_norm:
            ruta_raw = texto_norm.split(patron, 1)[-1].strip()
            for art in ("la carpeta ", "el archivo ", "la ", "el ", "los ", "las "):
                if ruta_raw.startswith(art):
                    ruta_raw = ruta_raw[len(art):]
            return medir_tamanio(ruta_raw)

    # ── CREAR CARPETA ─────────────────────────────────────────────────
    for patron in ("crea la carpeta", "crea una carpeta", "crea el directorio",
                   "crear la carpeta", "crear una carpeta", "nueva carpeta"):
        if patron in texto_norm:
            resto = texto_norm.split(patron, 1)[-1].strip()
            # Detectar destino: "llamada X en Y" o "X en Y"
            if " en " in resto:
                partes = resto.split(" en ", 1)
                nombre_carpeta = partes[0].replace("llamada", "").replace("llamado", "").strip()
                destino_base   = partes[1].strip()
                ruta_destino   = _resolver_ruta_natural(destino_base)
                ruta_completa  = os.path.join(ruta_destino, nombre_carpeta)
            else:
                nombre_carpeta = resto.replace("llamada", "").replace("llamado", "").strip()
                ruta_completa  = _resolver_ruta_natural(nombre_carpeta)
            return crear_carpeta(ruta_completa)

    # ── RENOMBRAR ─────────────────────────────────────────────────────
    for patron in ("renombra", "renombrar", "cambia el nombre de", "cambia nombre"):
        if patron in texto_norm:
            resto = texto_norm.split(patron, 1)[-1].strip()
            for conector in (" como ", " a ", " por ", " llamarlo ", " llamarle "):
                if conector in resto:
                    partes = resto.split(conector, 1)
                    origen_raw    = partes[0].strip()
                    nuevo_raw     = partes[1].strip()
                    for art in ("el archivo ", "la carpeta ", "el ", "la "):
                        if origen_raw.startswith(art):
                            origen_raw = origen_raw[len(art):]
                    return renombrar_archivo(origen_raw, nuevo_raw)
            break

    # ── MOVER ─────────────────────────────────────────────────────────
    for patron in ("mueve", "mover", "traslada", "trasladar"):
        if texto_norm.startswith(patron):
            resto = texto_norm[len(patron):].strip()
            for art in ("el archivo ", "la carpeta ", "el ", "la "):
                if resto.startswith(art):
                    resto = resto[len(art):]
            for conector in (" a ", " hacia ", " hasta ", " en "):
                if conector in resto:
                    partes = resto.split(conector, 1)
                    origen_raw  = partes[0].strip()
                    destino_raw = partes[1].strip()
                    # Limpiar "de X" si viene: "mueve informe de descargas a documentos"
                    for prep in (" de ", " desde "):
                        if prep in origen_raw:
                            sub = origen_raw.split(prep)
                            origen_real_raw = _resolver_ruta_natural(sub[-1].strip())
                            nombre_archivo  = sub[0].strip()
                            origen_raw = os.path.join(origen_real_raw, nombre_archivo)
                    return mover_archivo(origen_raw, destino_raw)
            break

    # ── COPIAR ────────────────────────────────────────────────────────
    for patron in ("copia", "copiar", "duplica", "duplicar"):
        if texto_norm.startswith(patron):
            resto = texto_norm[len(patron):].strip()
            for art in ("el archivo ", "la carpeta ", "el ", "la "):
                if resto.startswith(art):
                    resto = resto[len(art):]
            for conector in (" a ", " hacia ", " en ", " dentro de "):
                if conector in resto:
                    partes = resto.split(conector, 1)
                    return copiar_archivo(partes[0].strip(), partes[1].strip())
            break

    # ── ELIMINAR ─────────────────────────────────────────────────────
    for patron in ("elimina", "eliminar", "borra", "borrar", "borra el", "elimina el"):
        if texto_norm.startswith(patron):
            resto = texto_norm[len(patron):].strip()
            for art in ("el archivo ", "la carpeta ", "el ", "la "):
                if resto.startswith(art):
                    resto = resto[len(art):]
            # Extraer ubicación si viene: "borra notas.txt del escritorio"
            for prep in (" del ", " de la ", " de los ", " en ", " de "):
                if prep in resto:
                    partes = resto.split(prep, 1)
                    nombre = partes[0].strip()
                    carpeta_base = _resolver_ruta_natural(partes[1].strip())
                    ruta_completa = os.path.join(carpeta_base, nombre)
                    return eliminar_archivo(ruta_completa)
            return eliminar_archivo(resto)

    # ── No reconocido — intentar como comando shell genérico ─────────
    _log("warning", f"gestionar_archivo no reconoció el patrón: '{texto_norm[:60]}'")
    return _resultado(False,
                      f"No entendí qué quieres hacer con el archivo. "
                      f"Prueba siendo más específico: 'crea una carpeta X en escritorio', "
                      f"'mueve archivo.txt a documentos', etc.",
                      "shell_info")
# ──────────────────────────────────────────────────────────────────────────
# DIAGNÓSTICO GENERAL DEL SISTEMA (para "¿cómo estás?" dirigido a SARA)
# ──────────────────────────────────────────────────────────────────────────

def diagnostico_sistema() -> dict:
    """
    Reporte rápido del estado del sistema: RAM, disco, batería, Ollama.
    Pensado para responder a "SARA, ¿cómo estás?" con datos reales del entorno.
    Combina perceptor.py y shell.py para un reporte completo en 1-2 segundos.
    """
    lineas: list[str] = []
    alertas: list[str] = []

    # RAM
    try:
        r = info_ram()
        if r["exito"]:
            lineas.append(f"🧠 {r['mensaje']}")
    except Exception:
        pass

    # Disco C:
    try:
        r = info_disco("C:")
        if r["exito"]:
            lineas.append(f"💾 {r['mensaje']}")
            if r.get("alerta_disco"):
                alertas.append("espacio en disco crítico")
    except Exception:
        pass

    # Batería
    try:
        r = info_bateria()
        if r["exito"] and "escritorio" not in r["mensaje"].lower():
            lineas.append(f"🔋 {r['mensaje']}")
            if r.get("alerta_bateria"):
                alertas.append("batería baja")
    except Exception:
        pass
    # GPU (breve)
    try:
        r = info_gpu()
        if r["exito"]:
            primera_gpu = r["mensaje"].split("\n")[0]
            lineas.append(f"🎮 {primera_gpu}")
    except Exception:
        pass

    # Red activa + conectividad
    try:
        r = info_red_extendida()
        if r["exito"]:
            primera_red = r["mensaje"].split("\n")[0]
            lineas.append(f"🌐 {primera_red}")
    except Exception:
        pass

    try:
        # Ping rápido con 1 solo host y timeout corto para no bloquear el diagnóstico
        import subprocess as _sp
        proc = _sp.run(
            ["ping", "-n", "1", "-w", "1500", "8.8.8.8"],
            capture_output=True, timeout=3
        )
        hay_internet = proc.returncode == 0
        estado_internet = "✅ con internet" if hay_internet else "❌ sin internet"
        lineas.append(f"📡 Conectividad: {estado_internet}")
        if not hay_internet:
            alertas.append("sin conexión a internet")
    except Exception:
        pass
    # Ollama
    try:
        import perceptor
        r = perceptor.ollama_esta_vivo()
        estado_ollama = "✅ activo" if r["exito"] else "❌ no responde"
        lineas.append(f"🤖 Ollama/Qwen: {estado_ollama}")
        if not r["exito"]:
            alertas.append("Ollama no responde")
    except Exception:
        pass

    if not lineas:
        return _resultado(False, "No pude recopilar el estado del sistema.", "shell_diagnostico")

    resumen = "\n".join(lineas)
    if alertas:
        resumen += f"\n⚠ Alertas: {', '.join(alertas)}."

    return _resultado(True, resumen, "shell_diagnostico", {"alertas": alertas})