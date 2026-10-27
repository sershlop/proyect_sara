# 📁 app_actions.py — Acciones sobre aplicaciones (abrir, cerrar)

import os
import subprocess
import time
from typing import Optional

import re

try:
    import app_index
except ImportError:
    app_index = None

from utils import normalizar_texto, similitud, quitar_verbos_y_relleno

try:
    from config import (
        APLICACIONES_SISTEMA_FIJAS, UMBRAL_ABRIR_APLICACION, MARGEN_AMBIGUEDAD_APLICACION,
        UMBRAL_MINIMO_CERRAR_APLICACION, TIMEOUT_CIERRE_NORMAL_SEGUNDOS,
    )
except ImportError:
    APLICACIONES_SISTEMA_FIJAS = {}
    UMBRAL_ABRIR_APLICACION = 0.80
    MARGEN_AMBIGUEDAD_APLICACION = 0.05
    UMBRAL_MINIMO_CERRAR_APLICACION = 0.45
    TIMEOUT_CIERRE_NORMAL_SEGUNDOS = 5

_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # sin parpadeo de consola desde la GUI

try:
    import logger as _logger
except ImportError:
    _logger = None


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    """Log seguro con fallback."""
    try:
        if _logger is None:
            raise ImportError("logger.py no disponible")
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[APP_ACTIONS ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


# ═══════════════════════════════════════════════════════════════════════════
# ABRIR APLICACIONES
# ═══════════════════════════════════════════════════════════════════════════

def _lanzar(destino: str, argumentos: Optional[list] = None) -> None:
    """Abre un .lnk / .exe / URI (ms-settings:) como lo haría el Explorador."""
    if not argumentos and hasattr(os, "startfile"):
        os.startfile(destino)          # resuelve .lnk, PATH y URIs; no bloquea
        return
    subprocess.Popen([destino] + list(argumentos or []), shell=False,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=_SIN_VENTANA)


def abrir_aplicacion(nombre_o_ruta, argumentos: Optional[list] = None) -> bool:
    """
    Abre una aplicación por nombre, ruta, o dict del índice (app_index.buscar_aplicacion).
    Acepta .lnk (Menú Inicio) — antes Popen([lnk]) fallaba con WinError 193.
    """
    if isinstance(nombre_o_ruta, dict):
        nombre_o_ruta = nombre_o_ruta.get("exe") or nombre_o_ruta.get("ruta_ejecutable") or ""
    if not nombre_o_ruta:
        return False
    try:
        if os.path.exists(nombre_o_ruta) or nombre_o_ruta.endswith(":"):
            ruta = nombre_o_ruta
        else:
            ruta = _buscar_ejecutable(nombre_o_ruta)
            if not ruta:
                _log_error("app_actions", "abrir_aplicacion",
                           ValueError(f"No se encontró: {nombre_o_ruta}"))
                return False
        _lanzar(ruta, argumentos)
        return True
    except Exception as e:
        _log_error("app_actions", "abrir_aplicacion", e)
        return False


def _ruta_app_paths(nombre: str) -> Optional[str]:
    """Ruta oficial del .exe según HKLM/HKCU\\...\\App Paths (donde Windows registra chrome.exe, etc.)."""
    try:
        import winreg
    except ImportError:
        return None
    base = nombre.strip().lower()
    exes = {base if base.endswith(".exe") else base + ".exe",
            base.replace(" ", "") + ".exe", base.replace(" ", "_") + ".exe"}
    for exe in exes:
        clave = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
        for raiz in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for vista in (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)):
                try:
                    with winreg.OpenKey(raiz, clave, 0, winreg.KEY_READ | vista) as k:
                        valor = (winreg.QueryValueEx(k, "")[0] or "").strip().strip('"')
                    if valor and os.path.exists(valor):
                        return valor
                except OSError:
                    continue
    return None


