# -*- coding: utf-8 -*-
"""
sentinel.py — Observador proactivo del sistema para SARA v0.4.0.
Subsistema PRAXIS — módulo: El Instinto.

Responsabilidad principal:
    Vigilar en segundo plano el estado del equipo (RAM, disco, batería,
    procesos, Ollama) y avisar a SARA cuando algo merece la atención del
    usuario — SIN que el usuario tenga que preguntar primero. Es la pieza
    que hace que SARA se sienta presente, no solo reactiva.

Filosofía (la línea que separa "asistente" de "presencia"):
    perceptor.py responde "¿está bien esto?" cuando se le pregunta.
    shell.py ejecuta cuando se le ordena.
    sentinel.py es el único de los cuatro módulos de PRAXIS que actúa
    sin que nadie lo invoque: corre solo, en un hilo daemon de baja
    frecuencia, y decide POR SÍ MISMO cuándo algo es noticia.

Regla de oro de diseño (innegociable):
    sentinel.py NUNCA actúa sobre el sistema. Solo observa y reporta.
    Si una señal vigilada sugiere una acción correctiva (cerrar un
    proceso colgado, instalar una actualización), sentinel.py se limita
    a EMITIR el aviso con una sugerencia en texto — la ejecución, si el
    usuario la aprueba, pasa siempre por shell.ejecutar_controlado(),
    que aplica su propia clasificación de riesgo y confirmación. Este
    módulo jamás llama a una función de shell.py que modifique estado.

Cómo evita ruido y falsos avisos repetidos:
    Cada categoría de alerta tiene:
        - Un umbral de activación (ej. disco < 10% libre).
        - Un cooldown propio: una vez emitida, no se repite hasta que
          pase _COOLDOWN_SEGUNDOS, incluso si la condición persiste.
        - Una condición de "alerta resuelta": si la métrica vuelve a la
          normalidad, el cooldown se reinicia, así que si vuelve a
          degradarse después, se avisa de nuevo sin esperar el cooldown
          completo.

Integración prevista (no se modifica ningún módulo en este entregable):
    - server.py o sara.py inician sentinel.iniciar() en un hilo daemon,
      exactamente con el mismo patrón con el que ya se inician otros
      hilos de fondo en la arquitectura (ej. file_watcher.py).
    - sentinel.py llama internamente a perceptor.py y shell.py para
      obtener las métricas — no las reimplementa.
    - Los avisos se emiten a través de _emitir(), un wrapper que intenta
      sara._emitir() (para la GUI) y io_manager (para terminal/voz),
      degradando a logger.info() si ninguno está disponible — el mismo
      patrón de degradación elegante usado en todo PRAXIS.

Dependencias:
    - perceptor.py  (ram_disponible, espacio_disco_libre, ollama_esta_vivo,
                      app_esta_corriendo — todas ya validadas en este chat)
    - shell.py      (info_bateria — perceptor no cubre batería directamente,
                      shell.info_bateria() ya parsea y da alerta_bateria)
    - logger.py     (logging por niveles — import local)
    - threading     (hilo daemon de bajo costo, librería estándar)

Convenciones respetadas (Documento Maestro SARA v0.3.0 + PRAXIS):
    - Try/except obligatorio en cada ciclo de vigilancia: un fallo en una
      señal NUNCA debe detener el hilo completo ni las demás señales.
    - SARA arranca siempre: si perceptor o shell no están disponibles,
      sentinel.py reduce su cobertura pero no lanza excepciones fatales.
    - Costo de recursos mínimo: ciclo de baja frecuencia (configurable,
      default 45s), sin acumular estado pesado en memoria.
    - Formato de retorno estándar {"exito": bool, "mensaje": str} en las
      funciones de consulta puntual (estado_actual(), verificar_ahora()).
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

# ──────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN DE VIGILANCIA
# ──────────────────────────────────────────────────────────────────────────
# NOTA DE INTEGRACIÓN: igual que con intent_router.py, estos valores están
# pensados para vivir finalmente en config.py. Se definen aquí con nombres
# que coinciden con lo que tendrían allí, para que mover el bloque sea el
# único cambio necesario en la fase de integración.

# Intervalo entre ciclos de vigilancia. 45s es deliberadamente bajo: ninguna
# de las señales vigiladas (RAM, disco, batería, Ollama) cambia tan rápido
# como para necesitar más frecuencia, y este valor mantiene el uso de CPU
# del hilo daemon insignificante (una ráfaga de comandos cada 45s, no un
# bucle ajustado).
INTERVALO_CICLO_SEGUNDOS: float = 45.0

# Cooldown por categoría de alerta: una vez avisado, no se repite el mismo
# tipo de alerta hasta que pase este tiempo, aunque la condición persista.
# Evita que SARA repita "tu disco está lleno" cada 45 segundos.
COOLDOWN_ALERTA_SEGUNDOS: float = 600.0  # 10 minutos

# Umbrales de activación. Se mantienen alineados con los ya usados en
# perceptor.py (espacio_disco_libre marca alerta <10%, ram_disponible
# marca alerta >90% de uso) y shell.py (info_bateria marca alerta <20%),
# para que sentinel.py no introduzca un criterio distinto al ya validado.
UMBRAL_DISCO_PORCENTAJE_LIBRE: float = 10.0
UMBRAL_RAM_PORCENTAJE_USO: float = 90.0
UMBRAL_BATERIA_PORCENTAJE: float = 20.0

# Nombres de categorías de alerta — constantes para evitar strings mágicos
# dispersos, igual que CAT_* en intent_router.py.
ALERTA_DISCO            = "disco_critico"
ALERTA_RAM              = "ram_critica"
ALERTA_BATERIA          = "bateria_baja"
ALERTA_OLLAMA           = "ollama_caido"
ALERTA_PROCESO_COLGADO  = "proceso_colgado"
ALERTA_USB              = "usb_conectado"
ALERTA_CPU_APP          = "app_cpu_alta"
ALERTA_RED              = "red_sin_internet"
ALERTA_ACTUALIZACION    = "actualizacion_disponible"
ALERTA_SARA_PESADA      = "sara_uso_alto"

# Umbrales nuevos
UMBRAL_CPU_APP_PORCENTAJE: float = 40.0   # % CPU sostenido de una app para alertar
UMBRAL_SARA_CPU_PORCENTAJE: float = 40.0  # % CPU de SARA misma para alertar al usuario
UMBRAL_SARA_RAM_MB: float = 400.0         # MB RAM de SARA misma para alertar
COOLDOWN_USB_SEGUNDOS: float = 30.0       # USB: cooldown corto, es evento puntual
COOLDOWN_CPU_APP_SEGUNDOS: float = 300.0  # App CPU alta: cooldown 5 minutos
CICLOS_CPU_SOSTENIDO: int = 3             # Ciclos consecutivos alta CPU antes de alertar


# ──────────────────────────────────────────────────────────────────────────
# UTILIDAD INTERNA DE LOGGING SEGURO
# ──────────────────────────────────────────────────────────────────────────

def _log(nivel: str, mensaje: str, detalle: str = "") -> None:
    """Wrapper de logging seguro — mismo patrón que perceptor.py / shell.py."""
    try:
        import logger
        if nivel == "debug":
            logger.debug("sentinel", mensaje)
        elif nivel == "warning":
            logger.warning("sentinel", mensaje, detalle)
        elif nivel == "error":
            logger.error("sentinel", mensaje, detalle)
        else:
            logger.info("sentinel", mensaje)
    except Exception:
        pass


def _emitir(mensaje: str, tipo: str = "sentinel_alerta") -> None:
    """
    Emite un aviso proactivo hacia el usuario, intentando los canales
    disponibles en orden de preferencia:
        1. sara._emitir() — si SARA expone ese hook para la GUI (igual
           que server.py ya usa para eventos en tiempo real).
        2. io_manager — para terminal/voz, respetando el modo activo.
        3. logger.info() — degradación final: el aviso queda registrado
           aunque no llegue a ningún canal de cara al usuario.

    Esta función nunca lanza excepciones: un fallo en la emisión no debe
    detener el ciclo de vigilancia.
    """
    try:
        import sara
        if hasattr(sara, "_emitir"):
            sara._emitir(tipo, mensaje)
            return
    except Exception:
        pass

    try:
        import io_manager
        if hasattr(io_manager, "notificar"):
            io_manager.notificar(mensaje)
            return
        if hasattr(io_manager, "obtener_input"):
            # io_manager no expone un método de notificación pura en el
            # Documento Maestro — se degrada a logger sin asumir una
            # interfaz que no está confirmada.
            pass
    except Exception:
        pass

    _log("info", f"[AVISO PROACTIVO] {mensaje}")


# ──────────────────────────────────────────────────────────────────────────
# ESTADO INTERNO DEL OBSERVADOR (mínimo, en memoria, sin persistencia)
# ──────────────────────────────────────────────────────────────────────────

class _EstadoSentinel:
    """
    Mantiene el estado mínimo necesario para el control de cooldowns y
    el ciclo de vida del hilo. No persiste a disco — si SARA se reinicia,
    el historial de alertas recientes se reinicia también, lo cual es el
    comportamiento correcto (no queremos cooldowns que sobrevivan a un
    reinicio del propio asistente).
    """
    
    def __init__(self) -> None:
        self.hilo: Optional[threading.Thread] = None
        self.detener = threading.Event()
        self.activo = False
        self.ultima_alerta: dict[str, float] = {}
        self.lock = threading.Lock()

        # ── Estado para señales que requieren comparación entre ciclos ──
        # USB: set de InstanceIds conocidos en el ciclo anterior
        self.usbs_conocidos: set[str] = set()
        self.usbs_inicializados: bool = False

        # CPU por app: contador de ciclos consecutivos con CPU alta por proceso
        self.cpu_alta_ciclos: dict[str, int] = {}  # nombre_proceso → ciclos

        # Análisis de arranque: se ejecuta una sola vez al iniciar
        self.analisis_arranque_hecho: bool = False

        # Red: estado anterior de conectividad
        self.red_ok_anterior: Optional[bool] = None

        self.ciclo_actual: int = 0
        self.usb_cada_n_ciclos: int = 3
_estado = _EstadoSentinel()


def _en_cooldown(categoria: str) -> bool:
    """Verifica si una categoría de alerta sigue en cooldown."""
    with _estado.lock:
        ultima = _estado.ultima_alerta.get(categoria)
    if ultima is None:
        return False
    return (time.time() - ultima) < COOLDOWN_ALERTA_SEGUNDOS


def _marcar_alerta_emitida(categoria: str) -> None:
    """Registra el momento de la última alerta de una categoría."""
    with _estado.lock:
        _estado.ultima_alerta[categoria] = time.time()


def _limpiar_cooldown(categoria: str) -> None:
    """
    Elimina el cooldown de una categoría cuando la condición vuelve a la
    normalidad. Así, si el problema reaparece más tarde, se avisa de
    inmediato en vez de esperar el cooldown completo desde la última vez.
    """
    with _estado.lock:
        _estado.ultima_alerta.pop(categoria, None)


# ──────────────────────────────────────────────────────────────────────────
# VERIFICACIONES INDIVIDUALES — cada una usa perceptor.py / shell.py ya
# validados, nunca reimplementa la lógica de obtención de métricas.
# ──────────────────────────────────────────────────────────────────────────

def _verificar_disco() -> Optional[str]:
    """
    Retorna un mensaje de alerta si el disco está crítico, o None si todo
    está bien o si perceptor.py no está disponible.
    """
    try:
        import perceptor
        resultado = perceptor.espacio_disco_libre("C:\\")
    except Exception as e:
        _log("warning", "No se pudo verificar el disco", str(e))
        return None

    if not resultado.get("exito"):
        return None

    if resultado.get("alerta"):
        if _en_cooldown(ALERTA_DISCO):
            return None
        _marcar_alerta_emitida(ALERTA_DISCO)
        return (
            f"Tu disco está casi lleno: solo {resultado.get('libre_gb', '?')} GB "
            f"libres ({resultado.get('porcentaje_libre', '?')}%). Quizás quieras "
            f"liberar espacio."
        )

    # Condición normal — liberar el cooldown para que una futura alerta
    # no tenga que esperar el cooldown completo si el problema reaparece.
    _limpiar_cooldown(ALERTA_DISCO)
    return None


def _verificar_ram() -> Optional[str]:
    """
    Retorna un mensaje de alerta si el uso de RAM es crítico, o None.
    Requiere psutil (a través de perceptor.ram_disponible) — si no está
    disponible, perceptor ya degrada con exito=False y aquí se omite
    sin generar ruido ni excepción.
    """
    try:
        import perceptor
        resultado = perceptor.ram_disponible()
    except Exception as e:
        _log("warning", "No se pudo verificar la RAM", str(e))
        return None

    if not resultado.get("exito"):
        return None

    if resultado.get("alerta"):
        if _en_cooldown(ALERTA_RAM):
            return None
        _marcar_alerta_emitida(ALERTA_RAM)
        return (
            f"El uso de memoria está alto: {resultado.get('porcentaje_uso', '?')}% "
            f"en uso, solo {resultado.get('disponible_gb', '?')} GB disponibles."
        )

    _limpiar_cooldown(ALERTA_RAM)
    return None


def _verificar_bateria() -> Optional[str]:
    """
    Retorna un mensaje de alerta si la batería está baja, o None.

    Usa shell.info_bateria() en lugar de duplicar la lógica de WMI/PowerShell
    aquí — sentinel.py es un consumidor de las capacidades de PRAXIS, no
    una tercera implementación de las mismas consultas.
    """
    try:
        import shell
        resultado = shell.info_bateria()
    except Exception as e:
        _log("warning", "No se pudo verificar la batería", str(e))
        return None

    if not resultado.get("exito"):
        return None

    # info_bateria() retorna exito=True tanto si hay batería como si el
    # equipo es de escritorio (mensaje distinto) — solo se actúa si la
    # clave 'alerta_bateria' está explícitamente presente y en True.
    if resultado.get("alerta_bateria"):
        if _en_cooldown(ALERTA_BATERIA):
            return None
        _marcar_alerta_emitida(ALERTA_BATERIA)
        datos = resultado.get("datos_bateria", {})
        carga = datos.get("Carga", "?")
        return f"Te queda poca batería: {carga}%. Considera conectar el cargador."

    _limpiar_cooldown(ALERTA_BATERIA)
    return None


def _verificar_ollama() -> Optional[str]:
    """
    Retorna un mensaje de alerta si Ollama no está respondiendo, o None.

    Este es el caso de uso explícitamente mencionado en el diseño de
    PRAXIS: avisar antes de que el usuario note que Qwen no responde,
    en vez de que brain.py descubra el fallo solo cuando ya lo necesitaba.
    """
    try:
        import perceptor
        resultado = perceptor.ollama_esta_vivo()
    except Exception as e:
        _log("warning", "No se pudo verificar Ollama", str(e))
        return None

    if not resultado.get("exito"):
        if _en_cooldown(ALERTA_OLLAMA):
            return None
        _marcar_alerta_emitida(ALERTA_OLLAMA)
        return "Ollama no está respondiendo. El modelo Qwen local podría no estar disponible."

    _limpiar_cooldown(ALERTA_OLLAMA)
    return None

def _verificar_usb() -> Optional[str]:
    """
    Detecta dispositivos USB nuevos conectados desde el último ciclo.
    Usa Win32_PnPEntity (WMI) — más rápido que Get-PnpDevice, sin timeout.
    Primera ejecución: inicializa snapshot sin emitir alertas.
    Detecta también unidades extraíbles y dispositivos MTP (Android/iPhone).
    """
    try:
        import subprocess, json

        # ── Consulta principal: USB + WPD (celulares MTP) ────────────────
        cmd = [
            "powershell", "-NoProfile", "-Command",
            "Get-WmiObject Win32_PnPEntity -EA SilentlyContinue | "
            "Where-Object { $_.PNPClass -eq 'USB' -or $_.PNPClass -eq 'WPD' "
            "-or $_.Description -like '*Android*' "
            "-or $_.Description -like '*iPhone*' "
            "-or $_.Description -like '*MTP*' } | "
            "Where-Object Status -eq 'OK' | "
            "Select-Object DeviceID, Name, Description | ConvertTo-Json -Compress"
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=12, encoding="utf-8", errors="replace")

        salida = proc.stdout.strip()
        if not salida:
            # Sin dispositivos — inicializar snapshot vacío si es primera vez
            with _estado.lock:
                if not _estado.usbs_inicializados:
                    _estado.usbs_conocidos = set()
                    _estado.usbs_inicializados = True
            return None

        datos = json.loads(salida)
        if isinstance(datos, dict):
            datos = [datos]

        # Construir set de IDs y mapa de nombres actuales
        actuales: set[str] = set()
        nombres: dict[str, str] = {}
        for d in datos:
            did = d.get("DeviceID", "")
            if did:
                actuales.add(did)
                nombres[did] = d.get("Name") or d.get("Description") or "Dispositivo USB"

        with _estado.lock:
            if not _estado.usbs_inicializados:
                # Primera ejecución: snapshot sin alertar
                _estado.usbs_conocidos = actuales.copy()
                _estado.usbs_inicializados = True
                return None

            nuevos      = actuales - _estado.usbs_conocidos
            _estado.usbs_conocidos = actuales.copy()

        if not nuevos:
            return None

        if _en_cooldown(ALERTA_USB):
            return None

        _marcar_alerta_emitida(ALERTA_USB)

        # Filtrar hubs genéricos internos del sistema
        IGNORAR = {
            "usb root hub", "usb composite device", "generic usb hub",
            "usb hub", "intel usb", "amd usb", "xhci", "ehci"
        }
        mensajes = []
        for iid in list(nuevos)[:3]:
            nombre = nombres.get(iid, "Dispositivo USB")
            if not any(ig in nombre.lower() for ig in IGNORAR):
                mensajes.append(f"🔌 USB conectado: {nombre}")

        # Detectar si hay unidades extraíbles nuevas
        try:
            cmd_drive = [
                "powershell", "-NoProfile", "-Command",
                "Get-WmiObject Win32_LogicalDisk -EA SilentlyContinue | "
                "Where-Object DriveType -eq 2 | "
                "Select-Object DeviceID, VolumeName | ConvertTo-Json -Compress"
            ]
            proc2 = subprocess.run(cmd_drive, capture_output=True, text=True,
                                   timeout=8, encoding="utf-8", errors="replace")
            if proc2.stdout.strip():
                unidades = json.loads(proc2.stdout.strip())
                if isinstance(unidades, dict):
                    unidades = [unidades]
                for u in unidades:
                    letra = u.get("DeviceID", "")
                    vol   = u.get("VolumeName", "") or "Sin etiqueta"
                    if letra:
                        mensajes.append(
                            f"  📁 Unidad extraíble: {letra} ({vol})"
                            f" — ¿Quieres abrirla? Di 'abre {letra}'"
                        )
        except Exception:
            pass

        return "\n".join(mensajes) if mensajes else None

    except Exception as e:
        _log("warning", "No se pudo verificar USB", str(e))
        return None
def _verificar_cpu_apps() -> Optional[str]:
    """
    Detecta apps que consumen CPU de forma sostenida por CICLOS_CPU_SOSTENIDO
    ciclos consecutivos por encima de UMBRAL_CPU_APP_PORCENTAJE.
    Usa psutil para muestreo real de CPU — más preciso que wmic/tasklist.

    Excluye procesos del sistema y el propio proceso de SARA para evitar
    ruido. El contador por proceso se reinicia cuando baja el consumo,
    lo que evita alertas tardías sobre picos momentáneos legítimos.
    """
    PROCESOS_EXCLUIDOS = {
        "system", "registry", "smss.exe", "csrss.exe", "wininit.exe",
        "services.exe", "lsass.exe", "svchost.exe", "dwm.exe",
        "fontdrvhost.exe", "spoolsv.exe", "searchindexer.exe",
        "python.exe", "pythonw.exe",   # SARA misma — tiene su propia verificación
    }

    try:
        import psutil

        alto_cpu: list[tuple[str, float]] = []
        for proc in psutil.process_iter(["name", "cpu_percent", "pid"]):
            try:
                nombre = (proc.info["name"] or "").lower()
                cpu    = proc.info["cpu_percent"] or 0.0
                if nombre in PROCESOS_EXCLUIDOS:
                    continue
                # cpu_percent devuelve 0.0 en primer acceso — ignorar
                if cpu >= UMBRAL_CPU_APP_PORCENTAJE and cpu > 0.1:
                    alto_cpu.append((proc.info["name"], cpu))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Actualizar contadores de ciclos sostenidos
        with _estado.lock:
            # Incrementar ciclos para los que siguen altos
            for nombre, _ in alto_cpu:
                _estado.cpu_alta_ciclos[nombre] = _estado.cpu_alta_ciclos.get(nombre, 0) + 1
            # Reiniciar ciclos para los que bajaron
            nombres_altos = {n for n, _ in alto_cpu}
            for nombre in list(_estado.cpu_alta_ciclos.keys()):
                if nombre not in nombres_altos:
                    _estado.cpu_alta_ciclos.pop(nombre, None)
            # Obtener los que llevan CICLOS_CPU_SOSTENIDO ciclos consecutivos
            alertables = [
                (n, c) for n, c in _estado.cpu_alta_ciclos.items()
                if c >= CICLOS_CPU_SOSTENIDO
            ]

        if not alertables:
            _limpiar_cooldown(ALERTA_CPU_APP)
            return None

        if _en_cooldown(ALERTA_CPU_APP):
            return None

        # Obtener CPU real del proceso alertable
        detalles = []
        for nombre, _ in alertables[:2]:
            for proc in psutil.process_iter(["name", "cpu_percent"]):
                try:
                    if (proc.info["name"] or "").lower() == nombre.lower():
                        cpu_real = proc.info["cpu_percent"] or 0.0
                        # Ignorar si la lectura real ya bajó de umbral
                        if cpu_real < UMBRAL_CPU_APP_PORCENTAJE or cpu_real < 1.0:
                            continue
                        detalles.append(
                            f"  ⚠ {nombre}: {cpu_real:.1f}% CPU sostenido"
                        )
                        break
                except Exception:
                    pass

        if not detalles:
            return None

        with _estado.lock:
            _estado.ultima_alerta[ALERTA_CPU_APP] = time.time()

        return (
            "Detecté app(s) consumiendo CPU de forma sostenida:\n" +
            "\n".join(detalles) +
            "\n  ¿Quieres que cierre alguna? (di 'cierra [nombre del proceso]')"
        )

    except ImportError:
        return None   # psutil no disponible — degradar silenciosamente
    except Exception as e:
        _log("warning", "No se pudo verificar CPU de apps", str(e))
        return None


def _verificar_red() -> Optional[str]:
    """
    Verifica si hay conexión a internet haciendo ping a los hosts de
    HOSTS_PING_DEFAULT de config.py. Emite alerta cuando la red se pierde
    y una notificación de recuperación cuando vuelve.

    Distingue "sin internet ahora mismo" de "la red nunca estuvo disponible"
    para no alertar en equipos offline por diseño.
    """
    try:
        import subprocess

        hosts_ok = False
        try:
            from config import HOSTS_PING_DEFAULT
        except Exception:
            HOSTS_PING_DEFAULT = ["8.8.8.8", "1.1.1.1"]

        for host in HOSTS_PING_DEFAULT[:2]:
            try:
                proc = subprocess.run(
                    ["ping", "-n", "1", "-w", "1000", host],
                    capture_output=True, timeout=4
                )
                if proc.returncode == 0:
                    hosts_ok = True
                    break
            except Exception:
                continue

        with _estado.lock:
            anterior = _estado.red_ok_anterior
            _estado.red_ok_anterior = hosts_ok

        # Primera ejecución — sin alerta
        if anterior is None:
            return None

        # Red perdida
        if not hosts_ok and anterior:
            if _en_cooldown(ALERTA_RED):
                return None
            _marcar_alerta_emitida(ALERTA_RED)
            return "⚠ Sin conexión a internet — la red se perdió."

        # Red recuperada
        if hosts_ok and not anterior:
            _limpiar_cooldown(ALERTA_RED)
            return "✅ Conexión a internet restaurada."

        if hosts_ok:
            _limpiar_cooldown(ALERTA_RED)

        return None

    except Exception as e:
        _log("warning", "No se pudo verificar la red", str(e))
        return None


def _verificar_actualizaciones_windows() -> Optional[str]:
    """
    Verifica si hay actualizaciones de Windows pendientes usando
    COM/PowerShell. Se ejecuta con cooldown largo (6 horas) ya que
    las actualizaciones no cambian minuto a minuto.
    Emite solo una vez por sesión para no molestar.
    """
    COOLDOWN_UPDATES = 21600.0   # 6 horas

    with _estado.lock:
        ultima = _estado.ultima_alerta.get(ALERTA_ACTUALIZACION)
    if ultima and (time.time() - ultima) < COOLDOWN_UPDATES:
        return None

    try:
        import subprocess

        cmd = [
            "powershell", "-NoProfile", "-Command",
            "$UpdateSession = New-Object -ComObject Microsoft.Update.Session; "
            "$Searcher = $UpdateSession.CreateUpdateSearcher(); "
            "try { "
            "  $Result = $Searcher.Search('IsInstalled=0 and Type=''Software'''); "
            "  Write-Output $Result.Updates.Count "
            "} catch { Write-Output 0 }"
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=20, encoding="utf-8", errors="replace")
        salida = proc.stdout.strip()
        if not salida or not salida.isdigit():
            return None

        cantidad = int(salida)
        if cantidad > 0:
            with _estado.lock:
                _estado.ultima_alerta[ALERTA_ACTUALIZACION] = time.time()
            return (
                f"🔄 Hay {cantidad} actualización(es) de Windows pendiente(s). "
                f"Puedes instalarlas cuando gustes desde Configuración → Windows Update."
            )

        return None

    except Exception as e:
        _log("debug", "No se pudo verificar actualizaciones de Windows", str(e))
        return None


def _analisis_arranque() -> None:
    """
    Se ejecuta UNA SOLA VEZ cuando sentinel inicia. Realiza un diagnóstico
    completo del equipo donde SARA está despertando y reporta si su propia
    presencia podría ser un factor de carga relevante.

    Incluye:
        — RAM y CPU totales disponibles.
        — Uso de disco en C:.
        — Impacto estimado de SARA (proceso python.exe actual).
        — Recomendación si SARA usa más de UMBRAL_SARA_CPU_PORCENTAJE%
          de CPU o UMBRAL_SARA_RAM_MB MB de RAM de forma sostenida.
        — Mención de la futura capacidad de encriptar módulos pesados
          si los recursos son muy limitados.

    Este análisis no bloquea el inicio — corre en un sub-hilo de corta vida.
    """
    try:
        import psutil, os

        pid_actual = os.getpid()
        proc_sara = psutil.Process(pid_actual)

        # Medir impacto de SARA (sample con 1s de intervalo para CPU real)
        time.sleep(1.0)
        cpu_sara  = proc_sara.cpu_percent(interval=1.0)
        ram_sara  = proc_sara.memory_info().rss / 1024 / 1024   # MB

        # Estado general del sistema
        ram_total = psutil.virtual_memory().total / 1024 / 1024  # MB
        ram_libre = psutil.virtual_memory().available / 1024 / 1024
        cpu_total = psutil.cpu_percent(interval=1.0)
        ram_pct   = psutil.virtual_memory().percent

        lineas = [
            "🌅 SARA despertó — análisis del entorno:",
            f"  💻 CPU: {cpu_total:.1f}% en uso total | {psutil.cpu_count()} núcleos",
            f"  🧠 RAM: {ram_libre:.0f} MB libres de {ram_total:.0f} MB ({ram_pct:.1f}% en uso)",
            f"  🤖 Impacto de SARA: {cpu_sara:.1f}% CPU | {ram_sara:.0f} MB RAM",
        ]

        # Disco
        try:
            disco = psutil.disk_usage("C:\\")
            libre_gb = disco.free / 1024**3
            lineas.append(f"  💾 Disco C: {libre_gb:.1f} GB libres ({100-disco.percent:.1f}% disponible)")
        except Exception:
            pass

        # Evaluación del impacto de SARA
        advertencias = []
        if cpu_sara > UMBRAL_SARA_CPU_PORCENTAJE:
            advertencias.append(
                f"⚠ SARA está usando {cpu_sara:.1f}% de CPU — más del {UMBRAL_SARA_CPU_PORCENTAJE}% recomendado."
            )
        if ram_sara > UMBRAL_SARA_RAM_MB:
            advertencias.append(
                f"⚠ SARA está usando {ram_sara:.0f} MB de RAM — considera cerrar otras apps."
            )

        if advertencias:
            lineas.extend(advertencias)
            lineas.append(
                "  💡 En el futuro, SARA podrá encriptar módulos pesados (voz, visión) "
                "para liberar recursos y seguir operativa con los módulos esenciales."
            )
        else:
            lineas.append("  ✅ Impacto de SARA en el equipo: dentro de rangos normales.")

        # Equipo con poca RAM total — advertencia especial
        if ram_total < 4096:
            lineas.append(
                f"  ℹ️  Equipo con {ram_total:.0f} MB de RAM — SARA operará en modo conservador."
            )

        reporte = "\n".join(lineas)
        _emitir(reporte, tipo="sentinel_arranque")
        _log("info", "Análisis de arranque completado")

    except ImportError:
        _log("debug", "psutil no disponible — análisis de arranque omitido")
    except Exception as e:
        _log("warning", "Error en análisis de arranque", str(e))


def _verificar_impacto_sara() -> Optional[str]:
    """
    Verifica en cada ciclo si SARA misma está consumiendo recursos de
    forma sostenida por encima de los umbrales configurados.
    Es la conciencia de sí misma de SARA: sabe cuándo está siendo
    una carga para el equipo que la hospeda.

    Si el impacto es alto por CICLOS_CPU_SOSTENIDO ciclos, sugiere al
    usuario que evalúe encriptar módulos pesados (voz, modelos grandes).
    En esta versión la encriptación no es automática — la futura versión
    permitirá que SARA empaque módulos a .zip y se recargue sin ellos.
    """
    try:
        import psutil, os

        pid_actual = os.getpid()
        proc_sara  = psutil.Process(pid_actual)
        cpu_sara   = proc_sara.cpu_percent(interval=0.5)
        ram_sara   = proc_sara.memory_info().rss / 1024 / 1024

        impacto_alto = (
            cpu_sara > UMBRAL_SARA_CPU_PORCENTAJE or
            ram_sara > UMBRAL_SARA_RAM_MB
        )

        with _estado.lock:
            if impacto_alto:
                ciclos = _estado.cpu_alta_ciclos.get("__sara__", 0) + 1
                _estado.cpu_alta_ciclos["__sara__"] = ciclos
            else:
                _estado.cpu_alta_ciclos.pop("__sara__", None)
                ciclos = 0

        if ciclos < CICLOS_CPU_SOSTENIDO:
            if not impacto_alto:
                _limpiar_cooldown(ALERTA_SARA_PESADA)
            return None

        if _en_cooldown(ALERTA_SARA_PESADA):
            return None

        _marcar_alerta_emitida(ALERTA_SARA_PESADA)

        partes = []
        if cpu_sara > UMBRAL_SARA_CPU_PORCENTAJE:
            partes.append(f"{cpu_sara:.1f}% CPU")
        if ram_sara > UMBRAL_SARA_RAM_MB:
            partes.append(f"{ram_sara:.0f} MB RAM")

        return (
            f"🤖 SARA está consumiendo {' y '.join(partes)} de forma sostenida.\n"
            f"  Módulos activos más pesados: voz (Vosk/Whisper), embeddings (sentence-transformers).\n"
            f"  💡 En el futuro podrás decir 'desactiva el modo voz' o 'modo ligero' para "
            f"que SARA encripte módulos pesados y libere recursos."
        )

    except ImportError:
        return None
    except Exception as e:
        _log("debug", "No se pudo verificar impacto de SARA", str(e))
        return None
def _verificar_procesos_colgados(
    nombres_vigilados: tuple[str, ...] = ("chrome.exe", "explorer.exe"),
) -> Optional[str]:
    """
    Detecta procesos de la lista de vigilancia que consumen CPU alta de
    forma sostenida — señal de posible cuelgue o mal comportamiento.
    Usa el mismo mecanismo de ciclos sostenidos que _verificar_cpu_apps()
    pero focalizado en procesos críticos que el usuario querrá saber
    específicamente (navegador, explorador, etc.).

    Emite una sugerencia de reinicio — no ejecuta nada directamente.
    """
    try:
        import psutil

        for nombre in nombres_vigilados:
            for proc in psutil.process_iter(["name", "cpu_percent", "status"]):
                try:
                    if (proc.info["name"] or "").lower() == nombre.lower():
                        cpu = proc.info["cpu_percent"] or 0.0
                        if cpu > UMBRAL_CPU_APP_PORCENTAJE:
                            key = f"colgado_{nombre}"
                            with _estado.lock:
                                ciclos = _estado.cpu_alta_ciclos.get(key, 0) + 1
                                _estado.cpu_alta_ciclos[key] = ciclos
                            if ciclos >= CICLOS_CPU_SOSTENIDO:
                                if not _en_cooldown(f"colgado_{nombre}"):
                                    with _estado.lock:
                                        _estado.ultima_alerta[f"colgado_{nombre}"] = time.time()
                                    return (
                                        f"⚠ '{nombre}' lleva varios ciclos con CPU alta ({cpu:.1f}%). "
                                        f"Podría estar colgado.\n"
                                        f"  ¿Quieres cerrarlo? Di 'cierra {nombre}'."
                                    )
                        else:
                            with _estado.lock:
                                _estado.cpu_alta_ciclos.pop(f"colgado_{nombre}", None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
    except ImportError:
        pass
    except Exception as e:
        _log("debug", "No se pudo verificar procesos colgados", str(e))
    return None


# Registro de verificaciones activas. Añadir una nueva señal de vigilancia
# es: escribir la función _verificar_X() siguiendo el mismo contrato
# (retorna str si hay alerta, None si no) y añadirla a esta tupla.
_VERIFICACIONES: tuple[Callable[[], Optional[str]], ...] = (
    _verificar_disco,
    _verificar_ram,
    _verificar_bateria,
    _verificar_ollama,
    _verificar_red,
    _verificar_usb,
    _verificar_cpu_apps,
    _verificar_procesos_colgados,
    _verificar_impacto_sara,
    _verificar_actualizaciones_windows,
)


# ──────────────────────────────────────────────────────────────────────────
# CICLO PRINCIPAL DEL HILO DAEMON
# ──────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────
# CICLO PRINCIPAL DEL HILO DAEMON
# ──────────────────────────────────────────────────────────────────────────

def _ciclo_vigilancia() -> None:
    """
    Cuerpo del hilo daemon. Ejecuta todas las verificaciones registradas
    en cada iteración, emite los avisos que correspondan, y duerme
    INTERVALO_CICLO_SEGUNDOS antes de repetir.
    """
    _log("info", f"Sentinel iniciado — ciclo cada {INTERVALO_CICLO_SEGUNDOS}s")

    # ── Análisis de arranque (una sola vez, en hilo aparte para no bloquear) ──
    if not _estado.analisis_arranque_hecho:
        with _estado.lock:
            _estado.analisis_arranque_hecho = True
        hilo_arranque = threading.Thread(
            target=_analisis_arranque,
            name="SARA-Sentinel-Arranque",
            daemon=True
        )
        hilo_arranque.start()

    while not _estado.detener.is_set():
        with _estado.lock:
            _estado.ciclo_actual += 1
            ciclo = _estado.ciclo_actual

        # 1. Ejecuta las verificaciones del sistema estándar
        for verificacion in _VERIFICACIONES:
            # USB: solo cada N ciclos para evitar timeout repetido
            if verificacion.__name__ == "_verificar_usb" and ciclo % _estado.usb_cada_n_ciclos != 0:
                continue
            # Actualizaciones: solo cada 8 ciclos (~6 min)
            if verificacion.__name__ == "_verificar_actualizaciones_windows" and ciclo % 8 != 0:
                continue
            try:
                mensaje_alerta = verificacion()
                if mensaje_alerta:
                    _emitir(mensaje_alerta)
                    _log("info", f"Alerta proactiva emitida: {mensaje_alerta[:80]}")
            except Exception as e:
                _log("error", f"Fallo en verificación {verificacion.__name__}", str(e))
                continue

        # 2. ── Recordatorios próximos ────────────────────────────────────
        # Añadido al final del bloque de verificaciones dentro del loop principal
        try:
            import productivity as _prod
            disparados = _prod.verificar_recordatorios_pendientes()
            for rec in disparados:
                _emitir(f"⏰ Recordatorio: {rec['mensaje']}")
        except Exception:
            pass

        # Espera interrumpible para el siguiente ciclo
        _estado.detener.wait(timeout=INTERVALO_CICLO_SEGUNDOS)

    _log("info", "Sentinel detenido")


# ──────────────────────────────────────────────────────────────────────────
# API PÚBLICA — CONTROL DEL CICLO DE VIDA
# ──────────────────────────────────────────────────────────────────────────

def iniciar() -> dict:
    """
    Inicia el hilo daemon de vigilancia. Idempotente: si ya está activo,
    no crea un segundo hilo — retorna el estado actual sin error.

    Pensado para ser llamado una vez durante el arranque de SARA (en
    sara.py o server.py), con el mismo patrón que otros hilos de fondo
    ya existentes en la arquitectura (ej. file_watcher.py).

    Returns:
        {"exito": bool, "mensaje": str}
    """
    try:
        with _estado.lock:
            if _estado.activo and _estado.hilo and _estado.hilo.is_alive():
                return {"exito": True, "mensaje": "Sentinel ya estaba activo."}

            _estado.detener.clear()
            _estado.hilo = threading.Thread(
                target=_ciclo_vigilancia,
                name="SARA-Sentinel",
                daemon=True,
            )
            _estado.hilo.start()
            _estado.activo = True

        return {"exito": True, "mensaje": "Sentinel iniciado correctamente."}
    except Exception as e:
        _log("error", "Fallo al iniciar sentinel", str(e))
        return {"exito": False, "mensaje": "No se pudo iniciar el observador proactivo."}


def detener() -> dict:
    """
    Detiene el hilo daemon de vigilancia de forma ordenada. Espera hasta
    2 segundos a que el ciclo actual termine, sin bloquear indefinidamente
    el cierre de SARA si el hilo está dormido a mitad del intervalo.

    Returns:
        {"exito": bool, "mensaje": str}
    """
    try:
        with _estado.lock:
            if not _estado.activo:
                return {"exito": True, "mensaje": "Sentinel ya estaba detenido."}
            _estado.detener.set()
            hilo = _estado.hilo

        if hilo:
            hilo.join(timeout=2.0)

        with _estado.lock:
            _estado.activo = False

        return {"exito": True, "mensaje": "Sentinel detenido correctamente."}
    except Exception as e:
        _log("error", "Fallo al detener sentinel", str(e))
        return {"exito": False, "mensaje": "No se pudo detener el observador proactivo limpiamente."}


def esta_activo() -> bool:
    """Retorna True si el hilo de vigilancia está corriendo actualmente."""
    with _estado.lock:
        return _estado.activo and _estado.hilo is not None and _estado.hilo.is_alive()


# ──────────────────────────────────────────────────────────────────────────
# API PÚBLICA — CONSULTA PUNTUAL (sin esperar al ciclo del hilo)
# ──────────────────────────────────────────────────────────────────────────

def verificar_ahora() -> dict:
    """
    Ejecuta todas las verificaciones inmediatamente, fuera del ciclo del
    hilo daemon, IGNORANDO los cooldowns activos. Pensado para responder
    a peticiones directas del usuario como "SARA, ¿cómo estás?" o "revisa
    el sistema ahora", donde repetir una alerta reciente es exactamente
    lo que el usuario está pidiendo, no ruido a evitar.

    A diferencia del ciclo de fondo, esta función NO marca cooldowns ni
    los modifica — es una lectura de estado, no un evento de vigilancia.

    Returns:
        {"exito": bool, "mensaje": str, "alertas": list[str]}
        "alertas" contiene los mensajes de cada señal que esté en estado
        crítico en este momento, sin filtrar por cooldown.
    """
    alertas: list[str] = []

    # Se llaman las funciones internas de obtención de datos directamente
    # (no las _verificar_*, que aplican cooldown) para que esta consulta
    # puntual siempre refleje el estado real, sin importar cooldowns.
    try:
        import perceptor

        disco = perceptor.espacio_disco_libre("C:\\")
        if disco.get("exito") and disco.get("alerta"):
            alertas.append(
                f"Disco: {disco.get('libre_gb','?')} GB libres "
                f"({disco.get('porcentaje_libre','?')}%)."
            )

        ram = perceptor.ram_disponible()
        if ram.get("exito") and ram.get("alerta"):
            alertas.append(
                f"RAM: {ram.get('porcentaje_uso','?')}% en uso, "
                f"{ram.get('disponible_gb','?')} GB disponibles."
            )

        ollama = perceptor.ollama_esta_vivo()
        if not ollama.get("exito"):
            alertas.append("Ollama no está respondiendo.")
    except Exception as e:
        _log("warning", "verificar_ahora: fallo consultando perceptor", str(e))
    
    try:
        import shell

        bateria = shell.info_bateria()
        if bateria.get("exito") and bateria.get("alerta_bateria"):
            datos = bateria.get("datos_bateria", {})
            alertas.append(f"Batería: {datos.get('Carga','?')}%.")

        # Red
        red = shell.ping_host()
        if not red.get("exito"):
            alertas.append("Sin conexión a internet en este momento.")

        # GPU
        gpu = shell.info_gpu()
        if gpu.get("exito"):
            alertas.append(f"GPU: {gpu['mensaje'].split(chr(10))[0]}")

    except Exception as e:
        _log("warning", "verificar_ahora: fallo consultando shell", str(e))

    # SARA misma
    try:
        import psutil, os
        proc_sara = psutil.Process(os.getpid())
        cpu_s = proc_sara.cpu_percent(interval=0.5)
        ram_s = proc_sara.memory_info().rss / 1024 / 1024
        if cpu_s > UMBRAL_SARA_CPU_PORCENTAJE or ram_s > UMBRAL_SARA_RAM_MB:
            alertas.append(
                f"SARA: {cpu_s:.1f}% CPU / {ram_s:.0f} MB RAM — consumo elevado."
            )
    except Exception:
        pass

    # USBs conectados actualmente
    try:
        with _estado.lock:
            n_usb = len(_estado.usbs_conocidos)
        if n_usb > 0:
            alertas.append(f"Dispositivos USB detectados actualmente: {n_usb}.")
    except Exception:
        pass

    if alertas:
        return {
            "exito": True,
            "mensaje": "Hay " + str(len(alertas)) + " punto(s) que merecen tu atención.",
            "alertas": alertas,
        }

    return {
        "exito": True,
        "mensaje": "Todo en orden — no hay alertas activas en este momento.",
        "alertas": [],
    }


def estado_actual() -> dict:
    """
    Reporta el estado del propio observador (no del sistema vigilado):
    si está activo, cuántas categorías tienen un cooldown vigente, y
    desde cuándo. Útil para depuración y para que sara.py pueda mostrar
    en la GUI si la vigilancia proactiva está encendida.

    Returns:
        {"exito": bool, "activo": bool, "cooldowns_vigentes": dict[str, float]}
        "cooldowns_vigentes" mapea categoría → segundos restantes de cooldown.
    """
    try:
        with _estado.lock:
            ahora = time.time()
            cooldowns_vigentes = {
                categoria: round(max(0.0, COOLDOWN_ALERTA_SEGUNDOS - (ahora - ts)), 1)
                for categoria, ts in _estado.ultima_alerta.items()
                if (ahora - ts) < COOLDOWN_ALERTA_SEGUNDOS
            }
            activo     = (_estado.activo and
                          _estado.hilo is not None and
                          _estado.hilo.is_alive())
            n_usb      = len(_estado.usbs_conocidos)
            cpu_ciclos = dict(_estado.cpu_alta_ciclos)
        return {
            "exito": True,
            "activo": activo,
            "cooldowns_vigentes": cooldowns_vigentes,
            "usbs_conectados": n_usb,
            "arranque_analizado": _estado.analisis_arranque_hecho,
            "cpu_ciclos_altos": cpu_ciclos,
        }
    except Exception as e:
        _log("error", "Fallo al obtener estado de sentinel", str(e))
        return {"exito": False, "activo": False, "cooldowns_vigentes": {}}
