
@echo off
:: ── Auto-elevación ELIMINADA a propósito ───────────────────────────
:: SARA corre con permisos normales de usuario. Solo OpenHardwareMonitor
:: (más abajo) solicita su propio UAC puntual y aislado, sin elevar
:: todo el proceso de SARA. Esto evita los problemas de aislamiento de
:: escritorio (UIPI) que causaban fallos al abrir apps de usuario
:: (Opera, Spotify, accesos directos) cuando SARA corría como admin.
 
chcp 65001 >nul
setlocal EnableDelayedExpansion
 
 
 
:: ════════════════════════════════════════════════════════════════
 
::  SARA — Sistema Autónomo de Razonamiento Artificial v0.3.0
 
::  Arranque automático universal
 
:: ════════════════════════════════════════════════════════════════
 
 
 
chcp 65001 >nul
color 02
title SARA v0.3.0 — Cargando...
 
 
 
echo.
 
echo  ╔══════════════════════════════════════════════════╗
 
echo  ║   SARA — Sistema Autónomo de Razonamiento        ║
 
echo  ║              Artificial  v0.3.0              ║
 
echo  ║             Iniciando sistema...                 ║
 
echo  ╚══════════════════════════════════════════════════╝
 
echo.
 
 
 
:: ── Variables base ───────────────────────────────────────────────
 
set "BASE=%~dp0"
 
set "PYTHON_LOCAL=%BASE%python\python.exe"
 
set "OLLAMA_HOME=%BASE%.ollama"
 
set "OLLAMA_MODELS=%BASE%.ollama\models"
 
 
 
:: ── Python — detección en orden de prioridad ─────────────────────
 
echo  [1/6] Verificando Python...
 
 
 
if exist "%PYTHON_LOCAL%" (
 
    set "PYTHON=%PYTHON_LOCAL%"
 
    echo  [OK] Python local embebido encontrado
 
    goto :VERIFICAR_PIP
 
)
 
 
 
:: Buscar Python en el sistema de forma universal
 
for /f "delims=" %%i in ('where python 2^>nul') do (
 
    set "PYTHON=%%i"
 
    goto :PYTHON_SISTEMA_OK
 
)
 
 
 
:: Python no encontrado — descargar embebido 3.12
 
echo  [INFO] Python no encontrado. Descargando Python 3.12 embebido...
 
goto :DESCARGAR_PYTHON_312
 
 
 
:PYTHON_SISTEMA_OK
 
echo  [OK] Python del sistema: %PYTHON%
 
 
 
:: ── Verificar versión — Python 3.14+ no tiene wheels compatibles ──
 
for /f "tokens=*" %%v in ('"%PYTHON%" -c "import sys;print(sys.version_info.minor if sys.version_info.major==3 else 99)" 2^>nul') do set "PYMINOR=%%v"
 
 
 
if !PYMINOR! GTR 12 (
 
    echo  [WARN] Python 3.!PYMINOR! detectado — incompatible con algunas librerias
 
    echo  [INFO] Descargando Python 3.12 embebido para compatibilidad maxima...
 
    goto :DESCARGAR_PYTHON_312
 
)
 
 
 
echo  [OK] Version de Python compatible
 
goto :VERIFICAR_PIP
 
 
 
:DESCARGAR_PYTHON_312
 
if not exist "%BASE%python\" mkdir "%BASE%python"
 
 
 
curl -L --progress-bar -o "%BASE%python\python-embed.zip" ^
 
    "https://www.python.org/ftp/python/3.12.0/python-3.12.0-embed-amd64.zip"
 
 
 
if %errorlevel% neq 0 (
 
    echo  [ERROR] No se pudo descargar Python. Verifica tu conexion a internet.
 
    goto :ERROR_FATAL
 
)
 
 
 
powershell -NoProfile -Command ^
 
    "Expand-Archive -Path '%BASE%python\python-embed.zip' -DestinationPath '%BASE%python' -Force"
 
del "%BASE%python\python-embed.zip" >nul 2>&1
 
 
 
(
 
    echo python312.zip
 
    echo .
 
    echo ../
 
    echo import site
 
) > "%BASE%python\python312._pth"
 
 
 
set "PYTHON=%PYTHON_LOCAL%"
 
echo  [OK] Python 3.12 embebido instalado
 
 
 
:VERIFICAR_PIP
 
