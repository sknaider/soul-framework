[CmdletBinding()]
param(
    [string]$Version = "0.4.3",
    [string]$PythonVersion = "3.13.15",
    [string]$PythonSha256 = "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf",
    [switch]$InstallBuildTools
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $RepoRoot "build\windows-installer"
$Payload = Join-Path $BuildRoot "payload"
$DownloadDir = Join-Path $BuildRoot "downloads"
$WheelDir = Join-Path $BuildRoot "wheels"
$BuildVenv = Join-Path $BuildRoot "build-venv"
$DistDir = Join-Path $RepoRoot "dist\windows"

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    $PreviousErrorAction = $ErrorActionPreference
    $NativeExitCode = $null
    try {
        # Windows PowerShell 5.1 promotes native stderr to error records even
        # when the process succeeds. Preserve output and trust the exit code.
        $ErrorActionPreference = "Continue"
        & $Program @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
        $NativeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorAction
    }
    if ($null -eq $NativeExitCode -or $NativeExitCode -ne 0) {
        throw "$Program termino con codigo $NativeExitCode"
    }
}

function Install-AppLocalMsvcRuntime([string]$PythonHome) {
    $SitePackages = Join-Path $PythonHome "Lib\site-packages"
    $TorchLib = Join-Path $SitePackages "torch\lib"
    $RuntimeFiles = @(
        (Join-Path $PythonHome "vcruntime140.dll"),
        (Join-Path $PythonHome "vcruntime140_1.dll"),
        (Join-Path $SitePackages "sklearn\.libs\msvcp140.dll")
    )
    if (-not (Test-Path -LiteralPath $TorchLib -PathType Container)) {
        throw "No se encontro el directorio nativo de Torch: $TorchLib"
    }
    foreach ($RuntimeFile in $RuntimeFiles) {
        if (-not (Test-Path -LiteralPath $RuntimeFile -PathType Leaf)) {
            throw "Falta una dependencia MSVC bloqueada: $RuntimeFile"
        }
        Copy-Item -LiteralPath $RuntimeFile -Destination $TorchLib -Force
    }
}

function Remove-CoreBuildOriginMetadata([string]$PythonHome) {
    # A wheel installed from the local build directory gets a direct_url.json
    # containing C:\Users\<builder>\... . It is neither needed at runtime nor
    # acceptable provenance for a redistributable payload.
    $SitePackages = Join-Path $PythonHome "Lib\site-packages"
    $CoreDistInfo = @(Get-ChildItem -LiteralPath $SitePackages -Directory -Filter "soul_framework-*.dist-info")
    if ($CoreDistInfo.Count -ne 1) {
        throw "Se esperaba exactamente un dist-info de SOUL Core; encontrados: $($CoreDistInfo.Count)"
    }
    $DirectUrl = Join-Path $CoreDistInfo[0].FullName "direct_url.json"
    if (Test-Path -LiteralPath $DirectUrl -PathType Leaf) {
        Remove-Item -LiteralPath $DirectUrl -Force
    }
    if (Test-Path -LiteralPath $DirectUrl) {
        throw "No se pudo retirar metadata local del build: $DirectUrl"
    }
}

if (Test-Path $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Payload, $DownloadDir, $WheelDir, $DistDir | Out-Null

$PythonZip = Join-Path $DownloadDir "python-$PythonVersion-embed-amd64.zip"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
Write-Host "[1/6] Descargando runtime oficial de Python $PythonVersion..."
Invoke-WebRequest -UseBasicParsing -Uri $PythonUrl -OutFile $PythonZip
$ObservedPythonSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $PythonZip).Hash.ToLowerInvariant()
if ($ObservedPythonSha256 -ne $PythonSha256.ToLowerInvariant()) {
    throw "SHA-256 inesperado para Python embebido: $ObservedPythonSha256"
}
Expand-Archive -LiteralPath $PythonZip -DestinationPath $Payload -Force

