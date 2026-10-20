# 📁 web_navigator.py — Navegación web inteligente (v4: HACER vs BUSCAR)
#
# Cambios v4:
#   * "abre / pon / reproduce" LLEVAN al destino (portada, primer video de YouTube, primer resultado de Google);
#     "busca" solo muestra resultados con todas las palabras dichas (ya no se pierde el "que").
#   * "abre netflix en google": google es el LUGAR, no el término (antes buscaba "google" dentro de Netflix).
#   * Capa de IA local (conversacion.limpiar_busqueda) para órdenes de voz/WhatsApp.
#   * Logs SARA-NAV con el plan completo (modo, lugar, término, URL).
#
#
# Cambios v3:
#   * El navegador pedido en la frase ("abre chrome", "abre el navegador edge",
#     "abre youtube en opera gx", "busca gatos en google con firefox") AHORA se detecta y se usa.
#     Antes la frase nunca se miraba en busca de un navegador y "opera gx" acababa como término
#     de búsqueda ("Buscando 'opera' en youtube").
#   * Detección de navegadores independiente del índice de apps: rutas típicas (incl. Opera GX y
#     Brave en %LOCALAPPDATA%), Registro de Windows (App Paths) y Menú Inicio (.lnk resuelto).
#   * "abre chrome" / "abre el navegador chrome" (categoría ABRIR_APLICACION) se resuelve aquí.
#   * Si el navegador pedido no está instalado se AVISA (nunca se abre otro en silencio).
#   * Sitios y verbos con límite de palabra ("bing" ya no coincide dentro de "bingo").

import logging
import os
import re
import subprocess
import threading
import time
import urllib.request
import zlib
from typing import Optional, List, Dict, Tuple
from urllib.parse import quote

try:
    import winreg
except ImportError:  # fuera de Windows
    winreg = None

logger = logging.getLogger("SARA-NAV")

try:
    import logger as _logger
except ImportError:
    _logger = None

try:                                    # opcional (corolario v8): sin él se usa el orden fijo de siempre
    import navegador_activo
except Exception:
    navegador_activo = None

try:
    from config import (
        SITIOS_WEB_CONOCIDOS, RUTAS_PREDECIDAS_NAVEGADORES,
        INSTRUCCIONES_CONVERSACIONALES, VERBOS_PREVIOS_SITIO, VERBOS_BUSQUEDA_IGNORAR,
    )
except ImportError:
    SITIOS_WEB_CONOCIDOS = {}
    RUTAS_PREDECIDAS_NAVEGADORES = {}
    INSTRUCCIONES_CONVERSACIONALES = frozenset()
    VERBOS_PREVIOS_SITIO = frozenset()
    VERBOS_BUSQUEDA_IGNORAR = frozenset()

try:
    from utils import normalizar_texto, similitud
except ImportError:
    from utils import normalizar_texto
    similitud = lambda a, b: 1.0 if a == b else 0.0


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        if _logger is None:
            raise ImportError("logger.py no disponible")
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# CATÁLOGO DE NAVEGADORES
# ═══════════════════════════════════════════════════════════════════════════
# "marca": Opera y Opera GX comparten opera.exe; se distinguen por la ruta.

