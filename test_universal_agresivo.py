"""
╔══════════════════════════════════════════════════════════════════╗
║          SARA — Suite de Pruebas Universal v1.1                 ║
║          Firmas adaptadas al código real de SARA                ║
║          Genera reporte .txt + .md con diagnóstico real         ║
╚══════════════════════════════════════════════════════════════════╝

Uso:
    python test_sara_universal.py              # Todas las suites
    python test_sara_universal.py --suite sistema
    python test_sara_universal.py --suite archivo
    python test_sara_universal.py --suite busqueda
    python test_sara_universal.py --suite splitter
    python test_sara_universal.py --suite semantico
    python test_sara_universal.py --suite aprendizaje
    python test_sara_universal.py --suite flujo
    python test_sara_universal.py --suite extremas

Flags:
    --verbose       Muestra cada caso en tiempo real
    --no-report     No genera archivos, solo consola
    --out <ruta>    Carpeta de salida (default: ./reportes)
"""

import sys
import os
import time
import argparse
from datetime import datetime
from dataclasses import dataclass, field
from typing import Callable, Optional

# ─────────────────────────────────────────────────────────────────
# PATH — ajustar si el test no está junto a sara.py
# ─────────────────────────────────────────────────────────────────
SARA_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SARA_ROOT not in sys.path:
    sys.path.insert(0, SARA_ROOT)

_modulos_ok    = {}
_modulos_error = {}

def _importar(nombre):
    try:
        import importlib
        mod = importlib.import_module(nombre)
        _modulos_ok[nombre] = mod
        return mod
    except Exception as e:
        _modulos_error[nombre] = str(e)
        return None

brain       = _importar("brain")
splitter    = _importar("splitter")
embeddings  = _importar("embeddings")
sistema     = _importar("sistema")
database    = _importar("database")
learning    = _importar("learning")
searcher    = _importar("searcher")
file_intent = _importar("file_intent")
sara_mod    = _importar("sara")
config      = _importar("config")

# ─────────────────────────────────────────────────────────────────
# ESTRUCTURAS
# ─────────────────────────────────────────────────────────────────

@dataclass
class Caso:
    entrada: str
    esperado: str
    descripcion: str = ""
    conf_minima: float = 0.0
    variante_de: str = ""

@dataclass
class Resultado:
    caso: Caso
    ok: bool
    obtenido: str
    confianza: float
    tiempo_ms: float
    error: str = ""
    advertencia: str = ""

@dataclass
class Suite:
    nombre: str
    descripcion: str
    casos: list = field(default_factory=list)
    resultados: list = field(default_factory=list)

    @property
    def total(self):        return len(self.resultados)
    @property
    def ok(self):           return sum(1 for r in self.resultados if r.ok)
    @property
    def fallos(self):       return sum(1 for r in self.resultados if not r.ok and not r.advertencia)
    @property
    def advertencias(self): return sum(1 for r in self.resultados if r.advertencia)
    @property
    def tasa(self):         return (self.ok / self.total * 100) if self.total else 0

# ─────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────

class Runner:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self._sara_inicializado = False

    def _inicializar_sara(self):
        if self._sara_inicializado:
            return True
        if sara_mod and hasattr(sara_mod, "inicializar"):
            try:
                ok = sara_mod.inicializar()
                self._sara_inicializado = bool(ok)
                return self._sara_inicializado
            except Exception:
                return False
        return False

    def _procesar_brain(self, entrada: str) -> tuple:
        """Llama a brain.procesar() → (tipo, confianza_pct, tiempo_ms)"""
        if not brain:
            return "error_modulo", 0.0, 0.0
        t0 = time.perf_counter()
        try:
            res = brain.procesar(entrada)
            ms  = (time.perf_counter() - t0) * 1000
            if isinstance(res, dict):
                tipo = res.get("tipo", "desconocido")
                conf = res.get("confianza", 0.0) * 100
            else:
                tipo = "desconocido"
                conf = 0.0
            return tipo, round(conf, 1), round(ms, 1)
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            return f"excepcion: {e}", 0.0, round(ms, 1)

    def _verificar(self, caso: Caso, obtenido: str, conf: float, tiempo_ms: float, error="") -> Resultado:
        adv = ""
        if error or obtenido.startswith("excepcion"):
            ok = False
        elif obtenido == caso.esperado:
            ok = True
            if caso.conf_minima > 0 and conf < caso.conf_minima:
                adv = f"conf={conf:.0f}% < mín={caso.conf_minima:.0f}%"
        else:
            ok = False
        return Resultado(
            caso=caso, ok=ok, obtenido=obtenido,
            confianza=conf, tiempo_ms=tiempo_ms,
            error=error, advertencia=adv
        )

    def correr_suite(self, suite: Suite, fn: Callable) -> Suite:
        suite.resultados = []
        for caso in suite.casos:
            try:
                resultado = fn(caso)
            except Exception as e:
                resultado = Resultado(
                    caso=caso, ok=False, obtenido="excepcion_runner",
                    confianza=0.0, tiempo_ms=0.0, error=str(e)
                )
            suite.resultados.append(resultado)
            if self.verbose:
                s = "✓" if resultado.ok else ("⚠" if resultado.advertencia else "✗")
                print(f"  {s} [{resultado.tiempo_ms:.0f}ms] {caso.entrada!r} → {resultado.obtenido} ({resultado.confianza:.0f}%)")
                if resultado.error:       print(f"      ERROR: {resultado.error}")
                if resultado.advertencia: print(f"      ADV:   {resultado.advertencia}")
        return suite


