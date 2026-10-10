import unittest
from unittest.mock import patch

import file_manager
import shell


class RiesgoTests(unittest.TestCase):
    def test_confirmar_eliminar_no_puede_saltar_ruta_critica(self):
        with patch.object(file_manager, "_ruta_de_escritura_segura", return_value=False):
            resultado = file_manager.confirmar_eliminar("C:\\Windows", True)
        self.assertFalse(resultado["exito"])
        self.assertIn("ruta crítica", resultado["texto"])

    def test_confirmar_mover_revalida_origen_y_destino(self):
        with patch.object(file_manager, "_ruta_de_escritura_segura", side_effect=[True, False]), \
             patch.object(file_manager.shutil, "move") as mover:
            resultado = file_manager.confirmar_mover("C:\\temp\\origen", "C:\\Windows\\destino")
        self.assertFalse(resultado["exito"])
        mover.assert_not_called()

    def test_shell_revalida_zona_negra_antes_de_ejecutar(self):
        with patch.object(shell, "clasificar_riesgo", return_value="negra"), \
             patch.object(shell, "registrar_comando_historial") as registrar, \
             patch.object(shell, "ejecutar_comando") as ejecutar:
            resultado = shell.confirmar_y_ejecutar(
                "Remove-Item C:\\Windows -Recurse -Force",
                "amarilla",
                {"fuente": "qwen"},
                "borra Windows",
            )
        self.assertFalse(resultado["exitoso"])
        ejecutar.assert_not_called()
        registrar.assert_called_once()
        self.assertIn("rechazado", resultado["resumen"])

    def test_shell_blanca_no_se_convierte_en_autorizacion_llm(self):
        self.assertEqual(shell.clasificar_riesgo("Get-Process"), "blanca")
        self.assertEqual(shell.clasificar_riesgo("Get-Process; Remove-Item C:\\temp"), "amarilla")
        self.assertEqual(shell.clasificar_riesgo("Remove-Item C:\\Windows -Recurse -Force"), "negra")


if __name__ == "__main__":
    unittest.main()
