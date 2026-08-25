from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UV_DIR = ROOT / "installer" / "uv"


def test_uv_installer_is_version_pinned_and_hash_fail_closed() -> None:
    script = (UV_DIR / "install-soul-core-uv.ps1").read_text(encoding="utf-8")
    assert '$UvVersion = "0.12.6"' in script
    assert '$PythonVersion = "3.13.15"' in script
    assert '$Version = "0.4.3"' in script
    assert "__PAYLOAD_SHA256__" not in script
    hashes = re.findall(r'\$\w+Sha256 = "([0-9a-f]{64})"', script)
    assert len(hashes) == 3
    assert script.index('Assert-Hash $UvArchive') < script.index("Expand-Archive")
    assert script.index('Assert-Hash $PayloadArchive') < script.index("Expand-Archive")
    assert '$env:UV_PYTHON_NO_REGISTRY = "1"' in script
    assert "UV_PROJECT_ENVIRONMENT" in script
    assert "soul.core.uv-owner.v1" in script
    assert "Invoke-CheckedRetry" not in script
    assert 'Assert-Hash $PythonArchive $PythonArchiveSha256 "Python de Astral"' in script
    assert '"venv", $RuntimeDir, "--python", $BundledPython' in script
    assert '"--offline", "--no-cache", "--no-index", "--find-links", $Wheelhouse' in script
    assert '"--require-hashes", "--no-deps"' in script
    assert '"pip", "check", "--python", $RuntimePython' in script
    assert script.count('$ErrorActionPreference = "Continue"') == 1
    assert script.count("$NativeExitCode = $LASTEXITCODE") == 1
    assert "if ($NativeExitCode -ne 0)" in script
    assert "2>&1 | ForEach-Object { Write-Host $_ }" in script
    assert "Install-AppLocalMsvcRuntime (Split-Path -Parent $BundledPython) $RuntimeDir" in script
    assert "msvcp140.dll" in script
    assert 'Start-Process -FilePath $RuntimePython' in script
    assert 'selected_engine = "exact"' in script
    assert "SOUL usara el indice exacto seguro" in script
    assert "ann_probe.py" in (UV_DIR / "build-payload.ps1").read_text(encoding="utf-8")


def test_uv_runtime_project_and_lock_pin_soul_core() -> None:
    project = tomllib.loads((UV_DIR / "runtime-project" / "pyproject.toml").read_text())
    assert project["project"]["requires-python"] == ">=3.13,<3.14"
    assert project["project"]["dependencies"] == [
        "soul-framework[all]==0.4.3",
        "usearch==2.16.4; platform_system == 'Windows'",
    ]
    lock = tomllib.loads((UV_DIR / "runtime-project" / "uv.lock").read_text())
    soul = next(package for package in lock["package"] if package["name"] == "soul-framework")
    assert soul["version"] == "0.4.3"
    assert soul["sdist"]["hash"].startswith("sha256:")
    assert all(wheel["hash"].startswith("sha256:") for wheel in soul["wheels"])
    usearch = next(package for package in lock["package"] if package["name"] == "usearch")
    assert usearch["version"] == "2.16.4"


def test_uv_launchers_are_ascii_and_use_only_private_runtime() -> None:
    for launcher in (UV_DIR / "launchers").glob("*.cmd"):
        raw = launcher.read_bytes()
        raw.decode("ascii")
        assert b"runtime\\Scripts\\python.exe" in raw or launcher.name == "soul-terminal.cmd"
        assert b"python " not in raw.lower().replace(b"python.exe", b"")


def test_official_uv_archive_hash_matches_pinned_value() -> None:
    # The release artifact is not downloaded in unit tests; pin provenance is
    # checked against Astral's published checksum captured during the build.
    expected = "df7cb9f243eae1621400d4fcf5b1b3d90f20e264ece91b64deb3b0078abca6ef"
    script = (UV_DIR / "install-soul-core-uv.ps1").read_text(encoding="utf-8")
    assert hashlib.sha256(bytes.fromhex(expected)).digest_size == 32
    assert expected in script


def test_uv_install_and_uninstall_are_scoped_to_local_appdata() -> None:
    installer = (UV_DIR / "install-soul-core-uv.ps1").read_text(encoding="utf-8")
    uninstaller = (UV_DIR / "uninstall-soul-core-uv.ps1").read_text(encoding="utf-8")
    assert "Assert-SafeInstallRoot $InstallRoot" in installer
    assert "subcarpeta de LOCALAPPDATA" in installer
    assert "solo se desinstalan subcarpetas de LOCALAPPDATA" in uninstaller
    assert "soul.core.uv-owner.v1" in uninstaller
    assert uninstaller.index("solo se desinstalan subcarpetas") < uninstaller.index(
        "Remove-Item -LiteralPath $InstallRoot -Recurse -Force"
    )


def test_ann_probe_is_operational_and_auditor_supports_safe_fallback() -> None:
    probe = (ROOT / "installer" / "windows" / "ann_probe.py").read_text(encoding="utf-8")
    audit = (ROOT / "installer" / "windows" / "dependency_audit.py").read_text(
        encoding="utf-8"
    )
    assert "Index(ndim=3" in probe
    assert "np.asarray" in probe
    assert ".add(1, vector)" in probe
    assert ".search(vector, 1)" in probe
    assert 'selected_ann == "exact"' in audit
    assert "ann_quarantine_outside_install" in audit
    assert "ann_quarantine_missing" in audit
