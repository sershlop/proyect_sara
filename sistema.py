# 📁 sistema.py
"""
Módulo de funciones del sistema operativo para SARA.
Estas funciones NO se activan automáticamente - deben ser enseñadas a SARA manualmente.
"""

import inspect
import os
import sys
import platform
import psutil
import subprocess
import logger
from config import NIVEL_CONSOLA

# Detectar sistema operativo
SISTEMA = platform.system().lower()


def _obtener_interfaz_volumen():
    """Helper compartido para obtener la interfaz de volumen en Windows."""
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    from pycaw.pycaw import AudioDeviceEnumerator, EDataFlow, ERole
    from ctypes import cast, POINTER
    import comtypes
    
    try:
        # Método nuevo (pycaw >= 20231227)
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(
            IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None
        )
        return cast(interface, POINTER(IAudioEndpointVolume))
    except AttributeError:
        # Fallback para versiones muy nuevas
        enumerator = AudioDeviceEnumerator()
        device = enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender.value, ERole.eMultimedia.value)
        return device.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))
def _resultado(exito, resultados, fuente, mensaje=""):
    return {
        "exito":      exito,
        "resultados": resultados,
        "fuente":     fuente,
        "modo":       "real",
        "mensaje":    mensaje if mensaje else resultados
    }

# ── MULTIMEDIA ──────────────────────────────────────

def pausar_reproducir():
    """
    Función universal para pausar/reproducir multimedia.
    Compatible con Windows, macOS y Linux.
    """
    try:
        if SISTEMA == "windows":
            # En Windows usar teclas multimedia
            import ctypes
            VK_MEDIA_PLAY_PAUSE = 0xB3
            ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 2, 0)
            return _resultado(True, "Multimedia pausado/reproducido", "sistema")

        elif SISTEMA == "darwin":  # macOS
            # En macOS usar AppleScript
            script = '''
            tell application "System Events"
                key code 49 using {command down}  -- Space bar con command
            end tell
            '''
            subprocess.run(["osascript", "-e", script], capture_output=True)
            return _resultado(True, "Multimedia pausado/reproducido", "sistema")

        elif SISTEMA == "linux":
            # En Linux intentar varios players comunes
            players = ["rhythmbox", "vlc", "spotify", "clementine", "audacious"]
            for player in players:
                try:
                    # Intentar controlar el player
                    subprocess.run([player, "--play-pause"], capture_output=True, timeout=2)
                    return _resultado(True, f"Multimedia controlado via {player}", "sistema")
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    continue

            # Fallback: usar xdotool si está disponible
            try:
                subprocess.run(["xdotool", "key", "XF86AudioPlay"], capture_output=True, timeout=2)
                return _resultado(True, "Multimedia pausado/reproducido (xdotool)", "sistema")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return _resultado(False, "No se pudo controlar multimedia - instala un player compatible", "sistema")

        else:
            return _resultado(False, f"Sistema operativo no soportado: {SISTEMA}", "sistema")

    except Exception as e:
        logger.log_excepcion("sistema", "pausar_reproducir", e)
        return _resultado(False, f"Error al controlar multimedia: {e}", "sistema")

def siguiente_pista():
    """Saltar a la siguiente pista/cancion"""
    try:
        if SISTEMA == "windows":
            import ctypes
            VK_MEDIA_NEXT_TRACK = 0xB0
            ctypes.windll.user32.keybd_event(VK_MEDIA_NEXT_TRACK, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_MEDIA_NEXT_TRACK, 0, 2, 0)
            return _resultado(True, "Siguiente pista", "sistema")

        elif SISTEMA == "darwin":
            script = '''
            tell application "System Events"
                key code 124 using {command down}  -- Flecha derecha con command
            end tell
            '''
            subprocess.run(["osascript", "-e", script], capture_output=True)
            return _resultado(True, "Siguiente pista", "sistema")

        elif SISTEMA == "linux":
            try:
                subprocess.run(["xdotool", "key", "XF86AudioNext"], capture_output=True, timeout=2)
                return _resultado(True, "Siguiente pista", "sistema")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return _resultado(False, "xdotool no disponible para controlar multimedia", "sistema")

        return _resultado(False, f"Sistema operativo no soportado: {SISTEMA}", "sistema")

    except Exception as e:
        logger.log_excepcion("sistema", "siguiente_pista", e)
        return _resultado(False, f"Error al cambiar pista: {e}", "sistema")

