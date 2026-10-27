"""Respaldo externo: Groq/DeepSeek para generación de código, Gemini para
preguntas informativas y como blindaje TOTAL del árbitro local (Capa 3 de
intent_engine y la traducción de comandos de shell.py) cuando Ollama está
caído. Módulo aislado a propósito (Principio 1): nada aquí se llama nunca
si las API keys no están configuradas — se degrada a None, nunca truena
(Principio 3), y ningún otro módulo asume que esto está disponible.

Cadena de blindaje ante fallos de Ollama: Gemini primero, Groq como
segundo blindaje si Gemini también falla o no tiene key. DeepSeek NO
participa en el blindaje — su único rol es respaldo de Groq para
generación de código cuando se agotan tokens.
"""

import json
import re
import time
from typing import Optional

try:
    import requests
    _http = requests.Session()   # keep-alive: evita renegociar TLS en cada llamada
except ImportError:
    requests = None
    _http = None

try:
    from config import (
        USAR_GROQ_BACKUP, GROQ_API_KEY, GROQ_MODEL, GROQ_MODEL_CODIGO,
        GROQ_TEMPERATURA, GROQ_MAX_TOKENS,
        USAR_DEEPSEEK, DEEPSEEK_API_KEY, DEEPSEEK_MODEL,
        DEEPSEEK_TEMPERATURA, DEEPSEEK_MAX_TOKENS,
        USAR_GEMINI_BACKUP, GEMINI_API_KEY, GEMINI_MODEL,
        GEMINI_TEMPERATURA, GEMINI_MAX_TOKENS,
        RESPALDO_EXTERNO_TIMEOUT_SEGUNDOS,
    )
except Exception as _e_cfg:
    # Antes este fallo era SILENCIOSO: cualquier nombre ausente en config.py desactivaba todo el respaldo.
    print(f"[RESPALDO_EXTERNO] config.py no cargó ({type(_e_cfg).__name__}: {_e_cfg}) -> respaldo externo DESACTIVADO")
    USAR_GROQ_BACKUP = USAR_DEEPSEEK = USAR_GEMINI_BACKUP = False
    GROQ_API_KEY = DEEPSEEK_API_KEY = GEMINI_API_KEY = ""
    GROQ_MODEL = GROQ_MODEL_CODIGO = DEEPSEEK_MODEL = GEMINI_MODEL = ""
    GROQ_TEMPERATURA = DEEPSEEK_TEMPERATURA = GEMINI_TEMPERATURA = 0.5
    GROQ_MAX_TOKENS = DEEPSEEK_MAX_TOKENS = GEMINI_MAX_TOKENS = 500
    RESPALDO_EXTERNO_TIMEOUT_SEGUNDOS = 30

