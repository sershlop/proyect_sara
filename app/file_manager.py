# 📁 file_manager.py — Gestor de navegación y búsqueda de archivos

import os
import re
from typing import Optional, List, Dict

try:
    import logger as _logger
except ImportError:
    _logger = None

try:
    import file_index
    import file_actions
except ImportError:
    file_index = None
    file_actions = None


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    """Log seguro con fallback."""
    try:
        if _logger is None:
            raise ImportError("logger.py no disponible")
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[FILE_MANAGER ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


# ═══════════════════════════════════════════════════════════════════════════
# BÚSQUEDA Y NAVEGACIÓN
# ═══════════════════════════════════════════════════════════════════════════

def buscar_archivo(termino: str, en_carpeta: Optional[str] = None, limite: int = 20) -> Dict:
    """
    Busca archivos por nombre en el índice o en el filesystem.
    
    Retorna:
    {
        "exito": bool,
        "archivos": [...],
        "cantidad": int,
        "mensaje": str
    }
    """
    if not termino or not termino.strip():
        return {
            "exito": False,
            "archivos": [],
            "cantidad": 0,
            "mensaje": "Término de búsqueda vacío",
        }
    
    try:
        archivos = []
        
        # Intentar búsqueda en índice primero
        if file_index is not None:
            try:
                resultados_indice = file_index.buscar_archivo(termino, limite=limite)
                archivos.extend([
                    {
                        "ruta": r.get("ruta"),
                        "nombre": r.get("nombre"),
                        "es_directorio": bool(r.get("es_carpeta")),
                        "tamanio_kb": r.get("tamanio_kb", 0),
                        "relevancia": r.get("relevancia", 0.0),
                        "fuente": "índice",
                    }
                    for r in resultados_indice
                ])
            except Exception as e:
                _log_error("file_manager", "buscar_archivo (índice)", e)
        
        # Si no hay resultados o no hay índice, buscar en filesystem
        if not archivos and en_carpeta:
            try:
                archivos.extend(_buscar_en_filesystem(termino, en_carpeta, limite))
            except Exception as e:
                _log_error("file_manager", "buscar_archivo (filesystem)", e)
        
        return {
            "exito": len(archivos) > 0,
            "archivos": archivos[:limite],
            "cantidad": len(archivos),
            "mensaje": f"Se encontraron {len(archivos)} archivo(s)",
        }
    except Exception as e:
        _log_error("file_manager", "buscar_archivo", e)
        return {
            "exito": False,
            "archivos": [],
            "cantidad": 0,
            "mensaje": str(e),
        }


def _buscar_en_filesystem(termino: str, carpeta: str, limite: int = 20) -> List[Dict]:
    """Búsqueda recursiva en filesystem (fallback)."""
    resultados = []
    termino_lower = termino.lower()
    contador = 0
    
    try:
        for root, dirs, files in os.walk(carpeta):
            if contador >= limite:
                break
            
            # Buscar en archivos
            for archivo in files:
                if contador >= limite:
                    break
                if termino_lower in archivo.lower():
                    ruta_completa = os.path.join(root, archivo)
                    try:
                        tamanio = os.path.getsize(ruta_completa)
                        resultados.append({
                            "ruta": ruta_completa,
                            "nombre": archivo,
                            "es_directorio": False,
                            "tamanio_kb": tamanio // 1024,
                            "relevancia": 0.7,
                            "fuente": "filesystem",
                        })
                        contador += 1
                    except Exception:
                        pass
            
            # Buscar en directorios
            for directorio in dirs:
                if contador >= limite:
                    break
                if termino_lower in directorio.lower():
                    ruta_completa = os.path.join(root, directorio)
                    resultados.append({
                        "ruta": ruta_completa,
                        "nombre": directorio,
                        "es_directorio": True,
                        "tamanio_kb": 0,
                        "relevancia": 0.8,
                        "fuente": "filesystem",
                    })
                    contador += 1
    except Exception as e:
        _log_error("file_manager", "_buscar_en_filesystem", e)
    
    return resultados


def abrir_carpeta(ruta: str) -> Dict:
    """
    Abre una carpeta (muestra su contenido).
    
    Retorna:
    {
        "exito": bool,
        "ruta_actual": str,
        "archivos": [...],
        "directorios": [...],
        "mensaje": str
    }
    """
    try:
        if not ruta or not os.path.exists(ruta):
            return {
                "exito": False,
                "ruta_actual": ruta,
                "archivos": [],
                "directorios": [],
                "mensaje": f"Carpeta no encontrada: {ruta}",
            }
        
        if not os.path.isdir(ruta):
            return {
                "exito": False,
                "ruta_actual": ruta,
                "archivos": [],
                "directorios": [],
                "mensaje": f"{ruta} no es un directorio",
            }
        
        # Registrar acceso en índice
        if file_index is not None:
            try:
                file_index.registrar_acceso(ruta)
            except Exception:
                pass
        
        # Listar contenido
        archivos = []
        directorios = []
        
        try:
            for item in os.listdir(ruta):
                ruta_item = os.path.join(ruta, item)
                try:
                    if os.path.isdir(ruta_item):
                        directorios.append({
                            "nombre": item,
                            "ruta": ruta_item,
                            "tipo": "directorio",
                        })
                    else:
                        tamanio = os.path.getsize(ruta_item)
                        archivos.append({
                            "nombre": item,
                            "ruta": ruta_item,
                            "tipo": "archivo",
                            "tamanio_kb": tamanio // 1024,
                            "extension": os.path.splitext(item)[1],
                        })
                except Exception:
                    pass
        except PermissionError:
            return {
                "exito": False,
                "ruta_actual": ruta,
                "archivos": [],
                "directorios": [],
                "mensaje": f"Permiso denegado: {ruta}",
            }
        
        return {
            "exito": True,
            "ruta_actual": ruta,
            "archivos": sorted(archivos, key=lambda x: x["nombre"]),
            "directorios": sorted(directorios, key=lambda x: x["nombre"]),
            "mensaje": f"{len(archivos)} archivo(s), {len(directorios)} carpeta(s)",
        }
    except Exception as e:
        _log_error("file_manager", "abrir_carpeta", e)
        return {
            "exito": False,
            "ruta_actual": ruta,
            "archivos": [],
            "directorios": [],
            "mensaje": str(e),
        }


def obtener_ruta_parent(ruta: str) -> str:
    """Retorna la carpeta padre de una ruta."""
    try:
        parent = os.path.dirname(ruta)
        return parent if parent and parent != ruta else ruta
    except Exception:
        return ruta


def obtener_carpeta_conocida(nombre: str) -> Optional[str]:
    """
    Obtiene la ruta de una carpeta conocida por nombre.
    Busca en índice primero, luego en rutas comunes.
    """
    if not nombre:
        return None
    
    try:
        # Carpetas conocidas del sistema
        carpetas_comunes = {
            "desktop": os.path.expanduser("~/Desktop"),
            "escritorio": os.path.expanduser("~/Desktop"),
            "documents": os.path.expanduser("~/Documents"),
            "documentos": os.path.expanduser("~/Documents"),
            "downloads": os.path.expanduser("~/Downloads"),
            "descargas": os.path.expanduser("~/Downloads"),
            "music": os.path.expanduser("~/Music"),
            "musica": os.path.expanduser("~/Music"),
            "pictures": os.path.expanduser("~/Pictures"),
            "imagenes": os.path.expanduser("~/Pictures"),
            "videos": os.path.expanduser("~/Videos"),
            "home": os.path.expanduser("~"),
        }
        
        nombre_norm = nombre.lower()
        
        # Búsqueda exacta primero
        if nombre_norm in carpetas_comunes:
            ruta = carpetas_comunes[nombre_norm]
            if os.path.exists(ruta):
                return ruta
        
        # Búsqueda parcial
        for clave, ruta in carpetas_comunes.items():
            if nombre_norm in clave:
                if os.path.exists(ruta):
                    return ruta
        
        # Búsqueda en índice
        if file_index is not None:
            try:
                carpetas = file_index.obtener_carpetas_conocidas()
                for carpeta in carpetas:
                    if nombre_norm in carpeta.get("nombre", "").lower():
                        ruta = carpeta.get("ruta")
                        if ruta and os.path.exists(ruta):
                            return ruta
            except Exception:
                pass
        
        return None
    except Exception as e:
        _log_error("file_manager", "obtener_carpeta_conocida", e)
        return None


def registrar_carpeta_frecuente(ruta: str) -> bool:
    """Registra una carpeta como frecuentemente accedida."""
    if not ruta or not os.path.isdir(ruta):
        return False
    
    try:
        if file_index is not None:
            file_index.registrar_ruta(ruta)
            file_index.registrar_acceso(ruta)
            return True
    except Exception as e:
        _log_error("file_manager", "registrar_carpeta_frecuente", e)
    
    return False


def obtener_estadisticas() -> Dict:
    """Retorna estadísticas del índice de archivos."""
    if file_index is None:
        return {}
    
    try:
        return file_index.obtener_estadisticas_indice()
    except Exception as e:
        _log_error("file_manager", "obtener_estadisticas", e)
        return {}




# ═══════════════════════════════════════════════════════════════════════════
# GESTIÓN DE ARCHIVOS POR LENGUAJE NATURAL (crear/eliminar/mover/copiar/renombrar/comprimir)
# Contrato que main.py y core_bridge.py esperan:
#   resolver_gestion_archivo(res) -> None | {"estado", "texto"|"mensaje_confirmacion", ...}
#       estados: no_entendido | no_encontrado | ambiguo | rechazado | no_escribible | ejecutado | candidato
#   confirmar_gestion_archivo(accion, datos) -> {"exito", "texto"}
# Eliminar / mover / renombrar NUNCA se ejecutan directo: devuelven "candidato".
# ═══════════════════════════════════════════════════════════════════════════

import shutil

try:
    import deshacer          # papelera propia + «deshaz lo último» (opcional)
except Exception:
    deshacer = None

try:
    from utils import normalizar_texto
except ImportError:  # pragma: no cover
    normalizar_texto = lambda t: (t or "").lower().strip()

_CATS_GESTION = {"CREAR_CARPETA", "ELIMINAR_ARCHIVO", "MOVER_ARCHIVO",
                 "COPIAR_ARCHIVO", "RENOMBRAR_ARCHIVO", "COMPRIMIR_ARCHIVO"}
_RELLENO_GESTION = frozenset({
    "crea", "crear", "nueva", "nuevo", "haz", "hacer", "genera", "elimina", "eliminar", "borra", "borrar",
    "quita", "quitar", "mueve", "mover", "traslada", "copia", "copiar", "duplica", "renombra", "renombrar",
    "cambia", "cambiale", "comprime", "comprimir", "zippea", "empaqueta", "el", "la", "los", "las", "un", "una",
    "mi", "de", "del", "por", "favor", "carpeta", "directorio", "archivo", "fichero", "documento", "nombre",
    "llamada", "llamado", "que", "se", "llame", "con", "sara",
})
_ARTICULOS_DEST = frozenset({"el", "la", "los", "las", "mi", "de", "del", "carpeta", "directorio"})
_CARACTERES_INVALIDOS = set('<>:"/\\|?*')


def _palabras_utiles(original: str) -> List[str]:
    palabras = (original or "").strip().split()
    i = 0
    while i < len(palabras) and normalizar_texto(palabras[i]) in _RELLENO_GESTION:
        i += 1
    return palabras[i:]


def _cortar(palabras: List[str], separadores: set, ultimo: bool) -> tuple:
    idx = [i for i, p in enumerate(palabras) if normalizar_texto(p) in separadores and 0 < i < len(palabras) - 1]
    if not idx:
        return palabras, []
    k = idx[-1] if ultimo else idx[0]
    return palabras[:k], palabras[k + 1:]


def _sin_articulos(palabras: List[str]) -> List[str]:
    i = 0
    while i < len(palabras) - 1 and normalizar_texto(palabras[i]) in _ARTICULOS_DEST:
        i += 1
    return palabras[i:]


def _destino_a_ruta(palabras: List[str]) -> Optional[str]:
    """Carpeta destino: ruta absoluta, alias de Windows (escritorio, descargas...) o carpeta del índice."""
    texto = " ".join(_sin_articulos(palabras)).strip().strip('"')
    if not texto:
        return None
    if os.path.isdir(texto):
        return texto
    nombre = normalizar_texto(texto)
    if file_actions is not None:
        clave = getattr(file_actions, "_ALIAS_CARPETAS", {}).get(nombre)
        if clave:
            ruta = file_actions._ruta_carpeta_conocida(clave)
            if ruta:
                return ruta
    if file_index is not None and file_actions is not None:
        try:
            filas = [f for f in file_index.buscar_archivo(nombre, limite=30, solo_carpetas=True)
                     if os.path.isdir(f.get("ruta", ""))]
            estado, dato = file_actions._elegir_por_nombre(nombre, filas, 0.80, 0.05)
            if estado == "ok":
                return dato["ruta"]
        except Exception as e:
            _log_error("file_manager", "_destino_a_ruta", e)
    return None


def _buscar_origen(palabras: List[str]) -> tuple:
    """-> ('ok', fila) | ('no', None) | ('ambiguo', [filas]). Busca archivos Y carpetas."""
    consulta = normalizar_texto(" ".join(palabras))
    if not consulta:
        return "no", None
    if file_index is None or file_actions is None:
        return "no", None
    filas = [f for f in file_index.buscar_archivo(consulta, limite=30) if os.path.exists(f.get("ruta", ""))]
    return file_actions._elegir_por_nombre(consulta, filas, 0.75, 0.05)


def _fallo_origen(estado: str, dato, palabras: List[str]) -> dict:
    if estado == "ambiguo":
        return {"estado": "ambiguo", "texto": file_actions._texto_ambiguo("coincidencias", dato)}
    return {"estado": "no_encontrado", "texto": f"No encontré '{' '.join(palabras)}'."}


def _nombre_valido(nombre: str) -> bool:
    return bool(nombre) and not (_CARACTERES_INVALIDOS & set(nombre)) and nombre not in (".", "..")


# ═══════════════════════════════════════════════════════════════════════════
# CREAR (carpeta o archivo) — destino y nombre en lenguaje natural
#   «crea una carpeta en descargas llamada prueba»        → Descargas\prueba
#   «crea en documentos una carpeta fotos»                 → Documentos\fotos
#   «crea un archivo notas.txt en el escritorio»           → archivo vacío
#   «crea un archivo main.py en descargas que sume 2 números» → código con Groq (generador_codigo)
#   sin destino → SARADEFINITIVA\carpetas_creadas   (config.CARPETA_CREACION_DEFECTO)
# ═══════════════════════════════════════════════════════════════════════════

try:
    import config as _cfg
except Exception:  # pragma: no cover
    _cfg = None

CARPETA_CREACION_DEFECTO = getattr(_cfg, "CARPETA_CREACION_DEFECTO", "carpetas_creadas")

_PALABRAS_CARPETA = frozenset({"carpeta", "carpetas", "directorio", "folder"})
_PALABRAS_ARCHIVO = frozenset({"archivo", "fichero", "documento", "script", "programa", "codigo"})
_MARCA_NOMBRE = frozenset({"llamada", "llamado", "llamadas", "llamados", "titulada", "titulado", "nombrada", "nombrado"})
_MARCA_DESTINO = frozenset({"en", "dentro", "bajo"})
_MARCA_DESCRIPCION = frozenset({"que", "para", "con", "donde", "cuyo", "cuya"})
_ARTICULOS_PREVIOS = frozenset({"el", "la", "los", "las", "un", "una", "mi", "mis", "del", "de"})
_INICIO = (_RELLENO_GESTION | _PALABRAS_ARCHIVO | _PALABRAS_CARPETA
           | {"me", "puedes", "podrias", "quiero", "necesito", "oye", "porfa", "gracias", "ahora", "ya", "unos", "unas"})
_CONECTORES = frozenset({"en", "de", "con", "para", "un", "una", "el", "la"})
_RESERVADOS_WINDOWS = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_EXT_RESPALDO = {".py", ".js", ".html", ".css", ".json", ".md", ".txt", ".csv", ".sql", ".xml", ".yml", ".yaml"}
_LENGUAJES_RESPALDO = {"python", "py", "javascript", "js", "html", "css", "java", "php", "sql", "typescript", "ts",
                       "node", "nodejs", "bash", "powershell", "go", "rust", "ruby", "lua", "csharp", "cpp"}
_RE_COMILLAS = re.compile(r'["“«]([^"”»]+)["”»]')


def _lenguajes() -> set:
    try:
        import generador_codigo as g
        return set(g.EXTENSIONES_LENGUAJE)
    except Exception:
        return set(_LENGUAJES_RESPALDO)


def _extensiones_permitidas() -> set:
    try:
        import generador_codigo as g
        return set(g.EXTENSIONES_PERMITIDAS)
    except Exception:
        return set(_EXT_RESPALDO)


def carpeta_creacion_defecto() -> str:
    """SARADEFINITIVA\\carpetas_creadas (se crea si no existe). Una ruta absoluta en config también vale."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), CARPETA_CREACION_DEFECTO)
    os.makedirs(ruta, exist_ok=True)
    return ruta


def _nombre_creable(nombre: str) -> bool:
    """_nombre_valido + reglas de Windows que makedirs/open no avisan bien (punto o espacio final, CON, NUL…)."""
    return (_nombre_valido(nombre) and len(nombre) <= 100 and nombre == nombre.rstrip(" .")
            and nombre.split(".")[0].lower() not in _RESERVADOS_WINDOWS)


def _limpia_tok(t: str) -> str:
    return t.strip(" \t\r\n.,;:!?¿¡\"'«»“”()[]")


def _es_nombre_archivo(tok: str, permitidas: set) -> bool:
    return bool(re.fullmatch(r"[\w\-]+(?:\.[\w\-]+)*\.[A-Za-z0-9]{1,6}", tok)) \
        and os.path.splitext(tok)[1].lower() in permitidas


def _quitar_conj_final(toks: List[str]) -> List[str]:
    while toks and normalizar_texto(toks[-1]) in ("y", "e"):
        toks = toks[:-1]
    return toks


def _resolver_destinos(segmentos: List[List[str]]) -> tuple:
    """segmentos = textos tras «en / dentro de». -> (ruta|None, sobrante_tokens, error|None).
    El primero es la base (alias de Windows, ruta absoluta o carpeta del índice); los siguientes, subcarpetas suyas."""
    base, sobrante = None, []
    for k, seg in enumerate(segmentos):
        seg = _sin_articulos(_quitar_conj_final(seg))
        if not seg:
            continue
        if k > 0 and base:                                   # «en descargas en la carpeta pruebas»
            sub = os.path.join(base, " ".join(seg))
            if not os.path.isdir(sub):
                return None, [], f"No encontré la carpeta '{' '.join(seg)}' dentro de {base}."
            base = sub
            continue
        ruta, resto = None, []
        for n in range(min(len(seg), 5), 0, -1):             # el prefijo más largo que sea una carpeta real
            texto = normalizar_texto(" ".join(seg[:n]))
            if texto.replace(" ", "_") == normalizar_texto(os.path.basename(CARPETA_CREACION_DEFECTO.rstrip("\\/"))):
                ruta = carpeta_creacion_defecto()
            else:
                ruta = _destino_a_ruta(seg[:n])
            if ruta:
                resto = seg[n:]
                break
        if not ruta:
            return None, [], f"No encontré la carpeta destino '{' '.join(seg)}'."
        base, sobrante = ruta, resto
    return base, sobrante, None


def analizar_creacion(texto: str) -> dict:
    """Separa TIPO (carpeta|archivo), NOMBRE, DESTINO y DESCRIPCIÓN de una orden de crear. NO toca el disco
    (Principio 8: resolver ≠ ejecutar), salvo crear carpetas_creadas cuando hace falta el destino por defecto.
    Devuelve {estado: ok|rechazado|no_encontrado|no_entendido, texto, tipo, nombre, base, destino_explicito,
              nombre_explicito, descripcion, resto}. 'base' = carpeta destino absoluta (None si no se dijo)."""
    out = {"estado": "ok", "texto": "", "tipo": None, "nombre": None, "base": None, "destino_explicito": False,
           "nombre_explicito": False, "descripcion": "", "resto": ""}
    try:
        # 0) lo que va entre comillas es un nombre literal («llamada "fotos en familia"»)
        citas: List[str] = []

        def _guardar(m):
            citas.append(m.group(1).strip())
            return f" §{len(citas) - 1}§ "
        texto_p = _RE_COMILLAS.sub(_guardar, texto or "")
        toks = [t for t in (_limpia_tok(x) for x in texto_p.split()) if t]
        langs, permitidas = _lenguajes(), _extensiones_permitidas()
        while toks and normalizar_texto(toks[-1]) in ("favor", "porfa", "gracias", "plis"):     # cortesía final
            toks.pop()
            if toks and normalizar_texto(toks[-1]) == "por":
                toks.pop()
        i = 1
        while i < len(toks) - 1:                       # «prueba punto py» (dictado) → «prueba.py»
            if normalizar_texto(toks[i]) == "punto" and ("." + normalizar_texto(toks[i + 1])) in permitidas \
                    and re.fullmatch(r"[\w\-]+", toks[i - 1]):
                toks[i - 1:i + 2] = [toks[i - 1] + "." + normalizar_texto(toks[i + 1])]
            else:
                i += 1
        nt = [normalizar_texto(t) for t in toks]
        n = len(toks)

        # 1) tipo por palabras (se ignoran las que van tras «en / dentro de [la]»: «en la carpeta X»)
        tipo = None
        for i, w in enumerate(nt):
            if w in _PALABRAS_CARPETA or w in _PALABRAS_ARCHIVO:
                j = i - 1
                while j >= 0 and nt[j] in _ARTICULOS_PREVIOS:
                    j -= 1
                if j >= 0 and nt[j] in _MARCA_DESTINO:
                    continue
                tipo = "carpeta" if w in _PALABRAS_CARPETA else "archivo"
                break

        # 2) marcadores de nombre y de destino
        marcas = []                                   # (indice, clase, largo)
        for i, w in enumerate(nt):
            if w in _MARCA_NOMBRE:
                marcas.append((i, "nombre", 1))
            elif w == "que" and nt[i + 1:i + 2] == ["se"] and nt[i + 2:i + 3] and nt[i + 2] in ("llame", "llama", "llamen"):
                marcas.append((i, "nombre", 3))
            elif w in ("con", "de") and (nt[i + 1:i + 2] == ["nombre"] or nt[i + 1:i + 3] == ["el", "nombre"]):
                largo = 2 if nt[i + 1] == "nombre" else 3
                if nt[i + largo:i + largo + 1] == ["de"]:
                    largo += 1
                marcas.append((i, "nombre", largo))
            elif w in _MARCA_DESTINO and i + 1 < n:
                largo = 1
                if w == "dentro" and nt[i + 1] in ("de", "del"):
                    largo = 2
                    if i + 2 >= n:
                        continue
                if nt[i + largo] in langs:            # «en python», no es una carpeta
                    continue
                marcas.append((i, "destino", largo))
        cubierto = {k for (i, _c, l) in marcas for k in range(i, i + l)}

        # 3) descripción (solo si es un archivo): desde «que / para / con / donde» hasta el final
        hay_fichero = any(_es_nombre_archivo(t, permitidas) for t in toks)
        corte = n
        if tipo == "archivo" or (tipo is None and hay_fichero):
            for i, w in enumerate(nt):
                if w in _MARCA_DESCRIPCION and i not in cubierto:
                    corte = i
                    break
        descripcion = " ".join(toks[corte:]).strip()
        toks, nt = toks[:corte], nt[:corte]
        marcas = [m for m in marcas if m[0] < corte]

        # 4) trocear: antes de la primera marca (verbo + tipo + nombre implícito) y un trozo por marca
        marcas.sort()
        pieza0 = toks[:marcas[0][0]] if marcas else toks[:]
        trozos = []
        for k, (i, clase, largo) in enumerate(marcas):
            fin = marcas[k + 1][0] if k + 1 < len(marcas) else len(toks)
            trozos.append((clase, toks[i + largo:fin]))
        while pieza0 and normalizar_texto(pieza0[0]) in _INICIO:
            pieza0 = pieza0[1:]
        pieza0 = _quitar_conj_final(pieza0)

        # 5) destino(s)
        destinos = [t for c, t in trozos if c == "destino"]
        base, sobrante, error = (None, [], None)
        if destinos:
            base, sobrante, error = _resolver_destinos(destinos)
            if error:
                out.update(estado="no_encontrado", texto=error)
                return out
            out["destino_explicito"] = bool(base)
        if base:
            out["base"] = base

        # 6) nombre
        explicitos = [t for c, t in trozos if c == "nombre"]
        if explicitos:
            nombre_t = list(explicitos[-1])
            out["nombre_explicito"] = True
        elif tipo == "archivo" or (tipo is None and hay_fichero):
            fich = [t for t in (pieza0 + sobrante) if _es_nombre_archivo(t, permitidas)]
            if fich:
                nombre_t = [fich[0]]
                out["nombre_explicito"] = True
            else:                                      # «crea un archivo en python…»: lo que sobra no es un nombre
                cand = [t for t in pieza0 if normalizar_texto(t) not in langs and normalizar_texto(t) not in _CONECTORES]
                nombre_t = cand if 0 < len(cand) <= 4 else []
        else:
            nombre_t = pieza0 + [t for t in sobrante if normalizar_texto(t) not in _INICIO]
        while nombre_t and normalizar_texto(nombre_t[0]) in ("el", "la", "los", "las", "un", "una", "de"):
            nombre_t = nombre_t[1:]
        nombre_t = _quitar_conj_final(nombre_t)
        nombre = " ".join(nombre_t).strip()
        nombre = re.sub(r"§(\d+)§", lambda m: citas[int(m.group(1))], nombre)
        descripcion = re.sub(r"§(\d+)§", lambda m: citas[int(m.group(1))], descripcion)
        out["resto"] = " ".join(pieza0)

        ext = os.path.splitext(nombre)[1].lower()
        if tipo is None:
            tipo = "archivo" if ext in permitidas else "carpeta"
        if tipo == "archivo" and ext and ext not in permitidas:
            out.update(estado="rechazado", texto=f"No creo archivos {ext} (solo texto y código: .py .js .html .txt …).")
            return out
        out.update(tipo=tipo, nombre=nombre or None, descripcion=descripcion)
        return out
    except Exception as e:
        _log_error("file_manager", "analizar_creacion", e)
        out.update(estado="no_entendido", texto="No pude interpretar qué quieres crear.")
        return out


def _gestion_crear(resultado: dict) -> dict:
    plan = analizar_creacion(resultado.get("texto_original") or "")
    if plan["estado"] != "ok":
        return {"estado": plan["estado"], "texto": plan["texto"]}
    tipo, nombre = plan["tipo"], plan["nombre"]
    if tipo == "carpeta" and not nombre:
        return {"estado": "no_entendido", "texto": "¿Cómo se llama la carpeta? Dime: «crea una carpeta llamada X en descargas»."}
    if tipo == "archivo" and not nombre and not plan["descripcion"]:
        return {"estado": "no_entendido", "texto": "¿Cómo se llama el archivo? Dime: «crea un archivo notas.txt en documentos»."}
    if nombre and not _nombre_creable(nombre):
        return {"estado": "rechazado", "texto": f"'{nombre}' no es un nombre válido para Windows."}

    base = plan["base"] or carpeta_creacion_defecto()
    plan["base"] = base
    if file_actions is not None and file_actions._valida_ruta_critica(base):
        return {"estado": "rechazado", "texto": "Esa ruta es del sistema; no creo nada ahí."}
    if not os.access(base, os.W_OK):
        return {"estado": "no_escribible", "texto": f"No tengo permiso de escritura en {base}."}

    if tipo == "archivo" and plan["descripcion"]:            # archivo CON código → generador (Groq / DeepSeek)
        try:
            import generador_codigo as gen
        except Exception:
            return {"estado": "no_entendido", "texto": "El generador de código no está disponible. "
                    "Puedo crear el archivo vacío si me lo pides sin descripción."}
        r = gen.resolver_generar_codigo(dict(resultado, categoria="GENERAR_CODIGO", creacion=plan))
        if not r:
            return {"estado": "no_entendido", "texto": "No pude generar ese archivo."}
        return {"estado": "ejecutado", "exito": r.get("estado") in ("generado", "reutilizado"), "texto": r.get("texto", "Listo.")}

    if tipo == "archivo":                                    # archivo vacío
        if not os.path.splitext(nombre)[1]:
            nombre += ".txt"
        ruta = os.path.join(base, nombre)
        if os.path.exists(ruta):
            return {"estado": "ejecutado", "exito": False, "texto": f"Ya existe: {ruta}"}
        with open(ruta, "x", encoding="utf-8"):
            pass
        etiqueta = "Archivo creado"
    else:
        ruta = os.path.join(base, nombre)
        if os.path.exists(ruta):
            return {"estado": "ejecutado", "exito": False, "texto": f"Ya existe: {ruta}"}
        os.makedirs(ruta)
        etiqueta = "Carpeta creada"
    if file_index is not None:
        try:
            file_index.registrar_ruta(ruta)
        except Exception:
            pass
    if deshacer is not None:
        deshacer.registrar({"tipo": "crear", "final": ruta})
    return {"estado": "ejecutado", "exito": True, "texto": f"{etiqueta}: {ruta}"}


def resolver_gestion_archivo(resultado: dict) -> Optional[dict]:
    cat = (resultado.get("categoria") or "").upper()
    if cat not in _CATS_GESTION:
        return None
    if file_actions is None:
        return {"estado": "no_entendido", "texto": "El módulo de acciones sobre archivos no está disponible."}
    try:
        if cat == "CREAR_CARPETA":                           # crea carpetas Y archivos (ver analizar_creacion)
            return _gestion_crear(resultado)
        palabras = _palabras_utiles(resultado.get("texto_original") or "")
        if not palabras:
            return {"estado": "no_entendido", "texto": "¿Sobre qué archivo o carpeta? Dame un nombre."}
        if cat == "ELIMINAR_ARCHIVO":
            return _gestion_eliminar(palabras)
        if cat in ("MOVER_ARCHIVO", "COPIAR_ARCHIVO"):
            return _gestion_mover_copiar(palabras, "mover" if cat == "MOVER_ARCHIVO" else "copiar")
        if cat == "RENOMBRAR_ARCHIVO":
            return _gestion_renombrar(palabras)
        return _gestion_comprimir(palabras)
    except Exception as e:
        _log_error("file_manager", "resolver_gestion_archivo", e)
        return {"estado": "no_entendido", "texto": "No pude interpretar esa operación sobre archivos."}


def _gestion_eliminar(palabras):
    estado, dato = _buscar_origen(palabras)
    if estado != "ok":
        return _fallo_origen(estado, dato, palabras)
    if file_actions._valida_ruta_critica(dato["ruta"]):
        return {"estado": "rechazado", "texto": "Esa ruta es del sistema; no la elimino."}
    es_dir = os.path.isdir(dato["ruta"])
    if deshacer is not None and deshacer.activo():
        aviso = "Se moverá a la papelera de SARA (podrás recuperarlo con «deshaz lo último»)."
    else:
        try:
            import send2trash  # noqa: F401
            aviso = "Se enviará a la papelera."
        except ImportError:
            aviso = ("Solo se puede eliminar si está vacía (instala send2trash para papelera)." if es_dir
                     else "Se eliminará de forma permanente.")
    return {"estado": "candidato", "accion": "eliminar", "ruta": dato["ruta"], "es_directorio": es_dir,
            "mensaje_confirmacion": f"¿Eliminar {'la carpeta' if es_dir else 'el archivo'} '{dato['nombre']}'?\n"
                                    f"    {dato['ruta']}\n    {aviso}"}


def _gestion_mover_copiar(palabras, accion):
    origen_p, dest_p = _cortar(palabras, {"a", "hacia", "en", "dentro"}, ultimo=True)
    if not dest_p:
        return {"estado": "no_entendido", "texto": f"¿A dónde? Di por ejemplo: '{accion} informe a documentos'."}
    estado, dato = _buscar_origen(origen_p)
    if estado != "ok":
        return _fallo_origen(estado, dato, origen_p)
    destino = _destino_a_ruta(dest_p)
    if not destino:
        return {"estado": "no_encontrado", "texto": f"No encontré la carpeta destino '{' '.join(dest_p)}'."}
    if not os.access(destino, os.W_OK):
        return {"estado": "no_escribible", "texto": f"No tengo permiso de escritura en {destino}."}
    final = os.path.join(destino, os.path.basename(dato["ruta"]))
    if os.path.abspath(final) == os.path.abspath(dato["ruta"]):
        return {"estado": "rechazado", "texto": "El origen y el destino son el mismo."}
    datos = {"estado": "candidato", "accion": accion, "origen": dato["ruta"], "destino": destino,
             "es_directorio": os.path.isdir(dato["ruta"])}
    if accion == "mover" or os.path.exists(final):
        extra = "\n    ⚠️ Ya existe uno con ese nombre; se sobrescribirá." if os.path.exists(final) else ""
        datos["mensaje_confirmacion"] = (f"¿{'Mover' if accion == 'mover' else 'Copiar'} '{dato['nombre']}' a {destino}?"
                                         f"{extra}")
        return datos
    r = confirmar_gestion_archivo(accion, datos)
    return {"estado": "ejecutado", "exito": r["exito"], "texto": r["texto"]}


def _gestion_renombrar(palabras):
    viejo_p, nuevo_p = _cortar(palabras, {"a", "como", "por"}, ultimo=False)
    if not nuevo_p:
        return {"estado": "no_entendido", "texto": "¿Con qué nombre? Di: 'renombra informe a informe final'."}
    estado, dato = _buscar_origen(viejo_p)
    if estado != "ok":
        return _fallo_origen(estado, dato, viejo_p)
    nuevo = " ".join(nuevo_p).strip()
    if not _nombre_valido(nuevo):
        return {"estado": "rechazado", "texto": f"'{nuevo}' no es un nombre válido."}
    ext = os.path.splitext(dato["ruta"])[1]
    if ext and "." not in nuevo and not os.path.isdir(dato["ruta"]):
        nuevo += ext
    destino = os.path.join(os.path.dirname(dato["ruta"]), nuevo)
    if os.path.exists(destino):
        return {"estado": "rechazado", "texto": f"Ya existe '{nuevo}' en esa carpeta."}
    return {"estado": "candidato", "accion": "renombrar", "ruta": dato["ruta"], "nuevo": nuevo,
            "mensaje_confirmacion": f"¿Renombrar '{dato['nombre']}' a '{nuevo}'?"}


def _gestion_comprimir(palabras):
    estado, dato = _buscar_origen(palabras)
    if estado != "ok":
        return _fallo_origen(estado, dato, palabras)
    origen = dato["ruta"]
    if file_actions._valida_ruta_critica(origen):
        return {"estado": "rechazado", "texto": "Esa ruta es del sistema; no la comprimo."}
    padre, nombre = os.path.split(origen.rstrip("\\/"))
    if not os.access(padre, os.W_OK):
        return {"estado": "no_escribible", "texto": f"No tengo permiso de escritura en {padre}."}
    base = os.path.join(padre, os.path.splitext(nombre)[0] if os.path.isfile(origen) else nombre)
    destino, n = base, 1
    while os.path.exists(destino + ".zip"):
        n += 1
        destino = f"{base}_{n}"
    if os.path.isdir(origen):
        shutil.make_archive(destino, "zip", root_dir=padre, base_dir=nombre)
    else:
        import zipfile
        with zipfile.ZipFile(destino + ".zip", "w", zipfile.ZIP_DEFLATED) as z:
            z.write(origen, nombre)
    if file_index is not None:
        try:
            file_index.registrar_ruta(destino + ".zip")
        except Exception:
            pass
    if deshacer is not None:
        deshacer.registrar({"tipo": "crear", "final": destino + ".zip"})
    return {"estado": "ejecutado", "exito": True, "texto": f"Comprimido en: {destino}.zip"}


def confirmar_gestion_archivo(accion: str, datos: dict) -> dict:
    """Ejecuta la operación ya confirmada. Devuelve {"exito": bool, "texto": str}."""
    try:
        if file_actions is None:
            return {"exito": False, "texto": "Módulo de acciones no disponible."}
        if accion == "eliminar":
            ruta = datos["ruta"]
            if deshacer is not None and deshacer.activo():
                return deshacer.eliminar(ruta)               # reversible: papelera propia
            try:
                import send2trash
                send2trash.send2trash(ruta)
                return {"exito": True, "texto": f"Enviado a la papelera: {os.path.basename(ruta)}"}
            except ImportError:
                r = (file_actions.eliminar_directorio(ruta, recurso=False) if os.path.isdir(ruta)
                     else file_actions.eliminar_archivo(ruta))
        elif accion == "mover":
            final = os.path.join(datos["destino"], os.path.basename(datos["origen"]))
            sobre = deshacer.sobrescrito(final) if deshacer is not None else None      # lo que se pisa no se pierde
            r = file_actions.mover_archivo(datos["origen"], datos["destino"])
            if deshacer is not None:
                if r.get("exito"):
                    deshacer.registrar({"tipo": "mover", "origen": datos["origen"], "final": final, "sobrescrito": sobre})
                else:
                    deshacer.restaurar_sobrescrito(sobre, final)
        elif accion == "copiar":
            final = os.path.join(datos["destino"], os.path.basename(datos["origen"]))
            sobre = deshacer.sobrescrito(final) if deshacer is not None else None
            if os.path.isdir(datos["origen"]):
                r = file_actions.copiar_directorio(
                    datos["origen"], os.path.join(datos["destino"], os.path.basename(datos["origen"])))
            else:
                r = file_actions.copiar_archivo(datos["origen"], datos["destino"])
            if deshacer is not None:
                if r.get("exito"):
                    deshacer.registrar({"tipo": "copiar", "final": final, "sobrescrito": sobre})
                else:
                    deshacer.restaurar_sobrescrito(sobre, final)
        elif accion == "renombrar":
            r = file_actions.renombrar(datos["ruta"], datos["nuevo"])
            if deshacer is not None and r.get("exito"):
                deshacer.registrar({"tipo": "renombrar", "viejo": datos["ruta"],
                                    "nuevo": os.path.join(os.path.dirname(datos["ruta"]), datos["nuevo"])})
        else:
            return {"exito": False, "texto": f"Acción desconocida: {accion}"}
        return {"exito": bool(r.get("exito")), "texto": r.get("mensaje", "Listo." if r.get("exito") else "Falló.")}
    except Exception as e:
        _log_error("file_manager", "confirmar_gestion_archivo", e)
        return {"exito": False, "texto": f"No pude completar la operación: {e}"}


if __name__ == "__main__":
    print("file_manager.py está listo para usar")
