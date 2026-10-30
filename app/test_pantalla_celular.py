import base64
import json
import contextlib
import io
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pantalla_celular
import celular
import pantalla_vivo
import visor_pantalla_celular


class PantallaCelularTests(unittest.TestCase):
    def setUp(self):
        pantalla_celular.detener_todas()
        pantalla_celular.config = type("Config", (), {"CEL_PANTALLA_CELULAR_ACTIVA": True})()

    def test_interpreta_inicio_y_parada_solo_con_destino_celular(self):
        self.assertEqual(pantalla_celular.interpretar("activa la pantalla en vivo de mi celular"), "iniciar")
        self.assertEqual(pantalla_celular.interpretar("muéstrame en tiempo real la pantalla del teléfono"), "iniciar")
        self.assertEqual(pantalla_celular.interpretar("detén la pantalla en vivo del móvil"), "detener")
        self.assertIsNone(pantalla_celular.interpretar("activa la pantalla en vivo de la laptop"))

    def test_destino_contextual_y_dispositivo_explicito(self):
        resolver = pantalla_vivo.resolver_intencion
        self.assertEqual(resolver("activa la pantalla en vivo", "laptop"), ("celular-a-laptop", "iniciar"))
        self.assertEqual(resolver("activa la pantalla en vivo", "celular"), ("laptop-a-celular", "iniciar"))
        self.assertEqual(
            resolver("activa la pantalla en vivo de mi laptop", "celular"),
            ("laptop-a-celular", "iniciar"),
        )
        self.assertEqual(
            resolver("activa la pantalla en vivo del celular", "laptop"),
            ("celular-a-laptop", "iniciar"),
        )
        self.assertEqual(
            resolver("activa la pantalla en vivo en mi laptop", "laptop"),
            ("celular-a-laptop", "iniciar"),
        )
        self.assertEqual(
            resolver("muestra la pantalla en vivo de la laptop en mi celular", "laptop"),
            ("laptop-a-celular", "iniciar"),
        )
        self.assertEqual(
            resolver("detén la pantalla en vivo del celular", "laptop"),
            ("celular-a-laptop", "detener"),
        )

    def test_capacidades_de_laptop_se_enrutan_al_celular_sin_confundir_la_laptop(self):
        iniciar = celular.interpretar("activa la pantalla en vivo de mi celular")
        detener = celular.interpretar("detén la pantalla en vivo del teléfono")
        laptop = celular.interpretar("activa la pantalla en vivo de la laptop")
        self.assertEqual(iniciar["cap"]["id"], "pantalla_celular_vivo_iniciar")
        self.assertEqual(detener["cap"]["id"], "pantalla_celular_vivo_detener")
        self.assertIsNone(laptop)

    def test_una_sesion_con_fotogramas_monotonicos(self):
        sid, error = pantalla_celular.iniciar("tel-1")
        self.assertTrue(sid)
        self.assertEqual(error, "")
        self.assertTrue(pantalla_celular.aceptar_fotograma("tel-1", sid, 0, b"jpeg"))
        self.assertEqual(pantalla_celular.fotograma("tel-1", sid), (0, b"jpeg"))
        self.assertFalse(pantalla_celular.aceptar_fotograma("tel-1", sid, 0, b"replay"))
        self.assertTrue(pantalla_celular.aceptar_fotograma("tel-1", sid, 2, b"next"))
        self.assertIsNone(pantalla_celular.iniciar("tel-2")[0])
        self.assertIsNone(pantalla_celular.detener("tel-1", "0000000000"))
        self.assertEqual(pantalla_celular.detener("tel-1", sid), sid)
        self.assertFalse(pantalla_celular.activa("tel-1", sid))

    def test_control_remoto_y_perfiles_requieren_sesion_activa(self):
        sid, error = pantalla_vivo.iniciar("tel-control")
        self.assertTrue(sid)
        self.assertEqual(error, "")
        self.assertFalse(pantalla_vivo.control_activo("tel-control", sid))
        self.assertFalse(pantalla_vivo.cambiar_perfil("tel-control", sid, 30))
        for fps in pantalla_vivo.PERFILES:
            self.assertTrue(pantalla_vivo.cambiar_perfil("tel-control", sid, fps))
            self.assertEqual(pantalla_vivo.metadatos("tel-control", sid)["escala"], 1)
        self.assertFalse(pantalla_vivo.cambiar_control("tel-control", sid, 1))
        self.assertTrue(pantalla_vivo.cambiar_control("tel-control", sid, True))
        self.assertTrue(pantalla_vivo.control_activo("tel-control", sid))
        self.assertTrue(pantalla_vivo.detener("tel-control"))
        self.assertFalse(pantalla_vivo.control_activo("tel-control", sid))

        sid, error = pantalla_celular.iniciar("tel-control")
        self.assertTrue(sid)
        self.assertTrue(pantalla_celular.cambiar_perfil("tel-control", sid, 12))
        self.assertFalse(pantalla_celular.cambiar_control("tel-control", sid, "true"))
        self.assertFalse(pantalla_celular.control_activo("tel-control", sid))
        self.assertTrue(pantalla_celular.cambiar_control("tel-control", sid, True))
        self.assertTrue(pantalla_celular.control_activo("tel-control", sid))
        pantalla_celular.detener("tel-control", sid)

    def test_api_control_de_laptop_exige_switch_y_coordenadas_validas(self):
        sid, error = pantalla_vivo.iniciar("tel-api")
        self.assertTrue(sid)
        anterior_mod, anterior_interaccion = celular._mod, celular._interaccion_escritorio
        anterior_escritorio = celular._mostrar_escritorio
        eventos = []
        escritorios = []
        celular._mod = lambda nombre: pantalla_vivo if nombre == "pantalla_vivo" else None
        celular._interaccion_escritorio = lambda accion, x, y, dx=0, dy=0: (
            eventos.append((accion, x, y, dx, dy)) or True
        )
        celular._mostrar_escritorio = lambda: escritorios.append(True) or True

        class Handler:
            _send = celular._H._send

            def _responder(self, status, datos, _dev=None):
                self.respuesta = (status, datos)
                return self.respuesta

        try:
            handler = Handler()
            payload = {"tipo": "pantalla_control", "stream": sid, "accion": "toque", "x": 0.5, "y": 0.25}
            handler._send("tel-api", json.dumps(payload))
            self.assertEqual(handler.respuesta[0], 403)
            self.assertEqual(eventos, [])
            payload = {"tipo": "pantalla_control", "stream": sid, "accion": "inicio"}
            handler._send("tel-api", json.dumps(payload))
            self.assertEqual(handler.respuesta[0], 403)
            self.assertEqual(escritorios, [])

            payload = {"tipo": "pantalla_control", "stream": sid, "accion": "control", "activo": True}
            handler._send("tel-api", json.dumps(payload))
            self.assertEqual(handler.respuesta, (200, {"ok": True}))

            payload = {"tipo": "pantalla_control", "stream": sid, "accion": "inicio"}
            handler._send("tel-api", json.dumps(payload))
            self.assertEqual(handler.respuesta, (200, {"ok": True}))
            self.assertEqual(escritorios, [True])

            payload = {"tipo": "pantalla_control", "stream": sid, "accion": "toque", "x": 0.5, "y": 0.25}
            handler._send("tel-api", json.dumps(payload))
            self.assertEqual(handler.respuesta, (200, {"ok": True}))
            self.assertEqual(eventos, [("toque", 0.5, 0.25, 0, 0)])

            payload = {"tipo": "pantalla_control", "stream": sid, "accion": "clic_derecho",
                       "x": 0.25, "y": 0.75}
            handler._send("tel-api", json.dumps(payload))
            self.assertEqual(handler.respuesta, (200, {"ok": True}))

            payload = {"tipo": "pantalla_control", "stream": sid, "accion": "desplazar",
                       "x": 0.5, "y": 0.5, "dx": 0, "dy": 0.5}
            handler._send("tel-api", json.dumps(payload))
            self.assertEqual(handler.respuesta, (200, {"ok": True}))
            self.assertEqual(eventos[-2][0], "clic_derecho")
            self.assertEqual(eventos[-1], ("desplazar", 0.5, 0.5, 0, 0.5))

            payload["x"] = 2
            handler._send("tel-api", json.dumps(payload))
            self.assertEqual(handler.respuesta[0], 400)
            self.assertEqual(len(eventos), 3)
        finally:
            celular._mod, celular._interaccion_escritorio = anterior_mod, anterior_interaccion
            celular._mostrar_escritorio = anterior_escritorio
            pantalla_vivo.detener("tel-api")

    def test_estado_accesibilidad_actualiza_solo_el_visor_de_la_sesion_vigente(self):
        sid, error = pantalla_celular.iniciar("tel-estado")
        self.assertTrue(sid)
        self.assertEqual(error, "")
        salida = io.BytesIO()

        class Proceso:
            stdin = salida

            @staticmethod
            def poll():
                return None

        anterior_mod = celular._mod
        anterior_visores = celular._visores_pantalla_celular
        anterior_locks = celular._locks_visores_pantalla_celular
        celular._mod = lambda nombre: pantalla_celular if nombre == "pantalla_celular" else None
        celular._visores_pantalla_celular = {"tel-estado": Proceso()}
        celular._locks_visores_pantalla_celular = {"tel-estado": threading.Lock()}

        class Handler:
            _send = celular._H._send

            def _responder(self, status, datos, _dev=None):
                self.respuesta = (status, datos)
                return self.respuesta

        try:
            handler = Handler()
            payload = {
                "tipo": "pantalla_control_estado", "stream_id": sid,
                "activo": False, "solicitado_activo": True, "accesibilidad": False,
            }
            handler._send("tel-estado", json.dumps(payload))
            self.assertEqual(handler.respuesta, (200, {"ok": True}))
            self.assertFalse(pantalla_celular.control_activo("tel-estado", sid))
            self.assertIn(b"CONTROL_ESTADO|0|", salida.getvalue())

            salida.seek(0)
            salida.truncate()
            payload.update(activo=True, solicitado_activo=True, accesibilidad=True)
            handler._send("tel-estado", json.dumps(payload))
            self.assertEqual(handler.respuesta, (200, {"ok": True}))
            self.assertTrue(pantalla_celular.control_activo("tel-estado", sid))
            self.assertIn(b"CONTROL_ESTADO|1|", salida.getvalue())

            salida.seek(0)
            salida.truncate()
            payload["stream_id"] = "sesion-obsoleta"
            handler._send("tel-estado", json.dumps(payload))
            self.assertEqual(handler.respuesta[0], 404)
            self.assertEqual(salida.getvalue(), b"")
        finally:
            celular._mod = anterior_mod
            celular._visores_pantalla_celular = anterior_visores
            celular._locks_visores_pantalla_celular = anterior_locks
            pantalla_celular.detener("tel-estado", sid)

    def test_visor_refleja_rechazo_de_accesibilidad_recibido_por_stdin(self):
        mensaje = "Control remoto no activado: habilita SARA en Accesibilidad."
        linea = b"CONTROL_ESTADO|0|" + base64.urlsafe_b64encode(mensaje.encode("utf-8")) + b"\n"
        entrada_anterior = visor_pantalla_celular.sys.stdin
        estado_anterior = dict(visor_pantalla_celular._actual)

        class Entrada:
            buffer = io.BytesIO(linea)

        try:
            visor_pantalla_celular.sys.stdin = Entrada()
            visor_pantalla_celular._leer_fotogramas()
            self.assertFalse(visor_pantalla_celular._control_activo())
            estado = visor_pantalla_celular.Api("laptop").fotograma(0)
            self.assertEqual(estado["control_estado"], mensaje)
            salida = io.StringIO()
            with contextlib.redirect_stdout(salida):
                self.assertFalse(visor_pantalla_celular.Api("laptop").tocar(0.5, 0.5))
            self.assertEqual(salida.getvalue(), "")
        finally:
            visor_pantalla_celular.sys.stdin = entrada_anterior
            with visor_pantalla_celular._lock:
                visor_pantalla_celular._actual.clear()
                visor_pantalla_celular._actual.update(estado_anterior)

    def test_api_del_visor_no_emite_toques_hasta_activar_control(self):
        api = visor_pantalla_celular.Api("laptop")
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            self.assertFalse(api.tocar(0.5, 0.5))
            self.assertFalse(api.deslizar("arriba"))
            self.assertFalse(api.deslizar_delta(0.5, 0.5, 0.1, 0.1))
            self.assertFalse(api.ir_inicio())
            self.assertTrue(api.cambiar_control(True))
            self.assertFalse(api.cambiar_perfil(30))
            self.assertFalse(api.tocar(float("nan"), 0.5))
            self.assertTrue(api.cambiar_perfil(12))
            self.assertTrue(api.recordar_posicion(0.25, 0.75))
            self.assertTrue(api.tocar(0.25, 0.75))
            self.assertTrue(api.deslizar("arriba"))
            self.assertTrue(api.deslizar("abajo"))
            self.assertTrue(api.deslizar("izquierda"))
            self.assertTrue(api.deslizar("derecha"))
            self.assertFalse(api.arrastrar(0.2, 0.3, float("nan"), 0.9))
            self.assertTrue(api.arrastrar(0.2, 0.3, 0.8, 0.9))
            self.assertTrue(api.deslizar_delta(0.4, 0.6, -0.2, 0.3))
            self.assertFalse(api.deslizar_delta(0.4, 0.6, float("nan"), 0.3))
            self.assertTrue(api.ir_inicio())
            self.assertFalse(api.deslizar("diagonal"))
        self.assertEqual(
            salida.getvalue().splitlines(),
            [
                "CONTROL|1",
                "PERFIL|12",
                "TOQUE|0.250000|0.750000",
                "DESLIZAR|0.500000|0.500000|0.0000|-0.5500",
                "DESLIZAR|0.500000|0.500000|0.0000|0.5500",
                "DESLIZAR|0.500000|0.500000|-0.7500|0.0000",
                "DESLIZAR|0.500000|0.500000|0.9500|0.0000",
                "ARRASTRAR|0.200000|0.300000|0.800000|0.900000",
                "DESLIZAR|0.400000|0.600000|-0.2000|0.3000",
                "INICIO",
            ],
        )

    def test_parser_deslizamiento_rechaza_coordenadas_o_deltas_invalidos(self):
        self.assertEqual(
            celular._leer_deslizamiento("DESLIZAR|0.25|0.75|-0.55|0"),
            (0.25, 0.75, -0.55, 0.0),
        )
        for texto in (
            "OTRO|0.25|0.75|-0.55|0",
            "DESLIZAR|nan|0.75|-0.55|0",
            "DESLIZAR|1.1|0.75|-0.55|0",
            "DESLIZAR|0.25|0.75|0|0",
            "DESLIZAR|0.25|0.75|0|1.1",
            "DESLIZAR|0.25|0.75|0",
        ):
            with self.subTest(texto=texto):
                self.assertIsNone(celular._leer_deslizamiento(texto))

    def test_comandos_de_rueda_y_inicio_se_encolan_solo_con_control_activo(self):
        sid, error = pantalla_celular.iniciar("tel-visor-control")
        self.assertTrue(sid)
        self.assertEqual(error, "")
        self.assertTrue(pantalla_celular.cambiar_control("tel-visor-control", sid, True))
        acciones = []
        anterior_mod, anterior_cola = celular._mod, celular._cola
        celular._mod = lambda nombre: pantalla_celular if nombre == "pantalla_celular" else None
        celular._cola = lambda _dev: type("Cola", (), {"put": staticmethod(acciones.append)})()
        try:
            self.assertTrue(
                celular._procesar_control_visor(
                    "tel-visor-control", sid, "DESLIZAR|0.4|0.6|0|-0.2"
                )
            )
            self.assertTrue(celular._procesar_control_visor("tel-visor-control", sid, "INICIO"))
            self.assertEqual(acciones[0], {
                "tipo": "pantalla_celular", "accion": "desplazar", "stream_id": sid,
                "x": 0.4, "y": 0.6, "dx": 0.0, "dy": -0.2,
            })
            self.assertEqual(acciones[1], {
                "tipo": "pantalla_celular", "accion": "inicio", "stream_id": sid,
            })
            self.assertTrue(celular._procesar_control_visor(
                "tel-visor-control", sid, "ARRASTRAR|0.2|0.3|0.8|0.9"
            ))
            self.assertEqual(acciones[2], {
                "tipo": "pantalla_celular", "accion": "arrastrar", "stream_id": sid,
                "x": 0.2, "y": 0.3, "destino_x": 0.8, "destino_y": 0.9,
            })
            self.assertIsNone(celular._leer_arrastre("ARRASTRAR|nan|0|1|1"))
            self.assertFalse(
                celular._procesar_control_visor("tel-visor-control", sid, "DESLIZAR|nan|0|0|1")
            )
            self.assertTrue(pantalla_celular.cambiar_control("tel-visor-control", sid, False))
            self.assertFalse(celular._procesar_control_visor("tel-visor-control", sid, "INICIO"))
            self.assertEqual(len(acciones), 3)
        finally:
            celular._mod, celular._cola = anterior_mod, anterior_cola
            pantalla_celular.detener("tel-visor-control", sid)

    def test_bandera_desactiva_la_captura_remota(self):
        pantalla_celular.config = type("Config", (), {"CEL_PANTALLA_CELULAR_ACTIVA": False})()
        sid, mensaje = pantalla_celular.iniciar("tel-1")
        self.assertIsNone(sid)
        self.assertIn("desactivada", mensaje)

    def test_lenguaje_natural_de_laptop_usa_el_destino_celular(self):
        anterior_mod = celular._mod
        anterior_comando = celular.comando_pantalla_vivo
        llamadas = []
        celular._mod = lambda nombre: pantalla_vivo if nombre == "pantalla_vivo" else None
        celular.comando_pantalla_vivo = lambda arg: (True, llamadas.append(arg) or arg)
        try:
            frase = "activa la pantalla en vivo"
            self.assertTrue(celular.es_comando(frase))
            self.assertEqual(celular.ejecutar(frase), (True, "laptop-a-celular on"))
            self.assertEqual(llamadas, ["laptop-a-celular on"])
            self.assertIsNone(pantalla_vivo.interpretar("activa la pantalla en vivo de mi celular"))
        finally:
            celular._mod, celular.comando_pantalla_vivo = anterior_mod, anterior_comando

    def test_comando_rigido_cubre_ambos_destinos(self):
        pantalla_celular.config = type("Config", (), {"CEL_PANTALLA_CELULAR_ACTIVA": True})()
        pantalla_vivo._c = lambda nombre, defecto: True
        anterior_mod, anterior_dev = celular._mod, celular._dispositivo_activo
        anterior_transfer = celular.transfer
        anterior_cola = celular._cola
        anterior_ejecutar = celular.ejecutar
        anterior_c = pantalla_vivo._c
        anterior_config = celular.config
        anterior_config_celular = pantalla_celular.config
        mensajes = []
        celular._mod = lambda nombre: pantalla_vivo if nombre == "pantalla_vivo" else pantalla_celular
        celular._dispositivo_activo = lambda: "tel-rigido"
        celular.transfer = type("Transfer", (), {"cripto_disponible": staticmethod(lambda: True)})()
        celular._cola = lambda _dev: type("Cola", (), {"put": staticmethod(mensajes.append)})()
        try:
            ok, texto = celular.comando_pantalla_vivo("laptop-a-celular on")
            self.assertTrue(ok, texto)
            self.assertIn("Solicitud enviada", texto)
            self.assertEqual(mensajes[-1]["tipo"], "pantalla_vivo")
            self.assertEqual(mensajes[-1]["solicitado_desde"], "laptop")
            self.assertEqual(
                pantalla_vivo.metadatos("tel-rigido", mensajes[-1]["stream_id"])["solicitado_desde"],
                "laptop",
            )
            self.assertEqual(celular.comando_pantalla_vivo("laptop-a-celular estado")[0], True)
            self.assertTrue(pantalla_vivo.detener("tel-rigido"))

            celular.ejecutar = lambda texto: (True, texto)
            ok, texto = celular.comando_pantalla_vivo("celular-a-laptop on")
            self.assertTrue(ok)
            self.assertIn("activa la pantalla en vivo de mi celular", texto)
            self.assertFalse(celular.comando_pantalla_vivo("ambos on")[0])
            self.assertFalse(celular.comando_pantalla_vivo("laptop-a-celular quizá")[0])

            pantalla_vivo._c = lambda _nombre, _defecto: False
            ok, texto = celular.comando_pantalla_vivo("laptop-a-celular on")
            self.assertFalse(ok)
            self.assertIn("desactivada", texto)
            pantalla_vivo._c = lambda _nombre, defecto: defecto
            celular.config = type("Config", (), {"CEL_PANTALLA_CELULAR_ACTIVA": False})()
            pantalla_celular.config = type("Config", (), {"CEL_PANTALLA_CELULAR_ACTIVA": False})()
            ok, texto = celular.comando_pantalla_vivo("celular-a-laptop estado")
            self.assertFalse(ok)
            self.assertIn("desactivada", texto)
        finally:
            celular._mod, celular._dispositivo_activo = anterior_mod, anterior_dev
            celular.transfer, celular._cola = anterior_transfer, anterior_cola
            celular.ejecutar, pantalla_vivo._c = anterior_ejecutar, anterior_c
            celular.config = anterior_config
            pantalla_celular.config = anterior_config_celular
            pantalla_vivo.detener_todas()


if __name__ == "__main__":
    unittest.main()