def pista_anterior():
    """Volver a la pista/cancion anterior"""
    try:
        if SISTEMA == "windows":
            import ctypes
            VK_MEDIA_PREV_TRACK = 0xB1
            ctypes.windll.user32.keybd_event(VK_MEDIA_PREV_TRACK, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_MEDIA_PREV_TRACK, 0, 2, 0)
            return _resultado(True, "Pista anterior", "sistema")

        elif SISTEMA == "darwin":
            script = '''
            tell application "System Events"
                key code 123 using {command down}  -- Flecha izquierda con command
            end tell
            '''
            subprocess.run(["osascript", "-e", script], capture_output=True)
            return _resultado(True, "Pista anterior", "sistema")

        elif SISTEMA == "linux":
            try:
                subprocess.run(["xdotool", "key", "XF86AudioPrev"], capture_output=True, timeout=2)
                return _resultado(True, "Pista anterior", "sistema")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return _resultado(False, "xdotool no disponible para controlar multimedia", "sistema")

        return _resultado(False, f"Sistema operativo no soportado: {SISTEMA}", "sistema")

    except Exception as e:
        logger.log_excepcion("sistema", "pista_anterior", e)
        return _resultado(False, f"Error al cambiar pista: {e}", "sistema")

# ── CONTROL DE VOLUMEN ──────────────────────────────

def subir_volumen(cantidad=None):
    """Subir volumen del sistema. Si cantidad=None, sube 10 unidades."""
    try:
        # Si no se especifica cantidad, el salto por defecto es 10
        cantidad = cantidad or 10

        if SISTEMA == "windows":
            try:
                volume    = _obtener_interfaz_volumen()
                actual    = volume.GetMasterVolumeLevelScalar()
                nuevo     = min(1.0, actual + (cantidad / 100.0))
                volume.SetMasterVolumeLevelScalar(nuevo, None)
                return _resultado(True, f"Volumen subido a {int(nuevo * 100)}%", "sistema")
            except ImportError:
                import ctypes
                # Windows altera el volumen de 2 en 2 por cada pulsación de tecla virtual (0xAF).
                # Dividimos entre 2 para que el bucle pulse las veces necesarias para alcanzar la 'cantidad'.
                for _ in range(cantidad // 2):
                    ctypes.windll.user32.keybd_event(0xAF, 0, 0, 0)
                    ctypes.windll.user32.keybd_event(0xAF, 0, 2, 0)  # Corregido flag de liberación a 2 (KEYEVENTF_KEYUP)
                return _resultado(True, f"Volumen subido {cantidad} unidades", "sistema")

        elif SISTEMA == "darwin":
            # En macOS usar AppleScript
            script = f'''
            set volume output volume (output volume of (get volume settings) + {cantidad})
            '''
            subprocess.run(["osascript", "-e", script], capture_output=True)
            return _resultado(True, f"Volumen subido {cantidad} unidades", "sistema")

        elif SISTEMA == "linux":
            # En Linux usar amixer o pactl
            try:
                # Intentar con pactl (PulseAudio)
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{cantidad}%"],
                             capture_output=True, timeout=10)
                return _resultado(True, f"Volumen subido {cantidad}%", "sistema")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                try:
                    # Fallback con amixer (ALSA)
                    subprocess.run(["amixer", "set", "Master", f"{cantidad}%+"],
                                 capture_output=True, timeout=10)
                    return _resultado(True, f"Volumen subido {cantidad}%", "sistema")
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    return _resultado(False, "No se pudo controlar volumen - instala pactl o amixer", "sistema")

        return _resultado(False, f"Sistema operativo no soportado: {SISTEMA}", "sistema")

    except Exception as e:
        logger.log_excepcion("sistema", "subir_volumen", e)
        return _resultado(False, f"Error al subir volumen: {e}", "sistema")


