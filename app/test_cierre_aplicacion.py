import unittest

import app_actions
import app_index


class CierreAplicacionTests(unittest.TestCase):
    def test_resolver_cierre_rechaza_protegido(self):
        resultado = app_actions.resolver_cierre({"categoria": "CERRAR_APLICACION", "texto_norm": "cierra explorer"})
        self.assertEqual(resultado["estado"], "protegido")
        self.assertIn("explorer", resultado["texto"].lower())

    def test_listar_procesos_en_ejecucion_devuelve_datos(self):
        procesos = app_index.listar_procesos_en_ejecucion()
        self.assertIsInstance(procesos, dict)
        self.assertTrue(all(isinstance(v, list) for v in procesos.values()))

    def test_buscar_proceso_en_ejecucion_devuelve_lista(self):
        candidatos = app_index.buscar_proceso_en_ejecucion("notepad", limite=3)
        self.assertIsInstance(candidatos, list)


if __name__ == "__main__":
    unittest.main()