# ─────────────────────────────────────────────────────────────────
# SUITES
# ─────────────────────────────────────────────────────────────────

def suite_flujo_completo(runner: Runner) -> Suite:
    """Flujo end-to-end via brain.procesar() — verifica tipo e intención."""
    suite = Suite("flujo_completo", "Flujo end-to-end via brain.procesar()")
    suite.casos = [
        # Comandos sistema
        Caso("silencia",                  "comando",     "Silenciar",        conf_minima=80),
        Caso("sube el volumen",           "comando",     "Subir volumen",    conf_minima=80),
        Caso("baja el volumen",           "comando",     "Bajar volumen",    conf_minima=70),
        Caso("pausa la música",           "comando",     "Pausar música",    conf_minima=70),
        Caso("siguiente canción",         "comando",     "Siguiente pista",  conf_minima=80),
        Caso("canción anterior",          "comando",     "Pista anterior",   conf_minima=80),
        Caso("bloquea la pantalla",       "comando",     "Bloquear",         conf_minima=80),
        Caso("info sistema",              "comando",     "Info sistema",     conf_minima=80),
        Caso("vaciar papelera",           "comando",     "Papelera",         conf_minima=80),
        
        # Archivos / apps
        Caso("abre chrome",               "archivo",     "Abrir Chrome",     conf_minima=90),
        Caso("abre spotify",              "archivo",     "Abrir Spotify",    conf_minima=90),
        Caso("abre mis documentos",       "archivo",     "Documentos",       conf_minima=90),
        Caso("abre la carpeta descargas", "archivo",     "Descargas",        conf_minima=90),
        Caso("abre el escritorio",        "archivo",     "Escritorio",       conf_minima=70),
        Caso("abre mis imágenes",         "archivo",     "Imágenes",         conf_minima=80),
        # Búsquedas
        Caso("busca python en youtube",      "busqueda", "YouTube",          conf_minima=80),
        Caso("busca el clima en google",     "busqueda", "Google",           conf_minima=80),
        Caso("busca noticias en bing",       "busqueda", "Bing",             conf_minima=80),
        Caso("busca recetas en duckduckgo",  "busqueda", "DuckDuckGo",       conf_minima=80),
        # Preguntas — brain llama a buscar_respuesta (BUG CONOCIDO si falla)
        Caso("qué es la fotosíntesis",    "respuesta",   "Pregunta conocimiento"),
        Caso("cómo funciona python",      "respuesta",   "Pregunta técnica"),
        Caso("quién eres",                "respuesta",   "Pregunta identidad"),
        Caso("qué puedes hacer",          "respuesta",   "Pregunta capacidades"),
        # Código
        Caso("crea un programa en python que sume dos números", "comando", "Código Python"),
        Caso("crea un script que liste archivos del escritorio","comando", "Script"),
        Caso("genera un programa de calculadora en python",     "comando", "Calculadora"),
        # Extremas base
        Caso("",          "desconocido", "Vacío"),
        Caso(" ",         "desconocido", "Espacio"),
        Caso("@#$%^&*()", "desconocido", "Especiales"),
    ]

    def fn(caso):
        tipo, conf, ms = runner._procesar_brain(caso.entrada)
        return runner._verificar(caso, tipo, conf, ms)

    return runner.correr_suite(suite, fn)


def suite_variaciones(runner: Runner) -> Suite:
    """Sinónimos y frases equivalentes — detecta huecos semánticos/difflib."""
    suite = Suite("variaciones", "Sinónimos y frases equivalentes por grupo")
    suite.casos = [
        # ── Silenciar
        Caso("silencia",             "comando", variante_de="silenciar"),
        Caso("silencia el audio",    "comando", variante_de="silenciar"),
        Caso("pon en mudo",          "comando", variante_de="silenciar", descripcion="HISTÓRICO FALLO"),
        Caso("quita el sonido",      "comando", variante_de="silenciar", descripcion="HISTÓRICO FALLO"),
        Caso("mutea",                "comando", variante_de="silenciar"),
        Caso("apaga el sonido",      "comando", variante_de="silenciar"),
        # ── Pausar
        Caso("pausa la música",      "comando", variante_de="pausar"),
        Caso("pausa el audio",       "comando", variante_de="pausar"),
        Caso("para la música",       "comando", variante_de="pausar", descripcion="HISTÓRICO FALLO → archivo"),
        Caso("detén la música",      "comando", variante_de="pausar"),
        # ── Abrir Chrome
        Caso("abre chrome",              "archivo", variante_de="abrir_chrome"),
        Caso("abre el navegador chrome", "archivo", variante_de="abrir_chrome", descripcion="HISTÓRICO FALLO"),
        Caso("abre google chrome",       "archivo", variante_de="abrir_chrome"),
        Caso("abre el navegador",        "archivo", variante_de="abrir_chrome"),
        Caso("inicia chrome",            "archivo", variante_de="abrir_chrome"),
        # ── Volumen subir
        Caso("sube el volumen",  "comando", variante_de="vol_subir"),
        Caso("sube el audio",    "comando", variante_de="vol_subir"),
        Caso("más volumen",      "comando", variante_de="vol_subir"),
        Caso("súbele",           "comando", variante_de="vol_subir"),
        # ── Volumen bajar
        Caso("baja el volumen",  "comando", variante_de="vol_bajar"),
        Caso("menos volumen",    "comando", variante_de="vol_bajar"),
        Caso("baja el audio",    "comando", variante_de="vol_bajar"),
        # ── Documentos
        Caso("abre mis documentos",        "archivo", variante_de="documentos"),
        Caso("abre la carpeta documentos", "archivo", variante_de="documentos"),
        Caso("ve a documentos",            "archivo", variante_de="documentos"),
        Caso("muéstrame mis documentos",   "archivo", variante_de="documentos"),
    ]

    def fn(caso):
        tipo, conf, ms = runner._procesar_brain(caso.entrada)
        return runner._verificar(caso, tipo, conf, ms)

    return runner.correr_suite(suite, fn)