$PthFile = Get-ChildItem -LiteralPath $Payload -Filter "python*._pth" | Select-Object -First 1
if (-not $PthFile) { throw "No se encontró el archivo ._pth del Python embebido" }
$Pth = Get-Content -LiteralPath $PthFile.FullName
$Pth = $Pth -replace '^#import site$', 'import site'
if ($Pth -notcontains 'Lib\site-packages') { $Pth += 'Lib\site-packages' }
Set-Content -LiteralPath $PthFile.FullName -Value $Pth -Encoding Ascii

Write-Host "[2/6] Preparando build aislado..."
Invoke-Checked "py" @("-3.11", "-m", "venv", $BuildVenv)
$BuildPython = Join-Path $BuildVenv "Scripts\python.exe"
Invoke-Checked $BuildPython @("-m", "pip", "install", "--disable-pip-version-check", "--quiet", "--upgrade", "pip", "build")
Invoke-Checked $BuildPython @("-m", "build", "--wheel", "--outdir", $WheelDir, $RepoRoot)
$CoreWheel = Get-ChildItem -LiteralPath $WheelDir -Filter "soul_framework-*.whl" | Select-Object -First 1
if (-not $CoreWheel) { throw "No se construyó el wheel de SOUL Core" }

Write-Host "[3/6] Instalando dependencias dentro del runtime privado..."
$GetPip = Join-Path $DownloadDir "get-pip.py"
Invoke-WebRequest -UseBasicParsing -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPip
$RuntimePython = Join-Path $Payload "python.exe"
Invoke-Checked $RuntimePython @($GetPip, "--disable-pip-version-check", "--quiet")
Invoke-Checked $RuntimePython @(
    "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--quiet",
    "--target", (Join-Path $Payload "Lib\site-packages"), "$($CoreWheel.FullName)[all]"
)
Install-AppLocalMsvcRuntime $Payload
Remove-CoreBuildOriginMetadata $Payload

Write-Host "[4/6] Incorporando onboarding, diagnósticos y plantillas firmadas..."
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "setup_soul.py") -Destination $Payload
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "doctor.py") -Destination $Payload
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "dependency_audit.py") -Destination $Payload
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "ann_probe.py") -Destination $Payload
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "finalize_install.py") -Destination $Payload
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "soul.cmd") -Destination $Payload
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "soul-setup.cmd") -Destination $Payload
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "soul-doctor.cmd") -Destination $Payload
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "soul-terminal.cmd") -Destination $Payload
Copy-Item -LiteralPath (Join-Path $RepoRoot "LICENSE") -Destination $Payload
Copy-Item -LiteralPath (Join-Path $RepoRoot "README.md") -Destination (Join-Path $Payload "README-SOUL-CORE.md")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "official_trust_keys.json") -Destination $Payload
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "templates") -Destination $Payload -Recurse

# cmd.exe no interpreta de forma fiable un batch UTF-8 con saltos LF-only:
# trunca comandos ("title" -> "le", "echo." -> "o.") y puede ocultar el
# código real de salida. El payload debe contener launchers ASCII + CRLF aunque
# el checkout o la copia haya normalizado los saltos de línea.
$Ascii = [Text.ASCIIEncoding]::new()
Get-ChildItem -LiteralPath $Payload -Filter "*.cmd" | ForEach-Object {
    $LauncherText = [IO.File]::ReadAllText($_.FullName, [Text.UTF8Encoding]::new($false))
    if ($LauncherText.ToCharArray() | Where-Object { [int]$_ -gt 127 } | Select-Object -First 1) {
        throw "El launcher $($_.Name) debe ser ASCII para cmd.exe"
    }
    $LauncherText = ($LauncherText -replace "`r?`n", "`r`n").TrimEnd("`r", "`n") + "`r`n"
    [IO.File]::WriteAllText($_.FullName, $LauncherText, $Ascii)
}

