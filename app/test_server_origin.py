import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import server


class ServerOriginTests(unittest.IsolatedAsyncioTestCase):
    async def test_mensaje_preserva_origen_hasta_core(self):
        original_core = server.core_bridge
        original_estado = dict(server._estado_core)
        original_loop = server._loop
        procesados = []

        def procesar(texto, emit, origen="texto"):
            procesados.append((texto, origen))
            return {"exito": True, "texto": "ok"}

        try:
            server.core_bridge = SimpleNamespace(procesar_mensaje=procesar)
            server._estado_core.update(estado="listo", error=None)
            server._loop = asyncio.get_running_loop()
            server.manager.broadcast_json = AsyncMock()

            await server._manejar_mensaje("abre chrome", "voz")
            await server._manejar_mensaje("abre chrome")

            self.assertEqual(procesados, [("abre chrome", "voz"), ("abre chrome", "texto")])
            resultados = [
                llamada.kwargs for llamada in server.manager.broadcast_json.await_args_list
                if llamada.args and llamada.args[0] == "resultado_accion"
            ]
            self.assertEqual(len(resultados), 2)
        finally:
            server.core_bridge = original_core
            server._estado_core.clear()
            server._estado_core.update(original_estado)
            server._loop = original_loop


if __name__ == "__main__":
    unittest.main()