:: ── pip ──────────────────────────────────────────────────────────
 
if not exist "%BASE%python\Scripts\pip.exe" (
 
    if exist "%PYTHON_LOCAL%" (
 
        echo  [INFO] Instalando pip...
 
        curl -s -o "%BASE%get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
 
        "%PYTHON%" "%BASE%get-pip.py" --quiet
 
        del "%BASE%get-pip.py" >nul 2>&1
 
        echo  [OK] pip instalado
 
    )
 
)
 
 
 
:: ── Librerías ────────────────────────────────────────────────────
 
echo.
 
echo  [2/6] Verificando librerias...
 
 
 
if exist "%BASE%.libs_instaladas" (
 
    echo  [OK] Librerias ya instaladas
 
    goto :VERIFICAR_OHM
 
)
 
 
 
echo  [INFO] Primera ejecucion — instalando librerias (puede tardar unos minutos)...
 
echo.
 
 
 
"%PYTHON%" -m pip install --quiet --upgrade pip
 
pip install noisereduce scipy
 
:: ── NLP y Embeddings ─────────────────────────────────────────────
 
echo  Instalando NLP y embeddings...
 
"%PYTHON%" -m pip install --quiet sentence-transformers numpy
 
echo  [OK] NLP y embeddings listos
 
 
 
:: ── Voz base ─────────────────────────────────────────────────────
 
echo  Instalando modulos de voz...
 
"%PYTHON%" -m pip install --quiet SpeechRecognition edge-tts pyttsx3
 
echo  [OK] Modulos de voz base listos
 
 
 
:: ── pygame — wheel precompilado preferido ────────────────────────
 
echo  Instalando pygame...
 
"%PYTHON%" -m pip install --quiet pygame >nul 2>&1
 
if %errorlevel% equ 0 (
 
    echo  [OK] pygame instalado
 
) else (
 
    echo  [INFO] Intentando pygame via wheel precompilado...
 
    for /f "tokens=*" %%v in ('"%PYTHON%" -c "import sys;print(f'cp{sys.version_info.major}{sys.version_info.minor}')" 2^>nul') do set "PYVER=%%v"
 
    curl -sL -o "%BASE%pygame.whl" ^
 
        "https://files.pythonhosted.org/packages/pygame-2.6.1-!PYVER!-!PYVER!-win_amd64.whl" >nul 2>&1
 
    "%PYTHON%" -m pip install --quiet "%BASE%pygame.whl" >nul 2>&1
 
    del "%BASE%pygame.whl" >nul 2>&1
 
    if %errorlevel% equ 0 (
 
        echo  [OK] pygame instalado via wheel
 
    ) else (
 
        echo  [WARN] pygame no disponible — TTS usara pyttsx3 como fallback
 
    )
 
)
 
 
 
:: ── Audio — cascada de intentos sin requerir compilador ──────────
 
echo  Instalando modulos de audio...
 
 
 
:: Paso 1: sounddevice — siempre funciona, no requiere compilador
 
"%PYTHON%" -m pip install --quiet sounddevice soundfile >nul 2>&1
 
if %errorlevel% equ 0 (
 
    echo  [OK] sounddevice instalado como backend de audio
 
) else (
 
    echo  [WARN] sounddevice no disponible
 
)
 
 
 
:: Paso 2: PyAudio nativo
 
"%PYTHON%" -m pip install --quiet pyaudio >nul 2>&1
 
if %errorlevel% equ 0 (
 
    echo  [OK] PyAudio instalado nativamente
 
    goto :AUDIO_LISTO
 
)
 
 
 
:: Paso 3: PyAudio via wheel precompilado segun version
 
echo  [INFO] Intentando PyAudio via wheel precompilado...
 
for /f "tokens=*" %%v in ('"%PYTHON%" -c "import sys;print(f'cp{sys.version_info.major}{sys.version_info.minor}')" 2^>nul') do set "PYVER=%%v"
 
curl -sL -o "%BASE%PyAudio.whl" ^
 
    "https://github.com/o7q/pyaudio-wheels/releases/download/v0.2.14/PyAudio-0.2.14-!PYVER!-!PYVER!-win_amd64.whl" >nul 2>&1
 
"%PYTHON%" -m pip install --quiet "%BASE%PyAudio.whl" >nul 2>&1
 
del "%BASE%PyAudio.whl" >nul 2>&1
 
