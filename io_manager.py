# 📁 io_manager.py
_modo_voz    = False
_voice_module = None


def activar_modo_voz(voice_mod):
    global _modo_voz, _voice_module
    _modo_voz     = True
    _voice_module = voice_mod
    print("🎤 Modo voz activado. Di 'sara' para activarme.")


def desactivar_modo_voz():
    global _modo_voz, _voice_module
    _modo_voz     = False
    _voice_module = None
    print("⌨️  Modo voz desactivado. Volviendo a modo texto.")


def esta_en_modo_voz():
    return _modo_voz


def obtener_input():
    if _modo_voz and _voice_module:
        primer_ciclo = True
        while True:
            if primer_ciclo:
                print("💤 SARA: En espera... (di 'sara' para activarme)")
                primer_ciclo = False

            detectado, comando_inline = _voice_module.escuchar_wakeword()

            if detectado:
                if comando_inline:
                    print(f"🗣️  Tú dijiste: 'sara {comando_inline}'")
                    return comando_inline

                texto = _voice_module.escuchar_comando()
                if texto:
                    print(f"🗣️  Tú dijiste: '{texto}'")
                    return texto

                print("💤 SARA: En espera...")
                continue

    try:
        texto = input("Tú: ").strip()
        return texto
    except KeyboardInterrupt:
        print("\nSARA: Hasta luego 👋")
        exit(0)
    except EOFError:
        print("\nSARA: Entrada cerrada. Hasta luego.")
        exit(0)


def mostrar_respuesta(texto):
    if not texto or not texto.strip():
        return
    print(f"SARA: {texto}")
    if _modo_voz and _voice_module:
        _voice_module.hablar_async(texto)


def mostrar_error(mensaje):
    print(f"SARA [ERROR]: {mensaje}")


def mostrar_confianza(confianza):
    porcentaje = round(confianza * 100, 1)
    print(f"SARA [confianza: {porcentaje}%]")


def mostrar_bienvenida():
    print("=" * 45)
    print("  SARA — Sistema Autónomo de Razonamiento")
    print("         Artificial v0.1.0")
    print("  Escribe 'salir' para cerrar")
    print("=" * 45)


def mostrar_separador():
    print("-" * 40)


def mostrar_despedida():
    print("SARA: Hasta luego 👋")


PALABRAS_SALIDA = {"salir", "exit", "quit", "adios", "chao", "bye"}


def es_comando_salida(texto):
    return texto.strip().lower() in PALABRAS_SALIDA


def preguntar_si_no(mensaje):
    while True:
        try:
            respuesta = input(f"SARA: {mensaje} (si/no): ").strip().lower()
            if respuesta in ("si", "s", "yes", "y"):
                return True
            elif respuesta in ("no", "n"):
                return False
            else:
                print("SARA: Por favor responde 'si' o 'no'.")
        except KeyboardInterrupt:
            return False


def solicitar_respuesta_nueva():
    try:
        while True:
            print("SARA: Escribe la respuesta (o 'cancelar' para omitir):")
            respuesta = input("  → ").strip()
            if respuesta.lower() == "cancelar":
                return None
            if not respuesta:
                print("SARA: La respuesta no puede quedar vacía. Intenta de nuevo.")
                continue
            return respuesta
    except KeyboardInterrupt:
        return None


def solicitar_datos_comando():
    try:
        print("SARA: Vamos a registrar el comando.")
        print("      Escribe 'cancelar' en cualquier momento.\n")

        while True:
            accion = input("  → Acción (URL, ruta o comando): ").strip()
            if accion.lower() == "cancelar":
                return None
            if not accion:
                print("SARA: La acción no puede quedar vacía.")
                continue
            break

        while True:
            print("  → Tipo de comando:")
            print("     [1] web    (abrir página)")
            print("     [2] app    (abrir aplicación)")
            print("     [3] sistema (comando de terminal)")
            print("     [4] sistema_control (comando del sistema operativo)")
            tipo_opcion = input("  → Elige 1, 2, 3 o 4: ").strip()
            tipos       = {"1": "web", "2": "app", "3": "sistema", "4": "sistema_control"}
            tipo        = tipos.get(tipo_opcion)
            if tipo:
                break
            print("SARA: Opción inválida. Elige 1, 2, 3 o 4.")

        while True:
            palabras_clave = input("  → Palabras clave (ej: 'abre chrome, navegar'): ").strip()
            if palabras_clave.lower() == "cancelar":
                return None
            if not palabras_clave:
                print("SARA: Debes proporcionar al menos una palabra clave.")
                continue
            break

        descripcion = input("  → Descripción breve: ").strip()
        if not descripcion:
            descripcion = accion

        return {
            "accion":         accion,
            "tipo":           tipo,
            "palabras_clave": palabras_clave,
            "descripcion":    descripcion
        }
    except KeyboardInterrupt:
        return None


def solicitar_acciones_multiples():
    try:
        print("\nSARA: ¿Cuántas acciones quieres agregar? (máximo 10)")
        try:
            cantidad = int(input("  → Cantidad: ").strip())
        except ValueError:
            print("SARA: Número inválido. Cancelando.")
            return None

        if cantidad <= 0:
            return None
        if cantidad > 10:
            cantidad = 10

        acciones = []
        for i in range(1, cantidad + 1):
            print(f"\nSARA: Acción {i} de {cantidad}")
            print("─" * 35)

            while True:
                accion = input(f"  → Acción {i} (URL o ruta): ").strip()
                if accion.lower() == "cancelar":
                    return None
                if not accion:
                    print("SARA: La acción no puede quedar vacía.")
                    continue
                break

            while True:
                print(f"  → Tipo:")
                print("     [1] web  [2] app  [3] sistema  [4] sistema_control")
                tipo_opcion = input("  → Elige 1, 2, 3 o 4: ").strip()
                tipos       = {"1": "web", "2": "app", "3": "sistema", "4": "sistema_control"}
                tipo        = tipos.get(tipo_opcion)
                if tipo:
                    break
                print("SARA: Opción inválida. Elige 1, 2, 3 o 4 .")

            descripcion = input(f"  → Descripción: ").strip()
            if not descripcion:
                descripcion = accion

            acciones.append({
                "orden":       i,
                "accion":      accion,
                "tipo":        tipo,
                "descripcion": descripcion
            })
            print(f"  ✅ Acción {i} registrada.")

        return acciones if acciones else None
    except KeyboardInterrupt:
        return None
    

 