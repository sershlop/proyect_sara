# 📁 main.py — SARA v2.0 (núcleo mínimo)
#
# Punto de entrada de terminal para probar el motor de clasificación de
# forma interactiva. NO es una versión reducida del sara.py viejo — es
# un archivo nuevo, con un único propósito: clasificar lo que escribes
# y dejarte confirmar o corregir cada resultado, para ir alimentando
# generar_fixture_regresion() con casos reales de uso.
#
# Los verbos declarados en config.py (VERBOS_TAREA, VERBOS_RECORDATORIO,
# VERBOS_NOTA) se siembran como patrones de Capa 1 en cada arranque —
# agregar_patron() es idempotente (ON CONFLICT DO UPDATE), así que no
# duplica nada si ya existían.

import json
import shell
import database as db
import intent_engine as engine
import productivity
import system_info
import file_index
import file_manager
import file_actions
import app_index
import app_actions

try:
    import web_navigator
except ImportError:
    web_navigator = None

from config import (
    # ... imports existentes ...
    UMBRAL_CREAR_COMANDO,  # 🟢 NUEVO
)

# DESPUÉS (corregido — se restaura el import perdido):
try:
    import habitos
except ImportError:
    habitos = None

try:
    import memoria_core
except ImportError:
    memoria_core = None

try:
    import salud_sistema
except ImportError:
    salud_sistema = None

try:
    import respaldo_externo
except ImportError:
    respaldo_externo = None
try:
    import generador_codigo
except ImportError:
    generador_codigo = None
try:
    import informativo
except ImportError:
    informativo = None
# DESPUÉS:
# DESPUÉS:
# DESPUÉS:
from config import (
    VERBOS_TAREA, VERBOS_RECORDATORIO, VERBOS_NOTA,
    VERBOS_INFO_IP, VERBOS_INFO_BATERIA, VERBOS_INFO_RECURSOS, VERBOS_INFO_NOMBRE,
    VERBOS_ARCHIVO, VERBOS_ABRIR_ARCHIVO, VERBOS_CARPETA_ESPECIAL, VERBOS_ABRIR_APLICACION,
    VERBOS_CERRAR_APLICACION, TIMEOUT_CIERRE_NORMAL_SEGUNDOS, VERBOS_COMANDO_SISTEMA, VERBOS_CREAR_CARPETA,
    VERBOS_ELIMINAR_ARCHIVO, VERBOS_MOVER_ARCHIVO, VERBOS_COPIAR_ARCHIVO, VERBOS_RENOMBRAR_ARCHIVO, VERBOS_COMPRIMIR_ARCHIVO,
    VERBOS_BUSCAR_EN_NAVEGADOR, APLICACIONES_SISTEMA_FIJAS,
    VERBOS_GENERAR_CODIGO, VERBOS_PREGUNTA_INFORMATIVA,
    UMBRAL_CONFIANZA_QWEN,
)

def sembrar_patrones_iniciales() -> None:
    dominios = {
        "CREAR_CARPETA": VERBOS_CREAR_CARPETA,
        "ELIMINAR_ARCHIVO": VERBOS_ELIMINAR_ARCHIVO,
        "MOVER_ARCHIVO": VERBOS_MOVER_ARCHIVO,
        "COPIAR_ARCHIVO": VERBOS_COPIAR_ARCHIVO,
        "RENOMBRAR_ARCHIVO": VERBOS_RENOMBRAR_ARCHIVO,
        "COMPRIMIR_ARCHIVO": VERBOS_COMPRIMIR_ARCHIVO,
        "TAREA": VERBOS_TAREA,
        "RECORDATORIO": VERBOS_RECORDATORIO,
        "NOTA": VERBOS_NOTA,
        "COMANDO_SISTEMA": VERBOS_COMANDO_SISTEMA,
        "INFO_IP": VERBOS_INFO_IP,
        "INFO_BATERIA": VERBOS_INFO_BATERIA,
        "INFO_RECURSOS": VERBOS_INFO_RECURSOS,
        "INFO_NOMBRE": VERBOS_INFO_NOMBRE,
        "ARCHIVO": VERBOS_ARCHIVO,
        "ABRIR_ARCHIVO": VERBOS_ABRIR_ARCHIVO,
        "CARPETA_ESPECIAL": VERBOS_CARPETA_ESPECIAL,
        "ABRIR_APLICACION": VERBOS_ABRIR_APLICACION,
        "CERRAR_APLICACION": VERBOS_CERRAR_APLICACION,
        # DESPUÉS:
        "BUSCAR_EN_NAVEGADOR": VERBOS_BUSCAR_EN_NAVEGADOR,
        "GENERAR_CODIGO": VERBOS_GENERAR_CODIGO,
        "PREGUNTA_INFORMATIVA": VERBOS_PREGUNTA_INFORMATIVA,
    }
    # DESPUÉS:
    for categoria, verbos in dominios.items():
        for verbo in verbos:
            db.agregar_patron(categoria, "keyword", verbo)

    # Utilidades de sistema: sin esto, "abre configuracion"/"abre cmd"/
    # "abre terminal" no tenían patrón propio de Capa 1 y dependían de
    # que Qwen adivinara bien la categoría — a veces adivinaba mal.
    for nombre_app in APLICACIONES_SISTEMA_FIJAS:
        for verbo in ("abre", "abrir", "ejecuta", "ejecutar", "inicia", "lanza"):
            db.agregar_patron("ABRIR_APLICACION", "keyword", f"{verbo} {nombre_app}")

