@echo off
setlocal
title Nova - parar

REM ============================================================
REM  Detiene el servidor Nova y el tunel de ngrok. Doble clic.
REM ============================================================

echo Parando el servidor Nova...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*servidor.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo Parando el tunel de ngrok...
taskkill /F /IM ngrok.exe >nul 2>&1

echo.
echo  [OK] Nova y ngrok detenidos.
timeout /t 3 >nul