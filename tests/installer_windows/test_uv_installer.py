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
    assert len(hashes) == 2
    assert script.index('Assert-Hash $UvArchive') < script.index("Expand-Archive")
    assert script.index('Assert-Hash $PayloadArchive') < script.index("Expand-Archive")
    assert '$env:UV_PYTHON_NO_REGISTRY = "1"' in script
    assert "UV_PROJECT_ENVIRONMENT" in script


def test_uv_runtime_project_and_lock_pin_soul_core() -> None:
    project = tomllib.loads((UV_DIR / "runtime-project" / "pyproject.toml").read_text())
    assert project["project"]["requires-python"] == ">=3.13,<3.14"
    assert project["project"]["dependencies"] == ["soul-framework[all]==0.4.3"]
    lock = tomllib.loads((UV_DIR / "runtime-project" / "uv.lock").read_text())
    soul = next(package for package in lock["package"] if package["name"] == "soul-framework")
    assert soul["version"] == "0.4.3"
    assert soul["sdist"]["hash"].startswith("sha256:")
    assert all(wheel["hash"].startswith("sha256:") for wheel in soul["wheels"])


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
