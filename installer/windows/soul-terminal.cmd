@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
title Terminal - SOUL Core
echo SOUL Core listo. Ejemplos:
echo   soul create Maya
echo   soul remember Maya "William prefiere respuestas breves"
echo   soul boot Maya
echo.
doskey soul="%~dp0soul.cmd" $*
cmd /K
