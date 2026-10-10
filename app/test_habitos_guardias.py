import unittest
from unittest.mock import patch

import habitos


class HabitosGuardiasTests(unittest.TestCase):
    def setUp(self):
        self.paquete = {
            "id": 7,
            "miembros": [{
                "dominio": "aplicacion",
                "identificador": "C:\\Apps\\Editor.exe",
                "nombre_mostrar": "Editor",
            }],
        }

    def test_guardia_roja_bloquea_sin_abrir(self):
        with patch.object(habitos, "obtener_paquete", return_value=self.paquete), \
             patch.object(habitos, "evaluar_guardia_paquete", return_value={"tipo": "bloqueado", "texto": "RAM crítica"}), \
             patch.object(habitos, "_abrir_miembro") as abrir:
            resultado = habitos.ejecutar_paquete(7)
        self.assertFalse(resultado["exitoso"])
        self.assertEqual(resultado["estado"], "bloqueado")
        abrir.assert_not_called()

    def test_guardia_amarilla_no_abre_sin_confirmacion(self):
        advertencia = {"tipo": "advertencia", "texto": "RAM baja; ¿continuar?"}
        with patch.object(habitos, "obtener_paquete", return_value=self.paquete), \
             patch.object(habitos, "evaluar_guardia_paquete", return_value=advertencia), \
             patch.object(habitos, "_abrir_miembro") as abrir:
            resultado = habitos.ejecutar_paquete(7)
        self.assertFalse(resultado["exitoso"])
        self.assertEqual(resultado["estado"], "requiere_confirmacion")
        abrir.assert_not_called()

    def test_guardia_amarilla_abre_solo_tras_confirmacion(self):
        advertencia = {"tipo": "advertencia", "texto": "RAM baja; ¿continuar?"}
        with patch.object(habitos, "obtener_paquete", return_value=self.paquete), \
             patch.object(habitos, "evaluar_guardia_paquete", return_value=advertencia), \
             patch.object(habitos, "_abrir_miembro", return_value=None) as abrir:
            resultado = habitos.ejecutar_paquete(7, confirmar_advertencia=True)
        self.assertTrue(resultado["exitoso"])
        abrir.assert_called_once_with(self.paquete["miembros"][0])


if __name__ == "__main__":
    unittest.main()
