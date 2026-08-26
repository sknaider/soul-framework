import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[2] / "installer" / "windows"
SPEC = importlib.util.spec_from_file_location("setup_soul", HERE / "setup_soul.py")
setup_soul = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup_soul)


@pytest.mark.parametrize("name", ["asistente", "programador", "investigador", "companero"])
def test_all_bundled_templates_verify(name):
    template = setup_soul.load_official_template(name)
    assert template["name"]


def test_bundled_template_filenames_are_windows_portable():
    names = [path.name for path in (HERE / "templates").glob("*.json")]
    assert names
    assert all(name.isascii() for name in names)


def test_legacy_spanish_template_alias_still_works(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_soul, "SOUL_DIR", tmp_path)
    monkeypatch.setattr(setup_soul, "check_ollama", lambda *_args, **_kwargs: (True, "ok"))

    async def fake_create_soul(_name, _template, db_path):
        db_path.write_bytes(b"soul")

    monkeypatch.setattr(setup_soul, "create_soul", fake_create_soul)
    assert setup_soul.main([
        "--non-interactive", "--name", "Alias", "--template", "compañero",
        "--skip-ollama-check",
    ]) == 0
    config = json.loads((tmp_path / "Alias.config.json").read_text(encoding="utf-8"))
    assert config["template_id"] == "urn:soul:template:companero-v1"


def test_interactive_option_four_resolves_companion(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_soul, "SOUL_DIR", tmp_path)
    answers = iter(("Zero", "4", "http://127.0.0.1:11434", "qwen2.5:7b"))
    monkeypatch.setattr(setup_soul, "_ask", lambda *_args: next(answers))

    async def fake_create_soul(_name, _template, db_path):
        db_path.write_bytes(b"soul")

    monkeypatch.setattr(setup_soul, "create_soul", fake_create_soul)
    assert setup_soul.main(["--skip-ollama-check"]) == 0
    config = json.loads((tmp_path / "Zero.config.json").read_text(encoding="utf-8"))
    assert config["template_id"] == "urn:soul:template:companero-v1"


def test_one_byte_tamper_is_rejected(tmp_path, monkeypatch):
    source = HERE / "templates" / "programador.soul-template.json"
    template = json.loads(source.read_text(encoding="utf-8"))
    template["ocean"]["openness"] = 0.71
    target_dir = tmp_path / "templates"
    target_dir.mkdir()
    (target_dir / source.name).write_text(json.dumps(template), encoding="utf-8")
    monkeypatch.setattr(setup_soul, "TEMPLATES_DIR", target_dir)
    with pytest.raises(setup_soul.SetupError, match="firma"):
        setup_soul.load_official_template("programador")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("100.72.212.53:11434", "http://100.72.212.53:11434"),
        ("http://host:11434/v1", "http://host:11434"),
        ("http://host:11434/api/generate", "http://host:11434"),
    ],
)
def test_normalize_ollama_url(raw, expected):
    assert setup_soul.normalize_ollama_url(raw) == expected


def test_url_credentials_are_rejected():
    with pytest.raises(setup_soul.SetupError, match="credenciales"):
        setup_soul.normalize_ollama_url("http://user:password@host:11434")


def test_existing_soul_is_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_soul, "SOUL_DIR", tmp_path)
    (tmp_path / "Maya.db").write_bytes(b"existing")
    with pytest.raises(setup_soul.SetupError, match="no se sobrescribió"):
        setup_soul.main([
            "--non-interactive", "--name", "Maya", "--template", "asistente",
            "--skip-ollama-check",
        ])


def test_non_interactive_setup_creates_database_and_config(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_soul, "SOUL_DIR", tmp_path)
    monkeypatch.setattr(setup_soul, "check_ollama", lambda *_args, **_kwargs: (True, "Ollama conectado"))
    result = setup_soul.main([
        "--non-interactive", "--name", "Maya", "--template", "programador",
        "--ollama-url", "http://100.72.212.53:11434", "--model", "gemma4:12b-it-qat",
    ])
    assert result == 0
    assert (tmp_path / "Maya.db").stat().st_size > 0
    config = json.loads((tmp_path / "Maya.config.json").read_text(encoding="utf-8"))
    assert config["template_verified"] is True
    assert config["model"] == "gemma4:12b-it-qat"


def test_windows_build_bundles_all_declared_extras():
    build_script = (HERE / "build.ps1").read_text(encoding="utf-8")
    assert '"$($CoreWheel.FullName)[all]"' in build_script
    assert "dependency_audit.py" in build_script
    assert "finalize_install.py" in build_script
    assert "ann_probe.py" in build_script


def test_windows_build_fails_closed_on_native_exit_codes():
    build_script = (HERE / "build.ps1").read_text(encoding="utf-8")
    assert "function Invoke-Checked" in build_script
    assert "$NativeExitCode = $LASTEXITCODE" in build_script
    assert "Invoke-Checked $RuntimePython @((Join-Path $Payload \"dependency_audit.py\"))" in build_script
    assert "Invoke-Checked $Iscc" in build_script
    assert "pip list --format=freeze" in build_script
    assert "pip freeze" not in build_script


def test_windows_build_bundles_app_local_msvc_runtime():
    build_script = (HERE / "build.ps1").read_text(encoding="utf-8")
    assert "function Install-AppLocalMsvcRuntime" in build_script
    assert '"vcruntime140.dll"' in build_script
    assert '"vcruntime140_1.dll"' in build_script
    assert '"sklearn\\.libs\\msvcp140.dll"' in build_script
    assert "Install-AppLocalMsvcRuntime $Payload" in build_script


def test_windows_build_removes_local_core_origin_metadata():
    build_script = (HERE / "build.ps1").read_text(encoding="utf-8")
    assert "function Remove-CoreBuildOriginMetadata" in build_script
    assert '"soul_framework-*.dist-info"' in build_script
    assert '"direct_url.json"' in build_script
    assert "Remove-CoreBuildOriginMetadata $Payload" in build_script


def test_inno_installer_runs_target_hardware_finalizer():
    script = (HERE / "SOUL-Core.iss").read_text(encoding="utf-8")
    assert "procedure CurStepChanged(CurStep: TSetupStep);" in script
    assert "finalize_install.py" in script
    assert "ewWaitUntilTerminated" in script
    assert "RaiseException" in script


def test_cmd_launchers_are_ascii_crlf():
    launchers = sorted(HERE.glob("*.cmd"))
    assert launchers
    for launcher in launchers:
        payload = launcher.read_bytes()
        payload.decode("ascii")
        assert b"\r\n" in payload
        assert b"\n" not in payload.replace(b"\r\n", b"")


def test_windows_build_normalizes_cmd_payload_by_bytes():
    build_script = (HERE / "build.ps1").read_text(encoding="utf-8")
    assert "ASCIIEncoding" in build_script
    assert '($LauncherText -replace "`r?`n", "`r`n")' in build_script
    assert "[IO.File]::WriteAllText($_.FullName, $LauncherText, $Ascii)" in build_script


def test_cmd_launchers_are_ascii_with_windows_line_endings():
    launchers = sorted(HERE.glob("*.cmd"))
    assert launchers
    for launcher in launchers:
        payload = launcher.read_bytes()
        payload.decode("ascii")
        assert payload.endswith(b"\r\n"), launcher.name
        assert b"\n" not in payload.replace(b"\r\n", b""), launcher.name
