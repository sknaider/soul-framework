@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
title Diagnóstico - SOUL Core
"%~dp0python.exe" "%~dp0doctor.py" %*
set "SOUL_EXIT=%errorlevel%"
echo.
pause
exit /b %SOUL_EXIT%
