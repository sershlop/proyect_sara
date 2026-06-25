@echo off
setlocal enabledelayedexpansion
title SARA GUI — Instalando...
cd /d "%~dp0"

echo.
echo  ╔══════════════════════════════════════╗
echo  ║   SARA GUI  —  Lanzador v0.3.0       ║
echo  ╚══════════════════════════════════════╝
echo.

:: ── 1. Verificar Node.js ────────────────────────────────────────────────────
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo  [!] Node.js no encontrado.
    echo  [!] Descargalo en: https://nodejs.org  ^(v18 o superior^)
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('node --version') do set NODE_VER=%%v
echo  [OK] Node.js %NODE_VER% detectado.

:: ── 2. Instalar dependencias si faltan ──────────────────────────────────────
if not exist "node_modules\electron" (
    echo.
    echo  [>>] Instalando Electron ^(primera vez, puede tardar^)...
    call npm install --save-dev electron --quiet
    if %errorlevel% neq 0 (
        echo  [!] Error instalando Electron.
        pause
        exit /b 1
    )
    echo  [OK] Electron instalado.
)

:: ── 3. Lanzar GUI ────────────────────────────────────────────────────────────
echo.
echo  [>>] Iniciando interfaz visual SARA...
echo  [i]  Asegurate de que SARA ^(sara.bat^) ya este corriendo.
echo.
title SARA GUI
call npx electron . 2>nul
if %errorlevel% neq 0 (
    echo.
    echo  [!] Error al iniciar la GUI.
    echo  [!] Verifica que Node.js este correctamente instalado.
    pause
)
