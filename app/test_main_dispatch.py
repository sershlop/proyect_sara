import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import main


class MainDispatchTests(unittest.TestCase):
    def test_todas_las_ramas_imprimen_metadata(self):
        categorias = [
            "TAREA",
            "INFO_IP",
            "CARPETA_ESPECIAL",
            "ABRIR_APLICACION",
            "CERRAR_APLICACION",
            "COMANDO_SISTEMA",
            "CREAR_CARPETA",
            "ABRIR_ARCHIVO",
            "DESCONOCIDA",
        ]
        entradas = [f"prueba {indice}" for indice in range(len(categorias))] + ["/salir"]
        resultados = [
            {
                "categoria": categoria,
                "confianza": round(0.91 - indice * 0.01, 2),
                "metodo": "keyword" if indice % 2 == 0 else "cache",
                "texto_norm": entrada,
                "id_historial": indice + 1,
            }
            for indice, (categoria, entrada) in enumerate(zip(categorias, entradas))
        ]
        resultados_esperados = [resultado.copy() for resultado in resultados]

        def clasificar(_entrada):
            return resultados.pop(0).copy()

        def registrar_productividad(resultado):
            if resultado["categoria"] == "TAREA":
                return {"tipo": "tarea", "id": 1}
            return None

        def responder_sistema(resultado):
            if resultado["categoria"] == "INFO_IP":
                return {"texto": "IP local: 127.0.0.1", "exito": True}
            return None

        def resolver_carpeta(resultado):
            if resultado["categoria"] == "CARPETA_ESPECIAL":
                return {"texto": "Abriendo carpeta", "abierto": True}
            return None

        def abrir_aplicacion(resultado):
            if resultado["categoria"] == "ABRIR_APLICACION":
                return {"texto": "Abriendo Chrome", "abierto": True}
            return None

        def resolver_cierre(resultado):
            if resultado["categoria"] == "CERRAR_APLICACION":
                return {"estado": "protegido", "texto": "Proceso protegido"}
            return None

        def resolver_shell(resultado):
            if resultado["categoria"] == "COMANDO_SISTEMA":
                return {"estado": "ejecutado", "texto": "Comando ejecutado", "exito": True}
            return None

        def resolver_gestion(resultado):
            if resultado["categoria"] == "CREAR_CARPETA":
                return {"estado": "ejecutado", "texto": "Carpeta creada", "exito": True}
            return None

        def ejecutar_archivo(resultado):
            if resultado["categoria"] == "ABRIR_ARCHIVO":
                return {"texto": "Abriendo archivo", "abierto": True}
            return None

        def responder_archivo(resultado):
            return None

        salida = io.StringIO()
        patches = [
            patch.object(main.db, "crear_tablas"),
            patch.object(main.productivity, "crear_tablas"),
            patch.object(main.file_index, "crear_tablas"),
            patch.object(main.app_index, "crear_tablas"),
            patch.object(main.shell, "crear_tablas"),
            patch.object(main, "sembrar_patrones_iniciales"),
            patch.object(main.engine, "calentar_qwen", return_value=False),
            patch.object(main.file_index, "ejecutar_reindexado", return_value={"indexados": 0, "cortado_por": None}),
            patch.object(main.app_index, "ejecutar_reindexado_aplicaciones", return_value={"indexados": 0}),
            patch.object(main.file_index, "iniciar_reindexado_automatico"),
            patch.object(main.app_index, "iniciar_reindexado_automatico_aplicaciones"),
            patch.object(main.engine, "clasificar", side_effect=clasificar),
            patch.object(main.engine, "ajustar_aprendizaje_por_resultado"),
            patch.object(main.productivity, "registrar_desde_clasificacion", side_effect=registrar_productividad),
            patch.object(main.system_info, "responder_desde_clasificacion", side_effect=responder_sistema),
            patch.object(main.file_actions, "ejecutar_carpeta_especial", side_effect=resolver_carpeta),
            patch.object(main.app_actions, "ejecutar_desde_clasificacion", side_effect=abrir_aplicacion),
            patch.object(main.app_actions, "resolver_cierre", side_effect=resolver_cierre),
            patch.object(main.shell, "resolver_comando_sistema", side_effect=resolver_shell),
            patch.object(main.file_manager, "resolver_gestion_archivo", side_effect=resolver_gestion),
            patch.object(main.file_actions, "ejecutar_desde_clasificacion", side_effect=ejecutar_archivo),
            patch.object(main.file_index, "responder_desde_clasificacion", side_effect=responder_archivo),
            patch("builtins.input", side_effect=entradas),
        ]

        with redirect_stdout(salida):
            for parche in patches:
                parche.start()
            try:
                main.main()
            finally:
                for parche in reversed(patches):
                    parche.stop()

        texto = salida.getvalue()
        for resultado in resultados_esperados:
            metadata = main._detalle_clasificacion(resultado)
            self.assertIn(metadata, texto)


if __name__ == "__main__":
    unittest.main()
