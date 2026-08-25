[CmdletBinding()]
param(
    [string]$Version = "0.4.3"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildRoot = Join-Path $RepoRoot "build\uv-installer"
$Payload = Join-Path $BuildRoot "payload"
$DistDir = Join-Path $RepoRoot "dist\uv"
$Archive = Join-Path $DistDir "SOUL-Core-$Version-uv-payload.zip"

if (Test-Path -LiteralPath $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Payload, $DistDir | Out-Null
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
