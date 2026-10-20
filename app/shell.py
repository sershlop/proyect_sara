# 📁 shell.py — Ejecución de comandos con zonas de riesgo (v3, blindado)
#
# Cambios respecto a la versión anterior:
#   - La clasificación la hace seguridad_shell.evaluar_riesgo (rutas críticas en cualquier orden/estilo,
#     destino no verificable, perfiles normal/desarrollador, confirmación reforzada).
#   - Fail-closed: si el analizador falla, el comando se trata como NEGRO.
#   - Un programa "interactivo" (powershell -NoExit ...) ya NO se salta la clasificación.
#   - "ejecuta el comando X" usa X literal (Qwen no lo retraduce ni lo altera).
#   - Respaldo externo realmente activo (requests lanza sus propias excepciones, no las del sistema).
#   - Prompt de Qwen completo (JSON cerrado + lista legible de lo prohibido).
#   - Ejecución: cwd seguro, sin stdin, mata el árbol de procesos al vencer el timeout,
#     modo restringido (ConstrainedLanguage) en zona blanca, salida truncada.

import json
import os
import re
import subprocess
from typing import Optional

from utils import normalizar_texto, extraer_resto
from database import conectar
from seguridad_shell import (
    evaluar_riesgo, Veredicto, PERFIL_NORMAL, PERFIL_DEV, PRELUDIO_CLM,
)

try:
    import logger as _logger
except ImportError:
    _logger = None

try:
    import respaldo_externo
except ImportError:
    respaldo_externo = None

from config import (
    INTERPRETE_SHELL, TIMEOUT_COMANDO_SISTEMA_SEGUNDOS,
    PLANTILLAS_COMANDO_SISTEMA, PATRONES_ZONA_NEGRA,
    QWEN_MODEL, QWEN_TEMPERATURA, QWEN_MAX_TOKENS, OLLAMA_URL, QWEN_TIMEOUT_SEGUNDOS,
    QWEN_FORMAT, QWEN_MAX_CONTEXT,
    PROGRAMAS_INTERACTIVOS, FLAGS_FUERZAN_INTERACTIVO,
)

try:
    from config import DESCRIPCION_ZONA_NEGRA
except ImportError:
    DESCRIPCION_ZONA_NEGRA = ()

try:
    from config import VERBOS_COMANDO_SISTEMA
except ImportError:
    VERBOS_COMANDO_SISTEMA = frozenset({"ejecuta el comando", "corre el comando", "ejecuta comando"})

try:
    from config import PERFIL_SHELL as _PERFIL_INICIAL      # opcional: "normal" | "desarrollador"
except ImportError:
    _PERFIL_INICIAL = PERFIL_NORMAL

FRASE_CONFIRMACION_REFORZADA = "CONFIRMAR"
MAX_SALIDA_CARACTERES = 20_000
_CWD_SEGURO = os.path.expanduser("~")


# ═══════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════

def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        if _logger is None:
            raise ImportError("logger.py no disponible")
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        pass


# ── Perfil de seguridad (la GUI puede llamar a establecer_perfil) ──────────
_perfil_activo = _PERFIL_INICIAL if _PERFIL_INICIAL in (PERFIL_NORMAL, PERFIL_DEV) else PERFIL_NORMAL


def obtener_perfil() -> str:
    return _perfil_activo


def establecer_perfil(perfil: str) -> bool:
    """'normal' o 'desarrollador'. Devuelve False si el valor no es válido."""
    global _perfil_activo
    if perfil not in (PERFIL_NORMAL, PERFIL_DEV):
        return False
    _perfil_activo = perfil
    return True


def _es_programa_interactivo(comando_texto: str) -> bool:
    """Detecta si un comando es un programa interactivo sin argumentos resolutivos."""
    partes = (comando_texto or "").strip().split()
    if not partes:
        return False
    programa = partes[0].strip('"').lower()
    programa_base = programa.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    if programa_base not in PROGRAMAS_INTERACTIVOS:
        return False
    resto = partes[1:]
    if not resto:
        return True
    return any(flag.lower() in FLAGS_FUERZAN_INTERACTIVO for flag in resto)