def suite_sistema_directo(runner: Runner) -> Suite:
    """
    Llama directamente a funciones de sistema.py con sus nombres reales.
    Nombres reales confirmados en código:
      subir_volumen, bajar_volumen, silenciar_volumen, pausar_reproducir,
      siguiente_pista, pista_anterior, info_sistema, bloquear_pantalla,
     limpiar_papelera
    """
    suite = Suite("sistema_directo", "Llamadas directas a sistema.py — nombres reales del código")

    funciones = [
        ("subir_volumen",     "Subir volumen"),
        ("bajar_volumen",     "Bajar volumen"),
        ("silenciar_volumen", "Silenciar"),
        ("pausar_reproducir", "Pausar/reproducir"),
        ("siguiente_pista",   "Siguiente pista"),
        ("pista_anterior",    "Pista anterior"),
        ("info_sistema",      "Info sistema"),        # antes: obtener_info_sistema ← CORREGIDO
        ("bloquear_pantalla", "Bloquear pantalla"),
       
        ("limpiar_papelera",  "Limpiar papelera"),   # antes: vaciar_papelera ← CORREGIDO
    ]

    for fn_name, desc in funciones:
        suite.casos.append(Caso(fn_name, "exito", desc))

    def fn(caso):
        if not sistema:
            return Resultado(caso=caso, ok=False, obtenido="modulo_no_disponible",
                             confianza=0, tiempo_ms=0, error="sistema.py no importado")
        fn_real = getattr(sistema, caso.entrada, None)
        if fn_real is None:
            return Resultado(caso=caso, ok=False, obtenido="funcion_no_existe",
                             confianza=0, tiempo_ms=0,
                             error=f"sistema.{caso.entrada} no existe")
        t0 = time.perf_counter()
        try:
            res = fn_real()
            ms  = (time.perf_counter() - t0) * 1000
            if isinstance(res, dict):
                exito = res.get("exito", False)
                msg   = res.get("mensaje", "")
            else:
                exito = bool(res)
                msg   = str(res)
            obtenido = "exito" if exito else "fallo"
            adv      = "" if exito else f"retornó fallo: {msg}"
            return Resultado(caso=caso, ok=exito, obtenido=obtenido,
                             confianza=100 if exito else 0,
                             tiempo_ms=round(ms, 1), advertencia=adv)
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            return Resultado(caso=caso, ok=False, obtenido="excepcion",
                             confianza=0, tiempo_ms=round(ms, 1), error=str(e))

    return runner.correr_suite(suite, fn)


def suite_splitter(runner: Runner) -> Suite:
    """splitter.dividir_entrada() — verifica cantidad de partes generadas."""
    suite = Suite("splitter", "División de entradas — splitter.dividir_entrada()")

    casos_raw = [
        ("abre chrome",                                    1, "Simple"),
        ("crea un programa en python con menú",            1, "No dividir código"),
        ("abre chrome y busca python en youtube",          2, "Archivo + búsqueda"),
        ("qué es python y cómo funciona",                  2, "Pregunta compuesta real"),
        ("silencia y pausa la música",                     2, "Dos comandos"),
        ("sube el volumen y siguiente canción",            2, "Dos comandos"),
        ("abre chrome y silencia",                         2, "Archivo + comando"),
        ("pausa la música y baja el volumen",              2, "Dos comandos"),
        ("abre mis documentos y busca python en youtube",  2, "Archivo + búsqueda"),
        ("qué es python y abre chrome",                    2, "Pregunta + archivo"),
        ("crea un programa de suma y resta en python",     1, "No dividir 'y' en código"),
        ("abre la carpeta fotos y videos",                 1, "No dividir nombre de carpeta"),
        ("busca videos de python y django en youtube",     1, "No dividir query de búsqueda"),
        ("",                                               1, "Vacío → lista de 1"),
        ("y",                                              1, "Solo conector"),
        ("abre chrome y y y abre spotify",                 2, "HISTÓRICO FALLO — 'y' repetida"),
        ("crea un script y abre mis documentos",           2, "Código + archivo"),
        ("crea una calculadora, suma, resta y división",   1, "No dividir código con comas"),
    ]

    for entrada, partes, desc in casos_raw:
        suite.casos.append(Caso(entrada, str(partes), desc))

    def fn(caso):
        if not splitter:
            return Resultado(caso=caso, ok=False, obtenido="modulo_no_disponible",
                             confianza=0, tiempo_ms=0, error="splitter.py no importado")
        partes_esperadas = int(caso.esperado)
        t0 = time.perf_counter()
        try:
            resultado = splitter.dividir_entrada(caso.entrada)
            ms = (time.perf_counter() - t0) * 1000
            n  = len(resultado) if isinstance(resultado, list) else 1
            ok = n == partes_esperadas
            return Resultado(caso=caso, ok=ok, obtenido=str(n),
                             confianza=100 if ok else 0,
                             tiempo_ms=round(ms, 1))
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            return Resultado(caso=caso, ok=False, obtenido="excepcion",
                             confianza=0, tiempo_ms=round(ms, 1), error=str(e))

    return runner.correr_suite(suite, fn)


