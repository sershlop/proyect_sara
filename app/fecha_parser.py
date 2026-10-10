
import re
from datetime import datetime, timedelta, time as _hora
from typing import Optional

try:
    import dateparser
    from dateparser.search import search_dates
    _DATEPARSER_DISPONIBLE = True
except ImportError:
    dateparser = None
    search_dates = None
    _DATEPARSER_DISPONIBLE = False

try:
    import respaldo_externo
except ImportError:
    respaldo_externo = None

import logging
_log = logging.getLogger("SARA-FECHA")

# Principio «local primero»: dateparser es la capa OFFLINE. Si falta, las fechas se mandan a Gemini (externo) o se
# usa «+60 min». Antes eso pasaba SIN AVISAR: ahora queda una advertencia en el log al arrancar.
if not _DATEPARSER_DISPONIBLE:
    _log.warning("⚠️ dateparser NO está instalado: las fechas de recordatorios/tareas se interpretan con Gemini "
                 "(sale de tu equipo y necesita internet) o caen al valor por defecto. Instálalo: pip install dateparser")


def dateparser_disponible() -> bool:
    return _DATEPARSER_DISPONIBLE


def diagnostico() -> str:
    return ("Fechas: dateparser (offline) activo" if _DATEPARSER_DISPONIBLE else
            "Fechas: dateparser NO instalado → se usa Gemini (externo) o +%d min (pip install dateparser)" % MINUTOS_DEFECTO_SIN_FECHA)

try:
    from config import MINUTOS_DEFECTO_SIN_FECHA
except Exception:
    MINUTOS_DEFECTO_SIN_FECHA = 60


def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        import logger as _logger
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[FECHA_PARSER ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


_AJUSTES_DATEPARSER = {
    "PREFER_DATES_FROM": "future",  # "el viernes" dicho un martes -> el que viene, no el pasado
    "RETURN_AS_TIMEZONE_AWARE": False,
    "STRICT_PARSING": False,
}

_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def formatear_fecha_es(fecha_hora: datetime) -> str:
    """strftime('%A')/('%B') dependen del locale del sistema, que en
    Windows no podemos garantizar en español — se evita esa fragilidad
    con una tabla propia en vez de depender de configuración externa."""
    return f"{_DIAS_ES[fecha_hora.weekday()]} {fecha_hora.day} de {_MESES_ES[fecha_hora.month - 1]}, {fecha_hora.strftime('%H:%M')}"


def _extraer_via_respaldo(texto: str) -> Optional[datetime]:
    if respaldo_externo is None:
        return None
    ahora = datetime.now()
    try:
        fecha_str = respaldo_externo.extraer_fecha_hora_via_gemini(
            texto, ahora.strftime("%Y-%m-%d %H:%M"), _DIAS_ES[ahora.weekday()]
        )
    except Exception as e:                         # sin internet, sin clave o función ausente: se degrada, no se rompe
        _log_error("fecha_parser", "_extraer_via_respaldo (llamada)", e)
        return None
    if not fecha_str:
        return None
    try:
        return datetime.strptime(fecha_str, "%Y-%m-%d %H:%M")
    except Exception as e:
        _log_error("fecha_parser", "_extraer_via_respaldo (parseo)", e)
        return None



# ─── INTÉRPRETE LOCAL DE HORAS Y DÍAS EN ESPAÑOL (offline, determinista) ──────────────────────────────────
# Por qué existe: con dateparser real, «mañana a las 8» devolvía mañana a las 10:00 (la hora actual), «el viernes a
# las 9» un martes a las 00:00 y «pasado mañana a las 10» caía en mañana. Y como dateparser «respondía», nunca se
# llegaba al respaldo. Este intérprete resuelve SOLO lo que entiende con seguridad (una hora o un «en N minutos»);
# en cualquier otro caso devuelve None y todo sigue exactamente como antes (dateparser → Gemini → +60 min).
# Sin hora clara en la frase NO actúa.

_NUM_ES = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
           "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "quince": 15, "veinte": 20, "treinta": 30}
_NUMEROS = "|".join(sorted(_NUM_ES, key=len, reverse=True))
_MESES_RE = "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
_DIAS_SEMANA = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}

_RE_REL = re.compile(
    r"\b(?:en|dentro\s+de)\s+(?:(?P<media>media\s+hora)|(?P<n>\d{1,3}(?!\d)|" + _NUMEROS + r")\s+"
    r"(?P<u>minutos?|mins?|horas?|hrs?|d[ií]as?|semanas?))\b", re.I)
_RE_HORA = re.compile(
    r"\b(?:a|sobre)\s+las?\s+(?P<h>\d{1,2}(?!\d)|" + _NUMEROS + r")\b"
    r"(?:\s*[:.hH]\s*(?P<m>[0-5]\d)\b)?"
    r"(?:\s+y\s+(?P<y>media|cuarto)\b|\s+menos\s+(?P<menos>cuarto|veinticinco|veinte|diez|cinco)\b)?"
    r"(?:\s*(?P<ap>[ap]\.?\s?m\.?)(?![a-záéíóúñ])|\s+de\s+la\s+(?P<dp>ma[ñn]ana|tarde|noche|madrugada)\b)?", re.I)