def _buscar_ejecutable(nombre: str) -> Optional[str]:
    """
    Busca un ejecutable por nombre en rutas típicas.
    OPTIMIZADO: búsqueda local primero, luego sistema.
    """
    nombre_norm = nombre.lower().replace(" ", "_")
    
    # Rutas típicas del sistema Windows
    rutas_candidate = [
        # Aplicaciones de sistema fijas
        f"C:\\Windows\\System32\\{nombre}.exe",
        f"C:\\Windows\\{nombre}.exe",
        
        # Program Files
        f"C:\\Program Files\\{nombre}\\{nombre}.exe",
        f"C:\\Program Files (x86)\\{nombre}\\{nombre}.exe",
        
        # AppData
        f"C:\\Users\\{os.getenv('USERNAME')}\\AppData\\Local\\{nombre}\\{nombre}.exe",
    ]
    
    # Aplicaciones de sistema fijas que casi siempre están en System32
    aplicaciones_sistema = {
        "calc": "calc.exe",
        "calculadora": "calc.exe",
        "notepad": "notepad.exe",
        "bloc de notas": "notepad.exe",
        "cmd": "cmd.exe",
        "powershell": "powershell.exe",
        "explorer": "explorer.exe",
        "paint": "mspaint.exe",
        "word": "WINWORD.exe",
        "excel": "EXCEL.exe",
    }
    
    # Si es una aplicación conocida, buscarla directamente en System32
    if nombre_norm in aplicaciones_sistema:
        exe_name = aplicaciones_sistema[nombre_norm]
        ruta_system32 = f"C:\\Windows\\System32\\{exe_name}"
        if os.path.exists(ruta_system32):
            return ruta_system32
    
    # Buscar en rutas candidate
    for ruta in rutas_candidate:
        if os.path.exists(ruta):
            return ruta
    
    # Registro de Windows: App Paths (chrome, msedge, opera, firefox, brave, vlc, etc.)
    ruta_reg = _ruta_app_paths(nombre)
    if ruta_reg:
        return ruta_reg

    # Intentar con nombre directo (si está en PATH)
    try:
        resultado = subprocess.run(
            ["where", nombre],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if resultado.returncode == 0:
            return resultado.stdout.strip().split("\n")[0]
    except Exception:
        pass
    
    return None


# ═══════════════════════════════════════════════════════════════════════════
# CERRAR APLICACIONES
# ═══════════════════════════════════════════════════════════════════════════

def cerrar_aplicacion(nombre_o_pid: str) -> bool:
    """
    Cierra una aplicación por nombre o PID.
    OPTIMIZADO: intenta terminación normal antes de forzada.
    """
    if not nombre_o_pid:
        return False
    
    try:
        # Si es un número, es PID
        if nombre_o_pid.isdigit():
            pid = int(nombre_o_pid)
            return _cerrar_por_pid(pid)
        else:
            # Es un nombre, buscar el proceso
            return _cerrar_por_nombre(nombre_o_pid)
    except Exception as e:
        _log_error("app_actions", "cerrar_aplicacion", e)
        return False


def _cerrar_por_pid(pid: int, timeout_segundos: int = 5) -> bool:
    """
    Cierra un proceso por PID.
    Intenta SIGTERM antes de SIGKILL.
    """
    try:
        # Intentar terminación normal
        subprocess.run(
            ["taskkill", "/PID", str(pid)],
            capture_output=True,
            timeout=timeout_segundos,
        )
        
        # Esperar un poco a que se cierre
        time.sleep(1)
        
        # Verificar que se cerró
        try:
            resultado = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if str(pid) not in resultado.stdout:
                return True  # Se cerró exitosamente
        except Exception:
            pass
        
        # Si aún está abierto, forzar cierre
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            timeout=timeout_segundos,
        )
        return True
    except Exception as e:
        _log_error("app_actions", "_cerrar_por_pid", e)
        return False


