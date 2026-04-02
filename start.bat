@echo off
:: ─────────────────────────────────────────────────────────────────────────────
::  SIGMAC — Script de arranque para Windows
::  Doble click en start.bat o ejecutar desde PowerShell/CMD
:: ─────────────────────────────────────────────────────────────────────────────

echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║   SIGMAC -- DNTT Bolivia                     ║
echo  ║   Sistema de Inteligencia Geoespacial        ║
echo  ╚══════════════════════════════════════════════╝
echo.

:: Verificar Docker
docker --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Docker no esta instalado.
    echo Descarga Docker Desktop: https://docs.docker.com/get-docker/
    pause
    exit /b 1
)

docker compose version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Docker Compose no encontrado. Actualiza Docker Desktop.
    pause
    exit /b 1
)

echo [1/2] Construyendo imagenes y levantando servicios...
docker compose up --build -d

if %errorlevel% neq 0 (
    echo [ERROR] Fallo al levantar los servicios. Revisa los logs:
    echo   docker compose logs
    pause
    exit /b 1
)

echo [2/2] Esperando que la API este lista...
timeout /t 15 /nobreak >nul

echo.
echo  OK - SIGMAC esta corriendo
echo.
echo  Dashboard  --^>  http://localhost
echo  API Docs   --^>  http://localhost/docs
echo  Health     --^>  http://localhost/health
echo.
echo  Usuario:    admin
echo  Contrasena: sigmac2024
echo.
echo  Para ver logs:   docker compose logs -f
echo  Para detener:    docker compose down
echo.

:: Abrir el navegador automaticamente
start http://localhost

pause