_RE_AMPM = re.compile(r"(?<![\d:.])(?P<h>\d{1,2})(?:[:.](?P<m>[0-5]\d))?\s*(?P<ap>[ap]\.?\s?m\.?)(?![a-záéíóúñ])", re.I)
_RE_MEDIO = re.compile(r"\b(?:a|al)\s+(?:el\s+)?(?P<mm>mediod[ií]a|medianoche)\b", re.I)
_RE_DIA = re.compile(
    r"(?<!\bla\s)(?<!\besta\s)\b(?P<pasado>pasado\s+)?ma[ñn]ana\b|\bhoy\b"
    r"|\besta\s+(?P<ep>noche|tarde|ma[ñn]ana)\b"
    r"|\b(?:el\s+)?(?:(?:pr[oó]ximo|siguiente)\s+)?(?P<wd>lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b", re.I)
_RE_FECHA_EXPLICITA = re.compile(r"\b\d{1,2}\s+de\s+(?:" + _MESES_RE + r")\b|\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b"
                                 r"|\bel\s+\d{1,2}\b|\b(?:" + _MESES_RE + r")\b", re.I)


def _sin_tildes(s: str) -> str:
    return s.lower().translate(str.maketrans("áéíóú", "aeiou"))


def _num(valor: str) -> int:
    v = valor.lower()
    return int(v) if v.isdigit() else _NUM_ES[v]


def _resolver_hora(h: int, ap, dp, es_hoy: bool, minuto: int, ahora: datetime) -> Optional[int]:
    """Hora en 24 h. Con «am/pm» o «de la tarde/noche» manda lo dicho. Sin marca: 1-6 → tarde (a las 3 = 15:00);
    7-11 → mañana, salvo que sea hoy y esa hora ya pasó (entonces la noche); 12 → mediodía; 13+ → tal cual."""
    if h > 24:
        return None
    if h == 24:
        h = 0
    if ap:
        if ap.lower().startswith("p") and h < 12:
            return h + 12
        return 0 if (ap.lower().startswith("a") and h == 12) else h
    if dp:
        d = dp.lower()
        if d == "tarde" and h < 12:
            return h + 12
        if d == "noche":
            return h + 12 if 6 <= h <= 11 else (0 if h == 12 else h)
        if d in ("madrugada", "mañana", "manana") and h == 12:
            return 0 if d == "madrugada" else 12
        return h
    if 1 <= h <= 6:
        return h + 12
    if 7 <= h <= 11 and es_hoy and datetime.combine(ahora.date(), _hora(h, minuto)) <= ahora:
        return h + 12
    return h


def _fecha_explicita_dateparser(texto: str, ahora: datetime):
    """(fecha, fragmento) si dateparser localiza una fecha EXPLÍCITA (con número de día); si no, None. Descarta sus
    fragmentos sueltos tipo «a», que dateparser interpreta como fechas."""
    if not _DATEPARSER_DISPONIBLE:
        return None
    try:
        enc = search_dates(texto, languages=["es"], settings={**_AJUSTES_DATEPARSER, "RELATIVE_BASE": ahora})
    except Exception as e:
        _log_error("fecha_parser", "_fecha_explicita_dateparser", e)
        return None
    for frag, f in sorted(enc or [], key=lambda p: -len(p[0])):
        if re.search(r"\d", frag):          # solo con número de día: «agosto» a secas es ambiguo y no se adivina
            return f, frag
    return None