def _cerrar_por_nombre(nombre: str) -> bool:
    """
    Cierra procesos por nombre.
    """
    try:
        nombre_norm = nombre.lower()
        
        # Obtener lista de procesos
        resultado = subprocess.run(
            ["tasklist", "/FO", "CSV"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        
        # Buscar procesos que coincidan
        lineas = resultado.stdout.split("\n")
        pids_a_cerrar = []
        
        for linea in lineas[1:]:  # Skip header
            if not linea.strip():
                continue
            partes = linea.split(",")
            if len(partes) >= 2:
                proc_nombre = partes[0].strip('"').lower()
                proc_pid = partes[1].strip().strip('"')
                
                # Buscar coincidencia (exacta o parcial)
                if nombre_norm in proc_nombre or proc_nombre.startswith(nombre_norm):
                    pids_a_cerrar.append(int(proc_pid))
        
        # Cerrar todos los PIDs encontrados
        exito = True
        for pid in pids_a_cerrar:
            if not _cerrar_por_pid(pid):
                exito = False
        
        return exito and len(pids_a_cerrar) > 0
    except Exception as e:
        _log_error("app_actions", "_cerrar_por_nombre", e)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════

def aplicacion_esta_abierta(nombre: str) -> bool:
    """Verifica si una aplicación está ejecutándose."""
    try:
        resultado = subprocess.run(
            ["tasklist"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        nombre_norm = nombre.lower()
        return nombre_norm in resultado.stdout.lower()
    except Exception as e:
        _log_error("app_actions", "aplicacion_esta_abierta", e)
        return False


def obtener_procesos() -> dict:
    """
    Retorna dict de procesos agrupados por tipo.
    {'usuario': [...], 'sistema': [...]}
    """
    try:
        resultado = subprocess.run(
            ["tasklist", "/V", "/FO", "CSV"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        
        procesos_usuario = []
        procesos_sistema = []
        
        lineas = resultado.stdout.split("\n")
        procesos_sistema_nombres = {
            "svchost.exe", "lsass.exe", "dwm.exe", "explorer.exe",
            "csrss.exe", "services.exe", "wininit.exe",
        }
        
        for linea in lineas[1:]:
            if not linea.strip():
                continue
            try:
                partes = [p.strip('"') for p in linea.split(",")]
                if len(partes) >= 3:
                    nombre = partes[0]
                    pid = partes[1]
                    
                    item = {"nombre": nombre, "pid": pid}
                    
                    if nombre in procesos_sistema_nombres:
                        procesos_sistema.append(item)
                    else:
                        procesos_usuario.append(item)
            except Exception:
                continue
        
        return {
            "usuario": procesos_usuario,
            "sistema": procesos_sistema,
        }
    except Exception as e:
        _log_error("app_actions", "obtener_procesos", e)
        return {"usuario": [], "sistema": []}

# ═══════════════════════════════════════════════════════════════════════════
# AÑADIDO (integración GUI): contrato que main.py y core_bridge esperan
#   ejecutar_desde_clasificacion(res) -> {"abierto","texto","_mejor_coincidencia"} | None
#   resolver_cierre(res)              -> {"estado","texto"|"mensaje_confirmacion","pids"} | None
#   cerrar_procesos(pids, timeout_segundos) -> {"texto",...}
# ═══════════════════════════════════════════════════════════════════════════

_VERBOS_ABRIR = frozenset({"abre", "abrir", "abreme", "abrime", "ejecuta", "ejecutar", "inicia",
                           "iniciar", "lanza", "lanzar", "arranca", "arrancar", "pon", "corre"})
_VERBOS_CERRAR = frozenset({"cierra", "cerrar", "cierrame", "cierralo", "finaliza", "finalizar",
                            "termina", "terminar", "mata", "matar", "quita", "apaga", "deten", "stop"})
_RELLENO_APP = frozenset({"la", "el", "los", "las", "mi", "mis", "un", "una", "por", "favor",
                          "aplicacion", "programa", "app", "aplicaciones", "programas", "ventana", "de"})

# Nunca se cierran: el sistema, y SARA misma (python + el motor de su ventana).
_PROTEGIDOS = frozenset({
    "svchost.exe", "dwm.exe", "explorer.exe", "csrss.exe", "lsass.exe", "services.exe",
    "winlogon.exe", "wininit.exe", "smss.exe", "system", "taskhostw.exe", "conhost.exe",
    "python.exe", "pythonw.exe", "py.exe", "msedgewebview2.exe", "ollama.exe", "ollama app.exe",
})


def _extraer_nombre(res: dict, verbos: frozenset) -> str:
    tn = res.get("texto_norm") or normalizar_texto(res.get("texto_original", ""))
    return quitar_verbos_y_relleno(tn, (), verbos | _RELLENO_APP)


def _puntuar_nombre(consulta: str, candidato_norm: str) -> float:
    if not candidato_norm:
        return 0.0
    if consulta == candidato_norm:
        return 1.0
        
    # NUEVO: Si el nombre oficial de la app está incluido en lo que dijo el usuario
    # Ejemplo: candidato "chrome" está dentro de la consulta "google chrome"
    if all(t in consulta.split() for t in candidato_norm.split()):
        return 0.90
        
    # Si lo que dijo el usuario está incluido en el nombre oficial
    # Ejemplo: consulta "studio" dentro del candidato "android studio"
    if all(t in candidato_norm.split() for t in consulta.split()):
        return 0.80 + 0.20 * min(1.0, len(consulta) / len(candidato_norm))
        
    return similitud(consulta, candidato_norm)

def _app_del_sistema(nombre: str) -> Optional[tuple]:
    """APLICACIONES_SISTEMA_FIJAS: coincidencia exacta o por prefijo (gana la clave más larga)."""
    mejor = None
    for clave, valor in APLICACIONES_SISTEMA_FIJAS.items():
        cn = normalizar_texto(clave)
        if nombre == cn or nombre.startswith(cn + " "):
            if mejor is None or len(cn) > len(mejor[0]):
                mejor = (cn, valor)
    return mejor


def ejecutar_desde_clasificacion(resultado: dict) -> Optional[dict]:
    if resultado.get("categoria") != "ABRIR_APLICACION":
        return None
    
    nombre = _extraer_nombre(resultado, _VERBOS_ABRIR)
    if not nombre:
        return {"abierto": False, "texto": "¿Qué aplicación quieres abrir?"}

    print(f"\n[SARA-EXEC] Intentando abrir aplicación: '{nombre}'")

    # 1) Utilidades fijas del sistema (calculadora, bloc de notas, etc.)
    fija = _app_del_sistema(nombre)
    if fija:
        exe, args = fija[1]
        try:
            _lanzar(exe, args.split() if args else None)
            return {"abierto": True, "texto": f"Abriendo {fija[0]}...",
                    "_mejor_coincidencia": {"ruta_ejecutable": exe, "nombre": fija[0]}}
        except Exception as e:
            print(f"[SARA-EXEC] Falló app fija: {e}")

    # 2) Búsqueda en el índice de la BD (con flexibilidad)
    candidatos = []
    if app_index is not None:
        try:
            candidatos = app_index.buscar_aplicacion(nombre, limite=5)
        except Exception as e:
            print(f"[SARA-EXEC] Error consultando app_index: {e}")

    if candidatos:
        mejor = candidatos[0]
        etiqueta = mejor.get("nombre_mostrar") or mejor["nombre"]
        print(f"[SARA-EXEC] Encontrado en BD -> {etiqueta} ({mejor.get('exe')})")
        if abrir_aplicacion(mejor):
            try:
                app_index.registrar_apertura(mejor["id"])
            except Exception:
                pass
            return {"abierto": True, "texto": f"Abriendo {etiqueta}...",
                    "_mejor_coincidencia": {"ruta_ejecutable": mejor.get("exe") or "", "nombre": etiqueta}}

    # 3) PLAN DE FUERZA BRUTA DIRECTA (Si la BD no lo abrió, Windows lo busca por nombre exacto o comando)
    print(f"[SARA-EXEC] La BD no lo abrió. Forzando ejecución directa en el sistema para: '{nombre}'")
    try:
        # Intentar lanzamiento directo con el nombre proporcionado (ej. 'code', 'chrome', 'devenv')
        if abrir_aplicacion(nombre):
            return {"abierto": True, "texto": f"Abriendo {nombre} (modo directo)..."}
    except Exception as e:
        print(f"[SARA-EXEC] Falló ejecución directa: {e}")

    return {"abierto": False, "texto": f"No encontré ni pude abrir la aplicación '{nombre}'."}
import csv
import io

# exe (minúsculas) -> nombres con los que la gente lo llama
_ALIAS_PROCESOS = {
    "code.exe": ("visual studio code", "vs code", "vscode", "visual code", "code"),
    "devenv.exe": ("visual studio", "visual studio community", "devenv"),
    "cursor.exe": ("cursor",),
    "chrome.exe": ("google chrome", "chrome", "navegador chrome"),
    "msedge.exe": ("microsoft edge", "edge"),
    "firefox.exe": ("firefox", "mozilla firefox"),
    "opera.exe": ("opera", "opera gx"),
    "notepad.exe": ("bloc de notas", "block de notas", "notepad"),
    "notepad++.exe": ("notepad++", "notepad plus plus"),
    "calculatorapp.exe": ("calculadora", "calculator"),
    "spotify.exe": ("spotify",),
    "discord.exe": ("discord",),
    "whatsapp.exe": ("whatsapp",),
    "telegram.exe": ("telegram",),
    "winword.exe": ("word", "microsoft word"),
    "excel.exe": ("excel", "microsoft excel"),
    "powerpnt.exe": ("powerpoint", "power point"),
    "pycharm64.exe": ("pycharm", "py charm"),
    "sublime_text.exe": ("sublime", "sublime text"),
}

# Editores/ofimática: si piden guardar, NO se fuerza el cierre (evita perder trabajo).
_NO_FORZAR = frozenset({"code.exe", "cursor.exe", "devenv.exe", "winword.exe", "excel.exe",
                        "powerpnt.exe", "notepad++.exe", "pycharm64.exe", "sublime_text.exe"})

_NOMBRES_TERMINAL = frozenset({"terminal", "terminales", "consola", "consolas", "terminal windows",
                               "windows terminal", "linea comandos", "simbolo sistema"})
_EXES_TERMINAL = ("cmd.exe", "powershell.exe", "pwsh.exe")   # WindowsTerminal.exe NO: aloja también a SARA


def _mapa_padres() -> dict:
    """{pid: pid_padre} de todos los procesos (ctypes, sin dependencias)."""
    mapa: dict = {}
    try:
        import ctypes
        from ctypes import wintypes

        class PE(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PE)]
        k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PE)]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)          # TH32CS_SNAPPROCESS
        if not snap or snap == ctypes.c_void_p(-1).value:
            return mapa
        try:
            pe = PE()
            pe.dwSize = ctypes.sizeof(PE)
            ok = k32.Process32FirstW(snap, ctypes.byref(pe))
            while ok:
                mapa[pe.th32ProcessID] = pe.th32ParentProcessID
                ok = k32.Process32NextW(snap, ctypes.byref(pe))
        finally:
            k32.CloseHandle(snap)
    except Exception as e:
        _log_error("app_actions", "_mapa_padres", e)
    return mapa


def _propios() -> set:
    """SARA + TODOS sus ancestros (lanzador, cmd/powershell, Windows Terminal...). Intocables."""
    protegidos = {os.getpid(), os.getppid()}
    padres = _mapa_padres()
    pid = os.getpid()
    for _ in range(20):
        pid = padres.get(pid, 0)
        if not pid or pid in protegidos and pid not in (os.getppid(),):
            break
        protegidos.add(pid)
    return protegidos
def _procesos_vivos() -> dict:
    """{exe_minusculas: [pids]} con lectura FRESCA (sin caché)."""
    vivos: dict = {}
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                           errors="replace", timeout=8, creationflags=_SIN_VENTANA)
        for fila in csv.reader(io.StringIO(r.stdout)):
            if len(fila) >= 2 and fila[1].isdigit():
                vivos.setdefault(fila[0].lower(), []).append(int(fila[1]))
    except Exception as e:
        _log_error("app_actions", "_procesos_vivos", e)
    return vivos


def _buscar_directo(nombre: str) -> list:
    """Grupos {nombre_mostrar, nombre_proceso, pids, score} de procesos VIVOS que coinciden con `nombre`."""
    q = normalizar_texto(nombre)
    if not q:
        return []
    vivos = _procesos_vivos()
    propios = _propios()
    q_junto = q.replace(" ", "")
    elegidos: dict = {}

    # "la terminal": un solo grupo con las shells AJENAS (nunca la que aloja a SARA)
    if q in _NOMBRES_TERMINAL:
        pids = [p for exe in _EXES_TERMINAL for p in vivos.get(exe, []) if p not in propios]
        return ([{"nombre_mostrar": "Terminal", "nombre_proceso": "cmd.exe", "pids": pids, "score": 1.0}]
                if pids else [])

    # 1) Alias conocido (coincidencia exacta del nombre hablado)
    for exe, alias in _ALIAS_PROCESOS.items():
        if exe in vivos and q in {normalizar_texto(a) for a in alias}:
            elegidos[exe] = 1.0
    # 2) Nombre del .exe: "obs" -> obs64.exe, "steam" -> steam.exe
    if not elegidos:
        for exe in vivos:
            stem = normalizar_texto(exe.rsplit(".", 1)[0]).replace(" ", "")
            if not stem:
                continue
            if q_junto == stem:
                elegidos[exe] = 1.0
            elif len(q_junto) >= 4 and stem.startswith(q_junto):
                elegidos[exe] = 0.90

    grupos = []
    for exe, score in elegidos.items():
        pids = [p for p in vivos[exe] if p not in propios]
        if pids:
            grupos.append({"nombre_mostrar": exe.rsplit(".", 1)[0].title() if exe not in _ALIAS_PROCESOS
                           else _ALIAS_PROCESOS[exe][0].title(),
                           "nombre_proceso": exe, "pids": pids, "score": score})
    return grupos
def resolver_cierre(resultado: dict) -> Optional[dict]:
    if resultado.get("categoria") != "CERRAR_APLICACION":
        return None
    nombre = _extraer_nombre(resultado, _VERBOS_CERRAR)
    if not nombre:
        return {"estado": "no_encontrado", "texto": "¿Qué aplicación quieres cerrar?"}
    grupos = _buscar_directo(nombre)                       # lista fresca + alias (VS Code = Code.exe)
    if not grupos and app_index:                           # respaldo: el índice difuso de antes
        grupos = app_index.buscar_proceso_en_ejecucion(nombre, limite=6)
    grupos = [g for g in grupos if g.get("score", 0) >= UMBRAL_MINIMO_CERRAR_APLICACION and g.get("pids")]
    if not grupos:
        return {"estado": "no_encontrado", "texto": f"No encontré '{nombre}' abierta."}
    grupos.sort(key=lambda g: g["score"], reverse=True)

    # La decisión se toma sobre la MEJOR coincidencia: si es un proceso protegido se rechaza,
    # sin ofrecer "alternativas" difusas (antes "cierra python" proponía cerrar Chrome).
    mejor = grupos[0]
    if (mejor.get("nombre_proceso") or "").lower() in _PROTEGIDOS:
        return {"estado": "protegido",
                "texto": f"'{mejor.get('nombre_mostrar')}' es un proceso protegido "
                         f"(sistema o el propio SARA); no lo cierro."}
    # Solo compiten las coincidencias cercanas a la mejor (evita arrastrar fuzzy flojos).
    piso = max(UMBRAL_MINIMO_CERRAR_APLICACION, mejor["score"] - 0.15)
    libres = [g for g in grupos
              if g["score"] >= piso and (g.get("nombre_proceso") or "").lower() not in _PROTEGIDOS]
    if len(libres) > 1 and libres[0]["score"] < 1.0 \
            and libres[0]["score"] - libres[1]["score"] < MARGEN_AMBIGUEDAD_APLICACION:
        nombres = ", ".join(g["nombre_mostrar"] for g in libres[:4])
        return {"estado": "ambiguo", "texto": f"Hay varias abiertas que coinciden: {nombres}. ¿Cuál cierro?"}
    g = libres[0]
    n = len(g["pids"])
    return {"estado": "candidato", "pids": list(g["pids"]), "nombre": g["nombre_mostrar"],
            "mensaje_confirmacion": f"¿Cierro {g['nombre_mostrar']} ({n} proceso{'s' if n != 1 else ''})?"}


def _pid_vivo(pid: int) -> bool:
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                           text=True, timeout=3, creationflags=_SIN_VENTANA)
        return str(pid) in r.stdout
    except Exception:
        return False


