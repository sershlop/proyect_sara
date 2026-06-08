@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

:: ════════════════════════════════════════════════════════════════
::  SARA — Sistema Autónomo de Razonamiento Artificial v0.3.0
::  Arranque automático universal
:: ════════════════════════════════════════════════════════════════

title SARA v0.3.0 — Cargando...
color 02

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║   SARA — Sistema Autónomo de Razonamiento        ║
echo  ║          Artificial  v0.3.0                      ║
echo  ║          Iniciando sistema...                    ║
echo  ╚══════════════════════════════════════════════════╝
echo.

:: ── Variables base ───────────────────────────────────────────────
set BASE=%~dp0
set PYTHON_LOCAL=%BASE%python\python.exe
set OLLAMA_HOME=%BASE%.ollama
set OLLAMA_MODELS=%BASE%.ollama\models

:: ── Python — detección en orden de prioridad ─────────────────────
echo  [1/6] Verificando Python...

if exist "%PYTHON_LOCAL%" (
    set PYTHON=%PYTHON_LOCAL%
    echo  [OK] Python local embebido encontrado
    goto :VERIFICAR_PIP
)

:: Buscar Python en el sistema de forma universal
for /f "delims=" %%i in ('where python 2^>nul') do (
    set PYTHON=%%i
    goto :PYTHON_SISTEMA_OK
)

:: Python no encontrado — descargar embebido 3.12
echo  [INFO] Python no encontrado. Descargando Python 3.12 embebido...
goto :DESCARGAR_PYTHON_312

:PYTHON_SISTEMA_OK
echo  [OK] Python del sistema: %PYTHON%

:: ── Verificar versión — Python 3.14+ no tiene wheels compatibles ──
for /f "tokens=*" %%v in ('"%PYTHON%" -c "import sys;print(sys.version_info.minor if sys.version_info.major==3 else 99)" 2^>nul') do set PYMINOR=%%v

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

set PYTHON=%PYTHON_LOCAL%
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
    goto :VERIFICAR_OLLAMA
)

echo  [INFO] Primera ejecucion — instalando librerias (puede tardar unos minutos)...
echo.

"%PYTHON%" -m pip install --quiet --upgrade pip

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
    for /f "tokens=*" %%v in ('"%PYTHON%" -c "import sys;print(f'cp{sys.version_info.major}{sys.version_info.minor}')" 2^>nul') do set PYVER=%%v
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
for /f "tokens=*" %%v in ('"%PYTHON%" -c "import sys;print(f'cp{sys.version_info.major}{sys.version_info.minor}')" 2^>nul') do set PYVER=%%v
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

:: ── Web y red ────────────────────────────────────────────────────
echo  Instalando modulos web...
"%PYTHON%" -m pip install --quiet requests pandas python-dotenv watchdog
echo  [OK] Modulos web listos

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