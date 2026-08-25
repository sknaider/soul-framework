[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "Programs\SOUL Core UV"),
    [switch]$KeepCache
)

$ErrorActionPreference = "Stop"
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$StateFile = Join-Path $InstallRoot "install-state.json"

$LocalRoot = [IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd("\")
if (-not $InstallRoot.StartsWith($LocalRoot + "\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Por seguridad, solo se desinstalan subcarpetas de LOCALAPPDATA: $LocalRoot"
}

if (-not (Test-Path -LiteralPath $StateFile -PathType Leaf)) {
    throw "No se encontro una instalacion administrada de SOUL Core UV en: $InstallRoot"
}

$State = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
if ($State.schema -ne "soul.core.uv-install.v1" -or $State.install_root -ne $InstallRoot) {
    throw "El marcador de instalacion no coincide. No se borro nada."
}

$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\SOUL Core (uv)"
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "SOUL Core (uv).lnk"
if (Test-Path -LiteralPath $StartMenu) {
    Remove-Item -LiteralPath $StartMenu -Recurse -Force
}
if (Test-Path -LiteralPath $DesktopShortcut) {
    Remove-Item -LiteralPath $DesktopShortcut -Force
}

if ($KeepCache) {
    $Cache = Join-Path $InstallRoot "cache"
    $SavedCache = Join-Path ([IO.Path]::GetTempPath()) ("soul-uv-cache-" + [guid]::NewGuid().ToString("N"))
    if (Test-Path -LiteralPath $Cache) {
        Move-Item -LiteralPath $Cache -Destination $SavedCache
    }
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    if (Test-Path -LiteralPath $SavedCache) {
        Move-Item -LiteralPath $SavedCache -Destination $Cache
    }
    [ordered]@{
        schema = "soul.core.uv-owner.v1"
        install_root = $InstallRoot
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $InstallRoot "install-owner.json") -Encoding UTF8
} else {
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
}

Write-Host "SOUL Core UV fue desinstalado."
Write-Host "Tus almas se conservaron en: $(Join-Path $HOME '.soul')"