# DESPUÉS:
def _log_error(origen: str, contexto: str, excepcion: Exception) -> None:
    try:
        import logger as _logger
        _logger.log_excepcion(origen, contexto, excepcion)
    except Exception:
        print(f"[MAIN ERROR] {origen}.{contexto}: {type(excepcion).__name__}: {excepcion}")


def _preguntar_confirmacion(prompt: str) -> str:
    """input() que nunca truena: EOF o Ctrl+C se tratan como respuesta
    vacía (equivale a 'no'/cancelar), nunca cierran ni cuelgan SARA."""
    try:
        return input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _preguntar_texto(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _mostrar_ayuda() -> None:

    print("""
Comandos disponibles:
  /ayuda                    muestra esta ayuda
    /confirmar              marca la última clasificación como correcta
    /validar                guarda la última clasificación como regla permanente
  /corregir CATEGORIA     marca la última como incorrecta y la reclasifica
  
  /reindexar              vuelve a escanear las carpetas indexadas
    /reindexar_apps         vuelve a descubrir las aplicaciones instaladas
  
  /stats                  estadísticas de aprendizaje vectorial
  /calibrar               evidencia real de aciertos por nivel de confianza del árbitro
  /salud                  estado actual de RAM, batería, disco y CPU
  /fixture                muestra el fixture de regresión generado hasta ahora
  /salir                  cierra SARA
Cualquier otra cosa que escribas se envía al motor de clasificación.
""")


def _detalle_clasificacion(resultado: dict) -> str:
    return (
        f"[categoría={resultado.get('categoria', 'DESCONOCIDA')}, "
        f"método={resultado.get('metodo', 'ninguno')}, "
        f"confianza={resultado.get('confianza', 0.0)}]"
    )


def _imprimir_respuesta(resultado: dict, texto: str, marca: str = "→") -> None:
    print(f"  {marca} {texto}  {_detalle_clasificacion(resultado)}")


# 🟢 NUEVA FUNCIÓN: Validar nombre de comando
def _validar_nombre_comando(nombre: str) -> bool:
    """
    Valida que el nombre sea apto para comando.
    - 1-50 caracteres
    - Solo letras, números, guiones, guiones bajos
    - No puede ser palabra reservada
    """
    if not nombre or len(nombre) > 50:
        return False
        
    import re
    if not re.match(r"^[a-zA-Z0-9_-]+$", nombre):
        return False
        
    # Palabras reservadas de SARA
    reservadas = {
        "ayuda", "confirmar", "validar", "corregir", "reindexar", "stats",
        "fixture", "salir", "exit", "quit", "si", "no", "sí"
    }
    if nombre.lower() in reservadas:
        return False
        
    return True


def main() -> None:
    # DESPUÉS:
    db.crear_tablas()
    productivity.crear_tablas()
    file_index.crear_tablas()
    app_index.crear_tablas()
    shell.crear_tablas()
    if generador_codigo is not None:
        generador_codigo.crear_tablas()
    if informativo is not None:
        informativo.crear_tablas()
    
    # BUG 5 FIX: Indexar aplicaciones al inicio
    try:
        resultado = app_index.indexar_aplicaciones()
        if resultado.get("indexados", 0) == 0:
            print(f"  [AVISO] No se pudieron indexar aplicaciones ({resultado.get('error', 'desconocido')})")
    except Exception as e:
        print(f"  [AVISO] No pude indexar aplicaciones: {e}")
    
    # Pilar 1, 2, 3: inicialización al arrancar
    # BUG 4 FIX: Dejar que cada pilar cree su propia conexión
    if memoria_core is not None:
        try:
            # memoria_core debe tener función inicializar()
            if hasattr(memoria_core, 'inicializar'):
                memoria_core.inicializar()
            else:
                # Fallback si la función no existe
                from database import conectar
                with conectar() as conn:
                    if hasattr(memoria_core, '_crear_tablas'):
                        memoria_core._crear_tablas(conn)
        except Exception as e:
            print(f"  [AVISO] No pude inicializar memoria_core: {e}")
    
    if habitos is not None:
        try:
            # habitos debe tener función inicializar()
            if hasattr(habitos, 'inicializar'):
                habitos.inicializar()
            else:
                # Fallback si la función no existe
                habitos._crear_tablas()
        except Exception as e:
            print(f"  [AVISO] No pude inicializar habitos: {e}")
    
    # DESPUÉS (se deja solo una vez — no rompe nada dejarlo duplicado, pero es trabajo doble sin sentido en cada arranque):
    if salud_sistema is not None:
        try:
            salud_sistema.iniciar_cache()
        except Exception as e:
            print(f"  [AVISO] No pude inicializar salud_sistema: {e}")
    
    sembrar_patrones_iniciales()

    print("═" * 56)
    print("  SARA v2.0 — núcleo de clasificación (modo prueba)")
    print("═" * 56)

    print("Cargando modelo local en memoria (puede tardar la primera vez)...")
    if engine.calentar_qwen():
        print("Listo — Qwen respondiendo.\n")
    else:
        print("Qwen no respondió al calentamiento — la Capa 3 podría fallar o tardar. Revisa que 'ollama serve' esté activo.\n")

    print("Indexando carpetas principales (puede tardar la primera vez; luego se repite solo)...")
    resultado_indexado = file_index.ejecutar_reindexado()
    aviso_corte = f" (cortado por {resultado_indexado['cortado_por']})" if resultado_indexado["cortado_por"] else ""
    print(f"Indexado: {resultado_indexado['indexados']} archivo(s){aviso_corte}.\n")

    resultado_apps = app_index.ejecutar_reindexado_aplicaciones()
    print(f"Aplicaciones descubiertas: {resultado_apps['indexados']}.\n")

    file_index.iniciar_reindexado_automatico()
    app_index.iniciar_reindexado_automatico_aplicaciones()

    print("Escribe /ayuda para ver los comandos disponibles.\n")

    # DESPUÉS:
    ultimo_resultado = None

    while True:
        # Pilar 3: alertas proactivas de salud, antes de cada input. Son
        # solo notificaciones (no piden sí/no), así que no consumen el
        # turno ni compiten con la sugerencia de hábitos de más abajo.
        # DESPUÉS:
        if salud_sistema is not None:
            try:
                for alerta in salud_sistema.evaluar_alertas_proactivas():
                    print(f"  {alerta['texto']}")
            except Exception as e:
                _log_error("main", "evaluar_alertas_proactivas", e)

        # Recordatorios vencidos — mismo patrón síncrono, ver docstring de
        # productivity.obtener_alertas_recordatorios_pendientes().
        try:
            for alerta in productivity.obtener_alertas_recordatorios_pendientes():
                print(f"  {alerta}")
        except Exception as e:
            _log_error("main", "obtener_alertas_recordatorios_pendientes", e)

        # Pilar 2: sugerencia síncrona antes de cada input
        sugerencia = None
        sugerencia_mostrada = None
        if habitos is not None:
            try:
                sugerencia = habitos.sugerencia_pendiente()
            except Exception as e:
                sugerencia = None

        if sugerencia:
            miembros_sugerencia = sugerencia.get("miembros", [])
            restringido = False
            restriccion_texto = ""
            
            # AMARILLO 11 FIX: Capturar el texto de la restricción
            if memoria_core is not None:
                for m in miembros_sugerencia:
                    dominio = m.get("dominio", "")
                    identificador = m.get("identificador", "")
                    if dominio in ("aplicacion", "archivo"):
                        restriccion = memoria_core.verificar_restriccion(dominio, identificador)
                        if restriccion:
                            restringido = True
                            restriccion_texto = restriccion
                            break

            # AMARILLO 11 FIX: Si está restringido, no se hace nada
            # Una restricción del sistema no es una decisión del usuario
            if not restringido:
                texto_sugerencia = sugerencia.get("texto", f"¿Empezar con {sugerencia.get('nombre', 'algo')}?")
                mostrar = True
                guardia_salud = None
                
                # AMARILLO 10 FIX: Evaluar salud del sistema
                if salud_sistema is not None:
                    try:
                        guardia_salud = salud_sistema.evaluar_guardias_para_paquete(miembros_sugerencia)
                    except Exception as e:
                        guardia_salud = None
                    
                    if guardia_salud:
                        if guardia_salud["tipo"] == "bloqueado":
                            # AMARILLO 10 FIX: No mostrar = no marcar el turno como consumido
                            mostrar = False
                        else:  # "advertencia"
                            # AMARILLO 2 FIX: Mensaje amigable con emoji
                            texto_sugerencia = guardia_salud["texto"]
                
                # AMARILLO 10 FIX: Marcar SOLO si realmente se muestra
                if mostrar:
                    try:
                        habitos.marcar_sugerencia_mostrada(sugerencia["id"])
                    except Exception:
                        pass
                    print(f"  💡 {texto_sugerencia}")
                    sugerencia_mostrada = sugerencia

        try:
            entrada = input("Tú> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nHasta luego.")
            break

        # FIX 4: Capturar respuesta a la sugerencia SOLO si de verdad se mostró una
        if sugerencia_mostrada:
            respuesta_baja = entrada.lower()
            if respuesta_baja in {"si", "sí", "s", "yes", "y"}:
                try:
                    resultado_paquete = habitos.ejecutar_paquete(sugerencia_mostrada["id"])
                    habitos.confirmar_sugerencia(sugerencia_mostrada["id"])
                    print(f"  ✓ {resultado_paquete['texto']}")

                    # 🟢 INTEGRACIÓN: Ofrecer crear comando si alcanzó umbral
                    try:
                        paquete = habitos.obtener_paquete(sugerencia_mostrada["id"])
                        if paquete and paquete["veces_confirmado"] >= UMBRAL_CREAR_COMANDO:
                            print(f"\n  💡 Has confirmado '{paquete['nombre_paquete']}' {paquete['veces_confirmado']} veces.")
                            print(f"     ¿Quieres convertirlo en un comando permanente?")
                            
                            # DESPUÉS:
                            respuesta_comando = _preguntar_confirmacion("     (sí/no) > ")
                            if respuesta_comando in {"si", "sí", "s", "yes", "y"}:
                                # Pedir nombre personalizado
                                print(f"     ¿Cómo quieres llamarlo?")
                                print(f"     (default: '{paquete['nombre_paquete']}') >")
                                nombre_custom = _preguntar_texto("     ")
                                nombre_final = nombre_custom if nombre_custom else paquete['nombre_paquete']
                                
                                # Validar nombre
                                if _validar_nombre_comando(nombre_final):
                                    exito = habitos.crear_comando_desde_paquete(
                                        paquete["id"],
                                        nombre_final
                                    )
                                    if exito:
                                        print(f"  ✓ Comando '{nombre_final}' creado.")
                                        print(f"     Ahora puedes decir: '{nombre_final}'")
                                    else:
                                        print(f"  ✗ No pude crear el comando. ¿Ya existe?")
                                else:
                                    print(f"  ✗ Nombre inválido: usa solo letras, números y guiones.")
                    except Exception as e:
                        print(f"  [AVISO] No pude evaluar si ofrecer un comando permanente: {e}")
                        # No bloquea la ejecución

                except Exception as e:
                    print(f"  ✗ Error al ejecutar paquete: {e}")
                continue

            if respuesta_baja in {"no", "n"}:
                try:
                    habitos.rechazar_sugerencia(sugerencia_mostrada["id"])
                    print("  → Entendido, no lo sugiero de nuevo hoy.")
                except Exception:
                    pass
                continue
            # Si no fue s/n, la entrada sigue su camino normal hacia el motor de clasificación

        if not entrada:
            continue

        if entrada in ("/salir", "/exit", "/quit"):
            print("Hasta luego.")
            break

        if entrada == "/ayuda" or entrada == "/AYUDA":
            _mostrar_ayuda()
            continue

        # DESPUÉS:
        if entrada == "/stats":
            print(db.obtener_stats_intenciones_aprendidas())
            continue

        # DESPUÉS:
        if entrada == "/calibrar":
            filas = db.obtener_calibracion_confianza()
            if not filas:
                print("  Todavía no hay suficientes clasificaciones confirmadas o corregidas del árbitro "
                      "para calibrar. Usa /confirmar y /corregir con más frecuencia y vuelve a intentar.")
            else:
                print(f"  Calibración de confianza del árbitro (umbral actual: {UMBRAL_CONFIANZA_QWEN}):")
                for f in filas:
                    marca = " ← umbral actual" if f["bucket_desde"] <= UMBRAL_CONFIANZA_QWEN < f["bucket_hasta"] else ""
                    print(f"    [{f['bucket_desde']:.2f}-{f['bucket_hasta']:.2f}) — "
                          f"{f['aciertos']}/{f['total_evaluado']} correctas ({f['pct_acierto']}%){marca}")
            continue

        if entrada == "/salud":
            if salud_sistema is not None:
                print(f"  {salud_sistema.reporte_salud_para_usuario()}")
            else:
                print("  No puedo leer la salud del sistema ahora mismo.")
            continue

        if entrada == "/fixture":
            print(json.dumps(db.generar_fixture_regresion(), ensure_ascii=False, indent=2))
            continue

        if entrada == "/confirmar":
            if ultimo_resultado and ultimo_resultado.get("id_historial"):
                engine.confirmar_clasificacion(ultimo_resultado["id_historial"])
                print("  ✓ Confirmado.")
            else:
                print("  No hay una clasificación previa que confirmar.")
            continue

        if entrada == "/validar":
            if ultimo_resultado and ultimo_resultado.get("id_historial"):
                exito = engine.validar_clasificacion(
                    ultimo_resultado["id_historial"],
                    ultimo_resultado["texto_original"],
                    ultimo_resultado["categoria"],
                )
                if exito:
                    print(f"  ✓ Validado — '{ultimo_resultado['texto_original']}' quedó guardado como regla permanente.")
                else:
                    print("  ✗ No pude guardar la validación.")
            else:
                print("  No hay una clasificación previa que validar.")
            continue

        if entrada == "/reindexar":
            r = file_index.ejecutar_reindexado()
            if r["cortado_por"] == "en_progreso":
                print("  Ya hay un reindexado en curso en segundo plano — espera a que termine e inténtalo de nuevo.")
            else:
                aviso = f" (cortado por {r['cortado_por']})" if r["cortado_por"] else ""
                print(f"  Indexado: {r['indexados']} archivo(s){aviso}.")
            continue

        if entrada == "/reindexar_apps":
            r = app_index.ejecutar_reindexado_aplicaciones()
            print(f"  Aplicaciones descubiertas: {r['indexados']}.")
            continue

        if entrada.startswith("/corregir"):
            partes = entrada.split(maxsplit=1)
            if len(partes) < 2 or not ultimo_resultado:
                print("  Uso: /corregir CATEGORIA_CORRECTA  (debe haber una clasificación previa)")
                continue
            categoria_correcta = partes[1].strip().upper()
            exito = engine.corregir_clasificacion(
                ultimo_resultado["id_historial"],
                ultimo_resultado["texto_original"],
                ultimo_resultado["categoria"],
                categoria_correcta,
                ultimo_resultado["confianza"],
            )
            if exito:
                print(f"  ✓ Corregido — se aprende como {categoria_correcta}.")
            else:
                categorias = ", ".join(sorted(db.obtener_categorias_registradas()))
                print(f"  ✗ '{categoria_correcta}' no es una categoría que SARA reconozca. "
                      f"Categorías válidas ahora mismo: {categorias}")
            continue

        resultado = engine.clasificar(entrada)
        resultado["texto_original"] = entrada  # necesario para /corregir después
        ultimo_resultado = resultado

        # FIX 5: Bloque de dispatch para comandos dinámicos (EJECUTAR_PAQUETE)
        if habitos is not None and resultado["categoria"] == "EJECUTAR_PAQUETE":
            paquete_id = habitos.buscar_paquete_fijo_por_nombre(resultado["texto_norm"])
            if paquete_id:
                resultado_paquete = habitos.ejecutar_paquete(paquete_id)
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=resultado_paquete["exitoso"])
                _imprimir_respuesta(resultado, resultado_paquete["texto"], "✓" if resultado_paquete["exitoso"] else "→")
            else:
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=False)
                _imprimir_respuesta(resultado, "No encontré ese comando personalizado.")
            continue

        accion = productivity.registrar_desde_clasificacion(resultado)
        if accion:
            estado_accion = accion["estado"]
            if estado_accion == "creado":
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=True)
                _imprimir_respuesta(resultado, accion["texto"], "✓")
                continue
            if estado_accion == "completado":
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=True)
                _imprimir_respuesta(resultado, accion["texto"], "✓")
                continue
            if estado_accion == "listado":
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=True)
                _imprimir_respuesta(resultado, accion["texto"], "→")
                continue
            if estado_accion in ("no_encontrado", "ambiguo"):
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=False)
                _imprimir_respuesta(resultado, accion["texto"])
                continue
            if estado_accion == "candidato_eliminar":
                _imprimir_respuesta(resultado, accion["texto"])
                respuesta = _preguntar_confirmacion("Tú> ")
                afirmativo = respuesta in {"si", "sí", "s", "yes", "y"}
                if not afirmativo:
                    engine.ajustar_aprendizaje_por_resultado(resultado, exito=True)
                    print("  → Cancelado.")
                    continue
                confirmacion = productivity.confirmar_eliminacion(accion["tipo"], accion["id"])
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=confirmacion["exito"])
                marca = "✓" if confirmacion["exito"] else "→"
                _imprimir_respuesta(resultado, confirmacion["texto"], marca)
                continue

        # DESPUÉS:
        if generador_codigo is not None:
            accion_codigo = generador_codigo.resolver_generar_codigo(resultado)
            if accion_codigo:
                exito = accion_codigo["estado"] in ("generado", "reutilizado")
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=exito)
                _imprimir_respuesta(resultado, accion_codigo["texto"], "✓" if exito else "→")
                continue

        if informativo is not None:
            accion_informativa = informativo.resolver_pregunta_informativa(resultado)
            if accion_informativa:
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=accion_informativa.get("exito", True))
                _imprimir_respuesta(resultado, accion_informativa["texto"], "→")
                continue

        respuesta_info = system_info.responder_desde_clasificacion(resultado)
        if respuesta_info:
            engine.ajustar_aprendizaje_por_resultado(resultado, exito=respuesta_info.get("exito", True))
            _imprimir_respuesta(resultado, respuesta_info["texto"])
            continue

        accion_carpeta_especial = file_actions.ejecutar_carpeta_especial(resultado)
        if accion_carpeta_especial:
            engine.ajustar_aprendizaje_por_resultado(resultado, exito=accion_carpeta_especial["abierto"])
            marca = "✓" if accion_carpeta_especial["abierto"] else "→"
            _imprimir_respuesta(resultado, accion_carpeta_especial["texto"], marca)
            continue

        accion_aplicacion = app_actions.ejecutar_desde_clasificacion(resultado)  # ✅ PRIMERO DEFINE
        if accion_aplicacion:  # ✅ AHORA SÍ EXISTE
            engine.ajustar_aprendizaje_por_resultado(resultado, exito=accion_aplicacion["abierto"])
            marca = "✓" if accion_aplicacion["abierto"] else "→"
            _imprimir_respuesta(resultado, accion_aplicacion["texto"], marca)
            
            # Pilar 2: registrar ejecución para detectar combos
            if accion_aplicacion["abierto"] and habitos is not None:
                try:
                    mejor_app = accion_aplicacion.get("_mejor_coincidencia")
                    if mejor_app:
                        habitos.registrar_ejecucion(
                            "aplicacion",
                            mejor_app.get("ruta_ejecutable", ""),
                            mejor_app.get("nombre", "aplicación")
                        )
                        # DESPUÉS:
                        combo = habitos.detectar_y_registrar_combo()
                # DESPUÉS:
                except Exception as e:
                    _log_error("main", "registrar_ejecucion (aplicacion)", e)
            
            continue

        # DESPUÉS:
        # Búsquedas en navegador (Pilar 2 extendido)
        if web_navigator is not None and resultado["categoria"] == "BUSCAR_EN_NAVEGADOR":
            accion_navegador = web_navigator.ejecutar_desde_clasificacion(resultado)
            if accion_navegador:
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=accion_navegador["abierto"])
                marca = "✓" if accion_navegador["abierto"] else "→"
                _imprimir_respuesta(resultado, accion_navegador["texto"], marca)
                # NOTA: se quitó el registrar_uso_navegador() de aquí — según la
                # bitácora, web_navigator.abrir_en_navegador() ya lo hace
                # internamente al abrir con éxito. Esta línea lo contaba dos
                # veces en registro_ejecuciones. Pendiente confirmar en
                # web_navigator.py (no lo tengo) que en efecto solo queda ahí.
                continue
            else:
                # Si web_navigator rechaza, podría ser una aplicación real
                # (ej. "abre Spotify" mal clasificado como BUSCAR_EN_NAVEGADOR).
                # Se reintenta como ABRIR_APLICACION con el MISMO manejo
                # completo que el intento original más arriba en el dispatch
                # — antes esta llamada se hacía sin indentar (corría en CADA
                # turno, no solo aquí) y su resultado nunca se revisaba.
                resultado["categoria"] = "ABRIR_APLICACION"
                accion_aplicacion_fallback = app_actions.ejecutar_desde_clasificacion(resultado)
                if accion_aplicacion_fallback:
                    engine.ajustar_aprendizaje_por_resultado(resultado, exito=accion_aplicacion_fallback["abierto"])
                    marca = "✓" if accion_aplicacion_fallback["abierto"] else "→"
                    _imprimir_respuesta(resultado, accion_aplicacion_fallback["texto"], marca)
                    if accion_aplicacion_fallback["abierto"] and habitos is not None:
                        try:
                            mejor_app = accion_aplicacion_fallback.get("_mejor_coincidencia")
                            if mejor_app:
                                habitos.registrar_ejecucion(
                                    "aplicacion",
                                    mejor_app.get("ruta_ejecutable", ""),
                                    mejor_app.get("nombre", "aplicación"),
                                )
                                # DESPUÉS:
                                habitos.detectar_y_registrar_combo()
                        except Exception as e:
                            _log_error("main", "registrar_ejecucion (fallback navegador->app)", e)
                    continue
                # Ni navegador ni aplicación lo reconocieron — sigue el
                # dispatch normal (cierre/shell/archivos) con la categoría
                # ya mutada a ABRIR_APLICACION.

        resultado_cierre = app_actions.resolver_cierre(resultado)
        if resultado_cierre:
            estado = resultado_cierre["estado"]
            if estado == "protegido":
                _imprimir_respuesta(resultado, resultado_cierre["texto"])
                continue
            if estado in ("ambiguo", "no_encontrado"):
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=False)
                _imprimir_respuesta(resultado, resultado_cierre["texto"])
                continue
            # DESPUÉS:
            if estado == "candidato":
                _imprimir_respuesta(resultado, resultado_cierre["mensaje_confirmacion"])
                respuesta = _preguntar_confirmacion("Tú> ")
                afirmativo = respuesta in {"si", "sí", "s", "yes", "y"}
                if not afirmativo:
                    engine.ajustar_aprendizaje_por_resultado(resultado, exito=True)
                    print("  → Se canceló el cierre.")
                    continue
                cierre = app_actions.cerrar_procesos(resultado_cierre["pids"], timeout_segundos=TIMEOUT_CIERRE_NORMAL_SEGUNDOS)
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=True)
                _imprimir_respuesta(resultado, cierre["texto"])
                continue

        # DESPUÉS:
        resultado_comando = shell.resolver_comando_sistema(resultado)
        if resultado_comando:
            estado = resultado_comando["estado"]
            if estado == "requiere_consola":
                programa = resultado_comando["programa"]
                candidatos = app_index.buscar_aplicacion(programa, limite=1)
                if candidatos:
                    candidato = candidatos[0]
                    abierto = app_actions.abrir_aplicacion(candidato)
                    engine.ajustar_aprendizaje_por_resultado(resultado, exito=abierto)
                    texto = (f"Abriendo {candidato['nombre']} en su propia consola." if abierto
                             else f"Encontré {candidato['nombre']} pero no pude abrirlo.")
                    _imprimir_respuesta(resultado, texto, "✓" if abierto else "→")
                else:
                    engine.ajustar_aprendizaje_por_resultado(resultado, exito=False)
                    _imprimir_respuesta(resultado, resultado_comando["texto"])
                continue
            if estado in ("rechazado", "no_entendido"):
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=False)
                _imprimir_respuesta(resultado, resultado_comando["texto"])
                continue
            if estado == "ejecutado":
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=resultado_comando["exito"])
                _imprimir_respuesta(resultado, resultado_comando["texto"])
                continue
            # DESPUÉS:
            if estado == "candidato":
                _imprimir_respuesta(resultado, resultado_comando["mensaje_confirmacion"])
                escrito = ""
                if resultado_comando.get("reforzada"):
                    # Acción delicada: hay que ESCRIBIR la frase exacta (mayúsculas incluidas).
                    # Se usa _preguntar_texto porque _preguntar_confirmacion pasa todo a minúsculas.
                    escrito = _preguntar_texto("Tú> ")
                    afirmativo = escrito == resultado_comando.get("frase_confirmacion", "CONFIRMAR")
                else:
                    respuesta = _preguntar_confirmacion("Tú> ")
                    afirmativo = respuesta in {"si", "sí", "s", "yes", "y"}
                if not afirmativo:
                    engine.ajustar_aprendizaje_por_resultado(resultado, exito=True)
                    print("  → Se canceló el comando.")
                    continue
                ejec = shell.confirmar_y_ejecutar(
                    resultado_comando["comando"], resultado_comando["zona"],
                    resultado_comando["traduccion"], resultado["texto_original"],
                    confirmacion=escrito,   # lo que el usuario escribió, nunca una constante
                )
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=ejec["exitoso"])
                _imprimir_respuesta(resultado, ejec["resumen"])
                continue

        resultado_gestion = file_manager.resolver_gestion_archivo(resultado)
        if resultado_gestion:
            estado = resultado_gestion["estado"]
            if estado in ("no_entendido", "no_encontrado", "ambiguo", "rechazado", "no_escribible"):
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=False)
                _imprimir_respuesta(resultado, resultado_gestion["texto"])
                continue
            if estado == "ejecutado":
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=resultado_gestion.get("exito", True))
                _imprimir_respuesta(resultado, resultado_gestion["texto"])
                continue
            # DESPUÉS:
            if estado == "candidato":
                _imprimir_respuesta(resultado, resultado_gestion["mensaje_confirmacion"])
                respuesta = _preguntar_confirmacion("Tú> ")
                if respuesta not in {"si", "sí", "s", "yes", "y"}:
                    engine.ajustar_aprendizaje_por_resultado(resultado, exito=True)
                    print("  → Cancelado.")
                    continue
                confirmacion = file_manager.confirmar_gestion_archivo(resultado_gestion["accion"], resultado_gestion)
                engine.ajustar_aprendizaje_por_resultado(resultado, exito=confirmacion["exito"])
                _imprimir_respuesta(resultado, confirmacion["texto"])
                continue

        accion_abrir = file_actions.ejecutar_desde_clasificacion(resultado)
        if accion_abrir:
            engine.ajustar_aprendizaje_por_resultado(resultado, exito=accion_abrir["abierto"])
            marca = "✓" if accion_abrir["abierto"] else "→"
            _imprimir_respuesta(resultado, accion_abrir["texto"], marca)
            continue

        respuesta_archivo = file_index.responder_desde_clasificacion(resultado)
        if respuesta_archivo:
            engine.ajustar_aprendizaje_por_resultado(resultado, exito=respuesta_archivo.get("exito", True))
            _imprimir_respuesta(resultado, respuesta_archivo["texto"])
            
            # Pilar 2: registrar ejecución para detectar combos
            if respuesta_archivo.get("exito", False) and habitos is not None:
                try:
                    mejor_archivo = respuesta_archivo.get("_mejor_coincidencia")
                    if mejor_archivo:
                        habitos.registrar_ejecucion(
                            "archivo",
                            mejor_archivo.get("ruta", ""),
                            mejor_archivo.get("nombre", "archivo")
                        )
                        # DESPUÉS:
                        combo = habitos.detectar_y_registrar_combo()
                # DESPUÉS:
                except Exception as e:
                    _log_error("main", "registrar_ejecucion (archivo)", e)
            
            continue

        _imprimir_respuesta(resultado, f"categoría: {resultado['categoria']}")


if __name__ == "__main__":
    main()