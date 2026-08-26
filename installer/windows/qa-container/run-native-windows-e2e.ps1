[CmdletBinding()]
param(
    [string]$RepoRoot = "C:\Users\Dadito\soul-core-qa",
    [string]$ExpectedInstallerSha256 = "4f3b5a2c5d594da058d5c96708643b7ff8d91db31114c82e7ade19bbb23cb4b2",
    [string]$OllamaUrl = "http://127.0.0.1:11434",
    [string]$OllamaModel = "richardyoung/gemma-4-12b-coder-abliterated:Q8_0"
)

$ErrorActionPreference = "Stop"
$Payload = Join-Path $RepoRoot "build\windows-installer\payload"
$Installer = Join-Path $RepoRoot "dist\windows\SOUL-Core-0.4.3-Windows-x64.exe"
$RunId = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
$QaRoot = Join-Path $RepoRoot "qa-runs\$RunId"
if (Test-Path -LiteralPath $QaRoot) {
    throw "El perfil QA ya existe: $QaRoot"
}
New-Item -ItemType Directory -Path $QaRoot | Out-Null
$env:USERPROFILE = $QaRoot
$env:HOME = $QaRoot
$env:PYTHONUTF8 = "1"

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    $Previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $Program @Arguments
        $Code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $Previous
    }
    if ($Code -ne 0) {
        throw "$Program termino con codigo $Code"
    }
}

function Invoke-ExpectTwo([string]$Program, [string[]]$Arguments) {
    $Previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $Program @Arguments
        $Code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $Previous
    }
    if ($Code -ne 2) {
        throw "Se esperaba codigo 2 y se obtuvo $Code"
    }
}

$ObservedSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Installer).Hash.ToLowerInvariant()
if ($ObservedSha256 -ne $ExpectedInstallerSha256) {
    throw "SHA-256 inesperado: $ObservedSha256"
}
Write-Host "INSTALLER_SHA256_OK=$ObservedSha256"

$NonPortable = @(
    Get-ChildItem -LiteralPath (Join-Path $Payload "templates") -File |
        Where-Object { $_.Name.ToCharArray() | Where-Object { [int]$_ -gt 127 } | Select-Object -First 1 }
)
if ($NonPortable.Count -ne 0) {
    throw "Hay nombres de plantilla no ASCII: $($NonPortable.Name -join ', ')"
}
Write-Host "WINDOWS_PORTABLE_TEMPLATE_NAMES_OK"

$Python = Join-Path $Payload "python.exe"
Invoke-Checked $Python @("--version")
Invoke-Checked $Python @((Join-Path $Payload "dependency_audit.py"))
Invoke-Checked $Python @("-m", "pip", "check")

foreach ($Template in @("asistente", "programador", "investigador", "companero")) {
    $Name = "QA-$Template"
    Invoke-Checked $Python @(
        (Join-Path $Payload "setup_soul.py"),
        "--non-interactive", "--name", $Name, "--template", $Template,
        "--ollama-url", $OllamaUrl, "--model", $OllamaModel
    )
    $Database = Join-Path $QaRoot ".soul\$Name.db"
    Invoke-Checked $Python @("-m", "soul_framework.cli", "boot", $Name, "--db", $Database)
}
Write-Host "FOUR_SIGNED_TEMPLATES_AND_BOOT_OK"

Invoke-Checked $Python @((Join-Path $Payload "doctor.py"))

$MissingName = "QA-modelo-inexistente"
Invoke-ExpectTwo $Python @(
    (Join-Path $Payload "setup_soul.py"),
    "--non-interactive", "--name", $MissingName, "--template", "asistente",
    "--ollama-url", $OllamaUrl, "--model", "__soul_qa_missing_model__"
)
if (Test-Path -LiteralPath (Join-Path $QaRoot ".soul\$MissingName.db")) {
    throw "El caso de modelo inexistente creo una DB"
}
Write-Host "MISSING_MODEL_FAIL_CLOSED_OK"

$TamperApp = Join-Path $QaRoot "tamper-app"
New-Item -ItemType Directory -Path $TamperApp | Out-Null
Copy-Item -LiteralPath (Join-Path $Payload "setup_soul.py") -Destination $TamperApp
Copy-Item -LiteralPath (Join-Path $Payload "official_trust_keys.json") -Destination $TamperApp
Copy-Item -LiteralPath (Join-Path $Payload "templates") -Destination $TamperApp -Recurse
$Tampered = Join-Path $TamperApp "templates\programador.soul-template.json"
$TemplateObject = Get-Content -LiteralPath $Tampered -Raw | ConvertFrom-Json
$TemplateObject.ocean.openness = 0.71
$Utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($Tampered, ($TemplateObject | ConvertTo-Json -Depth 20 -Compress), $Utf8)
$TamperedName = "QA-firma-alterada"
Invoke-ExpectTwo $Python @(
    (Join-Path $TamperApp "setup_soul.py"),
    "--non-interactive", "--name", $TamperedName, "--template", "programador",
    "--skip-ollama-check"
)
if (Test-Path -LiteralPath (Join-Path $QaRoot ".soul\$TamperedName.db")) {
    throw "La plantilla alterada creo una DB"
}
Write-Host "TAMPERED_TEMPLATE_FAIL_CLOSED_OK"

Invoke-Checked $Python @(
    (Join-Path $RepoRoot "installer\windows\qa-container\ollama_probe.py"),
    $OllamaUrl,
    $OllamaModel
)

Write-Host "QA_ROOT=$QaRoot"
Write-Host "SOUL_NATIVE_WINDOWS_E2E_OK"
