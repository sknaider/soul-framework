[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "Programs\SOUL Core UV"),
    [string]$UvArchivePath = "",
    [string]$PayloadPath = "",
    [switch]$NoShortcuts,
    [switch]$DesktopShortcut,
    [switch]$SkipOnboarding
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Version = "0.4.3"
$PythonVersion = "3.13.15"
$PythonBuild = "20260825"
$UvVersion = "0.12.6"
$UvSha256 = "df7cb9f243eae1621400d4fcf5b1b3d90f20e264ece91b64deb3b0078abca6ef"
$PayloadSha256 = "c68b305061762292ab8d87646739df1d770a534d44bc90bbffe2fa27343afb65"
$PythonArchiveSha256 = "c1dc1e267f2a81493ce6e94837263f648f1eb6d0df73a1492469c1fed025ce8f"
$UvUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
$PayloadUrl = "https://github.com/sknaider/soul-framework/releases/download/v$Version/SOUL-Core-$Version-uv-payload.zip"
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) ("soul-core-uv-" + [guid]::NewGuid().ToString("N"))

function Assert-Hash([string]$Path, [string]$Expected, [string]$Label) {
    $Observed = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($Observed -ne $Expected.ToLowerInvariant()) {
        throw "SHA-256 inesperado para $Label. Esperado=$Expected Observado=$Observed"
    }
    Write-Host "[OK] $Label verificado: $Observed"
}

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    # Windows PowerShell 5.1 promotes every native stderr line to an error
    # record. uv writes download progress to stderr even on success, so keep
    # native output visible but decide success exclusively from the exit code.
    $PreviousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $Program @Arguments
        $NativeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorAction
    }
    if ($NativeExitCode -ne 0) {
        throw "$Program termino con codigo $NativeExitCode"
    }
}

function Invoke-Download([string]$Url, [string]$Destination, [string]$Label) {
    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Destination
            return
        } catch {
            if ($Attempt -eq 3) {
                throw
            }
            Write-Warning "$Label no termino de descargar (intento $Attempt/3). Reintentando..."
            Start-Sleep -Seconds 2
        }
    }
}

function New-SoulShortcut([string]$Path, [string]$Target, [string]$WorkingDirectory) {
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($Path)
    $Shortcut.TargetPath = $Target
    $Shortcut.WorkingDirectory = $WorkingDirectory
    $Shortcut.Save()
}