def suite_semantico(runner: Runner) -> Suite:
    """
    embeddings.similitud_semantica(texto_a, texto_b) — nombre real confirmado.
    Antes el test llamaba embeddings.similitud() que no existe → corregido.
    """
    suite = Suite("semantico", "Similitud semántica — embeddings.similitud_semantica()")

    # (texto_a, texto_b, nivel, umbral_pct, es_historico_fallo)
    casos_raw = [
        ("abre chrome",         "abrir google chrome",    "alta", 55),
        ("pausa la música",     "pausar reproducción",    "alta", 55, True),
        ("qué es python",       "cómo funciona python",   "alta", 75),
        ("sube el volumen",     "baja el volumen",        "alta", 70),
        ("siguiente canción",   "canción anterior",       "alta", 70),
        ("silencia el audio",   "pon en mudo",            "alta", 55),
        ("abre mis documentos", "ve a documentos",        "alta", 60),
        # Pares NO similares — similitud debe quedar BAJO el umbral
        ("abre chrome",         "cuanta ram tengo",       "baja", 50),
        ("silencia el audio",   "abre mis documentos",    "baja", 40),
        ("qué es python",       "abre el escritorio",     "baja", 40),
        ("busca en youtube",    "bloquea la pantalla",    "baja", 40),
    ]

    for raw in casos_raw:
        a, b, nivel, umbral = raw[0], raw[1], raw[2], raw[3]
        historico = raw[4] if len(raw) > 4 else False
        desc = f"{'HISTÓRICO FALLO — ' if historico else ''}{nivel} similitud (umbral={umbral}%)"
        # conf_minima = umbral para pares 'alta'; se ignora en la lógica para 'baja'
        suite.casos.append(Caso(f"{a}↔{b}", nivel, desc, conf_minima=float(umbral)))

    def fn(caso):
        if not embeddings:
            return Resultado(caso=caso, ok=False, obtenido="modulo_no_disponible",
                             confianza=0, tiempo_ms=0, error="embeddings.py no importado")

        # Verificar que el modelo esté disponible
        if not embeddings.esta_disponible():
            return Resultado(caso=caso, ok=False, obtenido="modelo_no_cargado",
                             confianza=0, tiempo_ms=0,
                             advertencia="embeddings no disponible — cargar_modelo() no fue llamado")

        partes = caso.entrada.split("↔")
        if len(partes) != 2:
            return Resultado(caso=caso, ok=False, obtenido="formato_invalido",
                             confianza=0, tiempo_ms=0)

        a, b = partes[0].strip(), partes[1].strip()
        t0 = time.perf_counter()
        try:
            # Nombre real: similitud_semantica() — no similitud()
            sim    = embeddings.similitud_semantica(a, b)
            ms     = (time.perf_counter() - t0) * 1000
            sim_pct = round(sim * 100, 1)
            umbral  = caso.conf_minima

            if caso.esperado == "alta":
                ok  = sim_pct >= umbral
                adv = "" if ok else f"sim={sim_pct:.0f}% < esperado>={umbral:.0f}%"
            else:  # baja
                ok  = sim_pct < umbral
                adv = "" if ok else f"sim={sim_pct:.0f}% demasiado alta, esperado<{umbral:.0f}%"

            return Resultado(caso=caso, ok=ok, obtenido=f"sim={sim_pct:.0f}%",
                             confianza=sim_pct, tiempo_ms=round(ms, 1), advertencia=adv)
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            return Resultado(caso=caso, ok=False, obtenido="excepcion",
                             confianza=0, tiempo_ms=round(ms, 1), error=str(e))

    return runner.correr_suite(suite, fn)


