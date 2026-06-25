const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  HeadingLevel, AlignmentType, BorderStyle, WidthType, ShadingType,
  VerticalAlign, PageNumber, PageOrientation
} = require('docx');
const fs = require('fs');

const W = 9360; // content width DXA (US Letter, 1" margins)

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 400, after: 160 },
    children: [new TextRun({ text, bold: true, size: 32, font: "Arial", color: "1F3864" })]
  });
}
function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 280, after: 120 },
    children: [new TextRun({ text, bold: true, size: 26, font: "Arial", color: "2E75B6" })]
  });
}
function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 200, after: 80 },
    children: [new TextRun({ text, bold: true, size: 22, font: "Arial", color: "1F497D" })]
  });
}
function p(text, opts = {}) {
  return new Paragraph({
    spacing: { before: 60, after: 60 },
    children: [new TextRun({ text, font: "Arial", size: 20, ...opts })]
  });
}
function bullet(text) {
  return new Paragraph({
    spacing: { before: 40, after: 40 },
    indent: { left: 360, hanging: 200 },
    children: [
      new TextRun({ text: "• ", font: "Arial", size: 20 }),
      new TextRun({ text, font: "Arial", size: 20 })
    ]
  });
}
function code(text) {
  return new Paragraph({
    spacing: { before: 40, after: 40 },
    indent: { left: 360 },
    children: [new TextRun({ text, font: "Courier New", size: 18, color: "C7254E" })]
  });
}
function sep() {
  return new Paragraph({
    spacing: { before: 120, after: 120 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "CCCCCC", space: 1 } },
    children: []
  });
}
function badgeNew(text) {
  return new Paragraph({
    spacing: { before: 60, after: 60 },
    children: [
      new TextRun({ text: "🆕 ", font: "Arial", size: 20 }),
      new TextRun({ text, font: "Arial", size: 20, bold: true, color: "107C10" })
    ]
  });
}

const border = { style: BorderStyle.SINGLE, size: 4, color: "D0D0D0" };
const borders = { top: border, bottom: border, left: border, right: border };
const hdrShading = { fill: "1F3864", type: ShadingType.CLEAR };
const altShading = { fill: "EBF3FB", type: ShadingType.CLEAR };

