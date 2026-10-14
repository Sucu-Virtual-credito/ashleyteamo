@echo off
setlocal
title Nova - arranque automatico

REM ============================================================
REM  Hace que Nova arranque SOLO al encender el PC.
REM  Doble clic una vez. Para desactivarlo, borra el acceso
REM  directo "Nova.lnk" de la carpeta Inicio (o pon /quitar).
REM ============================================================

set "INICIO=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "DESTINO=%INICIO%\Nova.lnk"
set "OBJETIVO=%~dp0arrancar-nova.bat"

if /I "%~1"=="/quitar" goto quitar

if not exist "%OBJETIVO%" (
  echo [ERROR] No encuentro arrancar-nova.bat junto a este archivo.
  pause
  exit /b 1
)

powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%DESTINO%'); $s.TargetPath='%OBJETIVO%'; $s.WorkingDirectory='%~dp0..'; $s.WindowStyle=7; $s.Save()"

echo.
echo  [OK] Nova arrancara solo al encender el PC.
echo       Acceso directo: %DESTINO%
echo.
echo  Para desactivarlo, ejecuta:  arranque-automatico.bat /quitar
pause
exit /b 0

:quitar
if exist "%DESTINO%" del "%DESTINO%"
echo.
echo  [OK] Arranque automatico desactivado.
timeout /t 3 >nul