def crear_tablas() -> None:
    try:
        with conectar() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS historial_comandos (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                comando_texto     TEXT NOT NULL,
                zona              TEXT NOT NULL CHECK(zona IN ('blanca', 'amarilla', 'negra')),
                resultado         TEXT NOT NULL,
                codigo_salida     INTEGER,
                salida_completa   TEXT DEFAULT '',
                fecha             DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS comandos_aprendidos (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                frase_normalizada   TEXT NOT NULL UNIQUE,
                comando_texto       TEXT NOT NULL,
                zona                TEXT NOT NULL,
                veces_confirmado    INTEGER DEFAULT 1,
                ultima_confirmacion DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")

            conn.commit()
    except Exception as e:
        _log_error("shell", "crear_tablas", e)


# ═══════════════════════════════════════════════════════════════════════════
# CLASIFICACIÓN DE RIESGO
# ═══════════════════════════════════════════════════════════════════════════

def evaluar_comando(comando_texto: str) -> Veredicto:
    """Veredicto completo (zona + reforzada + motivo). FAIL-CLOSED: ante cualquier error, NEGRA."""
    try:
        return evaluar_riesgo(comando_texto, _perfil_activo, PATRONES_ZONA_NEGRA)
    except Exception as e:
        _log_error("shell", "evaluar_comando", e)
        return Veredicto("negra", False, "error interno al analizar el comando (se bloquea por seguridad)")


def clasificar_riesgo(comando_texto: str) -> str:
    """Compatibilidad: devuelve solo 'blanca' | 'amarilla' | 'negra'."""
    return evaluar_comando(comando_texto).zona


# ═══════════════════════════════════════════════════════════════════════════
# APRENDIZAJE E HISTORIAL
# ═══════════════════════════════════════════════════════════════════════════

def buscar_comando_aprendido(frase_normalizada: str) -> Optional[dict]:
    try:
        with conectar() as conn:
            fila = conn.execute(
                "SELECT * FROM comandos_aprendidos WHERE frase_normalizada = ?",
                (frase_normalizada,),
            ).fetchone()
            return dict(fila) if fila else None
    except Exception as e:
        _log_error("shell", "buscar_comando_aprendido", e)
        return None


def guardar_comando_aprendido(frase_normalizada: str, comando_texto: str, zona: str) -> None:
    try:
        with conectar() as conn:
            conn.execute("""
                INSERT INTO comandos_aprendidos (frase_normalizada, comando_texto, zona)
                VALUES (?, ?, ?)
                ON CONFLICT(frase_normalizada) DO UPDATE SET
                    veces_confirmado = veces_confirmado + 1,
                    ultima_confirmacion = CURRENT_TIMESTAMP,
                    comando_texto = excluded.comando_texto,
                    zona = excluded.zona
            """, (frase_normalizada, comando_texto, zona))
            conn.commit()
    except Exception as e:
        _log_error("shell", "guardar_comando_aprendido", e)


def registrar_comando_historial(comando_texto: str, zona: str, resultado: str,
                                 codigo_salida: Optional[int] = None,
                                 salida_completa: str = "") -> None:
    try:
        with conectar() as conn:
            conn.execute("""
                INSERT INTO historial_comandos
                    (comando_texto, zona, resultado, codigo_salida, salida_completa)
                VALUES (?, ?, ?, ?, ?)
            """, (comando_texto, zona, resultado, codigo_salida, (salida_completa or "")[:MAX_SALIDA_CARACTERES]))
            conn.commit()
    except Exception as e:
        _log_error("shell", "registrar_comando_historial", e)


# ═══════════════════════════════════════════════════════════════════════════
# TRADUCCIÓN LENGUAJE NATURAL → COMANDO
# ═══════════════════════════════════════════════════════════════════════════

def _construir_prompt_qwen(texto_natural: str) -> str:
    prohibido = "\n".join(f"- {p}" for p in DESCRIPCION_ZONA_NEGRA)
    bloque_prohibido = (
        "NUNCA generes un comando que haga, contenga o se parezca a nada de esto "
        f"(ademas el sistema lo bloquea por su cuenta):\n{prohibido}\n\n" if prohibido
        else "NUNCA generes comandos destructivos (formatear, borrar System32/Windows/Program Files, tocar el Registro "
             "o el antivirus, descargar y ejecutar codigo, comandos ofuscados).\n\n"
    )
    return (
        "Traduce la peticion del usuario a UN SOLO comando literal de PowerShell 5.1. "
        "No expliques nada, no agregues comentarios ni texto extra. Sin '&&' (no existe en PowerShell 5.1).\n\n"
        f"Peticion: {json.dumps(texto_natural, ensure_ascii=False)}\n\n"
        f"{bloque_prohibido}"
        "Si la peticion requiere algo de esa lista, o no puedes resolverla de forma simple y segura, "
        "responde con comando null.\n\n"
        'Responde SOLO un JSON: {"comando": "<comando literal de powershell, o null>"}'
    )


def _traducir_via_respaldo(texto_natural: str) -> Optional[str]:
    if respaldo_externo is None:
        return None
    try:
        return respaldo_externo.traducir_comando_via_gemini(texto_natural, PATRONES_ZONA_NEGRA)
    except Exception as e:
        _log_error("shell", "_traducir_via_respaldo", e)
        return None


def _traducir_via_qwen(texto_natural: str) -> Optional[str]:
    """Qwen (Ollama) → comando PowerShell; si Ollama no responde, usa el respaldo externo."""
    try:
        import requests
    except ImportError as e:
        _log_error("shell", "_traducir_via_qwen (sin requests)", e)
        return _traducir_via_respaldo(texto_natural)

    try:
        respuesta = requests.post(
            OLLAMA_URL,
            json={
                "model": QWEN_MODEL,
                "prompt": _construir_prompt_qwen(texto_natural),
                "stream": False,
                "format": QWEN_FORMAT,
                "think": False,
                "options": {
                    "temperature": QWEN_TEMPERATURA,
                    "num_predict": QWEN_MAX_TOKENS,
                    "num_ctx": QWEN_MAX_CONTEXT,
                },
            },
            timeout=QWEN_TIMEOUT_SEGUNDOS,
        )
        respuesta.raise_for_status()
        datos = json.loads(respuesta.json().get("response", ""))
        comando = datos.get("comando")
    except requests.exceptions.RequestException as e:
        # BLINDAJE: Ollama caído / lento / error HTTP -> respaldo externo
        _log_error("shell", "_traducir_via_qwen (Ollama inalcanzable)", e)
        return _traducir_via_respaldo(texto_natural)
    except Exception as e:
        _log_error("shell", "_traducir_via_qwen", e)
        return None

    if not comando or not isinstance(comando, str) or comando.strip().lower() == "null":
        return None
    return comando.strip()


def _comando_literal_del_usuario(texto_natural: str) -> Optional[str]:
    """'ejecuta el comando Get-Date' -> 'Get-Date' (tal cual, sin pasar por la IA)."""
    texto = (texto_natural or "").strip()
    resto = extraer_resto(texto, VERBOS_COMANDO_SISTEMA)
    if resto and resto != texto:
        return resto
    return None


def traducir_a_comando(texto_natural: str) -> Optional[dict]:
    """Orden: literal del usuario → aprendido → plantilla → Qwen/respaldo."""
    frase_normalizada = normalizar_texto(texto_natural)
    if not frase_normalizada:
        return None

    literal = _comando_literal_del_usuario(texto_natural)
    if literal:
        return {"comando": literal, "fuente": "literal"}

    aprendido = buscar_comando_aprendido(frase_normalizada)
    if aprendido:
        return {"comando": aprendido["comando_texto"], "fuente": "aprendido"}

    plantilla = PLANTILLAS_COMANDO_SISTEMA.get(frase_normalizada)
    if plantilla:
        return {"comando": plantilla, "fuente": "plantilla"}

    comando_qwen = _traducir_via_qwen(texto_natural)
    if not comando_qwen:
        return None
    return {"comando": comando_qwen, "fuente": "qwen"}


# ═══════════════════════════════════════════════════════════════════════════
# EJECUCIÓN
# ═══════════════════════════════════════════════════════════════════════════

def _matar_arbol(proceso: subprocess.Popen) -> None:
    """Mata el proceso Y sus hijos (subprocess.run solo mataba el PowerShell, no lo que éste lanzó)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proceso.pid)],
                           capture_output=True, timeout=10)
        else:
            proceso.kill()
    except Exception:
        try:
            proceso.kill()
        except Exception:
            pass


def ejecutar_comando(comando_texto: str, zona: str = "amarilla") -> dict:
    """
    Ejecuta el comando en PowerShell. NO decide si es seguro: eso lo hace evaluar_comando() antes.
    En zona blanca antepone el modo restringido (ConstrainedLanguage) como segunda barrera.
    """
    comando_final = (PRELUDIO_CLM + comando_texto) if zona == "blanca" else comando_texto
    banderas = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proceso = subprocess.Popen(
            [INTERPRETE_SHELL, "-NoProfile", "-NonInteractive", "-Command", comando_final],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", cwd=_CWD_SEGURO, creationflags=banderas,
        )
    except Exception as e:
        _log_error("shell", "ejecutar_comando (inicio)", e)
        return {"ejecutado": False, "exitoso": False, "codigo_salida": None,
                "resumen": "No pude ejecutar el comando por un error inesperado.", "salida_completa": ""}

    try:
        stdout, stderr = proceso.communicate(timeout=TIMEOUT_COMANDO_SISTEMA_SEGUNDOS)
    except subprocess.TimeoutExpired:
        _matar_arbol(proceso)
        try:
            proceso.communicate(timeout=5)
        except Exception:
            pass
        return {"ejecutado": False, "exitoso": False, "codigo_salida": None,
                "resumen": f"El comando no respondió en {TIMEOUT_COMANDO_SISTEMA_SEGUNDOS}s y fue cancelado.",
                "salida_completa": ""}
    except Exception as e:
        _matar_arbol(proceso)
        _log_error("shell", "ejecutar_comando", e)
        return {"ejecutado": False, "exitoso": False, "codigo_salida": None,
                "resumen": "No pude ejecutar el comando por un error inesperado.", "salida_completa": ""}

    salida_completa = ((stdout or "") + (stderr or ""))[:MAX_SALIDA_CARACTERES]
    exitoso = proceso.returncode == 0
    lineas = salida_completa.strip().splitlines()
    resumen = lineas[0][:200] if lineas else ("Comando ejecutado sin salida." if exitoso else "El comando terminó con error.")
    return {"ejecutado": True, "exitoso": exitoso, "codigo_salida": proceso.returncode,
            "resumen": resumen, "salida_completa": salida_completa}


# ═══════════════════════════════════════════════════════════════════════════
# ORQUESTACIÓN
# ═══════════════════════════════════════════════════════════════════════════

def _mensaje_confirmacion(comando: str, veredicto: Veredicto) -> str:
    if veredicto.reforzada:
        return (f"⚠️ Acción delicada: {veredicto.motivo}.\n    {comando}\n"
                f"Para ejecutarla escribe exactamente {FRASE_CONFIRMACION_REFORZADA}.")
    return f"¿Seguro que quieres ejecutar este comando?\n    {comando}"


def resolver_comando_sistema(resultado: dict) -> Optional[dict]:
    """Orquesta traducción → validación → ejecución."""
    if resultado.get("categoria") != "COMANDO_SISTEMA":
        return None

    texto_natural = resultado.get("texto_original", "")
    traduccion = traducir_a_comando(texto_natural)
    if not traduccion:
        return {"estado": "no_entendido", "texto": "No entendí qué comando ejecutar."}

    comando = traduccion["comando"]

    # 1) Clasificar SIEMPRE primero (antes lo interactivo se saltaba esta revisión)
    veredicto = evaluar_comando(comando)
    zona = veredicto.zona

    if zona == "negra":
        registrar_comando_historial(comando, zona, "rechazado_negra", None, veredicto.motivo)
        return {"estado": "rechazado", "zona": zona, "motivo": veredicto.motivo,
                "texto": f"No puedo ejecutar ese comando — zona de riesgo negra ({veredicto.motivo}): {comando}"}

    # 2) Programa interactivo (ya validado arriba)
    if _es_programa_interactivo(comando):
        return {
            "estado": "requiere_consola",
            "programa": comando.split()[0].strip('"'),
            "comando_completo": comando,
            "zona": zona,
            "texto": f"'{comando}' abre una sesión interactiva — la abro en su propia consola.",
        }

    # 3) Blanca: se ejecuta sola
    if zona == "blanca":
        resultado_ejec = ejecutar_comando(comando, zona)
        registrar_comando_historial(comando, zona, "ejecutado" if resultado_ejec["exitoso"] else "error",
                                     resultado_ejec["codigo_salida"], resultado_ejec["salida_completa"])
        if traduccion["fuente"] == "qwen" and resultado_ejec["exitoso"]:
            guardar_comando_aprendido(normalizar_texto(texto_natural), comando, zona)
        return {"estado": "ejecutado", "zona": zona, "texto": resultado_ejec["resumen"],
                "exito": resultado_ejec["exitoso"], "salida_completa": resultado_ejec["salida_completa"]}

    # 4) Amarilla (normal o reforzada) → requiere confirmación
    return {
        "estado": "candidato", "comando": comando, "zona": zona, "traduccion": traduccion,
        "reforzada": veredicto.reforzada, "motivo": veredicto.motivo,
        "frase_confirmacion": FRASE_CONFIRMACION_REFORZADA if veredicto.reforzada else None,
        "mensaje_confirmacion": _mensaje_confirmacion(comando, veredicto),
    }


def confirmar_y_ejecutar(comando: str, zona: str, traduccion: dict, texto_natural: str,
                         confirmacion: str = "") -> dict:
    """
    Revalida (el perfil o las reglas pudieron cambiar) y ejecuta.
    Si el comando es de confirmación reforzada, `confirmacion` debe ser exactamente 'CONFIRMAR'.
    """
    veredicto = evaluar_comando(comando)
    zona_actual = veredicto.zona

    def _rechazo(texto: str, resultado_hist: str) -> dict:
        registrar_comando_historial(comando, zona_actual, resultado_hist, None, veredicto.motivo)
        return {"ejecutado": False, "exitoso": False, "codigo_salida": None,
                "resumen": texto, "salida_completa": ""}

    if zona_actual == "negra":
        return _rechazo(f"El comando fue rechazado al revalidarlo ({veredicto.motivo}): {comando}",
                        "rechazado_negra_revalidacion")

    if veredicto.reforzada and (confirmacion or "").strip() != FRASE_CONFIRMACION_REFORZADA:
        return _rechazo(f"Falta la confirmación escrita ({FRASE_CONFIRMACION_REFORZADA}); no se ejecutó.",
                        "cancelado_sin_confirmacion_reforzada")

    resultado_ejec = ejecutar_comando(comando, zona_actual)
    registrar_comando_historial(comando, zona_actual, "ejecutado" if resultado_ejec["exitoso"] else "error",
                                 resultado_ejec["codigo_salida"], resultado_ejec["salida_completa"])
    # Lo delicado no se auto-aprende: que siempre vuelva a pedir confirmación
    if traduccion.get("fuente") == "qwen" and resultado_ejec["exitoso"] and not veredicto.reforzada:
        guardar_comando_aprendido(normalizar_texto(texto_natural), comando, zona_actual)
    return resultado_ejec
