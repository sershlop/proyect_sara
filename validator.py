"""
validator.py
Filtro temprano de entradas para SARA: rechaza solicitudes sin sentido
antes de que pasen por todo el pipeline de procesamiento.
"""
from utils import normalizar_texto, similitud
import logger

# Intento usar pyspellchecker para detección rápida de palabras desconocidas
try:
    from spellchecker import SpellChecker
except Exception:
    SpellChecker = None

# Intento usar marisa-trie para lookup ultrarrápido
try:
    import marisa_trie
except Exception:
    marisa_trie = None


class ValidadorEntrada:
    def __init__(self):
        self.PALABRAS_RUIDO = {
            "desmute", "mute", "gghj", "fghj", "asdf",
            "xyzabc", "qwerty", "jajaja", "hahaha",
            "lololol", "zzz", "bla", "blah", "meh"
        }
        # Umbral de palabras desconocidas para rechazar
        self.UMBRAL_PALABRAS_DESCONOCIDAS = 0.5

        # Inicializar spellchecker si está disponible
        self.spell = None
        if SpellChecker:
            try:
                # Preferir español; si no está, caer a inglés
                try:
                    self.spell = SpellChecker(language='es')
                except Exception:
                    self.spell = SpellChecker()
            except Exception:
                self.spell = None

        # Construir vocabulario rápido (trie) a partir de módulos existentes
        self.trie = None
        if marisa_trie:
            vocab = set()
            # agregar palabras comunes de módulos si están disponibles
            try:
                import searcher as _searcher
                for nombre, datos in getattr(_searcher, 'PLATAFORMAS', {}).items():
                    kws = datos.get('keywords', [])
                    for k in kws:
                        vocab.add(normalizar_texto(k))
            except Exception:
                pass
            try:
                import brain as _brain
                for p in getattr(_brain, 'PALABRAS_COMANDO', []):
                    vocab.add(normalizar_texto(p))
            except Exception:
                pass
            try:
                import splitter as _splitter
                for p in getattr(_splitter, 'PREFIJOS_SOCIALES', []):
                    vocab.add(normalizar_texto(p))
                for p in getattr(_splitter, 'PALABRAS_ELIMINAR_SEGMENTO', []):
                    vocab.add(normalizar_texto(p))
            except Exception:
                pass
            try:
                import social as _social
                # recoger algunas palabras de social (saludos, negaciones, etc.)
                for attr in ('SALUDOS','DESPEDIDAS','AGRADECIMIENTOS','AFIRMACIONES','NEGACIONES'):
                    vals = getattr(_social, attr, None)
                    if vals:
                        for v in vals:
                            vocab.add(normalizar_texto(v))
            except Exception:
                pass

            # añadir vocabulario básico español
            BASICO = [
                'abre','abrir','buscar','busca','crear','ejecutar','ejecuta','mostrar','muestra',
                'por','favor','en','la','el','los','las','un','una','para','con','de'
            ]
            for b in BASICO:
                vocab.add(b)

            try:
                if vocab:
                    self.trie = marisa_trie.Trie(sorted(vocab))
            except Exception:
                self.trie = None

    def validar_entrada(self, texto):
        if not texto or not isinstance(texto, str) or not texto.strip():
            return False, ""

        texto_norm = normalizar_texto(texto)
        palabras = texto_norm.split()

        # Ruido obvio
        for p in palabras:
            if p in self.PALABRAS_RUIDO:
                logger.debug("validator", f"Ruido obvio detectado: '{texto[:30]}'")
                return False, "No entiendo eso. ¿Puedes intentarlo con otra frase?"
            if len(set(p)) == 1 and len(p) >= 4:
                # repetición excesiva: "zzzz", "aaaa"
                logger.debug("validator", f"Repetición detectada: '{texto[:30]}'")
                return False, "Eso parece ruido. ¿Puedes escribirlo de otra forma?"

        # Sin estructura mínima
        if len(palabras) == 0:
            return False, ""
        if len(palabras) > 50:
            return False, "Tu mensaje es demasiado largo. Intenta resumirlo."
        if all(len(p) <= 1 for p in palabras):
            return False, "No entendí eso. ¿Puedes ser más claro?"

        # Detección rápida con spellchecker si está disponible
        palabras_a_chequear = [p for p in palabras if len(p) > 2 and p.isalpha()]
        if self.spell and palabras_a_chequear:
            try:
                desconocidas = self.spell.unknown(palabras_a_chequear)
                ratio = len(desconocidas) / max(len(palabras_a_chequear), 1)
                if ratio >= self.UMBRAL_PALABRAS_DESCONOCIDAS:
                    logger.debug("validator", f"Alta proporción de palabras desconocidas: {desconocidas}")
                    # sugerencia rápida para la primera desconocida
                    suger = None
                    try:
                        suger = self.spell.correction(next(iter(desconocidas)))
                    except Exception:
                        suger = None
                    msg = "No logro entender varias palabras. ¿Puedes reformular?"
                    if suger:
                        msg += f" Tal vez quisiste decir '{suger}'?"
                    return False, msg
            except Exception:
                # Falla el spellchecker; continuar con otras reglas
                pass

        # Palabras sin vocales (muy probablemente typo o ruido) — fallback
        VOCALES = set('aeiou')
        sin_vocales = 0
        for p in palabras:
            if len(p) > 2 and not any(c in VOCALES for c in p):
                sin_vocales += 1
        if sin_vocales > 0 and sin_vocales / max(len(palabras), 1) > 0.6:
            logger.debug("validator", f"Muchas palabras sin vocales: '{texto[:30]}'")
            return False, "No logro entender esas palabras. ¿Puedes escribirlo diferente?"

        return True, ""

    def obtener_sugerencia(self, entrada):
        texto_norm = normalizar_texto(entrada)
        palabras = texto_norm.split()
        if not palabras:
            return None
        primera = palabras[0]
        candidatos = ["abre", "buscar", "busca", "crear", "ejecuta", "mostrar"]
        mejor = None
        mejor_sim = 0.0
        for c in candidatos:
            s = similitud(primera, c)
            if s > mejor_sim and s >= 0.6:
                mejor_sim = s
                mejor = c
        if mejor:
            return f"¿Quisiste decir '{mejor}'?"
        return None


_validador = ValidadorEntrada()


def validar_entrada(texto):
    return _validador.validar_entrada(texto)


def obtener_sugerencia(entrada):
    return _validador.obtener_sugerencia(entrada)
