import unittest

import logger


class LoggerTests(unittest.TestCase):
    def test_contrato_de_log_excepcion(self):
        try:
            raise ValueError("fallo controlado")
        except ValueError as error:
            resultado = logger.log_excepcion("test", "contrato", error)
        self.assertIsNone(resultado)


if __name__ == "__main__":
    unittest.main()