if %errorlevel% equ 0 (
 
    echo  [OK] PyAudio instalado via wheel precompilado
 
    goto :AUDIO_LISTO
 
)
 
 
 
:: Paso 4: Instalar Visual C++ Build Tools via winget y compilar
 
echo  [INFO] Instalando Visual C++ Build Tools automaticamente...
 
winget install Microsoft.VisualStudio.2022.BuildTools ^
 
    --silent ^
 
    --accept-package-agreements ^
 
    --accept-source-agreements ^
 
    --override "--quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended" >nul 2>&1
 
"%PYTHON%" -m pip install --quiet pyaudio >nul 2>&1
 
if %errorlevel% equ 0 (
 
    echo  [OK] PyAudio instalado tras Build Tools
 
    goto :AUDIO_LISTO
 
)
 
 
 
echo  [WARN] PyAudio no disponible — modo voz usara sounddevice como alternativa
 
 
 
:AUDIO_LISTO
 
 
 
:: ── Control del sistema ──────────────────────────────────────────
 
echo  Instalando control de sistema...
 
"%PYTHON%" -m pip install --quiet psutil pyautogui comtypes ^
 
    screen-brightness-control pygetwindow pycaw
 
echo  [OK] Control de sistema listo
 
 
 
:: ── Web, Servidor y Red ──────────────────────────────────────────
 
echo  Instalando modulos web y servidor API...
 
"%PYTHON%" -m pip install --quiet requests pandas python-dotenv watchdog fastapi uvicorn
 
echo  [OK] Modulos web y API listos
 
 
 
:: ── IAs externas ─────────────────────────────────────────────────
 
echo  Instalando clientes de IA...
 
"%PYTHON%" -m pip install --quiet groq google-genai openai ollama
 
echo  [OK] Clientes de IA listos
 
 
 
:: ── Verificar sqlite3 ────────────────────────────────────────────
 
"%PYTHON%" -c "import sqlite3" >nul 2>&1
 
if %errorlevel% equ 0 (
 
    echo  [OK] sqlite3 disponible
 
) else (
 
    echo  [WARN] sqlite3 no disponible
 
)
 
 
 
:: ── Marcar instalacion completa ──────────────────────────────────
 
echo ok > "%BASE%.libs_instaladas"
 
echo.
 
echo  [OK] Librerias instaladas correctamente
 
echo.
 
 
 
:: ════════════════════════════════════════════════════════════════
::  OpenHardwareMonitor — sensores de temperatura
::  FIX: este bloque vive FUERA del "if exist .libs_instaladas",
::  con su propio marcador de control (%OHM_EXE%). Antes quedaba
::  anidado dentro de la sección de librerías Python, así que en
::  cualquier arranque donde .libs_instaladas ya existiera de una
::  instalación previa, este bloque completo se saltaba con el
::  "goto :VERIFICAR_OLLAMA" y OpenHardwareMonitor nunca llegaba a
::  descargarse. Ahora se verifica siempre, de forma independiente.
:: ════════════════════════════════════════════════════════════════
 
:VERIFICAR_OHM
 
echo  Verificando OpenHardwareMonitor para sensores de temperatura...
 
set "OHM_DIR=%BASE%tools\OpenHardwareMonitor"
set "OHM_EXE=%OHM_DIR%\OpenHardwareMonitor.exe"
 
if exist "%OHM_EXE%" (
    echo  [OK] OpenHardwareMonitor ya instalado
    goto :OHM_LISTO
)
 
echo  [INFO] Descargando OpenHardwareMonitor...
if not exist "%OHM_DIR%" mkdir "%OHM_DIR%"
 
:: URL oficial del proyecto (la antigua de GitHub releases no resuelve a un
:: binario real — devuelve un 404/HTML que rompía la extraccion del ZIP).
:: Esta es la misma URL que usa el paquete oficial de Scoop, verificada
:: contra hash SHA256 por la comunidad.
curl -L --progress-bar -o "%BASE%tools\ohm.zip" ^
    "https://openhardwaremonitor.org/files/openhardwaremonitor-v0.9.6.zip"
 
if %errorlevel% neq 0 (
    echo  [WARN] No se pudo descargar OpenHardwareMonitor — temperatura no disponible
    goto :OHM_LISTO
)
 
