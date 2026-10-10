import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import file_index
import file_manager


_RUIDO_GESTION = {
    "carpeta", "carpetas", "archivo", "que", "se", "encuentra", "dentro", "de", "la"
}


class NaturalLanguageFileTests(unittest.TestCase):
    def test_parser_separa_nombre_y_carpeta_contenedora(self):
        consulta, carpeta = file_index.resolver_consulta_y_carpeta(
            "tareas que se encuentra dentro de la carpeta descargas",
            palabras_ruido=_RUIDO_GESTION,
        )
        self.assertEqual(consulta, "tareas")
        self.assertEqual(carpeta, "descargas")

    def test_resolver_origen_filtra_por_carpeta_contenedora(self):
        with tempfile.TemporaryDirectory() as temp:
            descargas = Path(temp) / "Downloads"
            tareas = descargas / "tareas"
            tareas.mkdir(parents=True)
            fila = {"ruta": str(tareas), "nombre": "tareas", "es_carpeta": True}
            with patch.object(file_manager, "_destino_a_ruta", return_value=str(descargas)), \
                 patch.object(file_index, "buscar_archivo", return_value=[fila]) as buscar:
                estado, resultado = file_manager._buscar_origen([
                    "tareas", "que", "se", "encuentra", "dentro", "de", "la", "carpeta", "descargas"
                ])

            self.assertEqual(estado, "ok")
            self.assertEqual(resultado["ruta"], str(tareas))
            self.assertEqual(buscar.call_args.kwargs["carpeta_objetivo"], str(descargas))


if __name__ == "__main__":
    unittest.main()