function tableHdr(cols, widths) {
  return new TableRow({
    tableHeader: true,
    children: cols.map((c, i) => new TableCell({
      borders,
      width: { size: widths[i], type: WidthType.DXA },
      shading: hdrShading,
      margins: { top: 80, bottom: 80, left: 120, right: 120 },
      children: [new Paragraph({ children: [new TextRun({ text: c, font: "Arial", size: 18, bold: true, color: "FFFFFF" })] })]
    }))
  });
}
function tableRow(cells, widths, shade = false) {
  return new TableRow({
    children: cells.map((c, i) => new TableCell({
      borders,
      width: { size: widths[i], type: WidthType.DXA },
      shading: shade ? altShading : { fill: "FFFFFF", type: ShadingType.CLEAR },
      margins: { top: 80, bottom: 80, left: 120, right: 120 },
      children: [new Paragraph({ children: [new TextRun({ text: c, font: "Arial", size: 18 })] })]
    }))
  });
}
function mkTable(headers, rows, widths) {
  return new Table({
    width: { size: W, type: WidthType.DXA },
    columnWidths: widths,
    rows: [
      tableHdr(headers, widths),
      ...rows.map((r, i) => tableRow(r, widths, i % 2 === 1))
    ]
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// DOCUMENT CONTENT
// ═══════════════════════════════════════════════════════════════════════════════

const children = [

  // ── PORTADA ─────────────────────────────────────────────────────────────────
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 600, after: 200 },
    children: [new TextRun({ text: "SARA", font: "Arial", size: 72, bold: true, color: "1F3864" })]
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 120 },
    children: [new TextRun({ text: "Sistema Autónomo de Razonamiento Artificial", font: "Arial", size: 28, color: "2E75B6" })]
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 80 },
    children: [new TextRun({ text: "Versión 0.4.0", font: "Arial", size: 24, bold: true, color: "1F497D" })]
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 400 },
    children: [new TextRun({ text: "Documento Maestro de Contexto Completo — System Prompt para Asistencia de Desarrollo", font: "Arial", size: 20, italics: true, color: "595959" })]
  }),

  // Recuadro instrucción IA
  mkTable(
    ["INSTRUCCIÓN PARA EL MODELO DE IA QUE LEE ESTE DOCUMENTO"],
    [["Eres un asistente experto en el desarrollo de SARA. Tu función es colaborar con el desarrollador principal (Sergio) en correcciones de bugs, nuevas funcionalidades, refactorizaciones y mejoras de arquitectura. Este documento es tu fuente de verdad absoluta sobre el estado del sistema. Conoces en profundidad cada módulo, su lógica interna, sus interacciones y las decisiones de diseño tomadas. Cuando se te muestre código o se te haga una pregunta sobre SARA, aplica primero el contexto de este documento antes de responder.\n\nRegla crítica: Nunca generes fragmentos de código parciales. Siempre entrega bloques completos y reemplazables. Si modificas una función, incluye la función entera. Aplica fixes generales, nunca parches de caso único.\n\nFormato de retorno estándar: {\"exito\": bool, \"mensaje\": str}"]],
    [W]
  ),

  // ── SECCIÓN 1 ────────────────────────────────────────────────────────────────
  h1("1. IDENTIDAD Y FILOSOFÍA DEL PROYECTO"),
  p("SARA es un agente autónomo de escritorio desarrollado en Python, diseñado para operar principalmente offline, aprender de cada interacción del usuario, controlar el sistema operativo Windows y escalar a modelos de IA externos únicamente cuando la respuesta local es insuficiente. Es portable, eficiente y no requiere configuración manual por parte del usuario final."),
  p("La versión 0.4.0 introduce el Subsistema PRAXIS, que le da a SARA manos reales: la capacidad de percibir el estado del equipo, actuar sobre él y verificar que la acción tuvo el efecto esperado, cerrando el ciclo que faltaba en versiones anteriores.", { bold: true }),

  h2("1.1 Clasificación técnica"),
  bullet("Agente autónomo de escritorio (offline-first)"),
  bullet("Asistente personal local con aprendizaje persistente en SQLite"),
  bullet("Sistema multimodelo con orquestación inteligente (Qwen local + Groq + Gemini + DeepSeek)"),
  bullet("Compatible con Windows como plataforma primaria (macOS/Linux secundarios)"),
  bullet("Distribución portable — un archivo .bat que instala y arranca todo sin intervención manual"),
  bullet("Interfaz visual nativa opcional via Electron"),
  bullet("🆕 Subsistema PRAXIS — percepción, ejecución y verificación del sistema operativo"),

  h2("1.2 Pilares de diseño"),
  mkTable(
    ["Pilar", "Descripción"],
    [
      ["Privacidad", "Los datos del usuario nunca salen del equipo salvo decisión explícita. Todo el procesamiento primario es local."],
      ["Portabilidad", "Funciona en cualquier máquina Windows modesta (hardware desde 2015) sin instalación manual."],
      ["Eficiencia", "Prioriza respuestas locales en milisegundos (BD + índice) antes de escalar a IAs externas."],
      ["Modularidad", "Cada módulo tiene una sola responsabilidad. Pueden evolucionar, reemplazarse o desactivarse independientemente."],
      ["Aprendizaje continuo", "Cada interacción confirmada por el usuario mejora el sistema permanentemente."],
      ["Resiliencia", "SARA arranca siempre aunque módulos opcionales fallen. Errores de inicialización son warnings, nunca fatales."],
      ["🆕 Percepción activa", "PRAXIS permite a SARA verificar el estado real del sistema antes y después de actuar, nunca asumiendo éxito por ausencia de excepción."],
    ],
    [2200, 7160]
  ),

  h2("1.4 Principios de desarrollo"),
  bullet("Soluciones universales y escalables — nunca parches para un caso específico."),
  bullet("Eficiencia sobre peso — preferir difflib+BD sobre llamadas a IA cuando la respuesta está disponible localmente."),
  bullet("Nunca romper funcionalidad existente al agregar features."),
  bullet("El marcador __DESTINO__ no debe ser normalizado, procesado por context, searcher, ni social."),
  bullet("En modo voz, ningún flujo puede usar input() directamente."),
  bullet("SARA Version 1.0.0 está reservada para cuando la GUI y la destilación del modelo estén completas."),
  bullet("La GUI es opcional — SARA debe funcionar completamente en modo terminal."),
  bullet("🆕 PRAXIS es opcional — cada módulo del subsistema degrada con gracia si no está disponible."),
  bullet("🆕 Patrón Acción+Verificación obligatorio: toda acción de sistema debe verificar su resultado con perceptor.py."),

  // ── SECCIÓN 2 ────────────────────────────────────────────────────────────────
  h1("2. ARQUITECTURA DE MÓDULOS"),
  p("La versión 0.4.0 añade el Subsistema PRAXIS: cuatro módulos nuevos (perceptor.py, intent_router.py, shell.py, sentinel.py) que trabajan en coordinación para darle a SARA percepción activa del entorno y capacidad de ejecución controlada sobre el sistema operativo."),

  h2("2.1 Tabla de módulos (actualizada v0.4.0)"),
  mkTable(
    ["Módulo", "Responsabilidad principal", "Dependencias clave"],
    [
      ["sara.py", "Orquestador principal, bucle de interacción, manejo de resultados, caché de intenciones, emisión de eventos GUI. 🆕 Arranca sentinel en hilo daemon.", "Todos los módulos"],
      ["brain.py", "Motor de decisiones, scoring multicapa, ranking unificado, árbitro Qwen. 🆕 Capa -2 (intent_router) y despacho shell_info en Capa -1.", "embeddings, database, external_service, file_intent, searcher, intent_router, shell"],
      ["server.py", "Servidor FastAPI + WebSocket. Bridge entre SARA y la GUI Electron. Emite eventos en tiempo real.", "fastapi, uvicorn, config, external_service"],
      ["🆕 shell.py", "Motor de ejecución controlada de comandos CMD/PowerShell. Listas blanca/negra/amarilla. Extracción de info del sistema, instalaciones, reproducción multimedia.", "config, logger, perceptor, io_manager"],
      ["🆕 perceptor.py", "Verificación del estado real del sistema antes y después de actuar. 17 funciones de percepción en 3 niveles: existencia, código y servicios.", "utils, logger, database, psutil (opcional)"],
      ["🆕 intent_router.py", "Discriminador semántico de intención de acción. Dos capas: verbos deterministas (Capa A) + embeddings semánticos (Capa B). Elimina el bug 'pon música → carpeta'.", "utils, config, embeddings, logger, perceptor"],
      ["🆕 sentinel.py", "Observador proactivo del sistema en hilo daemon. Vigila disco, RAM, batería, Ollama. Emite alertas sin actuar por sí solo.", "perceptor, shell, logger, config, io_manager"],
      ["file_intent.py", "Detección de intención sobre archivos, carpetas y apps. 🆕 Guardia PRAXIS: respeta clasificación REPRODUCIR de intent_router.", "database, utils, intent_router"],
      ["external_service.py", "Integración con IAs externas: Qwen/Ollama, Groq, Gemini, DeepSeek. Pipeline multi-agente.", "config, logger, database"],
      ["learning.py", "Aprendizaje persistente de comandos y preguntas, deduplicación semántica.", "database, embeddings, utils"],
      ["database.py", "SQLite WAL: comandos, conocimientos, historial, índice de archivos, vectores, caché de intenciones.", "config"],
      ["commands.py", "Ejecución de comandos OS, apertura de apps/webs/carpetas. 🆕 Tipos 'shell' y 'shell_info' enrutan a shell.py.", "database, logger, utils, shell"],
      ["sistema.py", "Control de volumen, multimedia, brillo, energía vía ctypes. Permanece para hardware puro donde CMD no es equivalente.", "logger"],
      ["splitter.py", "División de entradas compuestas, detección de patrones 'abre X en Y', múltiples destinos.", "utils, logger"],
      ["searcher.py", "Detección y construcción de búsquedas web por plataforma.", "utils"],
      ["embeddings.py", "Vectores semánticos sentence-transformers, similitud coseno, precalentamiento.", "config, logger"],
      ["context.py", "Contexto conversacional entre turnos, resolución de pronombres.", "utils"],
      ["social.py", "Saludos, despedidas, correcciones, interacciones sociales.", "database, utils"],
      ["voice.py", "STT offline (Vosk/Whisper) + TTS (Piper/edge-tts/pyttsx3) con fallbacks automáticos.", "config, logger, io_manager"],
      ["file_watcher.py", "Indexación inicial y monitoreo continuo de archivos.", "database, logger"],
      ["io_manager.py", "Entrada/salida unificada para modo texto y modo voz, prompts, confirmaciones.", "voice, logger"],
      ["logger.py", "Sistema de logging por niveles con colores en terminal.", "database"],
      ["utils.py", "Normalizar texto, similitud difflib, helpers generales.", "—"],
      ["config.py", "Configuración centralizada. 🆕 Constantes SHELL_*, VERBOS_REPRODUCCION, SENTINEL_*.", "—"],
      ["validator.py", "Filtro temprano de entradas sin sentido antes del pipeline.", "utils, logger"],
    ],
    [2200, 4600, 2560]
  ),

  // ── SECCIÓN 14 — SUBSISTEMA PRAXIS (nueva sección completa) ──────────────────
  h1("14. SUBSISTEMA PRAXIS — Sistema Nervioso de SARA v0.4.0"),

  new Paragraph({
    spacing: { before: 80, after: 80 },
    children: [new TextRun({ text: "🆕 Nueva funcionalidad completa en v0.4.0 — Percepción, Razonamiento, Acción, eXecución, Inteligencia de Sistema", font: "Arial", size: 20, bold: true, color: "107C10" })]
  }),

  p("Hasta v0.3.0, SARA tenía cerebro (brain.py), voz (voice.py) y memoria (database.py) — pero no tenía manos que sintieran lo que tocaban. PRAXIS es el sistema que le permite percibir el estado real de la máquina, actuar sobre ella y verificar que la acción tuvo el efecto esperado, cerrando el ciclo percepción → acción → verificación que faltaba."),

  p("PRAXIS se compone de cuatro módulos que trabajan en coordinación:"),
  mkTable(
    ["Módulo", "Rol", "Analogía"],
    [
      ["shell.py", "Motor de ejecución controlada (CMD/PowerShell)", "Las manos"],
      ["perceptor.py", "Verificación de estado antes y después de actuar", "Los cinco sentidos"],
      ["intent_router.py", "Discriminador semántico de intención de acción", "El discriminador (evita errores de interpretación)"],
      ["sentinel.py", "Observador proactivo en hilo daemon", "El instinto (vigila sin que lo pidan)"],
    ],
    [2200, 4560, 2600]
  ),

  h2("14.1 Módulo shell.py — Motor de Ejecución Controlada"),
  p("shell.py es el backend de ejecución de comandos de sistema operativo. No es un sustituto de commands.py — es su motor de bajo nivel para comandos de shell puro y el que permite a SARA obtener información real del sistema."),

  h3("Arquitectura de riesgo (3 zonas)"),
  mkTable(
    ["Zona", "Color", "Comportamiento", "Ejemplos"],
    [
      ["Lista blanca", "🟢 Verde", "Ejecución inmediata, sin confirmación. Solo lectura.", "systeminfo, wmic, ipconfig, tasklist, pip list"],
      ["Lista negra", "🔴 Roja", "Bloqueados siempre, sin excepción posible.", "format, del /f /s /q, rd /s /q C:\\, reg delete HKLM"],
      ["Zona amarilla", "🟡 Amarilla", "SARA describe la acción y solicita confirmación.", "taskkill, shutdown, pip install, schtasks /delete"],
      ["Scripts generados", "🔵 Azul", "Preview del contenido + confirmación obligatoria.", "Cualquier .py, .ps1, .bat generado por SARA"],
    ],
    [1800, 1200, 3360, 2900]
  ),

  h3("API pública de shell.py"),
  mkTable(
    ["Función", "Descripción", "Zona de riesgo"],
    [
      ["ejecutar_controlado(cmd, contexto)", "Punto de entrada principal. Clasifica, confirma si aplica y ejecuta.", "Automática según listas"],
      ["ejecutar_script(ruta, interprete)", "Ejecuta un script generado por SARA con preview + confirmación.", "Siempre amarilla"],
      ["info_ram()", "RAM total, usada y libre en tiempo real.", "Blanca"],
      ["info_cpu()", "Nombre del procesador, núcleos y uso actual.", "Blanca"],
      ["info_disco(unidad)", "Espacio libre y total de una unidad.", "Blanca"],
      ["info_ip()", "Dirección IP local y nombre del equipo.", "Blanca"],
      ["info_procesos(limite)", "Top N procesos por consumo de CPU.", "Blanca"],
      ["info_bateria()", "Porcentaje de carga y estado (cargando/descargando).", "Blanca"],
      ["info_usb()", "Dispositivos USB conectados actualmente.", "Blanca"],
      ["version_herramienta(nombre)", "Versión de python, node, git, pip, etc.", "Blanca"],
      ["matar_proceso(nombre)", "Termina un proceso por nombre (taskkill /f).", "Amarilla — confirmación"],
      ["apagar_equipo(minutos)", "Apaga el equipo con confirmación obligatoria.", "Amarilla — confirmación"],
      ["reiniciar_equipo(minutos)", "Reinicia el equipo con confirmación.", "Amarilla — confirmación"],
      ["instalar_pip(paquete)", "Instala paquete pip con verificación previa y posterior.", "Amarilla — confirmación"],
      ["instalar_winget(app)", "Instala app con Windows Package Manager.", "Amarilla — confirmación"],
      ["reproducir(plataforma, ruta_local)", "Abre Spotify/streaming o reproduce archivo local.", "Blanca"],
      ["reproducir_spotify()", "Abre Spotify o lo trae al frente si ya corre.", "Blanca"],
      ["reproducir_archivo_audio(ruta)", "Reproduce audio local con el reproductor predeterminado.", "Blanca"],
      ["ejecutar_tests(directorio)", "Ejecuta pytest con resumen parseado.", "Amarilla — confirmación"],
      ["listar_paquetes_pip()", "Lista paquetes pip instalados.", "Blanca"],
      ["info_git(directorio)", "Estado del repositorio git actual.", "Blanca"],
      ["diagnostico_sistema()", "Reporte completo: RAM, disco, batería, Ollama. Responde '¿cómo estás?'.", "Blanca"],
      ["clasificar_riesgo(comando)", "Retorna 'blanca', 'negra' o 'amarilla' para cualquier comando.", "N/A"],
    ],
    [3200, 4360, 1800]
  ),

  h2("14.2 Módulo perceptor.py — Los Cinco Sentidos"),
  p("perceptor.py verifica el estado real del sistema ANTES y DESPUÉS de que SARA actúe. No ejecuta acciones — solo observa, confirma y reporta. Materializa el patrón Acción+Verificación que hace que SARA siempre sepa que completó lo que dijo que haría."),

  h3("Patrón universal: Percepción previa → Acción → Percepción posterior → Respuesta honesta"),
  p("Toda acción relevante en SARA sigue este patrón desde v0.4.0. SARA ya no asume éxito solo porque subprocess.Popen() no lanzó una excepción."),

  h3("17 funciones organizadas en 3 niveles"),
  mkTable(
    ["Nivel", "Función", "Descripción"],
    [
      ["Nivel 1 — Existencia", "existe_archivo(ruta)", "Verifica si un archivo existe físicamente en disco."],
      ["Nivel 1 — Existencia", "existe_carpeta(ruta)", "Verifica si una carpeta existe físicamente."],
      ["Nivel 1 — Existencia", "existe_archivo_o_carpeta_similar(ruta)", "Si no existe, busca en indice_archivos y sugiere alternativas."],
      ["Nivel 1 — Existencia", "existe_app_instalada(nombre)", "Verifica si un ejecutable está en el PATH (shutil.which)."],
      ["Nivel 1 — Existencia", "app_esta_corriendo(nombre_proceso)", "Lista procesos activos y busca el nombre (requiere psutil)."],
      ["Nivel 2 — Código", "validar_sintaxis_python(codigo)", "Verifica sintaxis vía ast.parse(). Sin ejecutar el código."],
      ["Nivel 2 — Código", "compilar_check(codigo)", "Verificación más profunda con compile(). Detecta 'return' fuera de función."],
      ["Nivel 2 — Código", "verificar_script_generado(codigo)", "Combina las dos anteriores. Punto único de entrada para sara.py."],
      ["Nivel 3 — Servicios", "puerto_libre(puerto)", "Verifica si un puerto TCP está libre (socket)."],
      ["Nivel 3 — Servicios", "servicio_responde(url)", "HTTP GET a la URL. Usa urllib estándar, sin requests."],
      ["Nivel 3 — Servicios", "ollama_esta_vivo(puerto)", "Verificación específica de Ollama en puerto 11434."],
      ["Nivel 3 — Servicios", "espacio_disco_libre(unidad)", "Espacio libre y alerta si < 10% (shutil.disk_usage)."],
      ["Nivel 3 — Servicios", "ram_disponible()", "RAM disponible y alerta si uso > 90% (requiere psutil)."],
      ["Paquetes", "paquete_pip_instalado(nombre)", "Verifica instalación y versión vía importlib.metadata. Sin subprocess."],
      ["Paquetes", "comando_disponible(nombre)", "Alias semántico de existe_app_instalada() para herramientas CLI."],
      ["Post-acción", "verificar_resultado_apertura(nombre, proceso, espera)", "Confirma que un proceso arrancó tras subprocess.Popen(). Cierra el ciclo."],
      ["Audio", "es_archivo_audio(ruta)", "Verifica extensión de audio (.mp3, .wav, .flac, etc.). Soporte a intent_router."],
    ],
    [2200, 3200, 3960]
  ),

  h2("14.3 Módulo intent_router.py — El Discriminador"),
  p("intent_router.py intercepta la entrada del usuario ANTES de que file_intent.detectar_intencion_archivo() la evalúe. Resuelve el bug crítico documentado donde 'pon música' abría la carpeta Música en lugar de reproducir audio."),

  h3("Bug resuelto: 'pon música → carpeta Música'"),
  p("Causa raíz: file_intent.py en Capa 0.5 detectaba 'música' como keyword de CARPETAS_SISTEMA y devolvía confianza 1.0, ganando el ranking antes de que brain.py pudiera clasificar 'pon' como verbo de reproducción. intent_router intercepta en Capa -2, antes de ambas."),

  h3("Dos capas en cascada"),
  mkTable(
    ["Capa", "Mecanismo", "Velocidad", "Cobertura"],
    [
      ["Capa A — Determinista", "Verbo líder de la entrada. Sets de verbos por categoría (frozenset).", "<1ms, sin I/O", "~90% de los casos reales"],
      ["Capa B — Semántica", "Similitud coseno contra prototipos por categoría (embeddings.py).", "~50ms", "Casos ambiguos restantes"],
    ],
    [2200, 3560, 1600, 1960]
  ),

  h3("Categorías de intención"),
  mkTable(
    ["Constante", "Valor", "Ejemplos de activación"],
    [
      ["CAT_REPRODUCIR", "'REPRODUCIR'", "'pon música', 'reproduce algo', 'abre Spotify', 'toca una canción'"],
      ["CAT_ABRIR", "'ABRIR'", "'abre Chrome', 'lanza el explorador', 'entra a documentos'"],
      ["CAT_BUSCAR", "'BUSCAR'", "'busca el archivo', 'encuentra el proyecto', 'dónde está X'"],
      ["CAT_SHELL_INFO", "'SHELL_INFO'", "'cuánta RAM', 'mi IP', 'qué procesos corren', 'espacio en disco'"],
      ["CAT_SHELL_ACCION", "'SHELL_ACCION'", "'cierra Chrome', 'instala pandas', 'apaga el equipo'"],
      ["CAT_CODIGO", "'CODIGO'", "'crea un script', 'escríbeme un programa en Python'"],
      ["CAT_DESCONOCIDA", "'DESCONOCIDA'", "Nada coincide → flujo normal de brain.py sin cambios"],
    ],
    [2400, 1800, 5160]
  ),

  h3("API pública de intent_router.py"),
  mkTable(
    ["Función", "Descripción"],
    [
      ["clasificar(texto) → dict", "Punto de entrada principal. Retorna categoría, confianza, método y texto normalizado. Nunca lanza excepciones."],
      ["es_reproduccion(resultado) → bool", "Helper: True si la categoría es CAT_REPRODUCIR."],
      ["es_shell(resultado) → bool", "Helper: True si es SHELL_INFO o SHELL_ACCION."],
      ["requiere_confirmacion(resultado) → bool", "Helper: True si es SHELL_ACCION (zona amarilla en shell.py)."],
      ["obtener_plataforma_streaming(resultado) → str|None", "Retorna 'spotify', 'youtube', etc. si se detectó una plataforma. None si es reproducción local."],
      ["diagnosticar(texto) → dict", "Versión extendida con razonamiento: incluye resultado de Capa A, Capa B y explicación legible. Para logs y debug."],
    ],
    [3200, 6160]
  ),

  h2("14.4 Módulo sentinel.py — El Instinto Proactivo"),
  p("sentinel.py corre en un hilo daemon de baja frecuencia (configurable, por defecto 45s) y vigila señales del sistema sin ser invocado. Es la pieza que cruza la línea de 'asistente que responde' a 'presencia que cuida la máquina'. Nunca actúa por sí solo — solo observa y reporta."),

  h3("Señales vigiladas"),
  mkTable(
    ["Señal", "Cómo se detecta", "Umbral de alerta", "Qué hace SARA"],
    [
      ["Disco casi lleno", "perceptor.espacio_disco_libre()", "< 10% libre (SENTINEL_UMBRAL_DISCO_PCT)", "Avisa proactivamente al usuario"],
      ["RAM crítica", "perceptor.ram_disponible()", "> 90% uso (SENTINEL_UMBRAL_RAM_PCT)", "Sugiere cerrar apps pesadas"],
      ["Batería baja", "shell.info_bateria()", "< 20% carga (SENTINEL_UMBRAL_BATERIA_PCT)", "Avisa con urgencia si sigue bajando"],
      ["Ollama caído", "perceptor.ollama_esta_vivo()", "No responde en puerto 11434", "Avisa antes de que el usuario intente usar Qwen"],
      ["Instalación terminada", "Monitorea proceso de pip lanzado por shell.py", "returncode detectado", "'Ya terminó de instalarse X'"],
    ],
    [2200, 2800, 2160, 2200]
  ),

  h3("API pública de sentinel.py"),
  mkTable(
    ["Función", "Descripción"],
    [
      ["iniciar()", "Arranca el hilo daemon de vigilancia. Llamado desde sara.py en inicializar()."],
      ["detener()", "Señal de parada segura al hilo (para tests y cierre limpio)."],
      ["esta_activo() → bool", "True si el hilo daemon está corriendo."],
      ["obtener_alertas_activas() → list", "Lista de alertas activas en el momento actual (sin esperar al próximo ciclo)."],
    ],
    [3200, 6160]
  ),

  h2("14.5 Integración de PRAXIS en brain.py"),
  p("La Capa -2 (intent_router) se ejecuta ANTES de la Capa -1 (MAPA_COMANDOS_SISTEMA), que a su vez ocurre ANTES de la Capa 0 (buscar_comando). Este orden garantiza que las intenciones de reproducción y shell sean resueltas antes de llegar a file_intent."),

  h3("Flujo completo de brain.procesar() en v0.4.0"),
  mkTable(
    ["Capa", "Qué hace", "Cuándo retorna"],
    [
      ["Paso 0", "_resolver_intencion_con_destino() — intercepta marcadores __DESTINO__", "Siempre que el texto empiece con __DESTINO__"],
      ["Paso 0b", "_resolver_intencion_con_carpeta_ctx() — intercepta __CARPETA_CTX__", "Siempre que el texto empiece con __CARPETA_CTX__"],
      ["Caché", "Consulta cache_intenciones en BD (TTL vigente)", "Cache hit con TTL vigente"],
      ["🆕 Capa -2", "intent_router.clasificar() — discriminador semántico. CAT_REPRODUCIR → shell.reproducir(). CAT_SHELL_ACCION → shell.ejecutar_controlado().", "Si categoría es REPRODUCIR, SHELL_INFO sin mapa o SHELL_ACCION"],
      ["Capa -1", "MAPA_COMANDOS_SISTEMA_ORDENADO — frases de shell_info despachan a shell.py directamente (info_ram, info_disco, etc.)", "Frases exactas del mapa con confianza 0.95"],
      ["Capa 0", "buscar_comando() — scoring difflib + semántico contra todos los comandos en BD", "Si score >= UMBRAL_COMANDO, agrega al ranking"],
      ["Capa 0.5", "file_intent.detectar_intencion_archivo() — con guardia PRAXIS que respeta CAT_REPRODUCIR", "Agrega candidatos al ranking"],
      ["Ranking", "Ordena candidatos por score. Margen < 0.08: árbitro Qwen.", "Siempre retorna desde aquí si hay candidatos"],
      ["Capa 1", "searcher.analizar() — detecta patrones de búsqueda", "Si es_busqueda=True"],
      ["Capa 2", "detectar_intencion() — clasifica como pregunta/comando/desconocido", "No retorna, solo clasifica"],
      ["Capa 3", "buscar_respuesta() / retorno comando desconocido", "Siempre — último recurso"],
    ],
    [1600, 4760, 2900]
  ),

  h2("14.6 Modificación en file_intent.py"),
  p("Se añadió una guardia PRAXIS al inicio de detectar_intencion_archivo(). Si intent_router ya clasificó la entrada como CAT_REPRODUCIR, file_intent devuelve None inmediatamente sin evaluar CARPETAS_SISTEMA. Esto elimina el bug de raíz sin modificar la lógica de detección de archivos."),
  code("# Al inicio de detectar_intencion_archivo(), dentro del try:"),
  code("import intent_router as _ir"),
  code("if _ir.clasificar(texto_limpio).get('categoria') == _ir.CAT_REPRODUCIR:"),
  code("    return None  # No interferir con reproducción"),

  h2("14.7 Modificaciones en commands.py"),
  p("Se añadieron los tipos 'shell' y 'shell_info' en ejecutar_comando(). Cuando un comando aprendido en BD tiene tipo='shell', commands.py delega a shell.ejecutar_controlado() en lugar de usar subprocess directamente."),

  h2("14.8 Modificaciones en sara.py"),
  p("Sara.py recibió cuatro modificaciones para integrar PRAXIS:"),
  bullet("Import defensivo de shell, intent_router y perceptor al inicio (PRAXIS_DISPONIBLE = True/False)."),
  bullet("Comandos internos nuevos: /sistema, 'instala X', 'cierra X', 'versión de X'."),
  bullet("Arranque de sentinel.iniciar() en inicializar(), en hilo daemon, justo antes de return True."),
  bullet("Las respuestas de shell_info retornan tipo='respuesta' de brain.py, manejadas por la rama existente de _manejar_resultado() sin cambios adicionales."),

  h2("14.9 Constantes nuevas en config.py"),
  mkTable(
    ["Constante", "Valor por defecto", "Descripción"],
    [
      ["SHELL_LISTA_BLANCA", "frozenset de ~25 comandos", "Prefijos de comandos de solo lectura — ejecución sin confirmación."],
      ["SHELL_LISTA_NEGRA", "frozenset de ~18 patrones", "Comandos destructivos — bloqueados siempre, sin excepción."],
      ["SHELL_ZONA_AMARILLA", "frozenset de ~18 prefijos", "Comandos con efecto en el sistema — requieren confirmación."],
      ["VERBOS_REPRODUCCION", "frozenset de ~12 verbos", "Verbos que indican intención de reproducir (intent_router)."],
      ["SENTINEL_ACTIVO", "True", "Activa/desactiva el hilo daemon de sentinel."],
      ["SENTINEL_INTERVALO_SEGUNDOS", "45", "Frecuencia de chequeo del daemon de sentinel."],
      ["SENTINEL_UMBRAL_DISCO_PCT", "10.0", "% de espacio libre mínimo antes de alerta de disco."],
      ["SENTINEL_UMBRAL_RAM_PCT", "90.0", "% de uso de RAM máximo antes de alerta de memoria."],
      ["SENTINEL_UMBRAL_BATERIA_PCT", "20", "% de carga de batería mínimo antes de alerta."],
    ],
    [3200, 2360, 3800]
  ),

  h2("14.10 Impacto en recursos"),
  mkTable(
    ["Módulo", "RAM adicional", "Disco adicional", "Dependencias nuevas"],
    [
      ["shell.py", "0 MB", "~8 KB", "Ninguna (subprocess es stdlib)"],
      ["perceptor.py", "<5 MB (solo al correr ast.parse)", "~6 KB", "Ninguna (ast, socket, os, shutil son stdlib). psutil opcional."],
      ["intent_router.py", "0 MB extra", "~5 KB", "Ninguna — reutiliza embeddings.py existente"],
      ["sentinel.py", "~3-5 MB (hilo daemon)", "~4 KB", "psutil (ya contemplado en roadmap, ~2 MB)"],
      ["TOTAL PRAXIS", "~10 MB", "~25 KB", "Solo psutil (opcional)"],
    ],
    [2200, 2000, 2000, 3160]
  ),

  // ── SECCIÓN 15 — BUGS RESUELTOS (actualizada) ───────────────────────────────
  h1("15. BUGS RESUELTOS Y ESTADO ACTUAL"),

  h2("15.1 Bugs resueltos en el core ✅"),
  mkTable(
    ["Bug", "Módulo", "Solución implementada"],
    [
      ["Carpetas sistema no detectaban rutas OneDrive", "file_intent.py", "_resolver_ruta_carpeta() con múltiples candidatos incluyendo OneDrive"],
      ["Apps sistema llamaban al árbitro", "file_intent.py + brain.py", "APPS_SISTEMA con prioridad absoluta score 1.0"],
      ["Candidato único llamaba al árbitro innecesariamente", "brain.py", "Rama len(candidatos)==1 sin árbitro, con confirmación si score<0.75"],
      ["Post-arbitraje creaba comandos duplicados", "sara.py + database.py", "agregar_palabras_clave_comando() en lugar de nuevo registro"],
      ["abre X en Y interpretado como búsqueda", "splitter.py + brain.py", "Marcador __DESTINO__, interceptado ANTES de normalizar en brain.procesar()"],
      ["__DESTINO__ llegaba a context.resolver() y se colgaba", "sara.py", "Verificación cambiada de entrada a entrada_procesada"],
      ["Logs internos de apps Electron en terminal SARA", "commands.py", "DEVNULL en stdout/stderr/stdin + CREATE_NO_WINDOW en todo subprocess"],
      ["SARA se colgaba sin avisar en entradas problemáticas", "sara.py", "Watchdog threading con timeout 5s (aviso) y 15s (abortar + continuar)"],
      ["🆕 'pon música' abría la carpeta Música en lugar de reproducir", "file_intent.py + brain.py", "intent_router.py Capa -2 intercepta antes de file_intent. Guardia PRAXIS en detectar_intencion_archivo(). Bug eliminado de raíz."],
      ["🆕 MAPA_COMANDOS_SISTEMA con frases ambiguas ('ram' tapaba 'cuanta ram')", "brain.py", "MAPA_COMANDOS_SISTEMA_ORDENADO: pre-ordenado por longitud descendente al cargar el módulo."],
      ["🆕 SARA asumía éxito en apertura solo porque Popen() no lanzó excepción", "commands.py", "verificar_resultado_apertura() en perceptor.py cierra el ciclo con verificación real del proceso."],
    ],
    [3200, 2360, 3800]
  ),

  h2("15.2 Advertencias conocidas ⚠️"),
  mkTable(
    ["Issue", "Estado", "Mitigación actual"],
    [
      ["Whisper tiny impreciso en STT", "Conocido", "Configurar WHISPER_MODEL='base' en config.py"],
      ["Qwen 0.6b devuelve template en lugar de respuesta", "Ocasional", "verificar_peticion_qwen() detecta y usa entrada original"],
      ["Groq puede tardar 10-15s en código complejo", "Normal", "Sin solución sin GPU; watchdog avisa al usuario"],
      ["GUI no tiene botón funcional para activar voz", "Pendiente", "Activar via comando '/voz' en el chat."],
      ["🆕 sentinel.py requiere psutil para alertas de RAM y procesos", "Degradación controlada", "Sin psutil, sentinel omite esas verificaciones y continúa con disco y batería."],
      ["🆕 shell.py — PowerShell puede no estar en PATH en sistemas muy recortados", "Raro en Windows moderno", "shell.py verifica shutil.which() antes de ejecutar y retorna error descriptivo."],
    ],
    [3200, 1800, 4360]
  ),

  // ── SECCIÓN 16 — CONFIGURACIÓN actualizada ──────────────────────────────────
  h1("16. CONFIGURACIÓN (config.py) — Variables completas v0.4.0"),
  mkTable(
    ["Variable", "Valor por defecto", "Descripción"],
    [
      ["GUI_PORT", "8765", "Puerto TCP del servidor WebSocket de la GUI"],
      ["VERSION", "0.4.0", "Versión actual de SARA"],
      ["DB_NAME", "sara.db", "Ruta de la base de datos SQLite"],
      ["MODO_MOCK", "False", "Si True, búsquedas web retornan resultados simulados"],
      ["MOSTRAR_CONFIANZA", "True", "Muestra el % de confianza después de cada respuesta"],
      ["BUSQUEDA_EXTERNA_ACTIVA", "True", "Activa búsquedas web reales"],
      ["MODO_VOZ", "False", "Activa modo voz al arranque"],
      ["USAR_QWEN", "True", "Activa el modelo Qwen local via Ollama"],
      ["QWEN_MODEL", "qwen3:0.6b", "Nombre del modelo en Ollama"],
      ["QWEN_TEMPERATURA", "0.0", "Temperatura para Qwen (0.0 = determinista)"],
      ["QWEN_MAX_TOKENS", "150", "Máximo tokens de salida para Qwen"],
      ["QWEN_MAX_CONTEXT", "2048", "Ventana de contexto de Qwen en tokens"],
      ["QWEN_FORMAT", "json", "Fuerza grammar sampling JSON en Ollama"],
      ["USAR_GROQ_BACKUP", "True", "Activa Groq como fallback/generador de código"],
      ["GROQ_MODEL_CODIGO", "llama-3.3-70b-versatile", "Modelo de Groq para generación de código"],
      ["USAR_GEMINI_BACKUP", "True", "Activa Gemini como fallback"],
      ["USAR_DEEPSEEK", "False", "DeepSeek desactivado por defecto"],
      ["UMBRAL_COMANDO", "0.60", "Score mínimo para aceptar comando de BD"],
      ["UMBRAL_PREGUNTA", "0.55", "Score mínimo para aceptar respuesta de BD"],
      ["MARGEN_EMPATE", "0.08", "Margen para activar árbitro Qwen"],
      ["🆕 SHELL_LISTA_BLANCA", "frozenset", "Prefijos de comandos de solo lectura — sin confirmación"],
      ["🆕 SHELL_LISTA_NEGRA", "frozenset", "Patrones de comandos destructivos — bloqueados siempre"],
      ["🆕 SHELL_ZONA_AMARILLA", "frozenset", "Prefijos de comandos con efecto — requieren confirmación"],
      ["🆕 VERBOS_REPRODUCCION", "frozenset", "Verbos de reproducción multimedia para intent_router"],
      ["🆕 SENTINEL_ACTIVO", "True", "Activa/desactiva el hilo daemon de sentinel"],
      ["🆕 SENTINEL_INTERVALO_SEGUNDOS", "45", "Frecuencia de chequeo del daemon de sentinel"],
      ["🆕 SENTINEL_UMBRAL_DISCO_PCT", "10.0", "% libre mínimo en disco antes de alerta"],
      ["🆕 SENTINEL_UMBRAL_RAM_PCT", "90.0", "% uso máximo de RAM antes de alerta"],
      ["🆕 SENTINEL_UMBRAL_BATERIA_PCT", "20", "% carga mínima de batería antes de alerta"],
    ],
    [3200, 2360, 3800]
  ),

  // ── SECCIÓN 17 — ROADMAP actualizado ────────────────────────────────────────
  h1("17. ROADMAP"),
  mkTable(
    ["Funcionalidad", "Prioridad", "Estado", "Notas técnicas"],
    [
      ["🆕 Integrar sentinel con GUI (alertas proactivas en pantalla)", "Alta", "Pendiente", "Conectar _emitir() de sentinel con el frontend Electron."],
      ["🆕 Aprendizaje de comandos shell (tipo='shell' en BD)", "Alta", "Pendiente", "Cuando el usuario enseña un patrón CLI, guardarlo con tipo='shell' para enrutar por shell.py."],
      ["🆕 Expandir info del sistema a GPU y temperatura", "Media", "Pendiente", "wmic path Win32_VideoController y OpenHardwareMonitor API."],
      ["Botón de voz funcional en GUI", "Alta", "Pendiente", "Conectar el ícono de micrófono del sidebar al evento WebSocket activar_voz."],
      ["Fine-tuning Qwen 0.6b con datos propios", "Alta", "Pendiente", "Dataset ~500 ejemplos. Google Colab T4. Formato GGUF para Ollama."],
      ["commands.json configurable sin tocar código", "Alta", "Pendiente", "Permite agregar comandos editando JSON sin Python."],
      ["Generación de documentos DOCX/PDF", "Media", "Pendiente", "python-docx para DOCX, reportlab para PDF."],
      ["Sistema de recordatorios con scheduler", "Media", "Pendiente", "APScheduler en hilo de fondo. Notificaciones via io_manager y GUI."],
      ["Vosk modelo offline descarga automática", "Media", "Pendiente", "Integrar descarga en sara.bat paso 2. Modelo español ~50MB."],
      ["Piper TTS descarga automática", "Media", "Pendiente", "Integrar descarga de piper.exe + modelo .onnx en sara.bat."],
      ["System tray Windows", "Media", "Pendiente", "Electron Tray API. Clic derecho: Mostrar/Anclar/Salir."],
      ["Mayor conciencia del entorno (procesos activos)", "Completado", "✅ Implementado en PRAXIS", "psutil + shell.py + sentinel.py"],
      ["Modo multiusuario con perfiles", "Baja", "Pendiente", "Perfiles separados en BD con tabla usuarios."],
      ["Plugin system para extensiones", "Baja", "Diseño fase 2", "Carpeta plugins/ con interfaz estándar. Carga dinámica en inicializar()."],
      ["Memoria contextual entre sesiones", "Media", "Diseño fase 2", "Contexto conversacional que persiste entre reinicios."],
      ["Migrar GUI de Electron a pywebview", "Baja — si se supera 2GB", "Pendiente condicional", "Libera ~300MB eliminando Chromium embebido."],
    ],
    [3560, 1400, 1800, 2600]
  ),

  // ── SECCIÓN 18 — ESTRUCTURA DE ARCHIVOS actualizada ─────────────────────────
  h1("18. ESTRUCTURA DE ARCHIVOS DEL PROYECTO v0.4.0"),
  code("sara/"),
  code("├── sara.py                  # Orquestador principal"),
  code("├── brain.py                 # Motor de decisiones (incluye Capa -2 PRAXIS)"),
  code("├── file_intent.py           # Detección de intención (incluye guardia PRAXIS)"),
  code("├── external_service.py      # IAs externas: Qwen, Groq, Gemini, DeepSeek"),
  code("├── learning.py              # Aprendizaje persistente con deduplicación"),
  code("├── database.py              # SQLite WAL: todas las tablas"),
  code("├── commands.py              # Ejecución de comandos OS (incluye tipos shell)"),
  code("├── sistema.py               # Control de volumen, multimedia, energía"),
  code("├── splitter.py              # División de entradas y patrones __DESTINO__"),
  code("├── searcher.py              # Construcción de búsquedas web"),
  code("├── embeddings.py            # Vectores semánticos sentence-transformers"),
  code("├── context.py               # Contexto conversacional entre turnos"),
  code("├── social.py                # Saludos, correcciones, interacciones sociales"),
  code("├── voice.py                 # STT/TTS con fallbacks automáticos"),
  code("├── file_watcher.py          # Indexador y monitor de archivos en disco"),
  code("├── io_manager.py            # Entrada/salida unificada texto/voz"),
  code("├── logger.py                # Logging por niveles con colores"),
  code("├── utils.py                 # Normalización, similitud, helpers"),
  code("├── config.py                # Configuración centralizada (incluye constantes PRAXIS)"),
  code("├── validator.py             # Validación de entradas del usuario"),
  code("├── server.py                # Backend WebSocket GUI (FastAPI + uvicorn)"),
  code("│"),
  code("├── shell.py                 # 🆕 PRAXIS — Motor de ejecución controlada CMD/PowerShell"),
  code("├── perceptor.py             # 🆕 PRAXIS — Percepción y verificación del sistema"),
  code("├── intent_router.py         # 🆕 PRAXIS — Discriminador semántico de intención"),
  code("├── sentinel.py              # 🆕 PRAXIS — Vigilancia proactiva en hilo daemon"),
  code("│"),
  code("├── package.json             # Dependencias Electron"),
  code("├── gui.bat                  # Lanzador de la GUI"),
  code("├── gui/"),
  code("│   ├── main.js              # Proceso principal Electron"),
  code("│   ├── preload.js           # Bridge seguro renderer↔main"),
  code("│   └── index.html           # Interfaz completa (HTML + CSS + JS)"),
  code("│"),
  code("├── .env                     # API keys — NO versionar"),
  code("├── .env.example             # Plantilla de .env"),
  code("├── sara.bat                 # Arranque automático universal Windows"),
  code("├── sara.db                  # Base de datos SQLite (generada al iniciar)"),
  code("├── scripts/                 # Scripts Python generados por SARA"),
  code("├── models/                  # Modelos de voz (Vosk, Piper)"),
  code("└── .ollama/                 # Modelos Ollama"),
  code("    └── models/"),
  code("        └── qwen3:0.6b/      # Modelo Qwen cuantizado Q4"),

  // ── CIERRE ───────────────────────────────────────────────────────────────────
  sep(),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 240, after: 60 },
    children: [new TextRun({ text: "FIN DEL DOCUMENTO MAESTRO DE SARA v0.4.0", font: "Arial", size: 20, bold: true, color: "1F3864" })]
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 60 },
    children: [new TextRun({ text: "Este documento debe entregarse completo a cualquier IA colaboradora antes de iniciar una sesión de desarrollo.", font: "Arial", size: 18, italics: true, color: "595959" })]
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 60 },
    children: [new TextRun({ text: "Es la fuente de verdad sobre el estado, la arquitectura y las decisiones de diseño de SARA.", font: "Arial", size: 18, italics: true, color: "595959" })]
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 60, after: 0 },
    children: [new TextRun({ text: "Confidencial — Solo para desarrollo  |  SARA v0.4.0", font: "Arial", size: 16, color: "AAAAAA" })]
  }),
];

const doc = new Document({
  styles: {
    default: {
      document: { run: { font: "Arial", size: 20 } }
    },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: "1F3864" },
        paragraph: { spacing: { before: 400, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Arial", color: "2E75B6" },
        paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 22, bold: true, font: "Arial", color: "1F497D" },
        paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 2 } },
    ]
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 }
      }
    },
    children
  }]
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync('/mnt/user-data/outputs/SARA_Documento_Maestro_v0_4_0.docx', buf);
  console.log('OK');
});