def bajar_volumen(cantidad=None):
    """Bajar volumen del sistema. Si cantidad=None, baja 10 unidades."""
    try:
        # Si no se especifica cantidad, el salto por defecto es 10
        cantidad = cantidad or 10

        if SISTEMA == "windows":
            try:
                volume    = _obtener_interfaz_volumen()
                actual    = volume.GetMasterVolumeLevelScalar()
                nuevo     = max(0.0, actual - (cantidad / 100.0))
                volume.SetMasterVolumeLevelScalar(nuevo, None)
                return _resultado(True, f"Volumen bajado a {int(nuevo * 100)}%", "sistema")
            except ImportError:
                import ctypes
                # Windows altera el volumen de 2 en 2 por cada pulsación de tecla virtual (0xAE).
                # Dividimos entre 2 para que el bucle pulse las veces necesarias para alcanzar la 'cantidad'.
                for _ in range(cantidad // 2):
                    ctypes.windll.user32.keybd_event(0xAE, 0, 0, 0)
                    ctypes.windll.user32.keybd_event(0xAE, 0, 2, 0)  # Corregido flag de liberación a 2 (KEYEVENTF_KEYUP)
                return _resultado(True, f"Volumen bajado {cantidad} unidades", "sistema")

        elif SISTEMA == "darwin":
            script = f'''
            set volume output volume (output volume of (get volume settings) - {cantidad})
            '''
            subprocess.run(["osascript", "-e", script], capture_output=True)
            return _resultado(True, f"Volumen bajado {cantidad} unidades", "sistema")

        elif SISTEMA == "linux":
            try:
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{cantidad}%"],
                             capture_output=True, timeout=10)
                return _resultado(True, f"Volumen bajado {cantidad}%", "sistema")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                try:
                    subprocess.run(["amixer", "set", "Master", f"{cantidad}%-"],
                                 capture_output=True, timeout=10)
                    return _resultado(True, f"Volumen bajado {cantidad}%", "sistema")
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    return _resultado(False, "No se pudo controlar volumen - instala pactl o amixer", "sistema")

        return _resultado(False, f"Sistema operativo no soportado: {SISTEMA}", "sistema")

    except Exception as e:
        logger.log_excepcion("sistema", "bajar_volumen", e)
        return _resultado(False, f"Error al bajar volumen: {e}", "sistema")
def silenciar_volumen():
    """Silenciar o activar sonido"""
    try:
        if SISTEMA == "windows":
            try:
                volume  = _obtener_interfaz_volumen()
                muted   = volume.GetMute()
                volume.SetMute(not muted, None)
                estado  = "activado" if muted else "silenciado"
                return _resultado(True, f"Sonido {estado}", "sistema")
            except ImportError:
                import ctypes
                ctypes.windll.user32.keybd_event(0xAD, 0, 0, 0)
                ctypes.windll.user32.keybd_event(0xAD, 0, 10, 0)
                return _resultado(True, "Sonido silenciado/activado", "sistema")

        elif SISTEMA == "darwin":
            script = '''
            set volume with output muted
            '''
            subprocess.run(["osascript", "-e", script], capture_output=True)
            return _resultado(True, "Sonido silenciado/activado", "sistema")

        elif SISTEMA == "linux":
            try:
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
                             capture_output=True, timeout=10)
                return _resultado(True, "Sonido silenciado/activado", "sistema")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                try:
                    subprocess.run(["amixer", "set", "Master", "toggle"],
                                 capture_output=True, timeout=2)
                    return _resultado(True, "Sonido silenciado/activado", "sistema")
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    return _resultado(False, "No se pudo controlar mute - instala pactl o amixer", "sistema")

        return _resultado(False, f"Sistema operativo no soportado: {SISTEMA}", "sistema")

    except Exception as e:
        logger.log_excepcion("sistema", "silenciar_volumen", e)
        return _resultado(False, f"Error al silenciar volumen: {e}", "sistema")

def establecer_volumen(nivel):
    """Establecer volumen a un nivel exacto (0-100)"""
    try:
        # Validar rango
        nivel = int(nivel)
        if nivel < 0 or nivel > 100:
            return _resultado(False, f"Nivel debe estar entre 0 y 100, recibí {nivel}", "sistema")

        if SISTEMA == "windows":
            try:
                volume = _obtener_interfaz_volumen()
                volume.SetMasterVolumeLevelScalar(nivel / 100.0, None)
                return _resultado(True, f"Volumen establecido a {nivel}%", "sistema")
            except ImportError:
                return _resultado(False, "pycaw no instalado. Instala con: pip install pycaw", "sistema")

        elif SISTEMA == "darwin":
            # macOS solo acepta valores 0-100 directamente
            script = f'''
            set volume output volume {nivel}
            '''
            subprocess.run(["osascript", "-e", script], capture_output=True)
            return _resultado(True, f"Volumen establecido a {nivel}%", "sistema")

        elif SISTEMA == "linux":
            try:
                # pactl usa porcentajes
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{nivel}%"],
                             capture_output=True, timeout=2)
                return _resultado(True, f"Volumen establecido a {nivel}%", "sistema")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                try:
                    # amixer también usa porcentajes
                    subprocess.run(["amixer", "set", "Master", f"{nivel}%"],
                                 capture_output=True, timeout=2)
                    return _resultado(True, f"Volumen establecido a {nivel}%", "sistema")
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    return _resultado(False, "No se pudo establecer volumen - instala pactl o amixer", "sistema")

        return _resultado(False, f"Sistema operativo no soportado: {SISTEMA}", "sistema")

    except ValueError:
        return _resultado(False, f"Nivel debe ser un número entre 0 y 100, recibí: {nivel}", "sistema")
    except Exception as e:
        logger.log_excepcion("sistema", "establecer_volumen", e)
        return _resultado(False, f"Error al establecer volumen: {e}", "sistema")

