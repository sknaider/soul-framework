@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
title Diagnostico - SOUL Core
"%~dp0runtime\Scripts\python.exe" "%~dp0app\doctor.py" %*
set "SOUL_EXIT=%errorlevel%"
echo.
pause
exit /b %SOUL_EXIT%