def cerrar_procesos(pids: list, timeout_segundos: int = TIMEOUT_CIERRE_NORMAL_SEGUNDOS) -> dict:
    """Cierre educado (WM_CLOSE) -> espera -> /F /T solo a lo que siga vivo y no sea editor/ofimática.
    Informa lo que REALMENTE se cerró."""
    propios = _propios()
    objetivo = [int(p) for p in (pids or []) if int(p) not in propios]
    if not objetivo:
        return {"texto": "No hay procesos que cerrar.", "cerrados": 0, "forzados": 0}
    try:
        nombre_de = {p: exe for exe, ps in _procesos_vivos().items() for p in ps}
        subprocess.run(["taskkill"] + [x for p in objetivo for x in ("/PID", str(p), "/T")],
                       capture_output=True, timeout=timeout_segundos + 5, creationflags=_SIN_VENTANA)

        def _siguen(lista):
            activos = {p for ps in _procesos_vivos().values() for p in ps}
            return [p for p in lista if p in activos]

        limite = time.time() + timeout_segundos
        vivos = objetivo
        while vivos and time.time() < limite:
            time.sleep(0.5)
            vivos = _siguen(vivos)

        forzados, pendientes = 0, []
        for p in vivos:
            if nombre_de.get(p, "") in _NO_FORZAR:
                pendientes.append(p)            # probablemente pide guardar: no se mata
                continue
            subprocess.run(["taskkill", "/PID", str(p), "/F", "/T"], capture_output=True,
                           timeout=5, creationflags=_SIN_VENTANA)
            forzados += 1
        time.sleep(0.4)
        restantes = _siguen(objetivo)
        cerrados = len(objetivo) - len(restantes)

        if restantes and any(nombre_de.get(p, "") in _NO_FORZAR for p in restantes):
            return {"texto": "Le pedí que se cierre, pero sigue abierto (seguramente espera que guardes cambios). "
                             "No lo forcé para no perder tu trabajo.",
                    "cerrados": cerrados, "forzados": forzados}
        if restantes:
            return {"texto": f"No pude cerrar {len(restantes)} proceso(s).", "cerrados": cerrados, "forzados": forzados}
        txt = f"Cerré {cerrados} proceso{'s' if cerrados != 1 else ''}"
        txt += f" ({forzados} tuvo que forzarse)." if forzados else "."
        return {"texto": txt, "cerrados": cerrados, "forzados": forzados}
    except Exception as e:
        _log_error("app_actions", "cerrar_procesos", e)
        return {"texto": "No pude cerrar la aplicación.", "cerrados": 0, "forzados": 0}


if __name__ == "__main__":
    print("Testing app_actions:")
    print(f"  Buscando calc.exe: {_buscar_ejecutable('calc')}")
    print(f"  Buscando notepad: {_buscar_ejecutable('notepad')}")
    print(f"  Procesos abiertos: {len(obtener_procesos()['usuario'])} usuario, "
          f"{len(obtener_procesos()['sistema'])} sistema")