# ── INFORMACIÓN DEL SISTEMA ────────────────────────

def info_sistema():
    try:
        memoria = psutil.virtual_memory()
        cpu_uso = psutil.cpu_percent(interval=1)
        disco   = psutil.disk_usage('/')

        try:
            bateria = psutil.sensors_battery()
            bat_info = f"{bateria.percent:.0f}% {'(cargando)' if bateria.power_plugged else '(batería)'}" if bateria else "No disponible"
        except Exception:
            bat_info = "No disponible"

        resultado = (
            f"• Sistema:    {platform.system()} {platform.release()}\n"
            f"• CPU:        {cpu_uso:.1f}% uso | {psutil.cpu_count()} núcleos\n"
            f"• RAM total:  {memoria.total / (1024**3):.1f} GB\n"
            f"• RAM usada:  {memoria.used / (1024**3):.1f} GB ({memoria.percent:.1f}%)\n"
            f"• RAM libre:  {memoria.available / (1024**3):.1f} GB\n"
            f"• Disco total:{disco.total / (1024**3):.1f} GB\n"
            f"• Disco libre:{disco.free / (1024**3):.1f} GB\n"
            f"• Batería:    {bat_info}"
        )
        return _resultado(True, resultado, "sistema", mensaje=resultado)

    except Exception as e:
        logger.log_excepcion("sistema", "info_sistema", e)
        return _resultado(False, f"Error al obtener info del sistema: {e}", "sistema")

def info_procesos():
    """Mostrar procesos en ejecución (top 10 por uso de CPU)"""
    try:
        procesos = []
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
            try:
                info = proc.info
                procesos.append({
                    'pid': info['pid'],
                    'nombre': info['name'][:20],  # Limitar nombre
                    'cpu': info['cpu_percent'],
                    'memoria': info['memory_percent']
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Ordenar por CPU y tomar top 10
        procesos.sort(key=lambda x: x['cpu'], reverse=True)
        top_procesos = procesos[:10]

        resultado = "Top 10 procesos por uso de CPU:\n"
        resultado += "PID\tNombre\t\t\tCPU%\tMem%\n"
        resultado += "-" * 50 + "\n"

        for proc in top_procesos:
            resultado += f"{proc['pid']:6d}\t{proc['nombre']:<20}\t{proc['cpu']:5.1f}\t{proc['memoria']:5.1f}\n"

        return _resultado(True, resultado, "sistema", mensaje=resultado)

    except ImportError:
        return _resultado(False, "Instala psutil para ver información de procesos", "sistema")
    except Exception as e:
        logger.log_excepcion("sistema", "info_procesos", e)
        return _resultado(False, f"Error al obtener info de procesos: {e}", "sistema")

# ── CONTROL DE ENERGÍA ─────────────────────────────

def bloquear_pantalla():
    """Bloquear la pantalla del sistema"""
    try:
        if SISTEMA == "windows":
            subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], capture_output=True)
            return _resultado(True, "Pantalla bloqueada", "sistema")

        elif SISTEMA == "darwin":
            subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
            return _resultado(True, "Pantalla bloqueada", "sistema")

        elif SISTEMA == "linux":
            # Intentar varios comandos comunes
            comandos = [
                ["gnome-screensaver-command", "-l"],
                ["cinnamon-screensaver-command", "-l"],
                ["xdg-screensaver", "lock"],
                ["loginctl", "lock-session"]
            ]

            for cmd in comandos:
                try:
                    subprocess.run(cmd, capture_output=True, timeout=2)
                    return _resultado(True, "Pantalla bloqueada", "sistema")
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    continue

            return _resultado(False, "No se pudo bloquear pantalla - comando no encontrado", "sistema")

        return _resultado(False, f"Sistema operativo no soportado: {SISTEMA}", "sistema")

    except Exception as e:
        logger.log_excepcion("sistema", "bloquear_pantalla", e)
        return _resultado(False, f"Error al bloquear pantalla: {e}", "sistema")

