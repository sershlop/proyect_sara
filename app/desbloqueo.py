# 🔓 desbloqueo.py — Ventana de desbloqueo con TOTP compartida por envío de archivos y pantalla (v1)
# Un código TOTP correcto abre una ventana (DESBLOQUEO_VENTANA_S, 300 s por defecto). Apagar/reiniciar NO usan
# esta ventana: siguen pidiendo código siempre (telegram_bot + apagado.py).
import logging, threading, time
from typing import Tuple
log = logging.getLogger("SARA-DESBLOQUEO")
try:
    import config as _cfg
except Exception:
    _cfg = None
try:
    import apagado
except Exception:
    apagado = None
try:
    import interruptores
except Exception:
    interruptores = None

def _verif() -> bool:
    """False = el usuario apagó el protocolo de verificación (interruptores.py): no se pide código."""
    return interruptores.verificacion_activa() if interruptores is not None else True

def _c(n, d): return getattr(_cfg, n, d) if _cfg is not None else d

_lock = threading.Lock()
_hasta = 0.0
_fallos = 0
_bloq = 0.0

def disponible() -> bool:
    """¿Hay TOTP configurado? (python apagado.py totp)"""
    if not _verif():
        return True
    return bool(apagado is not None and apagado.totp_configurado())

def activo() -> bool:
    if not _verif():
        return True
    return time.time() < _hasta

def restante() -> int:
    return max(0, int(_hasta - time.time()))

def bloqueado() -> bool:
    return time.time() < _bloq

def cerrar() -> None:
    global _hasta
    with _lock:
        _hasta = 0.0

def intentar(codigo: str) -> Tuple[bool, str]:
    """Verifica el código de 6 dígitos. Nunca lanza."""
    global _hasta, _fallos, _bloq
    try:
        with _lock:
            if time.time() < _bloq:
                return False, f"🔒 Demasiados códigos incorrectos; bloqueado ~{int(_bloq - time.time()) // 60 + 1} min."
            if apagado is None or not apagado.totp_configurado():
                return False, "🔒 Falta configurar el TOTP (en el PC: python apagado.py totp)."
            if apagado.totp_verificar(codigo):
                _fallos = 0
                v = int(_c("DESBLOQUEO_VENTANA_S", 300))
                _hasta = time.time() + v
                log.warning("🔓 Ventana de desbloqueo abierta %d s", v)
                return True, f"🔓 Desbloqueado {v // 60} min (archivos y pantalla)."
            _fallos += 1
            maxi = int(_c("DESBLOQUEO_INTENTOS", 3))
            if _fallos >= maxi:
                _fallos = 0
                _bloq = time.time() + int(_c("DESBLOQUEO_BLOQUEO_S", 600))
                log.warning("🚨 Desbloqueo bloqueado por intentos fallidos")
                return False, "❌ Código incorrecto. Bloqueé archivos y pantalla por unos minutos."
            return False, f"❌ Código incorrecto. Te quedan {maxi - _fallos}."
    except Exception:
        log.exception("desbloqueo.intentar falló")
        return False, "No pude verificar el código."
