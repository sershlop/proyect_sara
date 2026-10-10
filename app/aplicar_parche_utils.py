"""Parche idempotente para TU utils.py (no estaba en el disco al preparar el zip).
Uso:  python aplicar_parche_utils.py
Arregla: file_index llama limpiar_ruido(texto, palabras_ruido) y utils.limpiar_ruido solo aceptaba 1
argumento (TypeError -> 'busca archivo' fallaba). Además usa palabras completas ('sara2' ya no pierde 'sara')
y extraer_resto acepta un str además de una lista (antes iteraba letra por letra)."""
import re, pathlib
p = pathlib.Path(__file__).with_name("utils.py")
s = p.read_text(encoding="utf-8")
if "palabras_ruido" in s:
    print("utils.py ya está parcheado."); raise SystemExit
i = s.index("def extraer_resto(")
j = s.index('if __name__ == "__main__":')
nuevo = '''def extraer_resto(texto: str, palabras_a_quitar) -> str:
    """Extrae el resto de la frase quitando los verbos o prefijos iniciales."""
    if not texto: return ""
    if isinstance(palabras_a_quitar, str):
        palabras_a_quitar = [palabras_a_quitar] if palabras_a_quitar else []
    t = texto.strip()
    for p in sorted(palabras_a_quitar, key=lambda x: len(str(x)), reverse=True):
        p = str(p)
        if t.lower().startswith(p.lower() + " "):
            return t[len(p):].strip()
        elif t.lower() == p.lower():
            return ""
    return t


def limpiar_ruido(texto: str, palabras_ruido=None) -> str:
    """Elimina palabras de relleno (solo palabras completas: 'sara2' no pierde 'sara')."""
    if not texto: return ""
    ruido = ["por favor", "sara", "oye", "hola", "podrias", "puedes", "quiero que", "necesito que"]
    ruido += [str(r) for r in (palabras_ruido or ())]
    t = texto.lower()
    for r in sorted(set(ruido), key=len, reverse=True):
        t = re.sub(r"(?<!\\w)" + re.escape(r.lower()) + r"(?!\\w)", " ", t)
    return re.sub(r"\\s+", " ", t).strip()

'''
p.write_text(s[:i] + nuevo + s[j:], encoding="utf-8")
print("utils.py parcheado.")