:: ── Verificar que el archivo descargado es un ZIP real ────────────
:: Un ZIP siempre empieza con los bytes 0x50 0x4B ("PK"). Si la descarga
:: devolvio una pagina de error HTML en vez del binario, esto lo detecta
:: ANTES de intentar extraer, evitando el error confuso de PowerShell
:: "Directorio central danado" y dando un mensaje claro en su lugar.
powershell -NoProfile -Command ^
    "$b = Get-Content -Path '%BASE%tools\ohm.zip' -Encoding Byte -TotalCount 2 -ErrorAction SilentlyContinue; if ($b.Length -eq 2 -and $b[0] -eq 0x50 -and $b[1] -eq 0x4B) { exit 0 } else { exit 1 }"
 
if %errorlevel% neq 0 (
    echo  [WARN] El archivo descargado no es un ZIP valido — el servidor pudo devolver un error
    echo  [WARN] OpenHardwareMonitor no se instalara esta vez. Temperatura no disponible.
    del "%BASE%tools\ohm.zip" >nul 2>&1
    goto :OHM_LISTO
)
 
powershell -NoProfile -Command ^
    "Expand-Archive -Path '%BASE%tools\ohm.zip' -DestinationPath '%OHM_DIR%' -Force"
 
del "%BASE%tools\ohm.zip" >nul 2>&1
 
if exist "%OHM_EXE%" (
    echo  [OK] OpenHardwareMonitor instalado en tools\OpenHardwareMonitor
) else (
    echo  [WARN] OpenHardwareMonitor no se pudo extraer correctamente
)
 
:OHM_LISTO
 
echo.
 
 
 
:: ── Ollama ───────────────────────────────────────────────────────
 
:VERIFICAR_OLLAMA
 
echo  [3/6] Verificando Ollama...
 
 
 
if not exist "%OLLAMA_MODELS%" mkdir "%OLLAMA_MODELS%"
 
 
 
where ollama >nul 2>&1
 
if %errorlevel% neq 0 (
 
    echo  [INFO] Ollama no encontrado. Descargando...
 
    curl -L --progress-bar -o "%BASE%ollama-setup.exe" ^
 
        "https://ollama.com/download/OllamaSetup.exe"
 
    if %errorlevel% neq 0 (
 
        echo  [ERROR] No se pudo descargar Ollama
 
        goto :ERROR_FATAL
 
    )
 
    "%BASE%ollama-setup.exe" /silent
 
    del "%BASE%ollama-setup.exe" >nul 2>&1
 
    timeout /t 5 /nobreak >nul
 
    echo  [OK] Ollama instalado
 
) else (
 
    echo  [OK] Ollama detectado
 
)
 
 
 
:: ── Modelo Qwen ──────────────────────────────────────────────────
 
echo  [4/6] Verificando modelo qwen3:0.6b...
 
 
 
:: Iniciar servidor Ollama si no está activo
 
ollama list >nul 2>&1
 
if %errorlevel% neq 0 (
 
    echo  [INFO] Iniciando servidor Ollama...
 
    start "OllamaServer" /MIN ollama serve
 
    timeout /t 5 /nobreak >nul
 
    echo  [OK] Servidor Ollama iniciado
 
) else (
 
    echo  [OK] Servidor Ollama ya activo
 
)
 
 
 
:: Verificar modelo
 
ollama list 2>nul | findstr "qwen3" >nul 2>&1
 
if %errorlevel% neq 0 (
 
    echo  [INFO] Descargando qwen3:0.6b — solo ocurre una vez...
 
    ollama pull qwen3:0.6b
 
    if %errorlevel% neq 0 (
 
        echo  [WARN] No se pudo descargar qwen3:0.6b — SARA usara Groq/Gemini
 
    ) else (
 
        echo  [OK] Modelo qwen3:0.6b listo
 
    )
 
) else (
 
    echo  [OK] Modelo qwen3:0.6b disponible
 
)
 
echo.
 
 
 
:: ── Archivos de SARA ─────────────────────────────────────────────
 
echo  [5/6] Verificando archivos de SARA...
 
 
 
if not exist "%BASE%sara.py" (
 
    echo  [ERROR] No se encontro sara.py
 
    goto :ERROR_FATAL
 
)
 
 
 