Write-Host "[5/6] Verificando el runtime antes de empaquetar..."
$BuildAnnState = [ordered]@{
    schema = "soul.core.ann-state.v1"
    probe_exit_code = 0
    selected_engine = "usearch"
    quarantine_path = ""
    build_validation_only = $true
}
$BuildAnnState | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Payload "ann-state.json") -Encoding UTF8
Invoke-Checked $RuntimePython @((Join-Path $Payload "dependency_audit.py"))
Invoke-Checked $RuntimePython @("-m", "pip", "check")
$FreezeOutput = & $RuntimePython -m pip list --format=freeze 2>&1
$FreezeExitCode = $LASTEXITCODE
if ($FreezeExitCode -ne 0) { throw "pip list termino con codigo $FreezeExitCode" }
$FreezeOutput | Set-Content -LiteralPath (Join-Path $Payload "DEPENDENCIES.txt") -Encoding UTF8
Invoke-Checked $RuntimePython @("-m", "soul_framework.cli", "--version")
Invoke-Checked $RuntimePython @((Join-Path $PSScriptRoot "setup_soul.py"), "--help")
Get-ChildItem -LiteralPath $Payload -Filter "*.cmd" | ForEach-Object {
    $LauncherBytes = [IO.File]::ReadAllBytes($_.FullName)
    $LauncherText = $Ascii.GetString($LauncherBytes)
    if ($LauncherText -match "(?<!`r)`n" -or $LauncherText -notmatch "`r`n") {
        throw "El launcher $($_.Name) no quedó normalizado a CRLF"
    }
}

$Iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe"
if (-not (Test-Path -LiteralPath $Iscc)) {
    $Iscc = "$env:ProgramFiles\Inno Setup 7\ISCC.exe"
}
if (-not (Test-Path -LiteralPath $Iscc) -and $InstallBuildTools) {
    Write-Host "Instalando Inno Setup 7.1.0 x64 desde la release oficial firmada..."
    $InnoInstaller = Join-Path $DownloadDir "innosetup-7.1.0-x64.exe"
    $InnoUrl = "https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe"
    Invoke-WebRequest -UseBasicParsing -Uri $InnoUrl -OutFile $InnoInstaller
    $InnoSignature = Get-AuthenticodeSignature -LiteralPath $InnoInstaller
    if ($InnoSignature.Status -ne "Valid" -or $InnoSignature.SignerCertificate.Subject -notmatch "Pyrsys B\.V\.") {
        throw "La firma Authenticode de Inno Setup no es válida/oficial: $($InnoSignature.Status)"
    }
    $InnoProcess = Start-Process -FilePath $InnoInstaller -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER" -Wait -PassThru
    if ($InnoProcess.ExitCode -ne 0) { throw "Inno Setup terminó con código $($InnoProcess.ExitCode)" }
    $Iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe"
    if (-not (Test-Path -LiteralPath $Iscc)) {
        $Iscc = "$env:ProgramFiles\Inno Setup 7\ISCC.exe"
    }
}
if (-not (Test-Path -LiteralPath $Iscc)) {
    throw "Falta Inno Setup 7 x64 (requerido para rutas extendidas). Ejecuta de nuevo con -InstallBuildTools."
}

Write-Host "[6/6] Compilando instalador EXE..."
Invoke-Checked $Iscc @("/DMyAppVersion=$Version", (Join-Path $PSScriptRoot "SOUL-Core.iss"))
$Installer = Join-Path $DistDir "SOUL-Core-$Version-Windows-x64.exe"
if (-not (Test-Path -LiteralPath $Installer)) { throw "No apareció el instalador esperado: $Installer" }
$Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $Installer
Write-Host "INSTALLER=$Installer"
Write-Host "SIZE=$((Get-Item -LiteralPath $Installer).Length)"
Write-Host "SHA256=$($Hash.Hash.ToLowerInvariant())"
