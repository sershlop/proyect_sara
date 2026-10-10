import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import app_actions
import database
import file_actions
import file_manager
import memoria_core


class RestriccionesTests(unittest.TestCase):
    def test_restriccion_de_app_bloquea_apertura_directa(self):
        with tempfile.TemporaryDirectory() as temp:
            ejecutable = Path(temp) / "Editor.exe"
            ejecutable.touch()
            candidato = {"nombre": "Editor", "exe": str(ejecutable)}
            with patch.object(memoria_core, "verificar_restriccion", return_value="No abrir durante trabajo"), \
                 patch.object(app_actions, "_lanzar") as lanzar:
                resultado = app_actions.abrir_aplicacion(candidato)
        self.assertFalse(resultado)
        lanzar.assert_not_called()

    def test_restriccion_de_archivo_bloquea_apertura_directa(self):
        with tempfile.TemporaryDirectory() as temp:
            archivo = Path(temp) / "privado.txt"
            archivo.touch()
            with patch.object(memoria_core, "verificar_restriccion", return_value="No abrir"), \
                 patch.object(file_actions, "_abrir_en_sistema") as abrir:
                resultado = file_actions.abrir_ruta(str(archivo))
        self.assertFalse(resultado)
        abrir.assert_not_called()

    def test_restriccion_de_archivo_bloquea_eliminacion(self):
        with tempfile.TemporaryDirectory() as temp:
            archivo = Path(temp) / "privado.txt"
            archivo.touch()
            with patch.object(memoria_core, "verificar_restriccion", return_value="No modificar"), \
                 patch.object(file_actions.os, "remove") as eliminar:
                resultado = file_actions.eliminar_archivo(str(archivo))
        self.assertFalse(resultado["exito"])
        eliminar.assert_not_called()

    def test_restriccion_de_directorio_cubre_archivos_descendientes(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = str(Path(temp) / "restricciones.db")
            carpeta = Path(temp) / "privado"
            archivo = carpeta / "informe.txt"
            carpeta.mkdir()
            archivo.touch()
            with patch.object(database, "DB_NAME", db_path):
                database.crear_tablas()
                self.assertTrue(memoria_core.agregar_restriccion("archivo", str(carpeta), "No abrir este directorio"))
                regla = memoria_core.verificar_restriccion("archivo", str(archivo))
        self.assertEqual(regla, "No abrir este directorio")

    def test_error_consultando_restricciones_bloquea_por_seguridad(self):
        with patch.object(memoria_core, "_conectar", side_effect=OSError("db indisponible")):
            regla = memoria_core.verificar_restriccion("archivo", "C:\\privado\\a.txt")
        self.assertIsNotNone(regla)
        self.assertIn("bloqueada", regla)

    def test_restriccion_bloquea_creacion_en_destino(self):
        with tempfile.TemporaryDirectory() as temp:
            destino = Path(temp) / "protegido"
            destino.mkdir()
            with patch.object(file_manager, "_destino_a_ruta", return_value=str(destino)), \
                 patch.object(file_manager, "_restriccion_archivo", return_value="solo lectura"):
                resultado = file_manager.resolver_gestion_archivo({
                    "categoria": "CREAR_CARPETA",
                    "texto_original": "crea una carpeta tareas dentro de protegido",
                })
        self.assertEqual(resultado["estado"], "rechazado")
        self.assertFalse((destino / "tareas").exists())

    def test_restriccion_bloquea_compresion(self):
        with tempfile.TemporaryDirectory() as temp:
            origen = Path(temp) / "protegido"
            origen.mkdir()
            (origen / "dato.txt").write_text("dato", encoding="utf-8")
            fila = {"ruta": str(origen), "nombre": origen.name, "es_carpeta": True}
            with patch.object(file_manager, "_buscar_origen", return_value=("ok", fila)), \
                 patch.object(file_manager, "_restriccion_archivo", return_value="no comprimir"), \
                 patch.object(file_manager.shutil, "make_archive") as comprimir:
                resultado = file_manager.resolver_gestion_archivo({
                    "categoria": "COMPRIMIR_ARCHIVO", "texto_original": "comprime protegido",
                })
        self.assertEqual(resultado["estado"], "rechazado")
        comprimir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