def suspender_sistema():
    """Suspender/hibernar el sistema"""
    try:
        if SISTEMA == "windows":
            subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], capture_output=True)
            return _resultado(True, "Sistema suspendido", "sistema")

        elif SISTEMA == "darwin":
            subprocess.run(["pmset", "sleepnow"], capture_output=True)
            return _resultado(True, "Sistema suspendido", "sistema")

        elif SISTEMA == "linux":
            # Intentar varios comandos
            comandos = [
                ["systemctl", "suspend"],
                ["pm-suspend"],
                ["dbus-send", "--system", "--print-reply", "--dest=org.freedesktop.login1",
                 "/org/freedesktop/login1", "org.freedesktop.login1.Manager.Suspend", "boolean:true"]
            ]

            for cmd in comandos:
                try:
                    subprocess.run(cmd, capture_output=True, timeout=2)
                    return _resultado(True, "Sistema suspendido", "sistema")
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    continue

            return _resultado(False, "No se pudo suspender - comando no encontrado", "sistema")

        return _resultado(False, f"Sistema operativo no soportado: {SISTEMA}", "sistema")

    except Exception as e:
        logger.log_excepcion("sistema", "suspender_sistema", e)
        return _resultado(False, f"Error al suspender sistema: {e}", "sistema")

# ── UTILIDADES VARIAS ──────────────────────────────

def limpiar_papelera():
    try:
        if SISTEMA == "windows":
            # Usar SHEmptyRecycleBin via ctypes — sin winshell
            import ctypes
            SHEmptyRecycleBin = ctypes.windll.shell32.SHEmptyRecycleBinW
            resultado = SHEmptyRecycleBin(None, None, 0x0007)
            # 0 = éxito, -2147418113 = ya estaba vacía (también éxito)
            if resultado in (0, -2147418113):
                return _resultado(True, "Papelera vaciada", "sistema")
            return _resultado(False, f"No se pudo vaciar papelera: código {resultado}", "sistema")
        elif SISTEMA == "darwin":
            subprocess.run(["osascript", "-e", 'tell application "Finder" to empty trash'], capture_output=True)
            return _resultado(True, "Papelera vaciada", "sistema")
        elif SISTEMA == "linux":
            try:
                subprocess.run(["trash-empty"], capture_output=True, timeout=5)
                return _resultado(True, "Papelera vaciada", "sistema")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                home = os.path.expanduser("~")
                trash = os.path.join(home, ".local", "share", "Trash")
                subprocess.run(["rm", "-rf", f"{trash}/files/*", f"{trash}/info/*"], capture_output=True)
                return _resultado(True, "Papelera vaciada", "sistema")
        return _resultado(False, f"Sistema no soportado: {SISTEMA}", "sistema")
    except Exception as e:
        logger.log_excepcion("sistema", "limpiar_papelera", e)
        return _resultado(False, f"Error al vaciar papelera: {e}", "sistema")

def abrir_explorador(ruta=None):
    """Abrir explorador de archivos en ruta específica"""
    try:
        ruta = ruta or os.getcwd()

        if SISTEMA == "windows":
            subprocess.run(["explorer", ruta], capture_output=True)
        elif SISTEMA == "darwin":
            subprocess.run(["open", ruta], capture_output=True)
        elif SISTEMA == "linux":
            subprocess.run(["xdg-open", ruta], capture_output=True)
        else:
            return _resultado(False, f"Sistema operativo no soportado: {SISTEMA}", "sistema")

        return _resultado(True, f"Explorador abierto en: {ruta}", "sistema")

    except Exception as e:
        logger.log_excepcion("sistema", "abrir_explorador", e)
        return _resultado(False, f"Error al abrir explorador: {e}", "sistema")

# ── FUNCIONES DE REGISTRO PARA SARA ────────────────

