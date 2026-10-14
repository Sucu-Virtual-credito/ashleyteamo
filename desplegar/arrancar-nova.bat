@echo off
setlocal
title Nova - lanzador

REM ============================================================
REM  Arranca el tunel de ngrok (dominio fijo) + el servidor Nova.
REM  Doble clic. Deja ESTA ventana abierta mientras quieras Nova.
REM  Para pararlo: cierra la ventana o usa parar-nova.bat
REM ============================================================

set "DOMINIO=https://riptide-slingshot-mandarin.ngrok-free.dev"
set "PROYECTO=%~dp0.."
set "PY=%PROYECTO%\.venv\Scripts\python.exe"

cd /d "%PROYECTO%"

if not exist "%PY%" (
  echo [ERROR] No encuentro el entorno virtual en:
  echo         %PY%
  echo         Crealo con:  python -m venv .venv
  echo         y luego:     .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)

where ngrok >nul 2>&1
if errorlevel 1 (
  echo [ERROR] ngrok no esta en el PATH. Instalalo y abrelo una vez.
  pause
  exit /b 1
)

echo.
echo   Nova         -^>  %DOMINIO%
echo   Panel ngrok  -^>  http://127.0.0.1:4040
echo.

start "ngrok-nova" ngrok http --url=%DOMINIO% 5000
timeout /t 4 >nul

echo Arrancando servidor Nova...  (cierra esta ventana o Ctrl+C para parar)
echo.
"%PY%" servidor.py

echo.
echo Nova se ha detenido. Cerrando tambien el tunel de ngrok...
taskkill /F /IM ngrok.exe >nul 2>&1
pause