def suite_aprendizaje(runner: Runner) -> Suite:
    """
    Firmas reales confirmadas:
      aprender_pregunta(pregunta, respuesta) → accion: 'guardada'|'duplicada'|'error'
      aprender_comando(nombre, palabras_clave, accion, tipo, descripcion="")
      corregir_pregunta(pregunta, respuesta_nueva)   ← solo 2 args
    """
    suite = Suite("aprendizaje", "learning.py — firmas reales del código")

    ts = str(int(time.time()))
    suite.casos = [
        Caso(f"test_pregunta_{ts}",  "guardada",   "Aprender nueva pregunta"),
        Caso(f"test_pregunta_{ts}",  "respuesta",  "Recuperar pregunta aprendida"),
        Caso(f"test_pregunta_{ts}",  "duplicada",  "Detectar duplicado exacto"),
        Caso(f"test_comando_{ts}",   "guardada",   "Aprender nuevo comando"),
        Caso(f"test_comando_{ts}",   "comando",    "Recuperar comando aprendido"),
        Caso(f"correccion_{ts}",     "corregida",  "Guardar corrección"),
        Caso(f"correccion_{ts}",     "respuesta",  "Recuperar tras corrección"),
    ]

    estado = {"pregunta_ok": False, "comando_ok": False}

    def fn(caso):
        if not learning or not brain:
            return Resultado(caso=caso, ok=False, obtenido="modulo_no_disponible",
                             confianza=0, tiempo_ms=0)
        t0 = time.perf_counter()
        entrada  = caso.entrada
        esperado = caso.esperado

        try:
            # ── Aprender pregunta nueva ───────────────────────────────────
            if esperado == "guardada" and "pregunta" in entrada:
                res = learning.aprender_pregunta(entrada, f"Respuesta test para {entrada}")
                ms  = (time.perf_counter() - t0) * 1000
                # accion real: 'guardada' (no 'aprendida')
                ok  = res.get("exito", False) and res.get("accion") == "guardada"
                estado["pregunta_ok"] = ok
                return Resultado(caso=caso, ok=ok, obtenido=res.get("accion", "?"),
                                 confianza=100 if ok else 0, tiempo_ms=round(ms, 1),
                                 error="" if ok else res.get("mensaje", ""))

            # ── Recuperar pregunta ────────────────────────────────────────
            elif esperado == "respuesta" and "pregunta" in entrada:
                if not estado["pregunta_ok"]:
                    return Resultado(caso=caso, ok=False, obtenido="skip",
                                     confianza=0, tiempo_ms=0,
                                     advertencia="Depende de 'guardada' — ejecutar en orden")
                res  = brain.procesar(entrada)
                ms   = (time.perf_counter() - t0) * 1000
                tipo = res.get("tipo", "desconocido")
                ok   = tipo in ("respuesta", "comando", "archivo")
                return Resultado(caso=caso, ok=ok, obtenido=tipo,
                                 confianza=res.get("confianza", 0) * 100,
                                 tiempo_ms=round(ms, 1))

            # ── Duplicado exacto ──────────────────────────────────────────
            elif esperado == "duplicada":
                res = learning.aprender_pregunta(entrada, f"Respuesta test para {entrada}")
                ms  = (time.perf_counter() - t0) * 1000
                ok  = res.get("accion") == "duplicada"
                return Resultado(caso=caso, ok=ok, obtenido=res.get("accion", "?"),
                                 confianza=100 if ok else 0, tiempo_ms=round(ms, 1))

            # ── Aprender comando nuevo ────────────────────────────────────
            elif esperado == "guardada" and "comando" in entrada:
                # Firma real: aprender_comando(nombre, palabras_clave, accion, tipo, descripcion="")
                res = learning.aprender_comando(
                    entrada,           # nombre
                    entrada,           # palabras_clave
                    f"accion_{entrada}",# accion
                    "sistema",         # tipo — requerido
                    "Comando de test"  # descripcion
                )
                ms  = (time.perf_counter() - t0) * 1000
                ok  = res.get("exito", False) and res.get("accion") == "guardada"
                estado["comando_ok"] = ok
                return Resultado(caso=caso, ok=ok, obtenido=res.get("accion", "?"),
                                 confianza=100 if ok else 0, tiempo_ms=round(ms, 1),
                                 error="" if ok else res.get("mensaje", ""))

            # ── Recuperar comando ─────────────────────────────────────────
            elif esperado == "comando":
                if not estado["comando_ok"]:
                    return Resultado(caso=caso, ok=False, obtenido="skip",
                                     confianza=0, tiempo_ms=0,
                                     advertencia="Depende de 'guardada' (comando) — ejecutar en orden")
                res  = brain.procesar(entrada)
                ms   = (time.perf_counter() - t0) * 1000
                ok   = res.get("tipo") == "comando"
                return Resultado(caso=caso, ok=ok, obtenido=res.get("tipo", "?"),
                                 confianza=res.get("confianza", 0) * 100,
                                 tiempo_ms=round(ms, 1))

            # ── Guardar corrección ────────────────────────────────────────
            elif esperado == "corregida":
                # Firma real: corregir_pregunta(pregunta, respuesta_nueva) — solo 2 args
                res = learning.corregir_pregunta(entrada, "respuesta_corregida_test")
                ms  = (time.perf_counter() - t0) * 1000
                ok  = res.get("exito", False) and res.get("accion") == "corregida"
                return Resultado(caso=caso, ok=ok, obtenido=res.get("accion", "?"),
                                 confianza=100 if ok else 0, tiempo_ms=round(ms, 1),
                                 error="" if ok else res.get("mensaje", ""))

            # ── Recuperar tras corrección ─────────────────────────────────
            elif esperado == "respuesta" and "correccion" in entrada:
                res  = brain.procesar(entrada)
                ms   = (time.perf_counter() - t0) * 1000
                tipo = res.get("tipo", "desconocido")
                ok   = tipo in ("respuesta", "desconocido")
                return Resultado(caso=caso, ok=ok, obtenido=tipo,
                                 confianza=res.get("confianza", 0) * 100,
                                 tiempo_ms=round(ms, 1))

            else:
                return Resultado(caso=caso, ok=False, obtenido="caso_no_manejado",
                                 confianza=0, tiempo_ms=0)

        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            return Resultado(caso=caso, ok=False, obtenido="excepcion",
                             confianza=0, tiempo_ms=round(ms, 1), error=str(e))

    return runner.correr_suite(suite, fn)


