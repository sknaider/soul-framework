@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
"%~dp0python.exe" -m soul_framework.cli %*
exit /b %errorlevel%
