import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import file_manager
import main


class FileManagerTests(unittest.TestCase):
    def test_operaciones_naturales_con_rutas_temporales(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            creations = root / "carpetas_creadas"
            downloads = root / "Downloads"
            documents = root / "Documents"
            creations.mkdir()
            downloads.mkdir()
            documents.mkdir()
            aliases = {
                "descargas": str(downloads),
                "documentos": str(documents),
                "carpetas_creadas": str(creations),
            }

            with patch.object(file_manager, "_ruta_carpeta_creaciones", lambda: str(creations)), \
                 patch.object(file_manager, "_resolver_ubicacion", lambda nombre: aliases.get(nombre)):
                crear = file_manager.resolver_crear_carpeta({
                    "categoria": "CREAR_CARPETA",
                    "texto_norm": "crea una carpeta llamada despensa dentro de descargas",
                    "patron": "crea una carpeta",
                })
                self.assertTrue(crear["exito"])
                self.assertTrue((downloads / "despensa").is_dir())

                tarea = creations / "tarea"
                tarea.mkdir()
                eliminar = file_manager.resolver_eliminar({
                    "categoria": "ELIMINAR_ARCHIVO",
                    "texto_norm": "elimina la carpeta tarea",
                    "patron": "elimina la carpeta",
                })
                self.assertEqual(eliminar["estado"], "candidato")
                self.assertIn("0 KB", eliminar["mensaje_confirmacion"])

                (creations / "tarea.docx").write_text("contenido", encoding="utf-8")
                mover = file_manager.resolver_mover({
                    "categoria": "MOVER_ARCHIVO",
                    "texto_norm": "mueve tarea.docx a documentos",
                    "patron": "mueve",
                })
                self.assertEqual(mover["estado"], "ejecutado")
                self.assertTrue((documents / "tarea.docx").exists())

                (creations / "viejo").mkdir()
                renombrar = file_manager.resolver_renombrar({
                    "categoria": "RENOMBRAR_ARCHIVO",
                    "texto_norm": "renombra la carpeta viejo como nuevo",
                    "patron": "renombra la carpeta",
                })
                self.assertEqual(renombrar["estado"], "ejecutado")
                self.assertTrue((creations / "nuevo").is_dir())

                comprimir = file_manager.resolver_comprimir({
                    "categoria": "COMPRIMIR_ARCHIVO",
                    "texto_norm": "comprime la carpeta tarea",
                    "patron": "comprime la carpeta",
                })
                self.assertEqual(comprimir["estado"], "ejecutado")
                self.assertTrue((creations / "tarea.zip").exists())

    def test_renombrar_con_conflicto_pide_confirmacion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            origen = root / "viejo"
            destino = root / "nuevo"
            origen.mkdir()
            destino.mkdir()

            resultado = file_manager.confirmar_renombrar(str(origen), str(destino))

            self.assertTrue(resultado["exito"])
            self.assertTrue(destino.is_dir())
            self.assertFalse(origen.exists())

    def test_mover_y_copiar_con_carpeta_origen_explicita(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            descargas = root / "Downloads"
            escritorio = root / "Desktop"
            documentos = root / "Documents"
            descargas.mkdir()
            escritorio.mkdir()
            documentos.mkdir()
            alias = {
                "descargas": str(descargas),
                "escritorio": str(escritorio),
                "documentos": str(documentos),
            }

            with patch.object(file_manager, "_resolver_ubicacion", lambda nombre: alias.get(nombre)):
                carpeta_mover = descargas / "tareas"
                carpeta_mover.mkdir()
                (carpeta_mover / "nota.txt").write_text("x", encoding="utf-8")
                mover = file_manager.resolver_mover({
                    "categoria": "MOVER_ARCHIVO",
                    "texto_norm": "mueve la carpeta tareas que esta dentro de la carpeta descargas a escritorio",
                    "patron": "mueve",
                })
                self.assertEqual(mover["estado"], "ejecutado")
                self.assertTrue((escritorio / "tareas" / "nota.txt").exists())

                carpeta_copiar = descargas / "proyecto"
                carpeta_copiar.mkdir()
                (carpeta_copiar / "a.txt").write_text("a", encoding="utf-8")
                copiar = file_manager.resolver_copiar({
                    "categoria": "COPIAR_ARCHIVO",
                    "texto_norm": "copia la carpeta proyecto que esta dentro de la carpeta descargas y pegala dentro de la carpeta documentos",
                    "patron": "copia",
                })
                self.assertEqual(copiar["estado"], "ejecutado")
                self.assertTrue((documentos / "proyecto" / "a.txt").exists())
                self.assertTrue((descargas / "proyecto" / "a.txt").exists())

    def test_mover_pide_extraccion_llm_si_falla_la_local(self):
        resultado = {
            "categoria": "MOVER_ARCHIVO",
            "texto_norm": "meuve la carpeta tareas que esta dentro de la carpeta descargas a escritorio",
            "patron": None,
        }
        origen = {"ruta": "C:\\temp\\descargas\\tareas", "es_carpeta": True, "score": 1.0}
        respuestas = [("meuve tareas que esta", "descargas"), ("tareas", "descargas")]

        with patch.object(file_manager.file_index, "resolver_consulta_y_carpeta", side_effect=respuestas), \
             patch.object(file_manager, "_resolver_origen", side_effect=[None, origen]):
            resuelto, destino = file_manager._resolver_transferencia(resultado)

        self.assertEqual(resuelto, origen)
        self.assertEqual(destino, "escritorio")

    def test_detalle_clasificacion_muestra_las_tres_fuentes(self):
        detalle = main._detalle_clasificacion({
            "categoria": "MOVER_ARCHIVO", "metodo": "cache", "confianza": 0.95,
        })
        self.assertEqual(detalle, "[categoría=MOVER_ARCHIVO, método=cache, confianza=0.95]")

    def test_documenta_ubicacion_no_escribible(self):
        with tempfile.TemporaryDirectory() as temp:
            ubicacion = Path(temp) / "Documents"
            ubicacion.mkdir()
            resultado = {
                "categoria": "CREAR_CARPETA",
                "texto_norm": "crea una carpeta llamada tareas dentro de documentos",
                "patron": "crea una carpeta",
            }
            with patch.object(file_manager, "_resolver_ubicacion", return_value=str(ubicacion)), \
                 patch.object(file_manager.os, "makedirs", side_effect=FileNotFoundError(2, "ruta no disponible")):
                respuesta = file_manager.resolver_crear_carpeta(resultado)

        self.assertEqual(respuesta["estado"], "no_escribible")
        self.assertFalse(respuesta["exito"])
        self.assertIn("no permite escribir", respuesta["texto"])
        self.assertIn("no cambiará silenciosamente", respuesta["texto"])


if __name__ == "__main__":
    unittest.main()