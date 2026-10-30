import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pantalla_vivo


def comprobar(nombre, condicion):
    print(("OK  " if condicion else "FALLA"), nombre)
    if not condicion:
        raise AssertionError(nombre)


_c_original = pantalla_vivo._c
pantalla_vivo._c = lambda nombre, defecto: True if nombre == "CEL_PANTALLA_VIVO_ACTIVA" else defecto
pantalla_vivo.detener_todas()

for frase in (
    "activa la pantalla en vivo",
    "muéstrame en vivo la pantalla de la laptop",
    "muestra en tiempo real lo que pasa en la pantalla",
    "activa la transmisión de pantalla de la laptop",
):
    comprobar(f"iniciar: {frase}", pantalla_vivo.interpretar(frase) == "iniciar")

for frase in (
    "detén la pantalla en vivo",
    "desactiva la pantalla en tiempo real",
    "cierra la pantalla en vivo de la laptop",
):
    comprobar(f"detener: {frase}", pantalla_vivo.interpretar(frase) == "detener")

comprobar("no confunde la pantalla del celular con la laptop",
          pantalla_vivo.interpretar("activa la pantalla en vivo de mi celular") is None)
sid, motivo = pantalla_vivo.iniciar("celular-a")
comprobar("inicia una sola sesión", bool(sid) and not motivo)
comprobar("limita una segunda sesión concurrente",
          pantalla_vivo.iniciar("celular-b")[0] is None)
evento = pantalla_vivo.sesion("celular-a", sid)
comprobar("reclama la sesión una vez", evento is not None and pantalla_vivo.sesion("celular-a", sid) is None)
comprobar("detiene y limpia la sesión", pantalla_vivo.detener("celular-a") and evento.is_set())
comprobar("detener sin sesión es inocuo", not pantalla_vivo.detener("celular-a"))

pantalla_vivo._c = _c_original
print("TODO OK")
