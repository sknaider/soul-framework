[CmdletBinding()]
param(
    [string]$Version = "0.4.3",
    [string]$PythonVersion = "3.13.15",
    [string]$PythonBuild = "20260825",
    [string]$PythonArchivePath = "",
    [string]$PythonSha256 = "c1dc1e267f2a81493ce6e94837263f648f1eb6d0df73a1492469c1fed025ce8f",
    [string]$WheelhousePath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $RepoRoot "build\uv-installer"
$Payload = Join-Path $BuildRoot "payload"
$DistDir = Join-Path $RepoRoot "dist\uv"
$Archive = Join-Path $DistDir "SOUL-Core-$Version-uv-payload.zip"
$PythonArchiveName = "cpython-$PythonVersion+$PythonBuild-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
$PythonUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/$PythonBuild/cpython-$PythonVersion%2B$PythonBuild-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"

if (Test-Path -LiteralPath $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Payload, $DistDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Payload "bootstrap") | Out-Null
$BundledPythonArchive = Join-Path $Payload "bootstrap\$PythonArchiveName"
if ($PythonArchivePath) {
    Copy-Item -LiteralPath $PythonArchivePath -Destination $BundledPythonArchive
} else {
    Write-Host "Descargando Python privado oficial de Astral..."
    Invoke-WebRequest -UseBasicParsing -Uri $PythonUrl -OutFile $BundledPythonArchive
}
$ObservedPythonSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $BundledPythonArchive).Hash.ToLowerInvariant()
if ($ObservedPythonSha256 -ne $PythonSha256.ToLowerInvariant()) {
    throw "SHA-256 inesperado para Python privado: $ObservedPythonSha256"
}
if (-not $WheelhousePath -or -not (Test-Path -LiteralPath $WheelhousePath -PathType Container)) {
    throw "Falta -WheelhousePath con el wheelhouse Windows verificado."
}
$WheelhouseManifest = Join-Path $WheelhousePath "WHEELHOUSE-MANIFEST.json"
$WheelhouseSums = Join-Path $WheelhousePath "WHEELHOUSE-SHA256SUMS"
$WheelhouseRequirements = Join-Path $WheelhousePath "requirements-windows-x64.txt"
if (-not (Test-Path -LiteralPath $WheelhouseManifest -PathType Leaf) -or -not (Test-Path -LiteralPath $WheelhouseSums -PathType Leaf) -or -not (Test-Path -LiteralPath $WheelhouseRequirements -PathType Leaf)) {
    throw "El wheelhouse no contiene manifiesto, requisitos y sumas SHA-256."
}
$Manifest = Get-Content -LiteralPath $WheelhouseManifest -Raw | ConvertFrom-Json
if ($Manifest.schema -ne "soul.core.wheelhouse.v1" -or @($Manifest.packages).Count -ne 52) {
    throw "El manifiesto del wheelhouse no contiene los 52 paquetes esperados."
}
foreach ($Package in $Manifest.packages) {
    $Wheel = Join-Path $WheelhousePath $Package.file
    $Observed = (Get-FileHash -Algorithm SHA256 -LiteralPath $Wheel).Hash.ToLowerInvariant()
    if ($Observed -ne $Package.sha256.ToLowerInvariant()) {
        throw "Wheel alterado o incompleto: $($Package.file)"
    }
}
Copy-Item -LiteralPath $WheelhousePath -Destination (Join-Path $Payload "bootstrap\wheels") -Recurse
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "runtime-project") -Destination (Join-Path $Payload "project") -Recurse
New-Item -ItemType Directory -Force -Path (Join-Path $Payload "app") | Out-Null
foreach ($Name in @("setup_soul.py", "doctor.py", "dependency_audit.py", "official_trust_keys.json")) {
    Copy-Item -LiteralPath (Join-Path $RepoRoot "installer\windows\$Name") -Destination (Join-Path $Payload "app\$Name")
}
Copy-Item -LiteralPath (Join-Path $RepoRoot "installer\windows\templates") -Destination (Join-Path $Payload "app\templates") -Recurse
Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot "launchers") -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $Payload
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "uninstall-soul-core-uv.ps1") -Destination $Payload
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README-INSTALL-UV.txt") -Destination $Payload
Copy-Item -LiteralPath (Join-Path $RepoRoot "LICENSE") -Destination $Payload

$Ascii = [Text.ASCIIEncoding]::new()
Get-ChildItem -LiteralPath $Payload -Filter "*.cmd" | ForEach-Object {
    $Text = [IO.File]::ReadAllText($_.FullName, [Text.UTF8Encoding]::new($false))
    if ($Text.ToCharArray() | Where-Object { [int]$_ -gt 127 } | Select-Object -First 1) {
        throw "El launcher $($_.Name) debe ser ASCII."
    }
    $Text = ($Text -replace "`r?`n", "`r`n").TrimEnd("`r", "`n") + "`r`n"
    [IO.File]::WriteAllText($_.FullName, $Text, $Ascii)
}

if (Test-Path -LiteralPath $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}
Compress-Archive -Path (Join-Path $Payload "*") -DestinationPath $Archive -CompressionLevel Optimal
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLowerInvariant()
Write-Host "PAYLOAD=$Archive"
Write-Host "SIZE=$((Get-Item -LiteralPath $Archive).Length)"
Write-Host "SHA256=$Hash"
