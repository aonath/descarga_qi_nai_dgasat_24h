@echo off
REM Lanzador para Windows: instala (si hace falta) y ejecuta la descarga.
REM Doble clic para correr.
setlocal

where download_dgasat_24h >nul 2>nul
if errorlevel 1 (
    echo Instalando dgasat24h por primera vez...
    python -m pip install "%~dp0"
    if errorlevel 1 (
        echo.
        echo ERROR: no se pudo instalar. Verifica que Python este instalado
        echo y agregado al PATH ^(https://www.python.org/downloads/^).
        pause
        exit /b 1
    )
)

echo.
echo Iniciando descarga DGASAT 24h... se abrira el dashboard en el navegador.
echo Cierra esta ventana cuando termines de mirar el dashboard.
echo.
download_dgasat_24h %*
pause