# Tiempos máximos (antes: hasta 3 modelos x 2 intentos x 30 s, y se repetía en clasificación + respuesta).
GEMINI_PRESUPUESTO_S = 20        # tiempo TOTAL máximo por llamada a Gemini (todos sus modelos e intentos)
GEMINI_PAUSA_TRAS_FALLO_S = 60   # si Gemini está caído/saturado, no se le insiste durante este tiempo
TIMEOUT_CONEXION_S = 5
TIMEOUT_PROVEEDOR_S = min(RESPALDO_EXTERNO_TIMEOUT_SEGUNDOS, 15)   # Groq / DeepSeek
_gemini_pausado_hasta = 0.0

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        import logger as _logger
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[RESPALDO_EXTERNO ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


def gemini_disponible() -> bool:
    return bool(USAR_GEMINI_BACKUP and GEMINI_API_KEY and requests is not None)


def groq_disponible() -> bool:
    return bool(USAR_GROQ_BACKUP and GROQ_API_KEY and requests is not None)


def deepseek_disponible() -> bool:
    return bool(USAR_DEEPSEEK and DEEPSEEK_API_KEY and requests is not None)


def _extraer_json(texto: str) -> str:
    """Los LLM a veces envuelven el JSON en ```json pese a que se les pide
    que no lo hagan — defensa en profundidad, mismo espíritu que shell.py
    nunca confía ciegamente en lo que devuelve un modelo."""
    texto = (texto or "").strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```[a-zA-Z]*\n?", "", texto)
        texto = re.sub(r"\n?```$", "", texto)
    return texto.strip()


# DESPUÉS:
def _llamar_groq(mensajes: list, modelo: str, max_tokens: int, temperatura: float,
                json_mode: bool = True) -> Optional[str]:
    """Todo uso actual de esta función pide JSON puro en el prompt — se
    fuerza también a nivel de API (response_format) en vez de confiar
    solo en la instrucción de texto, mismo tratamiento que ya tenía
    Gemini vía responseMimeType."""
    if not groq_disponible():
        return None
    try:
        r = _http.post(                       # sesión con keep-alive: sin handshake TLS en cada llamada
            _GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json=dict(
            {"model": modelo, "messages": mensajes,
            "temperature": temperatura, "max_tokens": max_tokens},
            **({"response_format": {"type": "json_object"}} if json_mode else {}),
            **({"reasoning_effort": "low"} if "gpt-oss" in modelo else {}),
        ),
            timeout=TIMEOUT_PROVEEDOR_S,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        _log_error("respaldo_externo", "_llamar_groq", e)
        return None


def _llamar_deepseek(mensajes: list) -> Optional[str]:
    if not deepseek_disponible():
        return None
    try:
        r = _http.post(
            _DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": DEEPSEEK_MODEL, "messages": mensajes,
                  "temperature": DEEPSEEK_TEMPERATURA, "max_tokens": DEEPSEEK_MAX_TOKENS},
            timeout=TIMEOUT_PROVEEDOR_S,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        _log_error("respaldo_externo", "_llamar_deepseek", e)
        return None


# Modelos a probar si el principal está saturado (503/429), retirado (404) o devuelve vacío.
_GEMINI_ALTERNOS = ("gemini-flash-latest", "gemini-3.5-flash-lite")


_gemini_sin_thinking = False   # se activa si el modelo rechaza thinkingConfig (HTTP 400)

def _llamar_gemini(prompt: str, max_tokens: int, temperatura: float, json_mode: bool = False,
                   presupuesto: Optional[float] = None) -> Optional[str]:
    global _gemini_pausado_hasta, _gemini_sin_thinking
    if not gemini_disponible():
        return None
    if time.monotonic() < _gemini_pausado_hasta:
        return None
    modelos = [GEMINI_MODEL] + [m for m in _GEMINI_ALTERNOS if m != GEMINI_MODEL]

    limite = time.monotonic() + (presupuesto or GEMINI_PRESUPUESTO_S)
    fallo_servicio = False
    for modelo in modelos:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        for intento in (1, 2):
            restante = limite - time.monotonic()
            if restante < 2:
                break
            gc = {"temperature": temperatura, "maxOutputTokens": max_tokens}
            if json_mode:
                gc["responseMimeType"] = "application/json"
            if not _gemini_sin_thinking:
                gc["thinkingConfig"] = {"thinkingBudget": 0}
            cuerpo = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gc}
            try:
                r = _http.post(url, headers={"x-goog-api-key": GEMINI_API_KEY}, json=cuerpo,
                               timeout=(min(TIMEOUT_CONEXION_S, restante), restante))
                if r.status_code == 400 and "thinkingConfig" in gc:
                    _gemini_sin_thinking = True      # este modelo no lo acepta: reintentar sin él
                    continue
                if r.status_code in (429, 500, 503) and intento == 1 and restante > 6:
                    fallo_servicio = True
                    time.sleep(0.5)
                    continue
                if r.status_code in (404, 429, 500, 503):
                    fallo_servicio = fallo_servicio or r.status_code != 404
                    _log_error("respaldo_externo", "_llamar_gemini",
                               RuntimeError(f"HTTP {r.status_code} con {modelo}; pruebo el siguiente modelo"))
                    break
                r.raise_for_status()
                _gemini_pausado_hasta = 0.0
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            except Exception as e:
                fallo_servicio = True
                _log_error("respaldo_externo", f"_llamar_gemini ({modelo})", e)
                break
        if limite - time.monotonic() < 2:
            break
    if fallo_servicio:
        _gemini_pausado_hasta = time.monotonic() + GEMINI_PAUSA_TRAS_FALLO_S
    return None

# ── BLINDAJE TOTAL DEL ÁRBITRO LOCAL (Ollama caído) ───────────────────────

def _prompt_clasificacion(texto_original: str, categorias: list, descripciones: dict, categoria_desconocida: str) -> tuple:
    opciones = categorias + [categoria_desconocida]
    desc_texto = "\n".join(f"- {c}: {descripciones.get(c, 'sin descripcion adicional')}" for c in opciones)
    prompt = (
        "Clasifica la frase del usuario en UNA sola categoria. Estas son las categorias validas:\n"
        f"{desc_texto}\n\nFrase: \"{texto_original}\"\n\n"
        "Responde SOLO un JSON con este formato exacto, sin texto adicional, sin markdown: "
        '{"categoria": "<una_de_la_lista>", "confianza": <numero entre 0 y 1>}'
    )
    return prompt, opciones


def clasificar_via_gemini(texto_original: str, categorias: list, descripciones: dict, categoria_desconocida: str) -> Optional[dict]:
    prompt, _ = _prompt_clasificacion(texto_original, categorias, descripciones, categoria_desconocida)
    contenido = _llamar_gemini(prompt, GEMINI_MAX_TOKENS, GEMINI_TEMPERATURA, json_mode=True)
    if not contenido:
        return None
    try:
        datos = json.loads(_extraer_json(contenido))
        return {"categoria": datos.get("categoria"), "confianza": datos.get("confianza", 0.0)}
    except Exception as e:
        _log_error("respaldo_externo", "clasificar_via_gemini", e)
        return None


def clasificar_via_groq(texto_original: str, categorias: list, descripciones: dict, categoria_desconocida: str) -> Optional[dict]:
    """Segundo blindaje — solo se intenta si Gemini falló o no está configurado."""
    prompt, _ = _prompt_clasificacion(texto_original, categorias, descripciones, categoria_desconocida)
    mensajes = [{"role": "user", "content": prompt}]
    contenido = _llamar_groq(mensajes, GROQ_MODEL, GROQ_MAX_TOKENS, GROQ_TEMPERATURA)
    if not contenido:
        return None
    try:
        datos = json.loads(_extraer_json(contenido))
        return {"categoria": datos.get("categoria"), "confianza": datos.get("confianza", 0.0)}
    except Exception as e:
        _log_error("respaldo_externo", "clasificar_via_groq", e)
        return None


def traducir_comando_via_gemini(texto_natural: str, patrones_zona_negra: tuple) -> Optional[str]:
    """Mismo blindaje, pero para el rol de shell.py (texto -> comando de
    PowerShell) en vez de clasificación de categoría."""
    try:                                    # lista legible para el LLM (las regex crudas no le sirven); sin ella, lo de antes
        from config import DESCRIPCION_ZONA_NEGRA as _desc_negra
    except Exception:
        _desc_negra = None
    prohibido = list(_desc_negra) if _desc_negra else list(patrones_zona_negra)
    prompt = (
        "Traduce la peticion del usuario a UN SOLO comando literal de PowerShell que la resuelva. "
        "No expliques nada, no agregues comentarios ni texto extra. Un solo comando: sin ';', sin '&&', sin tuberias "
        "salvo que sean imprescindibles.\n\n"
        f"Peticion: \"{texto_natural}\"\n\n"
        "NUNCA generes un comando que haga, contenga o se parezca a ninguna de estas cosas prohibidas "
        "(ademas el sistema las bloquea por su cuenta):\n"
        f"{chr(10).join('- ' + p for p in prohibido)}\n\n"
        "Si la peticion requiere algo de esa lista, o no puedes resolverla de forma simple y segura, "
        "responde con comando null.\n\n"
        'Responde SOLO un JSON: {"comando": "<comando literal de powershell, o null>"}'
    )
    contenido = _llamar_gemini(prompt, GEMINI_MAX_TOKENS, GEMINI_TEMPERATURA, json_mode=True)
    if not contenido:
        return None
    try:
        datos = json.loads(_extraer_json(contenido))
        comando = datos.get("comando")
    except Exception as e:
        _log_error("respaldo_externo", "traducir_comando_via_gemini", e)
        return None
    if not comando or not isinstance(comando, str) or comando.strip().lower() == "null":
        return None
    return comando.strip()


# ── GENERACIÓN DE CÓDIGO (Groq -> DeepSeek si se agotan tokens) ──────────

def generar_codigo(descripcion: str, lenguaje_solicitado: Optional[str]) -> Optional[dict]:
    prompt = (
        "Eres un generador de código. Devuelve SOLO un JSON con este formato exacto, "
        "sin texto adicional, sin markdown, sin backticks: "
        '{"codigo": "<codigo fuente completo>", "lenguaje": "<nombre del lenguaje>", "extension": "<.ext>"}\n\n'
        f"Petición del usuario: \"{descripcion}\"\n"
        + (f"El usuario pidió específicamente el lenguaje: {lenguaje_solicitado}.\n" if lenguaje_solicitado else "")
        + "Si no se especifica lenguaje, usa Python por defecto."
    )
    mensajes = [{"role": "user", "content": prompt}]

    contenido = _llamar_groq(mensajes, GROQ_MODEL_CODIGO, GROQ_MAX_TOKENS, GROQ_TEMPERATURA)
    fuente = "groq"
    if not contenido:
        contenido = _llamar_deepseek(mensajes)
        fuente = "deepseek"
    if not contenido:
        return None

    try:
        datos = json.loads(_extraer_json(contenido))
        codigo = datos.get("codigo")
        lenguaje = (datos.get("lenguaje") or "python").strip().lower()
        extension = datos.get("extension") or ".py"
    except Exception as e:
        _log_error("respaldo_externo", "generar_codigo (parseo)", e)
        return None

    if not codigo:
        return None
    return {"codigo": codigo, "lenguaje": lenguaje, "extension": extension, "fuente": fuente}


# ── PREGUNTAS INFORMATIVAS (solo Gemini) ──────────────────────────────────
# NUEVO — agregar junto a responder_pregunta_informativa():
def extraer_fecha_hora_via_gemini(texto_natural: str, ahora_str: str, dia_semana: str) -> Optional[str]:
    """Respaldo para fecha_parser.py cuando dateparser no logra extraer
    nada solo. Retorna 'YYYY-MM-DD HH:MM' o None."""
    if not gemini_disponible():
        return None
    prompt = (
        f"La fecha y hora actual es: {ahora_str} ({dia_semana}).\n"
        f"Extrae la fecha y hora exacta a la que se refiere este texto: \"{texto_natural}\"\n"
        "Responde SOLO un JSON con este formato exacto, sin texto adicional, sin markdown: "
        '{"fecha_hora": "<YYYY-MM-DD HH:MM>", "encontrada": true} '
        'o {"encontrada": false} si el texto no menciona ningún momento concreto.'
    )
    contenido = _llamar_gemini(prompt, 100, 0.0, json_mode=True)
    if not contenido:
        return None
    try:
        datos = json.loads(_extraer_json(contenido))
    except Exception as e:
        _log_error("respaldo_externo", "extraer_fecha_hora_via_gemini", e)
        return None
    if not datos.get("encontrada"):
        return None
    return datos.get("fecha_hora")
def responder_pregunta_informativa(pregunta: str) -> Optional[str]:
    import logging
    from concurrent.futures import ThreadPoolExecutor, as_completed
    prompt = (
        "Responde en español, directo y conciso: máximo 3 oraciones (una respuesta hablable). "
        "Sin markdown, sin listas, sin preámbulos.\n\n"
        f"Pregunta: \"{pregunta}\""
    )
    mensajes = [{"role": "user", "content": prompt}]
    t0 = time.monotonic()
    tareas = {}
    if gemini_disponible():
        tareas["gemini"] = lambda: _llamar_gemini(prompt, 600, GEMINI_TEMPERATURA, False, presupuesto=10)
    if groq_disponible():
        tareas["groq"] = lambda: _llamar_groq(mensajes, GROQ_MODEL, 600, GROQ_TEMPERATURA, json_mode=False)
    if tareas:
        pool = ThreadPoolExecutor(max_workers=len(tareas))
        try:
            futuros = {pool.submit(fn): nombre for nombre, fn in tareas.items()}
            for f in as_completed(futuros, timeout=12):
                try:
                    r = f.result()
                except Exception:
                    r = None
                if r and r.strip():
                    logging.getLogger("sara").info("[INFORMATIVA] ganó %s en %.1fs", futuros[f], time.monotonic() - t0)
                    return r.strip()
        except Exception as e:
            _log_error("respaldo_externo", "responder_pregunta_informativa (carrera)", e)
        finally:
            pool.shutdown(wait=False)
    logging.getLogger("sara").info("[INFORMATIVA] ninguno respondió a tiempo (%.1fs), paso a DeepSeek", time.monotonic() - t0)
    return _llamar_deepseek(mensajes)


def precalentar_conexiones() -> None:
    """Abre (y deja en el pool de la sesión) la conexión TLS con cada proveedor configurado, SIN enviar la clave
    ni gastar tokens: así la primera llamada real no paga DNS + TCP + TLS (≈ 0.2-0.6 s). Nunca lanza."""
    if _http is None:
        return
    destinos = []
    if groq_disponible():
        destinos.append("https://api.groq.com/")
    if gemini_disponible():
        destinos.append("https://generativelanguage.googleapis.com/")
    if deepseek_disponible():
        destinos.append("https://api.deepseek.com/")
    for url in destinos:
        try:
            _http.get(url, timeout=(TIMEOUT_CONEXION_S, 4))
        except Exception:
            pass      # da igual el código de respuesta o si falla: solo se busca la conexión caliente
