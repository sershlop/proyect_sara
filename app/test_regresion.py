# 📁 test_regresion.py — SARA v2.0
#
# Cierra el ciclo descrito en la idea conceptual: cada corrección real
# que hiciste en main.py (/corregir) y cada acierto confirmado
# (/confirmar) ya vive en historial_clasificacion y correcciones. Este
# script no tiene casos escritos a mano — los saca de ahí, vía
# database.generar_fixture_regresion(), y verifica que el motor de
# clasificación de HOY siga dando los mismos resultados.
#
# Se corre con: python test_regresion.py
# Sale con código 0 si todo pasa, 1 si algo falló (para poder engancharlo
# a CI o a un pre-commit hook más adelante).
#
# usar_cache=False en cada llamada es intencional: sin eso, el test solo
# repetiría el resultado cacheado de la última vez que se clasificó esa
# frase, en vez de evaluar el motor real con el código actual.

import sys

import database as db
import intent_engine as engine


def ejecutar_tests() -> tuple:
    fixture = db.generar_fixture_regresion()

    if not fixture:
        return 0, [], "No hay casos en el fixture todavia — usa /confirmar y /corregir en main.py para generarlos."

    total = 0
    fallos = []

    for categoria, casos in fixture.items():
        for texto in casos.get("debe_capturar", []):
            total += 1
            resultado = engine.clasificar(texto, usar_cache=False)
            if resultado["categoria"] != categoria:
                fallos.append(
                    f"DEBIA capturar {texto!r} como {categoria}, obtuvo "
                    f"{resultado['categoria']} (metodo={resultado['metodo']}, confianza={resultado['confianza']})"
                )

        for texto in casos.get("no_debe_capturar", []):
            total += 1
            resultado = engine.clasificar(texto, usar_cache=False)
            if resultado["categoria"] == categoria:
                fallos.append(
                    f"NO DEBIA capturar {texto!r} como {categoria}, pero lo hizo "
                    f"(metodo={resultado['metodo']}, confianza={resultado['confianza']})"
                )

    return total, fallos, None


def main() -> None:
    db.crear_tablas()
    total, fallos, aviso = ejecutar_tests()

    if aviso:
        print(aviso)
        sys.exit(0)

    print(f"Casos de regresion evaluados: {total}")

    if fallos:
        print(f"\nFALLOS ({len(fallos)}):")
        for f in fallos:
            print(f"  - {f}")
        print(f"\n{len(fallos)}/{total} casos fallaron.")
        sys.exit(1)

    print(f"Los {total} casos pasaron. Nada se rompio respecto al historial confirmado.")
    sys.exit(0)


if __name__ == "__main__":
    main()
