import unittest
from unittest.mock import patch

import core_bridge


class CoreSafetyTests(unittest.TestCase):
    def test_cierre_fallido_no_se_aprende_como_exito(self):
        candidato = {"estado": "candidato", "pids": [4242], "mensaje_confirmacion": "¿Cerrar app?"}

        def llamar(_modulo, funcion, *args, **kwargs):
            return candidato if funcion == "resolver_cierre" else None

        with patch.object(core_bridge, "_llamar", side_effect=llamar), \
             patch.object(core_bridge, "_pedir_confirmacion", return_value=True), \
             patch.object(core_bridge.app_actions, "cerrar_procesos", return_value={
                 "exito": False, "cerrado": False, "texto": "No pude cerrar la aplicación."
             }), \
             patch.object(core_bridge, "_aprender") as aprender:
            exito, texto = core_bridge._despachar({"categoria": "CERRAR_APLICACION"}, lambda *a, **k: None)

        self.assertFalse(exito)
        self.assertIn("No pude cerrar", texto)
        aprender.assert_called_once_with({"categoria": "CERRAR_APLICACION"}, False)

    def test_paquete_amarillo_pide_confirmacion_antes_de_ejecutar(self):
        res = {"categoria": "EJECUTAR_PAQUETE", "texto_norm": "rutina"}
        requiere = {"exitoso": False, "estado": "requiere_confirmacion", "texto": "RAM baja"}
        ejecutado = {"exitoso": True, "texto": "Paquete abierto"}

        def llamar(_modulo, funcion, *args, **kwargs):
            return None

        with patch.object(core_bridge, "_llamar", side_effect=llamar), \
             patch.object(core_bridge.habitos, "buscar_paquete_fijo_por_nombre", return_value=8), \
             patch.object(core_bridge.habitos, "ejecutar_paquete", side_effect=[requiere, ejecutado]) as ejecutar, \
             patch.object(core_bridge, "_pedir_confirmacion", return_value=True) as confirmar, \
             patch.object(core_bridge, "_aprender"):
            exito, texto = core_bridge._despachar(res, lambda *a, **k: None)

        confirmar.assert_called_once()
        self.assertEqual(ejecutar.call_args_list[1].kwargs, {"confirmar_advertencia": True})
        self.assertTrue(exito)
        self.assertEqual(texto, "Paquete abierto")


if __name__ == "__main__":
    unittest.main()
