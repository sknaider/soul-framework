@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
title Configurar mi alma - SOUL Core
"%~dp0python.exe" "%~dp0setup_soul.py" %*
set "SOUL_EXIT=%errorlevel%"
echo.
if not "%SOUL_EXIT%"=="0" echo La configuración no terminó correctamente ^(código %SOUL_EXIT%^).
pause
exit /b %SOUL_EXIT%