_CATALOGO: Dict[str, dict] = {
    "chrome": {
        "nombre": "Chrome", "alias": ["google chrome", "chrome"], "exe": "chrome.exe", "buscar_menu": "chrome",
        "rutas": [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                  r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                  r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"],
    },
    "edge": {
        "nombre": "Edge", "alias": ["microsoft edge", "edge"], "exe": "msedge.exe", "buscar_menu": "edge",
        "rutas": [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
                  r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"],
    },
    "firefox": {
        "nombre": "Firefox", "alias": ["mozilla firefox", "firefox", "mozilla"], "exe": "firefox.exe",
        "buscar_menu": "firefox",
        "rutas": [r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
                  r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe"],
    },
    "opera_gx": {
        "nombre": "Opera GX", "alias": ["opera gx", "operagx"], "exe": "opera.exe", "buscar_menu": "opera gx",
        "marca": "gx",
        "rutas": [r"%LOCALAPPDATA%\Programs\Opera GX\opera.exe",
                  r"%LOCALAPPDATA%\Programs\Opera GX\launcher.exe",
                  r"%ProgramFiles%\Opera GX\opera.exe",
                  r"%ProgramFiles(x86)%\Opera GX\opera.exe"],
    },
    "opera": {
        "nombre": "Opera", "alias": ["opera"], "exe": "opera.exe", "buscar_menu": "opera", "marca": "!gx",
        "rutas": [r"%LOCALAPPDATA%\Programs\Opera\opera.exe",
                  r"%LOCALAPPDATA%\Programs\Opera\launcher.exe",
                  r"%ProgramFiles%\Opera\opera.exe",
                  r"%ProgramFiles(x86)%\Opera\opera.exe"],
    },
    "brave": {
        "nombre": "Brave", "alias": ["brave"], "exe": "brave.exe", "buscar_menu": "brave",
        "rutas": [r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
                  r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"],
    },
}

_ORDEN_PREFERENCIA = ["chrome", "edge", "firefox", "opera_gx", "opera", "brave"]

_cache_rutas: Dict[str, str] = {}
_cache_lock = threading.Lock()


def limpiar_cache_navegadores() -> None:
    with _cache_lock:
        _cache_rutas.clear()


def _marca_valida(cfg: dict, ruta: str) -> bool:
    marca = cfg.get("marca")
    r = (ruta or "").lower()
    if marca == "gx":
        return "gx" in r
    if marca == "!gx":
        return "gx" not in r
    return True


def _ruta_registro(exe: str) -> Optional[str]:
    """Registro de Windows: HKLM/HKCU\\...\\App Paths\\<exe> (valor predeterminado)."""
    if winreg is None:
        return None
    clave = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for raiz in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for vista in (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)):
            try:
                with winreg.OpenKey(raiz, clave, 0, winreg.KEY_READ | vista) as k:
                    valor, _ = winreg.QueryValueEx(k, "")
                valor = (valor or "").strip().strip('"')
                if valor and os.path.exists(valor):
                    return valor
            except OSError:
                continue
    return None


def _resolver_lnk(ruta_lnk: str) -> Optional[str]:
    """Destino real de un acceso directo del Menú Inicio (necesita pywin32)."""
    try:
        import win32com.client
        destino = win32com.client.Dispatch("WScript.Shell").CreateShortCut(ruta_lnk).Targetpath
        return destino if destino and os.path.exists(destino) else None
    except Exception as e:
        _log_error("web_navigator", "_resolver_lnk", e)
        return None


def _ruta_menu_inicio(cfg: dict) -> Optional[str]:
    try:
        import app_index
        consulta = cfg["buscar_menu"]
        for c in app_index.buscar_aplicacion(consulta, limite=10):
            nombre = normalizar_texto(c.get("nombre") or "")
            if not re.search(rf"(?<!\w){re.escape(consulta)}(?!\w)", nombre):
                continue
            lnk = c.get("exe") or ""
            destino = _resolver_lnk(lnk) if lnk.lower().endswith(".lnk") else (lnk if os.path.exists(lnk) else None)
            if destino and destino.lower().endswith(".exe") and _marca_valida(cfg, destino + " " + nombre):
                return destino
    except Exception as e:
        _log_error("web_navigator", "_ruta_menu_inicio", e)
    return None


def _ruta_navegador(nav_id: str) -> Optional[str]:
    """Ruta del ejecutable: rutas típicas → Registro → Menú Inicio. None si no está instalado."""
    cfg = _CATALOGO.get(nav_id)
    if cfg is None:
        return None
    with _cache_lock:
        hit = _cache_rutas.get(nav_id)
    if hit and os.path.exists(hit):
        return hit

    candidatas = list(cfg["rutas"]) + [
        r for r in RUTAS_PREDECIDAS_NAVEGADORES.get(nav_id, []) if r not in cfg["rutas"]]
    usuario = os.environ.get("USERNAME", "")
    for ruta in candidatas:
        ruta = os.path.expandvars(ruta.replace("{username}", usuario))
        if os.path.exists(ruta) and _marca_valida(cfg, ruta):
            return _guardar_cache(nav_id, ruta)

    ruta = _ruta_registro(cfg["exe"])
    if ruta and _marca_valida(cfg, ruta):
        return _guardar_cache(nav_id, ruta)

    ruta = _ruta_menu_inicio(cfg)
    if ruta:
        return _guardar_cache(nav_id, ruta)
    return None


def _guardar_cache(nav_id: str, ruta: str) -> str:
    with _cache_lock:
        _cache_rutas[nav_id] = ruta
    return ruta


def nombre_navegador(nav_id: str) -> str:
    return _CATALOGO.get(nav_id, {}).get("nombre", nav_id)


def _nombre_por_ruta(ruta: Optional[str]) -> Optional[str]:
    """Nombre legible del navegador que SARA eligió sola (para decir «en Chrome» en la respuesta)."""
    with _cache_lock:
        for nav_id, r in _cache_rutas.items():
            if r == ruta:
                return nombre_navegador(nav_id)
    return None


# ═══════════════════════════════════════════════════════════════════════════
# DETECCIÓN Y SELECCIÓN DE NAVEGADOR
# ═══════════════════════════════════════════════════════════════════════════

def detectar_navegadores_instalados() -> Dict[str, str]:
    """{nav_id: ruta_exe} de los navegadores realmente instalados."""
    encontrados = {}
    for nav_id in _CATALOGO:
        ruta = _ruta_navegador(nav_id)
        if ruta:
            encontrados[nav_id] = ruta
    return encontrados


def detectar_navegador_en_texto(texto: str) -> Optional[str]:
    """nav_id mencionado en la frase ('opera gx' gana sobre 'opera'), o None."""
    t = normalizar_texto(texto or "")
    mejor: Optional[Tuple[str, int]] = None
    for nav_id, cfg in _CATALOGO.items():
        for alias in cfg["alias"]:
            if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", t) and (mejor is None or len(alias) > mejor[1]):
                mejor = (nav_id, len(alias))
    return mejor[0] if mejor else None


def _quitar_navegador(texto_norm: str, nav_id: str) -> str:
    """'abre youtube en opera gx' → 'abre youtube'; 'abre el navegador chrome' → 'abre'."""
    alias = "|".join(re.escape(a) for a in sorted(_CATALOGO[nav_id]["alias"], key=len, reverse=True))
    patron = rf"(?<!\w)(?:(?:en|con|usando|desde|mediante)\s+)?(?:el\s+)?(?:navegador\s+)?(?:{alias})(?!\w)"
    t = re.sub(patron, " ", texto_norm)
    t = re.sub(r"(?<!\w)navegador(?!\w)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def seleccionar_navegador(preferencia: Optional[str] = None) -> Optional[str]:
    """Ruta del navegador: el pedido (id o alias) → el que tienes abierto / el que más usas
    (navegador_activo) → orden de preferencia → cualquiera instalado."""
    if preferencia:
        nav_id = preferencia if preferencia in _CATALOGO else detectar_navegador_en_texto(preferencia)
        if nav_id:
            return _ruta_navegador(nav_id)
    navegadores = detectar_navegadores_instalados()
    if navegador_activo is not None:
        try:
            elegido = navegador_activo.elegir(navegadores)
            if elegido:
                return elegido["ruta"]
        except Exception as e:
            _log_error("web_navigator", "seleccionar_navegador (navegador_activo)", e)
    for nav_id in _ORDEN_PREFERENCIA:
        if nav_id in navegadores:
            return navegadores[nav_id]
    return next(iter(navegadores.values()), None)


def buscar_navegador_por_nombre(nombre: str) -> Optional[str]:
    nav_id = detectar_navegador_en_texto(nombre)
    return _ruta_navegador(nav_id) if nav_id else None


# ═══════════════════════════════════════════════════════════════════════════
# RESOLUCIÓN DE SITIOS WEB
# ═══════════════════════════════════════════════════════════════════════════

def resolver_sitio_web(nombre_sitio: str) -> Optional[Dict]:
    """Nombre de sitio → configuración (búsqueda exacta + fuzzy)."""
    nombre_norm = normalizar_texto(nombre_sitio).lower()

    for sitio_id, config in SITIOS_WEB_CONOCIDOS.items():
        for alias in config.get("alias", []):
            if normalizar_texto(alias).lower() == nombre_norm:
                return {"id": sitio_id, "url_base": config["url_base"], "url_home": config["url_home"]}

    mejores = {}
    for sitio_id, config in SITIOS_WEB_CONOCIDOS.items():
        for alias in config.get("alias", []):
            score = similitud(nombre_norm, normalizar_texto(alias).lower())
            if score > 0.7:
                mejores[sitio_id] = (config, score)

    if mejores:
        sitio_id = max(mejores.keys(), key=lambda k: mejores[k][1])
        config = mejores[sitio_id][0]
        return {"id": sitio_id, "url_base": config["url_base"], "url_home": config["url_home"]}
    return None

_SITIOS_SIN_BUSQUEDA = frozenset({"whatsapp", "outlook", "instagram"})
def generar_url_busqueda(termino: str, sitio_config: Dict) -> str:
    base = sitio_config.get("url_base", "")
    if (sitio_config.get("id") in _SITIOS_SIN_BUSQUEDA or not base) and sitio_config.get("url_home"):
        return sitio_config["url_home"]      # el sitio no tiene URL de búsqueda: abre la portada
    return f"{base}{quote(termino)}"


# ═══════════════════════════════════════════════════════════════════════════
# EXTRACCIÓN DE BÚSQUEDA
# ═══════════════════════════════════════════════════════════════════════════

_STOPWORDS_TERMINO = frozenset({
    "a", "al", "el", "la", "los", "las", "un", "una", "de", "del", "en", "con", "por", "para", "y", "o",
    "que", "mi", "mis", "tu", "su", "favor", "navegador", "usando", "desde", "mediante", "pagina", "sitio",
    "ya", "ahora", "rapido", "porfa", "porfavor", "otra", "vez", "nuevo", "nueva",
})

# Verbos de "poner / reproducir / ver" que abren o buscan contenido en un sitio ("pon X en youtube").
# Solo se quitan al INICIO de la frase, para no borrar palabras legítimas del término buscado.
_VERBOS_INICIALES = frozenset({
    "pon", "poner", "ponme", "ponga", "reproduce", "reproducir", "reproduceme", "toca", "tocar",
    "mira", "mirar", "ver", "veo", "escucha", "escuchar", "muestrame", "muestra", "ensename", "play",
    "dame", "quiero", "necesito", "me", "gustaria", "podrias", "puedes", "algo", "un", "una",
})


def _sin_palabras(texto: str, palabras) -> str:
    for p in sorted((normalizar_texto(str(x)) for x in palabras if x), key=len, reverse=True):
        if p:
            texto = re.sub(rf"(?<!\w){re.escape(p)}(?!\w)", " ", texto)
    return texto


def extraer_termino_busqueda(texto_original: str) -> Optional[str]:
    """Quita instrucciones, verbos de búsqueda/apertura y relleno; lo que queda es lo que se busca."""
    texto = normalizar_texto(texto_original)
    texto = _sin_palabras(texto, INSTRUCCIONES_CONVERSACIONALES)
    texto = _sin_palabras(texto, VERBOS_BUSQUEDA_IGNORAR)
    texto = _sin_palabras(texto, VERBOS_PREVIOS_SITIO)
    tokens = texto.split()
    while tokens and tokens[0] in _VERBOS_INICIALES:
        tokens.pop(0)
    palabras = [p for p in tokens if p not in _STOPWORDS_TERMINO]
    termino = " ".join(palabras).strip()
    return termino or None


def identificar_sitio_en_frase(texto_original: str) -> Optional[Dict]:
    """Sitio conocido mencionado en la frase (alias como palabra completa; gana el alias más largo)."""
    texto_norm = normalizar_texto(texto_original).lower()
    mejor = None
    for sitio_id, config in SITIOS_WEB_CONOCIDOS.items():
        for alias in config.get("alias", []):
            a = normalizar_texto(alias).lower()
            if a and re.search(rf"(?<!\w){re.escape(a)}(?!\w)", texto_norm) and (mejor is None or len(a) > mejor[0]):
                mejor = (len(a), {"sitio_id": sitio_id, "url_base": config["url_base"], "url_home": config["url_home"]})
    return mejor[1] if mejor else None


# ═══════════════════════════════════════════════════════════════════════════
# APERTURA
# ═══════════════════════════════════════════════════════════════════════════

def _abrir_en(nav_id: Optional[str], url: Optional[str]) -> Tuple[bool, Optional[str]]:
    """Abre `url` (o solo el navegador si url es None). nav_id None → navegador por defecto de SARA.
    Retorna (ok, ruta_usada)."""
    ruta = _ruta_navegador(nav_id) if nav_id else seleccionar_navegador()
    if ruta:
        try:
            subprocess.Popen([ruta] + ([url] if url else []), close_fds=True)
            if nav_id and navegador_activo is not None:      # lo pidió a mano: cuenta como un voto de uso
                navegador_activo.registrar_eleccion(nav_id)
            return True, ruta
        except OSError as e:
            _log_error("web_navigator", f"_abrir_en({nav_id})", e)
            return False, ruta
    if nav_id is None and url:      # ningún navegador detectado: el predeterminado de Windows
        try:
            os.startfile(url)
            return True, None
        except Exception as e:
            _log_error("web_navigator", "_abrir_en (startfile)", e)
    return False, None


def abrir_url(url: str, navegador: Optional[str] = None) -> bool:
    """Compatibilidad: `navegador` es la RUTA de un ejecutable (o None)."""
    if not url:
        return False
    if navegador:
        try:
            subprocess.Popen([navegador, url], close_fds=True)
            return True
        except OSError as e:
            _log_error("web_navigator", "abrir_url", e)
            return False
    return _abrir_en(None, url)[0]


def buscar_en_web(termino: str, sitio_preferido: Optional[str] = None,
                  navegador: Optional[str] = None) -> bool:
    """Compatibilidad: `navegador` es la RUTA de un ejecutable (o None)."""
    if not termino or not termino.strip():
        return False
    sitio_config = (resolver_sitio_web(sitio_preferido) if sitio_preferido else None) or resolver_sitio_web("google")
    if not sitio_config:
        return False
    return abrir_url(generar_url_busqueda(termino, sitio_config), navegador)


def abrir_home_navegador(navegador: Optional[str] = None) -> bool:
    """Abre el navegador sin URL. `navegador`: ruta de ejecutable, nav_id o None."""
    try:
        if navegador and navegador in _CATALOGO:
            return _abrir_en(navegador, None)[0]
        if navegador and os.path.exists(navegador):
            subprocess.Popen([navegador], close_fds=True)
            return True
        return _abrir_en(None, None)[0]
    except Exception as e:
        _log_error("web_navigator", "abrir_home_navegador", e)
        return False


def obtener_navegadores_disponibles() -> List[str]:
    return list(detectar_navegadores_instalados().keys())


# ═══════════════════════════════════════════════════════════════════════════
# ORQUESTACIÓN  (v4: HACER vs BUSCAR)
# ═══════════════════════════════════════════════════════════════════════════
# Dos intenciones distintas:
#   HACER  (abre, pon, reproduce, toca, entra a...) → lleva al usuario AL destino, sin que haga clic:
#       "abre netflix en google"            → portada de Netflix ("en google" es solo el lugar, no el término)
#       "pon botella tras botella en youtube" → reproduce el primer video
#       "abre la sep en google"             → primer resultado de Google ("voy a tener suerte")
#   BUSCAR (busca, buscame, googlea, encuentra, investiga) → solo la página de resultados, con TODAS las
#       palabras dichas ("busca en google que es python" → «que es python»).

_RE_DOMINIO = re.compile(r"(?<![\w.@-])((?:[a-z0-9-]+\.)+(?:com|net|org|mx|io|dev|app|edu|gov|es|co|tv|ai|info)(?:/\S*)?)(?![\w])")
_RE_VERBO_BUSQUEDA = re.compile(r"\b(busca|buscar|buscame|encuentra|investiga|googlea)\b")

_VERBOS_BUSCAR = frozenset({"busca", "buscar", "buscame", "busque", "encuentra", "encuentrame",
                            "investiga", "googlea", "googlear"})
_PREP_LUGAR = r"(?:en|con|por|desde|mediante|usando|a traves de)"
_ARTICULO = r"(?:el|la|los|las|mi)"
_RE_PISTA_WEB = re.compile(rf"(?<!\w){_PREP_LUGAR}\s+(?:{_ARTICULO}\s+)?(?:internet|web|linea|navegador)(?!\w)")
_RE_PISTA_WEB_2 = re.compile(r"(?<!\w)en\s+la\s+web(?!\w)")
_MOTOR_CON_PRIMER_RESULTADO = "google"          # único con "voy a tener suerte" (btnI)

# Cortesías / relleno que pueden ir ANTES del verbo ("oye sara, podrías abrir ...")
_RELLENO_ANTES_VERBO = ("oye sara", "hey sara", "hola sara", "ok sara", "sara", "por favor", "porfa",
                        "podrias", "puedes", "me puedes", "me podrias", "quiero que", "necesito que", "ayudame a")
_RELLENO_FINAL = ("por favor", "porfa", "porfavor", "gracias", "ya", "ahora")
_ARTICULOS_INICIALES = frozenset({"el", "la", "los", "las", "un", "una"})
# Verbos de HACER que se quitan del inicio del término (en modo buscar solo se quitan los de buscar)
_VERBOS_HACER = _VERBOS_INICIALES | frozenset({"abre", "abrir", "abreme", "entra", "ve", "ir", "lleva", "llevame",
                                               "reproduce", "ponme", "pon", "pone", "vamos"})


def _cfg_sitio(sitio_id: str) -> Optional[Dict]:
    c = SITIOS_WEB_CONOCIDOS.get(sitio_id)
    if not c:
        return None
    return {"id": sitio_id, "url_base": c.get("url_base", ""), "url_home": c.get("url_home", "")}


def _menciones_sitios(texto_norm: str) -> List[dict]:
    """Todos los sitios nombrados en la frase, con su posición y si van precedidos de 'en/con/por...'
    ('abre netflix en google' → netflix (objetivo), google (lugar))."""
    cand = []
    for sid, cfg in SITIOS_WEB_CONOCIDOS.items():
        for alias in cfg.get("alias", []):
            a = normalizar_texto(alias)
            if not a:
                continue
            for m in re.finditer(rf"(?<!\w){re.escape(a)}(?!\w)", texto_norm):
                cand.append({"id": sid, "ini": m.start(), "fin": m.end(), "len": len(a)})
    cand.sort(key=lambda c: (-c["len"], c["ini"]))          # el alias más largo gana ("google drive" > "google")
    elegidas: List[dict] = []
    for c in cand:
        if all(c["fin"] <= e["ini"] or c["ini"] >= e["fin"] for e in elegidas):
            elegidas.append(c)
    elegidas.sort(key=lambda c: c["ini"])
    for c in elegidas:
        m = re.search(rf"(?<!\w){_PREP_LUGAR}\s+(?:{_ARTICULO}\s+)?$", texto_norm[:c["ini"]])
        c["lugar"] = bool(m)
        c["ini_total"] = m.start() if m else c["ini"]
    return elegidas


def _quitar_tramos(texto: str, tramos: List[Tuple[int, int]]) -> str:
    for ini, fin in sorted(tramos, reverse=True):
        texto = texto[:ini] + " " + texto[fin:]
    return re.sub(r"\s+", " ", texto).strip()


def _sin_relleno_inicial(texto_norm: str) -> str:
    t, cambio = texto_norm, True
    while cambio and t:
        cambio = False
        for p in sorted(_RELLENO_ANTES_VERBO, key=len, reverse=True):
            if t == p:
                return ""
            if t.startswith(p + " "):
                t, cambio = t[len(p) + 1:], True
                break
    return t


def _modo_orden(texto_norm: str) -> str:
    """'buscar' si el verbo principal es de búsqueda; en cualquier otro caso 'hacer'."""
    t = _sin_relleno_inicial(texto_norm)
    primero = t.split()[0] if t.split() else ""
    if primero in _VERBOS_BUSCAR:
        return "buscar"
    if _RE_VERBO_BUSQUEDA.search(texto_norm) and primero not in _VERBOS_HACER:
        return "buscar"
    return "hacer"


def analizar_orden(frase_sin_nav: str) -> dict:
    """Parte la frase (ya SIN el navegador) en: modo, sitio-lugar, sitio-objetivo, término y pista web.
    No abre nada ni toca la red: sirve para decidir y para probar."""
    texto = normalizar_texto(frase_sin_nav)
    modo = _modo_orden(texto)
    men = _menciones_sitios(texto)

    lugares = [m for m in men if m["lugar"]]
    venue = lugares[-1] if lugares else (men[-1] if men else None)
    objetivos = [m for m in men if venue is not None and m is not venue and m["id"] != venue["id"] and not m["lugar"]]

    tramos: List[Tuple[int, int]] = []
    for m in men:
        if m is venue or m["lugar"] or modo == "hacer":      # en modo buscar, un sitio "suelto" es parte del texto buscado
            tramos.append((m["ini_total"], m["fin"]))
    pista_web = False
    for rx in (_RE_PISTA_WEB, _RE_PISTA_WEB_2):
        for m in rx.finditer(texto):
            pista_web = True
            tramos.append((m.start(), m.end()))
    resto = _quitar_tramos(texto, tramos)

    tokens = resto.split()
    verbos = _VERBOS_BUSCAR | _VERBOS_HACER | set(normalizar_texto(str(v)) for v in VERBOS_PREVIOS_SITIO)
    cambio, verbo_visto = True, False
    while tokens and cambio:                                   # quita relleno/verbos SOLO del inicio
        cambio = False
        if not verbo_visto:                                    # "sara"/"por favor" solo antes del verbo:
            for p in sorted(_RELLENO_ANTES_VERBO, key=len, reverse=True):   # "busca sara connor" conserva "sara"
                n = p.split()
                if tokens[:len(n)] == n:
                    tokens, cambio = tokens[len(n):], True
                    break
        if cambio or not tokens:
            continue
        if tokens[0] in verbos:
            tokens, cambio, verbo_visto = tokens[1:], True, True
        elif modo == "hacer" and tokens[0] in _ARTICULOS_INICIALES:
            tokens, cambio = tokens[1:], True
    while tokens:                                              # y "por favor" y similares del final
        for p in _RELLENO_FINAL:
            n = p.split()
            if tokens[-len(n):] == n:
                tokens = tokens[:-len(n)]
                break
        else:
            break
    while tokens and tokens[0] in ("en", "con", "por", "de", "a") and len(tokens) > 1 and modo == "hacer":
        tokens = tokens[1:]                                    # sobras de preposición ("pon en youtube X")
    termino = " ".join(tokens).strip() or None

    return {"modo": modo, "lugar": venue["id"] if venue else None,
            "lugar_explicito": bool(venue and venue["lugar"]),
            "objetivo": objetivos[-1]["id"] if objetivos else None,
            "termino": termino, "pista_web": pista_web}


# ─── PRIMER VIDEO DE YOUTUBE (sin API key ni dependencias) ────────────────

_cache_videos: Dict[str, str] = {}
_RE_VIDEO_ID = re.compile(r'"videoRenderer":\{"videoId":"([\w-]{11})"')
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")


def _primer_video_youtube(termino: str, timeout: float = 4.0) -> Optional[str]:
    """ID del primer VIDEO de los resultados de YouTube para `termino`, o None (se registra el motivo).
    Si YouTube cambia su HTML o no hay red, devuelve None y quien llama abre la página de resultados."""
    clave = termino.lower().strip()
    if clave in _cache_videos:
        return _cache_videos[clave]
    url = "https://www.youtube.com/results?search_query=" + quote(termino) + "&sp=EgIQAQ%253D%253D"   # solo videos
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Language": "es-MX,es;q=0.9",
                                                   "Accept-Encoding": "gzip",
                                                   "Cookie": "CONSENT=YES+1; SOCS=CAI"})
        m = None
        with urllib.request.urlopen(req, timeout=timeout) as r:
            # v4.1: lectura EN FLUJO (gzip) y se corta en cuanto aparece el primer video; antes se bajaban
            # hasta 2 MB sin comprimir aunque el primer resultado estuviera en los primeros cientos de KB.
            des = zlib.decompressobj(16 + zlib.MAX_WBITS) if "gzip" in (r.headers.get("Content-Encoding") or "") else None
            buf, leidos = "", 0
            while leidos < 2_000_000:
                trozo = r.read(32768)
                if not trozo:
                    break
                leidos += len(trozo)
                datos = des.decompress(trozo) if des else trozo
                previo = max(0, len(buf) - 80)
                buf += datos.decode("utf-8", "ignore")
                m = _RE_VIDEO_ID.search(buf, previo)
                if m:
                    break
    except Exception as e:
        logger.warning(f"⚠️ YouTube: no pude consultar los resultados ({type(e).__name__}: {e})")
        return None
    ms = int((time.perf_counter() - t0) * 1000)
    if not m:
        logger.warning(f"⚠️ YouTube: respondió ({ms} ms) pero no encontré ningún video en el HTML "
                       f"(¿cambió el formato de la página?)")
        return None
    logger.info(f"▶️ YouTube: primer video para «{termino}» → {m.group(1)} ({ms} ms)")
    if len(_cache_videos) >= 64:
        _cache_videos.clear()
    _cache_videos[clave] = m.group(1)
    return m.group(1)


def _url_primer_resultado_google(termino: str) -> str:
    """'Voy a tener suerte': Google redirige al primer resultado (si no puede, muestra los resultados)."""
    return f"https://www.google.com/search?q={quote(termino)}&btnI=1"


# ─── REFINAMIENTO CON IA LOCAL (voz / WhatsApp) ───────────────────────────

_ORIGENES_IA_BUSQUEDA = ("voz", "whatsapp")     # el texto tecleado no pasa por la IA local


def _origen_ia(resultado: dict) -> Optional[str]:
    """Una nota de voz de Telegram llega con origen «telegram» pero entrada «voz»: cuenta como voz (lo dictado
    puede venir mal oído); lo tecleado en Telegram no. Sin dato de entrada se usa el origen de siempre."""
    return "voz" if resultado.get("entrada") == "voz" else resultado.get("origen")
_MSG_NO_ENTENDI_BUSQUEDA = "No entendí qué buscar."


def _refinar_con_ia_local(frase: str, termino: Optional[str], lugar: Optional[str],
                          origen: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Capa STT: la IA local (conversacion.limpiar_busqueda) corrige lo dictado ('yutub') y confirma el sitio.
    Solo actúa en órdenes de voz/WhatsApp. NUNCA borra lo que las reglas ya sacaron: si la IA no propone nada
    útil devuelve (termino, lugar) sin tocar. Cualquier fallo se registra y se sigue con las reglas."""
    if origen not in _ORIGENES_IA_BUSQUEDA:
        return termino, lugar
    try:
        import conversacion
        limpiar = getattr(conversacion, "limpiar_busqueda", None)
        if limpiar is None:
            logger.warning("⚠️ conversacion.limpiar_busqueda no existe en el archivo actual")
            return termino, lugar
        sitios = {sid: list(cfg.get("alias", [])) for sid, cfg in SITIOS_WEB_CONOCIDOS.items()}
        r = limpiar(frase, sitios, termino) or {}
    except Exception as e:
        _log_error("web_navigator", "_refinar_con_ia_local", e)
        logger.warning(f"⚠️ IA local de búsqueda falló ({type(e).__name__}: {e}); sigo con las reglas")
        return termino, lugar

    ia_busqueda = " ".join(str(r.get("busqueda") or "").split())
    ia_sitio = str(r.get("sitio") or "")
    if ia_sitio and ia_sitio in SITIOS_WEB_CONOCIDOS and not lugar:
        lugar = ia_sitio
    if ia_busqueda:
        if lugar:
            cfg = SITIOS_WEB_CONOCIDOS.get(lugar, {})
            ruido = list(cfg.get("alias", [])) + [lugar]
            ia_busqueda = " ".join(_sin_palabras(normalizar_texto(ia_busqueda), ruido).split())
        if ia_busqueda:
            logger.info(f"🔎 IA local afinó la búsqueda: «{termino}» → «{ia_busqueda}»")
            termino = ia_busqueda
    return termino, lugar


# ─── DECISIÓN: ¿a dónde se lleva al usuario? ──────────────────────────────

def _decidir_destino(plan: dict, hay_nav: bool) -> Optional[dict]:
    """plan → {"url", "mensaje"} o None ('No entendí'). No abre nada."""
    modo, lugar, objetivo = plan["modo"], plan["lugar"], plan["objetivo"]
    termino, pista = plan["termino"], plan["pista_web"]

    if modo == "hacer":
        if objetivo and not termino:                           # "abre netflix en google" → portada de netflix
            cfg = _cfg_sitio(objetivo)
            return {"url": cfg["url_home"], "mensaje": f"Abriendo {objetivo}"}
        sid = objetivo if (objetivo and termino) else lugar    # "abre netflix stranger things en google" → busca EN netflix
        if sid is None:
            if termino and pista:
                sid = "google"
            elif termino and hay_nav:
                return {"url": generar_url_busqueda(termino, _cfg_sitio("google")),
                        "mensaje": f"Buscando '{termino}' en google"}
            else:
                return None
        cfg = _cfg_sitio(sid)
        if cfg is None:
            return None
        if not termino:
            return {"url": cfg["url_home"], "mensaje": f"Abriendo {sid}"}
        if sid == "youtube":
            vid = _primer_video_youtube(termino)
            if vid:
                return {"url": f"https://www.youtube.com/watch?v={vid}", "mensaje": f"Reproduciendo '{termino}' en youtube"}
            logger.warning(f"⚠️ No pude resolver el video de «{termino}»: abro la búsqueda en su lugar")
            return {"url": generar_url_busqueda(termino, cfg),
                    "mensaje": f"No pude resolver el video; busqué '{termino}' en youtube"}
        if sid == _MOTOR_CON_PRIMER_RESULTADO:
            return {"url": _url_primer_resultado_google(termino), "mensaje": f"Abriendo '{termino}' (primer resultado de google)"}
        return {"url": generar_url_busqueda(termino, cfg), "mensaje": f"Buscando '{termino}' en {sid}"}

    # modo buscar: SOLO resultados, con todas las palabras dichas
    sid = lugar or "google"
    cfg = _cfg_sitio(sid)
    if cfg is None:
        return None
    if not termino:
        if lugar:                                              # "busca en youtube" a secas → portada
            return {"url": cfg["url_home"], "mensaje": f"Abriendo {sid}"}
        return None
    return {"url": generar_url_busqueda(termino, cfg), "mensaje": f"Buscando '{termino}' en {sid}"}


def resolver_busqueda_navegador(resultado: dict) -> Optional[dict]:
    """
    HACER:   "abre netflix en google" → portada · "pon X en youtube" → reproduce el primer video
             "abre la sep en google" → primer resultado de google · "abre youtube en opera gx" → portada en ese navegador
    BUSCAR:  "busca gatos en youtube con edge" → resultados · "busca en google que es python" → resultados
    Solo el navegador: "abre chrome" / "abre el navegador edge".
    """
    texto_original = resultado.get("texto_original", "")
    if not texto_original:
        return {"estado": "error", "mensaje": "No entendí qué abrir o buscar."}

    texto_norm = normalizar_texto(texto_original)
    nav_id = detectar_navegador_en_texto(texto_norm)
    sin_nav = _quitar_navegador(texto_norm, nav_id) if nav_id else texto_norm
    nombre_nav = nombre_navegador(nav_id) if nav_id else None

    dominio = _RE_DOMINIO.search(sin_nav)
    if dominio:
        if nav_id and _ruta_navegador(nav_id) is None:
            return {"estado": "error", "mensaje": f"No encontré {nombre_nav} instalado (revisé rutas típicas, el Registro y el Menú Inicio)."}
        url = "https://" + dominio.group(1)
        ok, ruta = _abrir_en(nav_id, url)
        destino_d = f" en {nombre_nav or _nombre_por_ruta(ruta)}" if (nombre_nav or _nombre_por_ruta(ruta)) else ""
        if ok:
            return {"estado": "ok", "mensaje": f"Abriendo {dominio.group(1)}{destino_d}...",
                    "_mejor_coincidencia": ({"ruta_ejecutable": ruta or "", "nombre": nombre_nav or "Navegador",
                                             "dominio": "navegador"} if ruta else None)}
        return {"estado": "error", "mensaje": f"No pude abrir {dominio.group(1)}{destino_d}."}

    plan = analizar_orden(sin_nav)
    plan["termino"], plan["lugar"] = _refinar_con_ia_local(sin_nav, plan["termino"], plan["lugar"],
                                                           _origen_ia(resultado))
    logger.info(f"🌐 navegador: frase=«{texto_original[:80]}» origen={resultado.get('origen')}/{resultado.get('entrada')} modo={plan['modo']} "
                f"lugar={plan['lugar']} objetivo={plan['objetivo']} término=«{plan['termino']}» "
                f"pista_web={plan['pista_web']} navegador={nav_id}")

    if nav_id and _ruta_navegador(nav_id) is None:
        return {"estado": "error",
                "mensaje": f"No encontré {nombre_nav} instalado (revisé rutas típicas, el Registro y el Menú Inicio)."}

    def _ok(ruta, texto):
        mejor = {"ruta_ejecutable": ruta or "", "nombre": nombre_nav or "Navegador", "dominio": "navegador"}
        return {"estado": "ok", "mensaje": texto, "_mejor_coincidencia": mejor if ruta else None}

    destino = f" en {nombre_nav}" if nombre_nav else ""

    # Solo el navegador ("abre chrome", "abre el navegador edge")
    if nav_id and not plan["lugar"] and not plan["objetivo"] and not plan["termino"]:
        ok, ruta = _abrir_en(nav_id, None)
        return _ok(ruta, f"Abriendo {nombre_nav}...") if ok else {"estado": "error", "mensaje": f"No pude abrir {nombre_nav}."}

    dest = _decidir_destino(plan, hay_nav=bool(nav_id))
    if dest is None:
        return {"estado": "error", "mensaje": _MSG_NO_ENTENDI_BUSQUEDA}
    logger.info(f"🌐 abriendo: {dest['url']}")
    ok, ruta = _abrir_en(nav_id, dest["url"])
    if ok and not nav_id and _nombre_por_ruta(ruta):          # lo eligió SARA: lo dice
        destino = f" en {_nombre_por_ruta(ruta)}"
    if ok:
        return _ok(ruta, f"{dest['mensaje']}{destino}...")
    return {"estado": "error", "mensaje": f"No pude abrir el navegador{destino}."}


def ejecutar_desde_clasificacion(resultado: dict, forzar_sitio: bool = False) -> Optional[dict]:
    """Contrato de core_bridge/main: {"abierto","texto","_mejor_coincidencia"} | None.
    None = no es asunto de este módulo ("abre spotify" mal clasificado) → el llamador reintenta como app."""
    cat = resultado.get("categoria")
    if cat not in ("BUSCAR_EN_NAVEGADOR", "ABRIR_APLICACION"):
        return None
    texto = resultado.get("texto_original") or ""
    if not texto:
        return None

    texto_norm = normalizar_texto(texto)
    nav_id = detectar_navegador_en_texto(texto_norm)
    sin_nav = _quitar_navegador(texto_norm, nav_id) if nav_id else texto_norm
    plan = analizar_orden(sin_nav)

    if cat == "ABRIR_APLICACION" and not nav_id:
        if plan["lugar"] is None and not plan["pista_web"]:
            return None                   # abrir una app normal no es asunto de este módulo
        # Un "abre netflix" a secas se deja primero a las apps (podría haber app de escritorio) y solo llega
        # aquí con forzar_sitio=True cuando core_bridge ya probó como app y falló. Pero si el usuario dijo
        # DÓNDE ("en google", "en internet") o trae algo que buscar/reproducir, es web: se atiende ya.
        solo_portada = plan["modo"] == "hacer" and not plan["termino"]
        dijo_donde = plan["lugar_explicito"] or plan["pista_web"]
        if solo_portada and not dijo_donde and not forzar_sitio:
            return None

    # Por voz/WhatsApp el sitio puede venir mal oído ("yutub"): las reglas no lo reconocen, pero la IA local sí.
    por_ia = (cat == "BUSCAR_EN_NAVEGADOR" and _origen_ia(resultado) in _ORIGENES_IA_BUSQUEDA)
    if not (nav_id or plan["lugar"] is not None or _RE_DOMINIO.search(sin_nav)
            or plan["modo"] == "buscar" or plan["pista_web"] or por_ia):
        return None

    r = resolver_busqueda_navegador(resultado)
    if not r:
        return None
    if por_ia and r.get("estado") == "error" and r.get("mensaje") == _MSG_NO_ENTENDI_BUSQUEDA:
        return None
    salida = {"abierto": r.get("estado") == "ok", "texto": r.get("mensaje", "")}
    if r.get("_mejor_coincidencia"):
        salida["_mejor_coincidencia"] = r["_mejor_coincidencia"]
    return salida


if __name__ == "__main__":
    print("Navegadores detectados:", detectar_navegadores_instalados())
    for frase in ("abre netflix en google", "pon botella tras botella en youtube",
                  "busca en google que es python", "abre youtube en opera gx", "abre la sep en google"):
        print(f"{frase!r} →", analizar_orden(_quitar_navegador(normalizar_texto(frase), detectar_navegador_en_texto(frase))
                                             if detectar_navegador_en_texto(frase) else frase))