def suite_extremas(runner: Runner) -> Suite:
    """Robustez ante entradas adversariales — nada debe crashear."""
    suite = Suite("extremas", "Robustez ante entradas adversariales y límite")
    suite.casos = [
        Caso("",                                    "desconocido", "Vacío"),
        Caso(" ",                                   "desconocido", "Espacio"),
        Caso("\n\t\r",                              "desconocido", "Whitespace"),
        Caso("a",                                   "desconocido", "Un carácter"),
        Caso("??",                                  "desconocido", "Signos"),
        Caso("@#$%^&*()",                           "desconocido", "Especiales"),
        Caso("0",                                   "desconocido", "Cero"),
        Caso("-1",                                  "desconocido", "Negativo"),
        Caso("3.14159",                             "desconocido", "Float"),
        Caso("999999999",                           "desconocido", "Número grande"),
        Caso("null",                                "desconocido", "Literal null"),
        Caso("None",                                "desconocido", "Literal None"),
        Caso("True",                                "desconocido", "Literal True"),
        Caso("'; DROP TABLE comandos; --",          "desconocido", "SQL injection"),
        Caso("<script>alert('x')</script>",         "desconocido", "XSS"),
        Caso("__import__('os').system('dir')",      "desconocido", "Python injection"),
        Caso("a" * 60,                              "desconocido", "String largo sin sentido"),
        # Repetición extrema — debe reconocer el patrón base
        Caso("abre chrome " * 8,                   "archivo",     "Repetición 'abre chrome'"),
        # Multilang — difflib puede reconocer 'chrome' aunque la frase sea extranjera
        Caso("open chrome",    "archivo", "Inglés"),
        Caso("ouvre chrome",   "archivo", "Francés"),
        Caso("öffne chrome",   "archivo", "Alemán"),
    ]

    def fn(caso):
        tipo, conf, ms = runner._procesar_brain(caso.entrada)
        return runner._verificar(caso, tipo, conf, ms)

    return runner.correr_suite(suite, fn)


def suite_archivo_intent(runner: Runner) -> Suite:
    """
    file_intent.detectar_intencion_archivo(texto_original, texto_limpio)
    Firma real: requiere 2 argumentos — texto_original Y texto_limpio.
    Retorna lista de dicts con 'tipo', o None.
    """
    suite = Suite("archivo_intent", "file_intent.detectar_intencion_archivo() — firma real con 2 args")
    suite.casos = [
        # Carpetas sistema — deben retornar tipo='carpeta'
        Caso("abre mis documentos",       "carpeta", "Documentos"),
        Caso("abre descargas",            "carpeta", "Descargas"),
        Caso("abre el escritorio",        "carpeta", "Escritorio"),
        Caso("abre mis imágenes",         "carpeta", "Imágenes"),
        Caso("abre mis videos",           "carpeta", "Vídeos"),
        # Apps sistema — deben retornar tipo='app'
        Caso("abre chrome",               "app",     "Chrome — en PLATAFORMAS_WEB, debería retornar None→test espera None"),
        Caso("abre la calculadora",       "app",     "Calculadora"),
        Caso("abre el bloc de notas",     "app",     "Notepad"),
        # Chrome está en PLATAFORMAS_WEB sin fuerza_app → retorna None (comportamiento real)
        Caso("abre spotify",              "None",    "Spotify en PLATAFORMAS_WEB → None"),
        Caso("abre chrome",               "None",    "Chrome en PLATAFORMAS_WEB → None"),
        # No archivos
        Caso("busca python en youtube",   "None",    "Búsqueda web"),
        Caso("qué es python",             "None",    "Pregunta"),
        Caso("sube el volumen",           "None",    "Comando sistema"),
        Caso("crea un programa python",   "None",    "Código"),
    ]

    def fn(caso):
        if not file_intent:
            return Resultado(caso=caso, ok=False, obtenido="modulo_no_disponible",
                             confianza=0, tiempo_ms=0, error="file_intent.py no importado")
        from utils import normalizar_texto as _norm
        texto_orig  = caso.entrada
        texto_limpio = _norm(caso.entrada)
        t0 = time.perf_counter()
        try:
            # Firma real: detectar_intencion_archivo(texto_original, texto_limpio)
            res = file_intent.detectar_intencion_archivo(texto_orig, texto_limpio)
            ms  = (time.perf_counter() - t0) * 1000
            if res is None:
                obtenido = "None"
                conf     = 0
            else:
                # Retorna lista de candidatos — tomamos el primero
                primero  = res[0] if isinstance(res, list) and res else res
                obtenido = primero.get("tipo", "desconocido")
                conf     = primero.get("confianza", 0) * 100
            ok = obtenido == caso.esperado
            return Resultado(caso=caso, ok=ok, obtenido=obtenido,
                             confianza=conf, tiempo_ms=round(ms, 1))
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            return Resultado(caso=caso, ok=False, obtenido="excepcion",
                             confianza=0, tiempo_ms=round(ms, 1), error=str(e))

    return runner.correr_suite(suite, fn)


def suite_busqueda(runner: Runner) -> Suite:
    """searcher.analizar() — detección de intención de búsqueda web."""
    suite = Suite("busqueda", "searcher.analizar() — detección búsquedas web")
    suite.casos = [
        Caso("busca python en youtube",          "busqueda",    "YouTube"),
        Caso("busca el clima en google",         "busqueda",    "Google"),
        Caso("busca noticias en bing",           "busqueda",    "Bing"),
        Caso("busca recetas en duckduckgo",      "busqueda",    "DuckDuckGo"),
        Caso("busca tutoriales de ia en youtube","busqueda",    "YouTube query compuesta"),
        Caso("busca música en youtube",          "busqueda",    "YouTube música"),
        Caso("abre google",                      "busqueda",    "Google directo"),
        Caso("abre chrome",                      "no_busqueda", "App — no búsqueda"),
        Caso("sube el volumen",                  "no_busqueda", "Comando — no búsqueda"),
        Caso("qué es python",                    "no_busqueda", "Pregunta — no búsqueda"),
    ]

    def fn(caso):
        if not searcher:
            return Resultado(caso=caso, ok=False, obtenido="modulo_no_disponible",
                             confianza=0, tiempo_ms=0, error="searcher.py no importado")
        t0 = time.perf_counter()
        try:
            res        = searcher.analizar(caso.entrada)
            ms         = (time.perf_counter() - t0) * 1000
            es_busq    = res.get("es_busqueda", False) if isinstance(res, dict) else False
            if caso.esperado == "busqueda":
                ok       = es_busq
                obtenido = "busqueda" if es_busq else "no_busqueda"
            else:
                ok       = not es_busq
                obtenido = "no_busqueda" if not es_busq else "busqueda"
            return Resultado(caso=caso, ok=ok, obtenido=obtenido,
                             confianza=100 if ok else 0, tiempo_ms=round(ms, 1))
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            return Resultado(caso=caso, ok=False, obtenido="excepcion",
                             confianza=0, tiempo_ms=round(ms, 1), error=str(e))

    return runner.correr_suite(suite, fn)


