# 📁 file_actions.py — Acciones sobre archivos (copiar, mover, eliminar, renombrar)

import os
import shutil
from typing import Optional, Dict, Tuple

try:
    import logger as _logger
except ImportError:
    _logger = None

try:
    import file_index
except ImportError:
    file_index = None


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    """Log seguro con fallback."""
    try:
        if _logger is None:
            raise ImportError("logger.py no disponible")
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[FILE_ACTIONS ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


# ═══════════════════════════════════════════════════════════════════════════
# VALIDACIÓN
# ═══════════════════════════════════════════════════════════════════════════

def _valida_ruta_critica(ruta: str) -> bool:
    """Detecta rutas críticas del sistema."""
    if not ruta:
        return False
    
    ruta_norm = ruta.lower()
    rutas_criticas = {
        "c:\\windows", "c:\\system32", "c:\\program files",
        "c:\\programdata", "/windows", "/system32", "/usr", "/etc", "/root", "/bin",
    }
    
    return any(ruta_norm.startswith(critica) for critica in rutas_criticas)


def _valida_existencia(ruta: str) -> Tuple[bool, str]:
    """Valida que una ruta exista. Retorna (válida, mensaje_error)."""
    if not ruta:
        return False, "Ruta vacía"
    
    if not os.path.exists(ruta):
        return False, f"No existe: {ruta}"
    
    return True, ""


# ═══════════════════════════════════════════════════════════════════════════
# COPIAR
# ═══════════════════════════════════════════════════════════════════════════

def copiar_archivo(origen: str, destino: str) -> Dict:
    """
    Copia un archivo de origen a destino.
    
    Retorna:
    {
        "exito": bool,
        "ruta_destino": str,
        "tamanio_bytes": int,
        "mensaje": str
    }
    """
    try:
        # Validaciones
        if _valida_ruta_critica(origen):
            return {
                "exito": False,
                "mensaje": f"No puedo copiar desde {origen} (ruta crítica del sistema)",
            }
        
        valido, error = _valida_existencia(origen)
        if not valido:
            return {"exito": False, "mensaje": error}
        
        if _valida_ruta_critica(destino):
            return {
                "exito": False,
                "mensaje": f"No puedo copiar hacia {destino} (ruta crítica del sistema)",
            }
        
        # Si destino es un directorio, usar nombre original
        if os.path.isdir(destino):
            destino = os.path.join(destino, os.path.basename(origen))
        
        # Crear directorio de destino si no existe
        dir_destino = os.path.dirname(destino)
        if dir_destino and not os.path.exists(dir_destino):
            os.makedirs(dir_destino, exist_ok=True)
        
        # Copiar
        shutil.copy2(origen, destino)
        
        tamanio = os.path.getsize(destino)
        
        # Registrar en índice
        if file_index is not None:
            try:
                file_index.registrar_ruta(destino)
            except Exception:
                pass
        
        return {
            "exito": True,
            "ruta_destino": destino,
            "tamanio_bytes": tamanio,
            "mensaje": f"Archivo copiado: {os.path.basename(destino)} ({tamanio} bytes)",
        }
    except Exception as e:
        _log_error("file_actions", "copiar_archivo", e)
        return {"exito": False, "mensaje": str(e)}


def copiar_directorio(origen: str, destino: str) -> Dict:
    """
    Copia un directorio completo (recursivo).
    
    Retorna:
    {
        "exito": bool,
        "ruta_destino": str,
        "archivos_copiados": int,
        "mensaje": str
    }
    """
    try:
        # Validaciones
        if _valida_ruta_critica(origen):
            return {
                "exito": False,
                "mensaje": f"No puedo copiar desde {origen} (ruta crítica del sistema)",
            }
        
        valido, error = _valida_existencia(origen)
        if not valido:
            return {"exito": False, "mensaje": error}
        
        if not os.path.isdir(origen):
            return {"exito": False, "mensaje": f"{origen} no es un directorio"}
        
        if _valida_ruta_critica(destino):
            return {
                "exito": False,
                "mensaje": f"No puedo copiar hacia {destino} (ruta crítica del sistema)",
            }
        
        # Copiar
        shutil.copytree(origen, destino, dirs_exist_ok=True)
        
        # Contar archivos
        cantidad = sum([len(files) for _, _, files in os.walk(destino)])
        
        return {
            "exito": True,
            "ruta_destino": destino,
            "archivos_copiados": cantidad,
            "mensaje": f"Directorio copiado: {cantidad} archivos",
        }
    except Exception as e:
        _log_error("file_actions", "copiar_directorio", e)
        return {"exito": False, "mensaje": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# MOVER
# ═══════════════════════════════════════════════════════════════════════════

def mover_archivo(origen: str, destino: str) -> Dict:
    """
    Mueve un archivo de origen a destino.
    
    Retorna:
    {
        "exito": bool,
        "ruta_destino": str,
        "mensaje": str
    }
    """
    try:
        # Validaciones
        if _valida_ruta_critica(origen):
            return {
                "exito": False,
                "mensaje": f"No puedo mover desde {origen} (ruta crítica del sistema)",
            }
        
        valido, error = _valida_existencia(origen)
        if not valido:
            return {"exito": False, "mensaje": error}
        
        if _valida_ruta_critica(destino):
            return {
                "exito": False,
                "mensaje": f"No puedo mover hacia {destino} (ruta crítica del sistema)",
            }
        
        # Si destino es un directorio, usar nombre original
        if os.path.isdir(destino):
            destino = os.path.join(destino, os.path.basename(origen))
        
        # Crear directorio de destino si no existe
        dir_destino = os.path.dirname(destino)
        if dir_destino and not os.path.exists(dir_destino):
            os.makedirs(dir_destino, exist_ok=True)
        
        # Mover
        shutil.move(origen, destino)
        
        # Actualizar índice
        if file_index is not None:
            try:
                file_index.registrar_ruta(destino)
            except Exception:
                pass
        
        return {
            "exito": True,
            "ruta_destino": destino,
            "mensaje": f"Archivo movido a: {destino}",
        }
    except Exception as e:
        _log_error("file_actions", "mover_archivo", e)
        return {"exito": False, "mensaje": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# ELIMINAR
# ═══════════════════════════════════════════════════════════════════════════

def eliminar_archivo(ruta: str) -> Dict:
    """
    Elimina un archivo (no directorio).
    
    Retorna:
    {
        "exito": bool,
        "mensaje": str
    }
    """
    try:
        # Validaciones
        if _valida_ruta_critica(ruta):
            return {
                "exito": False,
                "mensaje": f"No puedo eliminar {ruta} (ruta crítica del sistema)",
            }
        
        valido, error = _valida_existencia(ruta)
        if not valido:
            return {"exito": False, "mensaje": error}
        
        if os.path.isdir(ruta):
            return {
                "exito": False,
                "mensaje": f"{ruta} es un directorio. Usa eliminar_directorio() en su lugar.",
            }
        
        # Eliminar
        os.remove(ruta)
        
        return {
            "exito": True,
            "mensaje": f"Archivo eliminado: {os.path.basename(ruta)}",
        }
    except Exception as e:
        _log_error("file_actions", "eliminar_archivo", e)
        return {"exito": False, "mensaje": str(e)}


def eliminar_directorio(ruta: str, recurso: bool = False) -> Dict:
    """
    Elimina un directorio.
    
    Args:
        ruta: Ruta del directorio
        recurso: Si True, elimina recursivamente; si False, solo si está vacío
    
    Retorna:
    {
        "exito": bool,
        "mensaje": str
    }
    """
    try:
        # Validaciones
        if _valida_ruta_critica(ruta):
            return {
                "exito": False,
                "mensaje": f"No puedo eliminar {ruta} (ruta crítica del sistema)",
            }
        
        valido, error = _valida_existencia(ruta)
        if not valido:
            return {"exito": False, "mensaje": error}
        
        if not os.path.isdir(ruta):
            return {"exito": False, "mensaje": f"{ruta} no es un directorio"}
        
        # Eliminar
        if recurso:
            shutil.rmtree(ruta)
            return {
                "exito": True,
                "mensaje": f"Directorio eliminado (junto con su contenido): {ruta}",
            }
        else:
            # Intentar eliminar solo si está vacío
            os.rmdir(ruta)
            return {
                "exito": True,
                "mensaje": f"Directorio eliminado: {ruta}",
            }
    except OSError as e:
        if "directory not empty" in str(e).lower():
            return {
                "exito": False,
                "mensaje": f"El directorio {ruta} no está vacío. Usa recurso=True para eliminarlo con contenido.",
            }
        _log_error("file_actions", "eliminar_directorio", e)
        return {"exito": False, "mensaje": str(e)}
    except Exception as e:
        _log_error("file_actions", "eliminar_directorio", e)
        return {"exito": False, "mensaje": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# RENOMBRAR
# ═══════════════════════════════════════════════════════════════════════════

def renombrar(ruta_antigua: str, nombre_nuevo: str) -> Dict:
    """
    Renombra un archivo o directorio.
    
    Retorna:
    {
        "exito": bool,
        "ruta_nueva": str,
        "mensaje": str
    }
    """
    try:
        # Validaciones
        if _valida_ruta_critica(ruta_antigua):
            return {
                "exito": False,
                "mensaje": f"No puedo renombrar {ruta_antigua} (ruta crítica del sistema)",
            }
        
        valido, error = _valida_existencia(ruta_antigua)
        if not valido:
            return {"exito": False, "mensaje": error}
        
        if not nombre_nuevo or not nombre_nuevo.strip():
            return {"exito": False, "mensaje": "Nombre nuevo vacío"}
        
        # Construir nueva ruta
        directorio = os.path.dirname(ruta_antigua)
        ruta_nueva = os.path.join(directorio, nombre_nuevo) if directorio else nombre_nuevo
        
        # Renombrar
        os.rename(ruta_antigua, ruta_nueva)
        
        # Actualizar índice
        if file_index is not None:
            try:
                file_index.registrar_ruta(ruta_nueva)
            except Exception:
                pass
        
        return {
            "exito": True,
            "ruta_nueva": ruta_nueva,
            "mensaje": f"Renombrado a: {nombre_nuevo}",
        }
    except Exception as e:
        _log_error("file_actions", "renombrar", e)
        return {"exito": False, "mensaje": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════

def obtener_info_archivo(ruta: str) -> Optional[Dict]:
    """Retorna información de un archivo."""
    try:
        valido, _ = _valida_existencia(ruta)
        if not valido:
            return None
        
        stats = os.stat(ruta)
        return {
            "nombre": os.path.basename(ruta),
            "ruta": ruta,
            "es_directorio": os.path.isdir(ruta),
            "tamanio_bytes": stats.st_size,
            "fecha_modificacion": stats.st_mtime,
            "extension": os.path.splitext(ruta)[1],
        }
    except Exception as e:
        _log_error("file_actions", "obtener_info_archivo", e)
        return None


def listar_directorio(ruta: str) -> Dict:
    """
    Lista contenido de un directorio.
    
    Retorna:
    {
        "exito": bool,
        "archivos": [...],
        "directorios": [...],
        "mensaje": str
    }
    """
    try:
        valido, error = _valida_existencia(ruta)
        if not valido:
            return {"exito": False, "mensaje": error}
        
        if not os.path.isdir(ruta):
            return {"exito": False, "mensaje": f"{ruta} no es un directorio"}
        
        archivos = []
        directorios = []
        
        try:
            for item in os.listdir(ruta):
                ruta_item = os.path.join(ruta, item)
                if os.path.isdir(ruta_item):
                    directorios.append(item)
                else:
                    archivos.append(item)
        except PermissionError:
            return {
                "exito": False,
                "mensaje": f"Permiso denegado para listar {ruta}",
            }
        
        return {
            "exito": True,
            "archivos": sorted(archivos),
            "directorios": sorted(directorios),
            "mensaje": f"{len(archivos)} archivo(s), {len(directorios)} directorio(s)",
        }
    except Exception as e:
        _log_error("file_actions", "listar_directorio", e)
        return {"exito": False, "mensaje": str(e)}


if __name__ == "__main__":
    print("file_actions.py está listo para usar")

# ═══════════════════════════════════════════════════════════════════════════
# AÑADIDO (integración GUI): contrato que main.py y core_bridge esperan
#   ejecutar_carpeta_especial(res)    -> {"abierto","texto"} | None   (CARPETA_ESPECIAL)
#   ejecutar_desde_clasificacion(res) -> {"abierto","texto"} | None   (ABRIR_ARCHIVO)
# ═══════════════════════════════════════════════════════════════════════════

import re
import subprocess

try:
    from utils import normalizar_texto, quitar_verbos_y_relleno
except ImportError:  # pragma: no cover
    normalizar_texto = quitar_verbos_y_relleno = None

try:
    from config import (
        VERBOS_ABRIR_ARCHIVO, UMBRAL_ABRIR_ARCHIVO, MARGEN_AMBIGUEDAD_ARCHIVO,
    )
except ImportError:
    VERBOS_ABRIR_ARCHIVO = frozenset()
    UMBRAL_ABRIR_ARCHIVO = 0.75
    MARGEN_AMBIGUEDAD_ARCHIVO = 0.05

_RELLENO_ABRIR = frozenset({
    "abre", "abrir", "abreme", "abrime", "muestra", "muestrame", "ve", "ir", "a", "la", "el", "los",
    "las", "mi", "mis", "un", "una", "de", "carpeta", "directorio", "por", "favor",
})
_RELLENO_ABRIR_ARCHIVO = _RELLENO_ABRIR | frozenset({"archivo", "documento", "pdf", "imagen", "foto"})

# Carpetas de usuario: se piden a Windows (respeta OneDrive), no se adivinan con ~/Documents.
_GUID_CARPETAS = {
    "desktop":   "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "pictures":  "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music":     "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos":    "18989B1D-99B5-42FC-AB31-B4FAE4F5E1E2",
}
_ALIAS_CARPETAS = {
    "escritorio": "desktop", "desktop": "desktop", "descargas": "downloads", "downloads": "downloads",
    "documentos": "documents", "documents": "documents", "fotos": "pictures", "imagenes": "pictures",
    "pictures": "pictures", "musica": "music", "music": "music", "videos": "videos",
}
_ETIQUETAS_CARPETAS = {"desktop": "el Escritorio", "downloads": "Descargas", "documents": "Documentos",
                       "pictures": "Imágenes", "music": "Música", "videos": "Vídeos"}


def _ruta_carpeta_conocida(clave: str) -> Optional[str]:
    """Ruta real de la carpeta de usuario vía SHGetKnownFolderPath; None si no se puede resolver."""
    guid_txt = _GUID_CARPETAS.get(clave)
    if not guid_txt:
        return None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]
        g = uuid.UUID(guid_txt)
        guid = _GUID(g.time_low, g.time_mid, g.time_hi_version, (ctypes.c_ubyte * 8)(*g.bytes[8:]))
        buf = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(buf)) != 0:
            return None
        ruta = buf.value
        ctypes.windll.ole32.CoTaskMemFree(buf)
        return ruta if ruta and os.path.isdir(ruta) else None
    except Exception as e:
        _log_error("file_actions", "_ruta_carpeta_conocida", e)
        return None


def _abrir_en_sistema(ruta: str) -> None:
    if hasattr(os, "startfile"):
        os.startfile(ruta)
    else:  # solo pruebas fuera de Windows
        subprocess.Popen(["xdg-open", ruta])


def _elegir_por_nombre(consulta: str, filas: list, umbral: float, margen: float) -> tuple:
    """-> ('ok', fila) | ('ambiguo', [filas]) | ('no', None). Usa nombre sin extensión."""
    puntuadas = []
    for f in filas:
        base = f.get("nombre") or ""
        if not f.get("es_carpeta"):
            base = os.path.splitext(base)[0]
        cand = normalizar_texto(base)
        if not cand:
            continue
        if consulta == cand:
            s = 1.0
        elif all(t in cand for t in consulta.split()):
            s = 0.80 + 0.20 * min(1.0, len(consulta) / len(cand))
        else:
            continue
        if s >= umbral:
            puntuadas.append((s, f))
    if not puntuadas:
        return "no", None
    puntuadas.sort(key=lambda p: (p[0], p[1].get("veces_accedido") or 0, p[1].get("relevancia") or 0),
                   reverse=True)
    if len(puntuadas) > 1:
        a, b = puntuadas[0], puntuadas[1]
        empate = (a[0] - b[0] < margen and (a[1].get("veces_accedido") or 0) == (b[1].get("veces_accedido") or 0))
        if a[0] < 1.0 and empate:
            return "ambiguo", [f for _s, f in puntuadas[:4]]
        if a[0] == 1.0 and b[0] == 1.0 and (a[1].get("veces_accedido") or 0) == (b[1].get("veces_accedido") or 0):
            return "ambiguo", [f for s, f in puntuadas if s == 1.0][:4]
    return "ok", puntuadas[0][1]


def _texto_ambiguo(que: str, filas: list) -> str:
    lista = "\n".join(f"  • {f['ruta']}" for f in filas)
    return f"Encontré varias {que} con ese nombre:\n{lista}\nDime la ruta completa o sé más específico."


def ejecutar_carpeta_especial(resultado: dict) -> Optional[dict]:
    if resultado.get("categoria") != "CARPETA_ESPECIAL":
        return None
    tn = resultado.get("texto_norm") or normalizar_texto(resultado.get("texto_original", ""))
    nombre = quitar_verbos_y_relleno(tn, (), _RELLENO_ABRIR)
    if not nombre:
        return {"abierto": False, "texto": "¿Qué carpeta quieres abrir?"}

    # 1) Carpetas de usuario (escritorio, descargas, documentos...)
    clave = _ALIAS_CARPETAS.get(nombre)
    if clave:
        ruta = _ruta_carpeta_conocida(clave)
        if not ruta:
            return {"abierto": False, "texto": f"No pude localizar {_ETIQUETAS_CARPETAS[clave]}."}
        try:
            _abrir_en_sistema(ruta)
            return {"abierto": True, "texto": f"Abriendo {_ETIQUETAS_CARPETAS[clave]}..."}
        except Exception as e:
            _log_error("file_actions", "ejecutar_carpeta_especial", e)
            return {"abierto": False, "texto": f"No pude abrir {_ETIQUETAS_CARPETAS[clave]}."}

    # 2) Cualquier otra carpeta: índice de archivos
    if file_index is None:
        return {"abierto": False, "texto": f"No encontré la carpeta '{nombre}'."}
    filas = [f for f in file_index.buscar_archivo(nombre, limite=30) if f.get("es_carpeta")]
    filas = [f for f in filas if os.path.isdir(f.get("ruta", ""))]
    estado, dato = _elegir_por_nombre(nombre, filas, 0.80, MARGEN_AMBIGUEDAD_ARCHIVO)
    if estado == "no":
        return {"abierto": False, "texto": f"No encontré una carpeta llamada '{nombre}'."}
    if estado == "ambiguo":
        return {"abierto": False, "texto": _texto_ambiguo("carpetas", dato)}
    try:
        _abrir_en_sistema(dato["ruta"])
        file_index.registrar_acceso(dato["ruta"])
        return {"abierto": True, "texto": f"Abriendo la carpeta {dato['nombre']}..."}
    except Exception as e:
        _log_error("file_actions", "ejecutar_carpeta_especial (índice)", e)
        return {"abierto": False, "texto": f"Encontré {dato['nombre']} pero no pude abrirla."}


def ejecutar_desde_clasificacion(resultado: dict) -> Optional[dict]:
    if resultado.get("categoria") != "ABRIR_ARCHIVO":
        return None
    tn = resultado.get("texto_norm") or normalizar_texto(resultado.get("texto_original", ""))
    nombre = quitar_verbos_y_relleno(tn, VERBOS_ABRIR_ARCHIVO, _RELLENO_ABRIR_ARCHIVO)
    if not nombre:
        return {"abierto": False, "texto": "¿Qué archivo quieres abrir?"}
    if file_index is None:
        return {"abierto": False, "texto": f"No encontré el archivo '{nombre}'."}
    filas = [f for f in file_index.buscar_archivo(nombre, limite=30)
             if not f.get("es_carpeta") and os.path.isfile(f.get("ruta", ""))]
    estado, dato = _elegir_por_nombre(nombre, filas, UMBRAL_ABRIR_ARCHIVO, MARGEN_AMBIGUEDAD_ARCHIVO)
    if estado == "no":
        return {"abierto": False, "texto": f"No encontré un archivo llamado '{nombre}'."}
    if estado == "ambiguo":
        return {"abierto": False, "texto": _texto_ambiguo("archivos", dato)}
    if _valida_ruta_critica(dato["ruta"]):
        return {"abierto": False, "texto": "Ese archivo está en una ruta del sistema; no lo abro."}
    try:
        _abrir_en_sistema(dato["ruta"])
        file_index.registrar_acceso(dato["ruta"])
        return {"abierto": True, "texto": f"Abriendo {dato['nombre']}...",
                "_mejor_coincidencia": {"ruta": dato["ruta"], "nombre": dato["nombre"]}}
    except Exception as e:
        _log_error("file_actions", "ejecutar_desde_clasificacion", e)
        return {"abierto": False, "texto": f"Encontré {dato['nombre']} pero no pude abrirlo."}


def abrir_ruta(ruta: str) -> bool:
    """Abre un archivo/carpeta con el programa asociado (lo usa habitos.ejecutar_paquete)."""
    try:
        if not ruta or not os.path.exists(ruta) or _valida_ruta_critica(ruta):
            return False
        _abrir_en_sistema(ruta)
        if file_index is not None:
            try:
                file_index.registrar_acceso(ruta)
            except Exception:
                pass
        return True
    except Exception as e:
        _log_error("file_actions", "abrir_ruta", e)
        return False