try {
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "SOUL Core requiere Windows x64."
    }
    New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
    $UvArchive = Join-Path $TempRoot "uv.zip"
    $PayloadArchive = Join-Path $TempRoot "payload.zip"

    Write-Host "SOUL Core $Version - instalacion aislada con uv"
    Write-Host "Destino: $InstallRoot"
    Write-Host "No se modificara Python global, Ollama ni sus modelos."

    if ($UvArchivePath) {
        Copy-Item -LiteralPath $UvArchivePath -Destination $UvArchive
    } else {
        Write-Host "[1/7] Descargando uv $UvVersion desde Astral..."
        Invoke-Download $UvUrl $UvArchive "uv"
    }
    Assert-Hash $UvArchive $UvSha256 "uv oficial"

    if ($PayloadPath) {
        Copy-Item -LiteralPath $PayloadPath -Destination $PayloadArchive
    } else {
        Write-Host "[2/7] Descargando payload oficial de SOUL Core..."
        Invoke-Download $PayloadUrl $PayloadArchive "payload de SOUL Core"
    }
    Assert-Hash $PayloadArchive $PayloadSha256 "payload de SOUL Core"

    $Stage = Join-Path $TempRoot "stage"
    $UvStage = Join-Path $TempRoot "uv"
    Expand-Archive -LiteralPath $PayloadArchive -DestinationPath $Stage -Force
    Expand-Archive -LiteralPath $UvArchive -DestinationPath $UvStage -Force
    if (-not (Test-Path -LiteralPath (Join-Path $Stage "project\uv.lock") -PathType Leaf)) {
        throw "El payload no contiene project\uv.lock."
    }
    $PythonArchive = Join-Path $Stage "bootstrap\cpython-$PythonVersion+$PythonBuild-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
    if (-not (Test-Path -LiteralPath $PythonArchive -PathType Leaf)) {
        throw "El payload no contiene el runtime privado de Python."
    }
    Assert-Hash $PythonArchive $PythonArchiveSha256 "Python de Astral"
    $Wheelhouse = Join-Path $Stage "bootstrap\wheels"
    if (-not (Test-Path -LiteralPath (Join-Path $Wheelhouse "WHEELHOUSE-MANIFEST.json") -PathType Leaf)) {
        throw "El payload no contiene el wheelhouse bloqueado de Windows."
    }
    $WindowsRequirements = Join-Path $Wheelhouse "requirements-windows-x64.txt"
    if (-not (Test-Path -LiteralPath $WindowsRequirements -PathType Leaf)) {
        throw "El payload no contiene los requisitos Windows bloqueados."
    }

    $StateFile = Join-Path $InstallRoot "install-state.json"
    $OwnerFile = Join-Path $InstallRoot "install-owner.json"
    if (Test-Path -LiteralPath $InstallRoot) {
        $Children = @(Get-ChildItem -LiteralPath $InstallRoot -Force -ErrorAction SilentlyContinue)
        if ($Children.Count -gt 0) {
            $Managed = $false
            foreach ($Marker in @($StateFile, $OwnerFile)) {
                if (Test-Path -LiteralPath $Marker -PathType Leaf) {
                    try {
                        $Metadata = Get-Content -LiteralPath $Marker -Raw | ConvertFrom-Json
                        $Managed = $Managed -or (
                            $Metadata.install_root -eq $InstallRoot -and
                            $Metadata.schema -in @("soul.core.uv-install.v1", "soul.core.uv-owner.v1")
                        )
                    } catch {
                        continue
                    }
                }
            }
            if (-not $Managed) {
                throw "El destino existe y no pertenece al instalador SOUL Core UV: $InstallRoot"
            }
        }
    }

    Write-Host "[3/7] Copiando componentes verificados..."
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    [ordered]@{
        schema = "soul.core.uv-owner.v1"
        install_root = $InstallRoot
    } | ConvertTo-Json | Set-Content -LiteralPath $OwnerFile -Encoding UTF8
    Get-ChildItem -LiteralPath $Stage -Force | Where-Object { $_.Name -ne "bootstrap" } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $InstallRoot -Recurse -Force
    }
    $ToolsDir = Join-Path $InstallRoot "tools"
    New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $UvStage "uv.exe") -Destination (Join-Path $ToolsDir "uv.exe") -Force
    $Uv = Join-Path $ToolsDir "uv.exe"

    $PythonDir = Join-Path $InstallRoot "python"
    $CacheDir = Join-Path $InstallRoot "cache"
    $RuntimeDir = Join-Path $InstallRoot "runtime"
    $ProjectDir = Join-Path $InstallRoot "project"
    $env:UV_PYTHON_INSTALL_DIR = $PythonDir
    $env:UV_CACHE_DIR = $CacheDir
    $env:UV_PYTHON_NO_REGISTRY = "1"
    $env:UV_PROJECT_ENVIRONMENT = $RuntimeDir
    $env:PYTHONUTF8 = "1"

    Write-Host "[4/7] Preparando Python privado $PythonVersion..."
    New-Item -ItemType Directory -Force -Path $PythonDir | Out-Null
    $Tar = (Get-Command "tar.exe" -ErrorAction SilentlyContinue).Source
    if (-not $Tar) {
        throw "Windows no incluye tar.exe; se requiere Windows 10 1803 o superior."
    }
    Invoke-Checked $Tar @("-xzf", $PythonArchive, "-C", $PythonDir)
    $BundledPython = Join-Path $PythonDir "python\python.exe"
    if (-not (Test-Path -LiteralPath $BundledPython -PathType Leaf)) {
        throw "No aparecio el Python privado esperado: $BundledPython"
    }
    Invoke-Checked $BundledPython @("--version")

    Write-Host "[5/7] Instalando dependencias locales bloqueadas por uv.lock..."
    Invoke-Checked $Uv @("venv", $RuntimeDir, "--python", $BundledPython, "--no-managed-python", "--allow-existing")

    $RuntimePython = Join-Path $RuntimeDir "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $RuntimePython -PathType Leaf)) {
        throw "No aparecio el Python privado esperado: $RuntimePython"
    }
    Invoke-Checked $Uv @("pip", "install", "--python", $RuntimePython, "--offline", "--no-cache", "--no-index", "--find-links", $Wheelhouse, "--require-hashes", "--no-deps", "--requirements", $WindowsRequirements)
    Invoke-Checked $Uv @("pip", "check", "--python", $RuntimePython)
    Write-Host "[6/7] Auditando imports y versiones reales..."
    Invoke-Checked $RuntimePython @((Join-Path $InstallRoot "app\dependency_audit.py"))
    Invoke-Checked $RuntimePython @("-m", "soul_framework.cli", "--version")

    $State = [ordered]@{
        schema = "soul.core.uv-install.v1"
        version = $Version
        install_root = $InstallRoot
        python_version = $PythonVersion
        python_build = $PythonBuild
        python_archive_sha256 = $PythonArchiveSha256
        uv_version = $UvVersion
        uv_sha256 = $UvSha256
        payload_sha256 = $PayloadSha256
        installed_at = [DateTime]::UtcNow.ToString("o")
    }
    $State | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
    foreach ($Transient in @((Join-Path $InstallRoot "bootstrap"), $CacheDir)) {
        if (Test-Path -LiteralPath $Transient) {
            Remove-Item -LiteralPath $Transient -Recurse -Force
        }
    }

    Write-Host "[7/7] Creando accesos del usuario..."
    if (-not $NoShortcuts) {
        $StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\SOUL Core (uv)"
        New-Item -ItemType Directory -Force -Path $StartMenu | Out-Null
        New-SoulShortcut (Join-Path $StartMenu "Configurar mi alma.lnk") (Join-Path $InstallRoot "soul-setup.cmd") $InstallRoot
        New-SoulShortcut (Join-Path $StartMenu "Terminal de SOUL Core.lnk") (Join-Path $InstallRoot "soul-terminal.cmd") $InstallRoot
        New-SoulShortcut (Join-Path $StartMenu "Diagnostico de SOUL Core.lnk") (Join-Path $InstallRoot "soul-doctor.cmd") $InstallRoot
        $Uninstall = Join-Path $InstallRoot "uninstall-soul-core-uv.ps1"
        $UninstallShortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $StartMenu "Desinstalar SOUL Core (uv).lnk"))
        $UninstallShortcut.TargetPath = "powershell.exe"
        $UninstallShortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Uninstall`""
        $UninstallShortcut.WorkingDirectory = $InstallRoot
        $UninstallShortcut.Save()
        if ($DesktopShortcut) {
            New-SoulShortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "SOUL Core (uv).lnk") (Join-Path $InstallRoot "soul-setup.cmd") $InstallRoot
        }
    }

    Write-Host ""
    Write-Host "INSTALACION_OK"
    Write-Host "SOUL Core: $Version"
    Write-Host "Ruta: $InstallRoot"
    Write-Host "Tus almas: $(Join-Path $HOME '.soul')"
    if (-not $SkipOnboarding) {
        Start-Process -FilePath (Join-Path $InstallRoot "soul-setup.cmd") -WorkingDirectory $InstallRoot
    }
} finally {
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
}