if not exist "%BASE%config.py" (
 
    if exist "%BASE%config_example.py" (
 
        echo  [INFO] Creando config.py desde config_example.py...
 
        copy "%BASE%config_example.py" "%BASE%config.py" >nul
 
        echo  [OK] config.py creado — edita tus valores antes de continuar
 
        pause
 
    ) else (
 
        echo  [ERROR] No existe config.py ni config_example.py
 
        goto :ERROR_FATAL
 
    )
 
)
 
 
 
if not exist "%BASE%.env" (
 
    if exist "%BASE%.env.example" (
 
        echo  [INFO] Creando .env desde .env.example...
 
        copy "%BASE%.env.example" "%BASE%.env" >nul
 
        echo  [OK] .env creado — agrega tus API keys
 
        pause
 
    ) else (
 
        echo  [WARN] No existe .env — las API keys deben estar en config.py
 
    )
 
)
 
 
 
:: Crear carpetas necesarias
 
if not exist "%BASE%scripts\" mkdir "%BASE%scripts"
 
if not exist "%BASE%models\" mkdir "%BASE%models"
 
 
 
echo  [OK] Archivos verificados
 
echo.
 
 
 
:: ── Iniciar OpenHardwareMonitor en segundo plano ─────────────────
:: Único punto del script que solicita elevación — aislado a este
:: proceso específico, sin afectar los permisos de SARA ni de sara.py.
:: Si el usuario cancela el UAC, OHM simplemente no inicia y SARA
:: continúa normalmente sin lectura de temperatura (degradación
:: elegante, no bloqueante).
 
set "OHM_EXE=%BASE%tools\OpenHardwareMonitor\OpenHardwareMonitor.exe"
 
if exist "%OHM_EXE%" (
    tasklist 2>nul | findstr /i "OpenHardwareMonitor" >nul 2>&1
    if %errorlevel% neq 0 (
        echo  [INFO] OpenHardwareMonitor necesita permisos de administrador para leer sensores.
        echo  [INFO] Se abrira una ventana de Windows pidiendo tu autorizacion solo para esto.
        powershell -NoProfile -Command ^
            "Start-Process '%OHM_EXE%' -Verb RunAs -WindowStyle Minimized" >nul 2>&1
        timeout /t 3 /nobreak >nul
        tasklist 2>nul | findstr /i "OpenHardwareMonitor" >nul 2>&1
        if %errorlevel% equ 0 (
            echo  [OK] OpenHardwareMonitor activo — temperatura disponible
        ) else (
            echo  [WARN] OpenHardwareMonitor no se inicio — temperatura no disponible esta sesion
        )
    ) else (
        echo  [OK] OpenHardwareMonitor ya estaba corriendo
    )
) else (
    echo  [INFO] OpenHardwareMonitor no encontrado — temperatura no disponible esta sesion
)
 
echo.
 
 
 
:: ── Iniciar SARA ─────────────────────────────────────────────────
 
echo  [6/6] Iniciando SARA...
 
echo.
 
title SARA v0.3.0 — Activa
 
color 02
 
 
 
"%PYTHON%" "%BASE%sara.py"
 
 
 
:: ── Manejo de cierre ─────────────────────────────────────────────
 
if %errorlevel% neq 0 (
 
    echo.
 
    echo  ╔══════════════════════════════════════════════════╗
 
    echo  ║   [ERROR] SARA termino con un error              ║
 
    echo  ║   Revisa los mensajes anteriores                 ║
 
    echo  ╚══════════════════════════════════════════════════╝
 
    echo.
 
    pause
 
    exit /b 1
 
)
 
 
 
echo.
 
:: ── Cerrar OpenHardwareMonitor al cerrar SARA ────────────────────
 
tasklist 2>nul | findstr /i "OpenHardwareMonitor" >nul 2>&1
if %errorlevel% equ 0 (
    taskkill /f /im "OpenHardwareMonitor.exe" >nul 2>&1
    echo  [OK] OpenHardwareMonitor cerrado
)
 
echo  [OK] SARA cerrada correctamente
 
timeout /t 2 /nobreak >nul
 
exit /b 0
 
 
 
:: ── Error fatal ──────────────────────────────────────────────────
 
:ERROR_FATAL
 
echo.
 
echo  ╔══════════════════════════════════════════════════╗
 
echo  ║   [ERROR FATAL] No se pudo iniciar SARA          ║
 
echo  ║   Revisa los mensajes anteriores                 ║
 
echo  ╚══════════════════════════════════════════════════╝
 
echo.
 
pause
 
exit /b 1



``` 