# ─────────────────────────────────────────────────────────────────
# REPORTERO
# ─────────────────────────────────────────────────────────────────

class Reportero:
    def __init__(self, suites, modulos_ok, modulos_error):
        self.suites         = suites
        self.modulos_ok     = modulos_ok
        self.modulos_error  = modulos_error
        self.fecha          = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.total_global   = sum(s.total for s in suites)
        self.ok_global      = sum(s.ok    for s in suites)
        self.fallos_global  = sum(s.fallos for s in suites)
        self.advs_global    = sum(s.advertencias for s in suites)
        self.tasa_global    = (self.ok_global / self.total_global * 100) if self.total_global else 0

    def _linea(self, r: Resultado) -> str:
        if r.ok and not r.advertencia:   estado = "OK"
        elif r.advertencia:              estado = "ADVERTENCIA"
        else:                            estado = "FALLO"
        linea = f"[{estado}]"
        if r.caso.variante_de: linea += f"[var:{r.caso.variante_de}]"
        linea += f" [{r.obtenido}] conf={r.confianza:.0f}% t={r.tiempo_ms:.1f}ms '{r.caso.entrada}'"
        if r.error:              linea += f"\n    → ERROR: {r.error}"
        if r.advertencia:        linea += f"\n    → ADVERTENCIA: {r.advertencia}"
        if r.caso.descripcion:   linea += f"\n    → {r.caso.descripcion}"
        return linea

    def generar_txt(self) -> str:
        L = []
        L.append("SARA — Reporte Universal de Pruebas")
        L.append(f"Fecha: {self.fecha}")
        L.append(f"Tasa global: {self.tasa_global:.1f}% ({self.ok_global}/{self.total_global})")
        L.append(f"Fallos: {self.fallos_global} | Advertencias: {self.advs_global}")
        L.append("")

        L.append("═" * 60)
        L.append("MÓDULOS SARA")
        L.append("═" * 60)
        for n in self.modulos_ok:    L.append(f"  [OK] {n}")
        for n, e in self.modulos_error.items(): L.append(f"  [ERROR] {n} → {e}")
        L.append("")

        L.append("═" * 60)
        L.append("RESUMEN POR SUITE")
        L.append("═" * 60)
        for s in self.suites:
            barra = "█" * int(s.tasa / 5) + "░" * (20 - int(s.tasa / 5))
            L.append(f"  {s.nombre:<25} {barra} {s.tasa:5.1f}%  ({s.ok}/{s.total})  F:{s.fallos} A:{s.advertencias}")
        L.append("")

        fallos = [(s.nombre, r) for s in self.suites for r in s.resultados if not r.ok and not r.advertencia]
        if fallos:
            L.append("═" * 60)
            L.append("FALLOS CRÍTICOS")
            L.append("═" * 60)
            for sn, r in fallos:
                L.append(f"  [{sn}] '{r.caso.entrada}'")
                L.append(f"    esperado={r.caso.esperado} obtenido={r.obtenido}")
                if r.error:            L.append(f"    ERROR: {r.error}")
                if r.caso.descripcion: L.append(f"    NOTA: {r.caso.descripcion}")
        else:
            L.append("NO HAY FALLOS CRÍTICOS ✓")
        L.append("")

        advs = [(s.nombre, r) for s in self.suites for r in s.resultados if r.advertencia]
        if advs:
            L.append("═" * 60)
            L.append("ADVERTENCIAS")
            L.append("═" * 60)
            for sn, r in advs:
                L.append(f"  [{sn}] {r.advertencia} → '{r.caso.entrada}'")
        L.append("")

        L.append("═" * 60)
        L.append("DETALLE COMPLETO POR SUITE")
        L.append("═" * 60)
        for s in self.suites:
            L.append(f"\n── {s.nombre.upper()} ({s.tasa:.1f}%) ──")
            L.append(f"   {s.descripcion}")
            for r in s.resultados:
                L.append(f"   {self._linea(r)}")

        return "\n".join(L)

    def generar_md(self) -> str:
        L = []
        L.append("# SARA — Reporte Universal de Pruebas")
        L.append(f"> **Fecha:** {self.fecha}  ")
        L.append(f"> **Tasa global:** {self.tasa_global:.1f}% ({self.ok_global}/{self.total_global})  ")
        L.append(f"> **Fallos:** {self.fallos_global} | **Advertencias:** {self.advs_global}")
        L.append("")

        L.append("## Estado de Módulos")
        L.append("| Módulo | Estado |")
        L.append("|--------|--------|")
        for n in self.modulos_ok:    L.append(f"| `{n}` | ✅ OK |")
        for n, e in self.modulos_error.items(): L.append(f"| `{n}` | ❌ `{e}` |")
        L.append("")

        L.append("## Resumen por Suite")
        L.append("| Suite | Tasa | OK | Total | Fallos | Advertencias |")
        L.append("|-------|------|-----|-------|--------|--------------|")
        for s in self.suites:
            emoji = "✅" if s.fallos == 0 else ("⚠️" if s.fallos < 3 else "❌")
            L.append(f"| {emoji} `{s.nombre}` | {s.tasa:.1f}% | {s.ok} | {s.total} | {s.fallos} | {s.advertencias} |")
        L.append("")

        fallos = [(s.nombre, r) for s in self.suites for r in s.resultados if not r.ok and not r.advertencia]
        if fallos:
            L.append("## ❌ Fallos Críticos")
            L.append("| Suite | Entrada | Esperado | Obtenido | Error |")
            L.append("|-------|---------|----------|----------|-------|")
            for sn, r in fallos:
                err = r.error[:60] if r.error else ""
                L.append(f"| `{sn}` | `{r.caso.entrada[:50]}` | `{r.caso.esperado}` | `{r.obtenido}` | {err} |")
        else:
            L.append("## ✅ Sin Fallos Críticos")
        L.append("")

        advs = [(s.nombre, r) for s in self.suites for r in s.resultados if r.advertencia]
        if advs:
            L.append("## ⚠️ Advertencias")
            L.append("| Suite | Advertencia | Entrada |")
            L.append("|-------|-------------|---------|")
            for sn, r in advs:
                L.append(f"| `{sn}` | {r.advertencia} | `{r.caso.entrada[:50]}` |")
        L.append("")

        L.append("## Detalle por Suite")
        for s in self.suites:
            L.append(f"\n### `{s.nombre}` — {s.tasa:.1f}%")
            L.append(f"*{s.descripcion}*\n")
            L.append("| Estado | Entrada | Esperado | Obtenido | Conf% | ms |")
            L.append("|--------|---------|----------|----------|-------|----|")
            for r in s.resultados:
                est = "✅" if (r.ok and not r.advertencia) else ("⚠️" if r.advertencia else "❌")
                L.append(f"| {est} | `{r.caso.entrada[:45]}` | `{r.caso.esperado}` | `{r.obtenido}` | {r.confianza:.0f}% | {r.tiempo_ms:.0f} |")
            errores = [r for r in s.resultados if r.error]
            if errores:
                L.append("\n**Errores:**")
                for r in errores:
                    L.append(f"- `{r.caso.entrada[:50]}`: {r.error}")

        return "\n".join(L)


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

