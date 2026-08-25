import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[2] / "installer" / "windows"
SPEC = importlib.util.spec_from_file_location("setup_soul", HERE / "setup_soul.py")
setup_soul = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup_soul)


@pytest.mark.parametrize("name", ["asistente", "programador", "investigador", "compañero"])
def test_all_bundled_templates_verify(name):
    template = setup_soul.load_official_template(name)
    assert template["name"]


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
