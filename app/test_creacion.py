"""test_creacion.py — crear carpetas/archivos con destino y nombre (file_manager.analizar_creacion + generador_codigo).
Sin red, sin Groq real y sin tocar tus carpetas: todo en un directorio temporal.   Uso:  python test_creacion.py"""
import os, sys, tempfile, types

import file_actions as fa
import file_manager as fm
import generador_codigo as gen

tmp = tempfile.mkdtemp()
for d in ("Desktop", "Downloads", "Documents", "Downloads/pruebas", "Documents/mis proyectos"):
    os.makedirs(os.path.join(tmp, d), exist_ok=True)
fa._ruta_carpeta_conocida = lambda k: {"desktop": f"{tmp}/Desktop", "downloads": f"{tmp}/Downloads",
                                       "documents": f"{tmp}/Documents"}.get(k)
fm.file_index = types.SimpleNamespace(buscar_archivo=lambda *a, **k: [], registrar_ruta=lambda r: None)
fm.CARPETA_CREACION_DEFECTO = os.path.join(tmp, "carpetas_creadas")
llamadas = []
gen.respaldo_externo = types.SimpleNamespace(
    generar_codigo=lambda d, l: llamadas.append((d, l)) or {"codigo": "print(1)\n", "extension": ".py", "lenguaje": "python", "fuente": "simulado"},
    groq_disponible=lambda: True, deepseek_disponible=lambda: False)
gen._registrar_generacion = lambda *a, **k: None
gen._buscar_reutilizable = lambda *a, **k: None
gen._carpeta_codigo_generado = lambda: os.makedirs(f"{tmp}/codigo_generado", exist_ok=True) or f"{tmp}/codigo_generado"


def crear(frase):
    return fm.resolver_gestion_archivo({"categoria": "CREAR_CARPETA", "texto_original": frase})


def esperar(frase, estado, existe=None, no_existe=None):
    r = crear(frase)
    assert r["estado"] == estado, f"{frase!r} → {r}"
    for rel in ([existe] if isinstance(existe, str) else existe or []):
        assert os.path.exists(os.path.join(tmp, rel)), f"{frase!r}: falta {rel}"
    for rel in ([no_existe] if isinstance(no_existe, str) else no_existe or []):
        assert not os.path.exists(os.path.join(tmp, rel)), f"{frase!r}: NO debía existir {rel}"


# — el bug original —
esperar("crea una carpeta en descargas llamada prueba", "ejecutado", "Downloads/prueba", "Desktop/en descargas llamada prueba")
esperar("crea una carpeta llamada prueba2 en descargas", "ejecutado", "Downloads/prueba2")
esperar("crea en documentos una carpeta fotos", "ejecutado", "Documents/fotos")
esperar('crea una carpeta llamada "fotos en familia" en el escritorio', "ejecutado", "Desktop/fotos en familia")
esperar("crea una carpeta en la carpeta inexistente llamada api", "no_encontrado")
esperar("crea una carpeta datos en descargas en la carpeta pruebas", "ejecutado", "Downloads/pruebas/datos")
esperar("crea una carpeta nueva dentro de descargas llamada x1 por favor", "ejecutado", "Downloads/x1")
# — sin destino → carpetas_creadas —
esperar("crea la carpeta tareas", "ejecutado", "carpetas_creadas/tareas", "Desktop/tareas")
esperar("crea una carpeta en carpetas creadas llamada interna", "ejecutado", "carpetas_creadas/interna")
# — archivos —
esperar("crea un archivo notas.txt en el escritorio", "ejecutado", "Desktop/notas.txt")
esperar("crea un archivo llamado lista en descargas", "ejecutado", "Downloads/lista.txt", "Downloads/lista")
esperar("crea un archivo prueba punto py en documentos", "ejecutado", "Documents/prueba.py")
esperar("crea un archivo notas.txt en el escritorio", "ejecutado")                              # ya existe → no sobrescribe
assert crear("crea un archivo notas.txt en el escritorio")["exito"] is False
# — archivo con código (Groq) —
llamadas.clear()
esperar("crea un archivo main.py en documentos que sume dos numeros", "ejecutado", "Documents/main.py")
assert llamadas and "sume dos numeros" in llamadas[0][0], llamadas
esperar("crea un archivo en python que sume dos numeros", "ejecutado")
assert os.listdir(f"{tmp}/carpetas_creadas")
# — rechazos —
esperar("crea un archivo virus.exe en descargas", "rechazado", no_existe="Downloads/virus.exe")
esperar("crea una carpeta en la nada llamada y", "no_encontrado")
esperar("crea una carpeta llamada con", "rechazado")
esperar("crea una carpeta", "no_entendido")
esperar("crea un archivo", "no_entendido")
# — la ruta directa de código sigue igual si no hay destino —
r = gen.resolver_generar_codigo({"categoria": "GENERAR_CODIGO", "texto_original": "crea un script para sumar 2 numeros",
                                 "texto_norm": "crea un script para sumar 2 numeros", "patron": "crea un script"})
assert r["estado"] == "generado" and "codigo_generado" in r["ruta"], r
r = gen.resolver_generar_codigo({"categoria": "GENERAR_CODIGO", "texto_original": "crea un script en descargas para sumar dos numeros",
                                 "texto_norm": "crea un script en descargas para sumar dos numeros", "patron": "crea un script"})
assert r["estado"] == "generado" and r["ruta"].startswith(f"{tmp}/Downloads"), r
print("✅ test_creacion: todo en orden")
