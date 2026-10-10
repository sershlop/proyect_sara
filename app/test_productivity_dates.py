import unittest
from datetime import datetime
from unittest.mock import patch

import fecha_parser
import productivity


class ProductivityDateTests(unittest.TestCase):
    def _resultado(self):
        return {
            "categoria": "RECORDATORIO",
            "texto_original": "recuérdame llamar mañana a las 8",
            "texto_norm": "recuerdame llamar manana a las 8",
            "patron": "recuérdame",
        }

    def test_recordatorio_usa_fecha_parser_y_elimina_fragmento_temporal(self):
        fecha = datetime(2026, 10, 1, 8, 0)
        extraido = {"fecha_hora": fecha, "texto_sin_fecha": "llamar", "fuente": "dateparser"}
        with patch.object(fecha_parser, "extraer_fecha_y_texto_limpio", return_value=extraido), \
             patch.object(productivity, "crear_recordatorio", return_value=13) as crear:
            respuesta = productivity.registrar_desde_clasificacion(self._resultado())

        self.assertEqual(respuesta["estado"], "creado")
        self.assertEqual(crear.call_args.args, ("llamar", fecha))
        self.assertIn("01/10 08:00", respuesta["texto"])
        self.assertNotIn("margen predeterminado", respuesta["texto"])

    def test_recordatorio_avisa_cuando_usa_fecha_predeterminada(self):
        fecha = datetime(2026, 10, 1, 9, 0)
        extraido = {"fecha_hora": fecha, "texto_sin_fecha": "llamar", "fuente": "default_sin_fecha"}
        with patch.object(productivity, "_extraer_fecha_recordatorio", return_value=extraido), \
             patch.object(productivity, "crear_recordatorio", return_value=13):
            respuesta = productivity.registrar_desde_clasificacion(self._resultado())
        self.assertIn("no detecté una fecha concreta", respuesta["texto"].lower())

    def test_parser_dedicado_ausente_degrada_al_parser_local(self):
        with patch.object(fecha_parser, "extraer_fecha_y_texto_limpio", side_effect=RuntimeError("temporal")):
            fallback = productivity._extraer_fecha_recordatorio("recuérdame llamar mañana")
        self.assertEqual(fallback["fuente"], "parser_simple")
        self.assertIsInstance(fallback["fecha_hora"], datetime)


if __name__ == "__main__":
    unittest.main()