def _extraer_local_es(texto: str, ahora: datetime):
    """{'fecha': datetime, 'spans': [(ini, fin), ...]} si entiende la frase con seguridad; None si no."""
    # 1) Relativo: «en 10 minutos», «dentro de 2 horas», «en media hora»
    m = _RE_REL.search(texto)
    if m:
        if m.group("media"):
            delta = timedelta(minutes=30)
        else:
            n, u = _num(m.group("n")), _sin_tildes(m.group("u"))
            delta = (timedelta(minutes=n) if u.startswith("min") else timedelta(hours=n) if u.startswith(("hora", "hr"))
                     else timedelta(weeks=n) if u.startswith("semana") else timedelta(days=n))
        return {"fecha": (ahora + delta).replace(second=0, microsecond=0), "spans": [m.span()]}

    # 2) Hora del día
    mh, medio = _RE_HORA.search(texto), None
    if mh is None:
        mh = _RE_AMPM.search(texto)
    if mh is None:
        medio = _RE_MEDIO.search(texto)
        if medio is None:
            return None
    spans = [(mh or medio).span()]
    g = (lambda k: mh.groupdict().get(k)) if mh else (lambda k: None)
    dp = g("dp")
    # el texto sin la hora conserva los índices (se rellena con espacios) → el día se busca sin confundir «de la mañana»
    s0, e0 = spans[0]
    sin_hora = texto[:s0] + " " * (e0 - s0) + texto[e0:]

    # 3) Día
    dia_expl, es_hoy_dicho, avanzar_si_pasado, base = False, False, 1, ahora.date()
    md = _RE_DIA.search(sin_hora)
    if md:
        spans.append(md.span())
        dia_expl = True
        if md.group("wd"):
            delta = (_DIAS_SEMANA[_sin_tildes(md.group("wd"))] - ahora.weekday()) % 7
            base, avanzar_si_pasado = ahora.date() + timedelta(days=delta), 7
        elif md.group("ep"):
            es_hoy_dicho = True
            dp = dp or ("noche" if md.group("ep").lower() == "noche" else "tarde" if md.group("ep").lower() == "tarde" else "mañana")
        elif md.group(0).lower().strip() == "hoy":
            es_hoy_dicho = True
        else:
            base = ahora.date() + timedelta(days=2 if md.group("pasado") else 1)
    elif _RE_FECHA_EXPLICITA.search(sin_hora):
        exp = _fecha_explicita_dateparser(sin_hora, ahora)
        if exp is None:
            return None                       # hay una fecha que este intérprete no sabe leer: que decida el flujo de siempre
        base, dia_expl = exp[0].date(), True
        ini = texto.find(exp[1])
        if ini >= 0:
            spans.append((ini, ini + len(exp[1])))

    es_hoy = base == ahora.date()
    if medio is not None:
        h, minuto, menos = (12 if medio.group("mm").lower().startswith("mediod") else 0), 0, 0
    else:
        h0 = _num(g("h")) if g("h") else 0
        minuto = int(g("m")) if g("m") else (30 if (g("y") or "").lower() == "media" else 15 if (g("y") or "").lower() == "cuarto" else 0)
        menos = {"cuarto": 15, "veinticinco": 25, "veinte": 20, "diez": 10, "cinco": 5}.get((g("menos") or "").lower(), 0)
        h = _resolver_hora(h0, g("ap"), dp, es_hoy, minuto, ahora)
        if h is None:
            return None
    fecha = datetime.combine(base, _hora(h, minuto)) - timedelta(minutes=menos)
    if fecha <= ahora and not es_hoy_dicho and not (dia_expl and not md):
        fecha += timedelta(days=avanzar_si_pasado)         # «a las 8» ya pasada → mañana; «el viernes» ya pasado → el próximo
    return {"fecha": fecha, "spans": spans}


def _quitar_spans(texto: str, spans) -> str:
    out = texto
    for ini, fin in sorted(spans, reverse=True):
        out = out[:ini] + " " + out[fin:]
    return out


def extraer_fecha_y_texto_limpio(texto_original: str, ahora: Optional[datetime] = None) -> dict:
    """
    Retorna {"fecha_hora": datetime, "fuente": str, "texto_sin_fecha": str}.
    fuente ∈ {'dateparser', 'respaldo_externo', 'default_sin_fecha'}.
    texto_sin_fecha es texto_original con el fragmento de fecha ya
    quitado (cuando se pudo aislar) — para que el mensaje guardado no
    repita "mañana a las 8" dentro de su propio texto.
    """
    texto_sin_fecha = texto_original
    fecha = None
    fuente = "default_sin_fecha"
    ahora = ahora or datetime.now()

    # Capa 0 (offline, determinista): horas y días dichos en español. Si no entiende la frase con seguridad → None
    # y sigue el flujo de siempre. «fuente» conserva el valor «dateparser» (los consumidores no esperan otro);
    # «detalle» dice quién la resolvió de verdad.
    try:
        local = _extraer_local_es(texto_original, ahora)
    except Exception as e:
        _log_error("fecha_parser", "extraer_fecha_y_texto_limpio (intérprete local)", e)
        local = None
    if local:
        return {"fecha_hora": local["fecha"], "fuente": "dateparser", "detalle": "local_es",
                "texto_sin_fecha": _quitar_spans(texto_original, local["spans"])}

    if _DATEPARSER_DISPONIBLE:
        try:
            encontrados = search_dates(texto_original, languages=["es"], settings=_AJUSTES_DATEPARSER)
        except Exception as e:
            _log_error("fecha_parser", "extraer_fecha_y_texto_limpio (search_dates)", e)
            encontrados = None
        if encontrados:
            # Match más largo — evita que un número suelto del propio
            # mensaje (ej. "tarea numero 2") se confunda con una fecha.
            fragmento, fecha_encontrada = max(encontrados, key=lambda par: len(par[0]))
            fecha = fecha_encontrada
            texto_sin_fecha = texto_original.replace(fragmento, " ", 1)
            fuente = "dateparser"

    if fecha is None:
        fecha = _extraer_via_respaldo(texto_original)
        if fecha:
            fuente = "respaldo_externo"
            # No se puede aislar con la misma precisión el fragmento que
            # usó el modelo externo — texto_sin_fecha queda igual al
            # original; la limpieza normal de verbos en productivity.py
            # sigue aplicando encima de esto.

    if fecha is None:
        fecha = ahora + timedelta(minutes=MINUTOS_DEFECTO_SIN_FECHA)
        fuente = "default_sin_fecha"

    return {"fecha_hora": fecha, "fuente": fuente, "texto_sin_fecha": texto_sin_fecha}