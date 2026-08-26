import importlib.util
import json
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parents[2] / "installer" / "windows"
SPEC = importlib.util.spec_from_file_location("dependency_audit", HERE / "dependency_audit.py")
dependency_audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dependency_audit)


@pytest.mark.parametrize("layout", ["exe", "uv"])
def test_dependency_audit_accepts_both_supported_install_layouts(tmp_path, monkeypatch, layout):
    app = tmp_path if layout == "exe" else tmp_path / "app"
    app.mkdir(exist_ok=True)
    state_root = app if layout == "exe" else tmp_path
    (state_root / "ann-state.json").write_text(
        json.dumps({"selected_engine": "usearch", "quarantine_path": ""}),
        encoding="utf-8",
    )
    monkeypatch.setattr(dependency_audit, "__file__", str(app / "dependency_audit.py"))
    monkeypatch.setattr(dependency_audit, "REQUIRED_DISTRIBUTIONS", ())
    monkeypatch.setattr(dependency_audit, "REQUIRED_IMPORTS", ())
    monkeypatch.setattr(dependency_audit, "version", lambda _name: "test")
    monkeypatch.setattr(dependency_audit.importlib, "import_module", lambda _name: object())
    assert dependency_audit.main() == 0
