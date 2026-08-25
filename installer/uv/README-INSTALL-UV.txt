SOUL Core 0.4.3 - instalacion con uv

Esta es la via recomendada para quien no quiere depender del instalador EXE de
SOUL. Usa el ejecutable oficial de uv, un Python 3.13.15 privado de Astral y
todas las dependencias dentro de una carpeta privada del usuario. Python y los
52 paquetes vienen dentro del payload verificado: despues de descargar los dos
archivos de la release, la instalacion de dependencias se ejecuta offline.

Requisitos:
- Windows 10/11 x64
- PowerShell 5.1 o superior
- Internet durante la instalacion

No modifica el Python del sistema, no registra Python globalmente y no toca
Ollama ni sus modelos. Las almas viven en %USERPROFILE%\.soul y se conservan al
desinstalar.

La instalacion predeterminada queda en:
%LOCALAPPDATA%\Programs\SOUL Core UV

Accesos:
- Configurar mi alma
- Terminal de SOUL Core
- Diagnostico de SOUL Core
- Desinstalar SOUL Core (uv)
