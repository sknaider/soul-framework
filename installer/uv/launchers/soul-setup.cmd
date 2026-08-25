@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
title Configurar mi alma - SOUL Core
"%~dp0runtime\Scripts\python.exe" "%~dp0app\setup_soul.py" %*
set "SOUL_EXIT=%errorlevel%"
echo.
if not "%SOUL_EXIT%"=="0" echo La configuracion no termino correctamente ^(codigo %SOUL_EXIT%^).
pause
exit /b %SOUL_EXIT%
