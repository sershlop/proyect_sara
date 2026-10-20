@echo off
:: ==========================================================================
::  SARA - Arrancador unico (doble clic)
::  Solo hace UNA cosa: conseguir un Python propio. Todo lo demas (detectar
::  el equipo, instalar, descargar, abrir la ventana) lo hace bootstrap.py.
::  ASCII puro a proposito: cmd.exe lee este archivo antes de aplicar chcp.
::  Opciones (se pasan a bootstrap.py):  --componentes  --reparar
::                                       --diagnostico  --navegador  --auto
:: ==========================================================================
setlocal
title SARA
cd /d "%~dp0"

set "SARA_ROOT=%~dp0"
set "SARA_PYDIR=%~dp0runtime\python"
set "SARA_VENDOR=%~dp0bootstrap\vendor\python-embed.zip"
set "SARA_PYURL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"

if not exist "%~dp0bootstrap\bootstrap.py" (
    echo.
    echo  [ERROR] No encuentro bootstrap\bootstrap.py
    echo  Extrae TODO el .zip en una carpeta antes de abrir SARA.bat
    echo  ^(no lo ejecutes desde dentro del .zip^). Se recomienda algo como C:\SARA
    echo.
    pause
    exit /b 1
)

if exist "%SARA_PYDIR%\python.exe" goto :LANZAR

echo.
echo  ====================================================
echo    SARA - preparando su Python propio (solo una vez)
echo  ====================================================
echo.

:: Quita el bloqueo "archivo descargado de internet" de lo que SARA va a ejecutar
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath $env:SARA_ROOT -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1

if exist "%SARA_VENDOR%" (
    echo  [..] Usando el Python incluido en el paquete...
) else (
    echo  [..] Descargando Python 3.12 ^(unos 10 MB^)...
    if not exist "%~dp0bootstrap\vendor" mkdir "%~dp0bootstrap\vendor"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri $env:SARA_PYURL -OutFile $env:SARA_VENDOR"
    if errorlevel 1 (
        echo  [ERROR] No se pudo descargar Python. Revisa tu conexion a internet.
        pause
        exit /b 1
    )
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath $env:SARA_VENDOR -DestinationPath $env:SARA_PYDIR -Force"
if not exist "%SARA_PYDIR%\python.exe" (
    echo  [ERROR] No se pudo preparar Python en %SARA_PYDIR%
    pause
    exit /b 1
)
echo  [OK] Python listo

:LANZAR
"%SARA_PYDIR%\python.exe" "%~dp0bootstrap\bootstrap.py" %*
if errorlevel 1 (
    echo.
    echo  ====================================================
    echo    SARA no pudo iniciar. Detalles en:
    echo    %~dp0runtime\instalacion.log
    echo  ====================================================
    echo.
    pause
    exit /b 1
)
exit /b 0