def registrar_comandos_sistema():
    """
    Devuelve una lista de comandos disponibles en este módulo.
    Esta función se puede usar para registrar los comandos en SARA.
    """
    comandos = [
        {
            "nombre": "pausar musica",
            "tipo": "sistema_control",
            "accion": "pausar_reproducir",
            "descripcion": "Pausar o reanudar reproducción multimedia"
        },
        {
            "nombre": "reproducir musica",
            "tipo": "sistema_control",
            "accion": "pausar_reproducir",
            "descripcion": "Pausar o reanudar reproducción multimedia"
        },
        {
            "nombre": "siguiente cancion",
            "tipo": "sistema_control",
            "accion": "siguiente_pista",
            "descripcion": "Saltar a la siguiente canción"
        },
        {
            "nombre": "cancion anterior",
            "tipo": "sistema_control",
            "accion": "pista_anterior",
            "descripcion": "Volver a la canción anterior"
        },
        {
            "nombre": "subir volumen",
            "tipo": "sistema_control",
            "accion": "subir_volumen",
            "descripcion": "Subir volumen del sistema"
        },
        {
            "nombre": "bajar volumen",
            "tipo": "sistema_control",
            "accion": "bajar_volumen",
            "descripcion": "Bajar volumen del sistema"
        },
        {
            "nombre": "silenciar",
            "tipo": "sistema_control",
            "accion": "silenciar_volumen",
            "descripcion": "Silenciar o activar sonido"
        },
        {
            "nombre": "info sistema",
            "tipo": "sistema_control",
            "accion": "info_sistema",
            "descripcion": "Mostrar información del sistema"
        },
        {
            "nombre": "procesos sistema",
            "tipo": "sistema_control",
            "accion": "info_procesos",
            "descripcion": "Mostrar procesos en ejecución"
        },
        {
            "nombre": "bloquear pantalla",
            "tipo": "sistema_control",
            "accion": "bloquear_pantalla",
            "descripcion": "Bloquear la pantalla"
        },
        {
            "nombre": "suspender pc",
            "tipo": "sistema_control",
            "accion": "suspender_sistema",
            "descripcion": "Suspender/hibernar el sistema"
        },
        {
            "nombre": "vaciar papelera",
            "tipo": "sistema_control",
            "accion": "limpiar_papelera",
            "descripcion": "Vaciar papelera de reciclaje"
        },
        {
            "nombre": "abrir explorador",
            "tipo": "sistema_control",
            "accion": "abrir_explorador",
            "descripcion": "Abrir explorador de archivos"
        }
    ]

    return comandos

# ── EJECUTOR PRINCIPAL ─────────────────────────────

def ejecutar_funcion_sistema(nombre_funcion, *args, **kwargs):
    """
    Ejecutor principal para funciones del sistema.
    Esta función es llamada por SARA cuando un comando sistema_control
    tiene una acción que coincide con una función aquí.
    """
    funciones_disponibles = {
        "pausar_reproducir": pausar_reproducir,
        "siguiente_pista": siguiente_pista,
        "pista_anterior": pista_anterior,
        "subir_volumen": subir_volumen,
        "bajar_volumen": bajar_volumen,
        "silenciar_volumen": silenciar_volumen,
        "establecer_volumen": establecer_volumen,
        "info_sistema": info_sistema,
        "info_procesos": info_procesos,
        "bloquear_pantalla": bloquear_pantalla,
        "suspender_sistema": suspender_sistema,
        "limpiar_papelera": limpiar_papelera,
        "abrir_explorador": abrir_explorador,
    }

    if nombre_funcion in funciones_disponibles:
        funcion = funciones_disponibles[nombre_funcion]

        # Si hay argumentos, intentar convertir strings a números
        if args:
            args_procesados = []
            for arg in args:
                if isinstance(arg, str) and arg.isdigit():
                    args_procesados.append(int(arg))
                else:
                    args_procesados.append(arg)
            args = tuple(args_procesados)

        # Validar argumentos obligatorios antes de ejecutar
        firma = inspect.signature(funcion)
        parametros_requeridos = [p for p in firma.parameters.values()
                                  if p.default is inspect._empty
                                  and p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                                 inspect.Parameter.POSITIONAL_OR_KEYWORD)]
        if len(args) < len(parametros_requeridos):
            faltantes = len(parametros_requeridos) - len(args)
            return _resultado(False,
                              f"Faltan {faltantes} argumento(s) para {nombre_funcion}.",
                              "sistema")

        try:
            return funcion(*args, **kwargs)
        except Exception as e:
            logger.log_excepcion("sistema", nombre_funcion, e)
            return _resultado(False, f"Error en {nombre_funcion}: {e}", "sistema")
    else:
        return _resultado(False, f"Función '{nombre_funcion}' no encontrada", "sistema")