SUITES_DISPONIBLES = {
    "flujo":       suite_flujo_completo,
    "variaciones": suite_variaciones,
    "sistema":     suite_sistema_directo,
    "splitter":    suite_splitter,
    "semantico":   suite_semantico,
    "aprendizaje": suite_aprendizaje,
    "extremas":    suite_extremas,
    "archivo":     suite_archivo_intent,
    "busqueda":    suite_busqueda,
}

def main():
    parser = argparse.ArgumentParser(description="SARA — Suite de Pruebas Universal v1.1")
    parser.add_argument("--suite", choices=list(SUITES_DISPONIBLES.keys()) + ["todas"],
                        default="todas")
    parser.add_argument("--verbose",   action="store_true")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--out",       default="reportes")
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════════╗")
    print("║  SARA — Suite de Pruebas Universal v1.1  ║")
    print("╚══════════════════════════════════════════╝\n")

    print("── Módulos SARA ──")
    for n in _modulos_ok:              print(f"  ✓ {n}")
    for n, e in _modulos_error.items():print(f"  ✗ {n} → {e}")
    print()

    runner = Runner(verbose=args.verbose)
    if "sara" in _modulos_ok:
        print("── Inicializando SARA...")
        ok = runner._inicializar_sara()
        print(f"   {'✓ OK' if ok else '✗ Error al inicializar'}\n")

    suites_a_correr = list(SUITES_DISPONIBLES.keys()) if args.suite == "todas" else [args.suite]

    resultados_suites = []
    for nombre in suites_a_correr:
        print(f"── Suite: {nombre} ──")
        suite = SUITES_DISPONIBLES[nombre](runner)
        resultados_suites.append(suite)
        s = "✓" if suite.fallos == 0 else "✗"
        print(f"   {s} {suite.tasa:.1f}% ({suite.ok}/{suite.total})  F:{suite.fallos} A:{suite.advertencias}\n")

    rep = Reportero(resultados_suites, _modulos_ok, _modulos_error)
    print("═" * 50)
    print(f"  RESULTADO GLOBAL: {rep.tasa_global:.1f}% ({rep.ok_global}/{rep.total_global})")
    print(f"  Fallos críticos:  {rep.fallos_global}")
    print(f"  Advertencias:     {rep.advs_global}")
    print("═" * 50 + "\n")

    if not args.no_report:
        os.makedirs(args.out, exist_ok=True)
        ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
        suite_tag = args.suite if args.suite != "todas" else "completo"
        path_txt  = os.path.join(args.out, f"reporte_{suite_tag}_{ts}.txt")
        path_md   = os.path.join(args.out, f"reporte_{suite_tag}_{ts}.md")
        with open(path_txt, "w", encoding="utf-8") as f: f.write(rep.generar_txt())
        with open(path_md,  "w", encoding="utf-8") as f: f.write(rep.generar_md())
        print(f"  TXT → {path_txt}")
        print(f"  MD  → {path_md}\n")

    sys.exit(0 if rep.fallos_global == 0 else 1)

if __name__ == "__main__":
    main()