import importlib.util
import json
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parents[2] / "installer" / "windows"
SPEC = importlib.util.spec_from_file_location("finalize_install", HERE / "finalize_install.py")
finalize_install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(finalize_install)


def _completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_finalizer_keeps_working_native_engine(tmp_path, monkeypatch):
    results = iter((_completed(0, "USEARCH_OPERATION_OK\n"), _completed(0, "audit ok\n")))
    monkeypatch.setattr(finalize_install, "APP_DIR", tmp_path)
    monkeypatch.setattr(finalize_install, "_run", lambda _command: next(results))

    assert finalize_install.main() == 0
    state = json.loads((tmp_path / "ann-state.json").read_text(encoding="utf-8"))
    assert state["selected_engine"] == "usearch"
    assert state["probe_exit_code"] == 0
    assert state["build_validation_only"] is False
    assert (tmp_path / "dependency-audit.stdout.log").read_text() == "audit ok\n"


def test_finalizer_quarantines_crashing_native_engine(tmp_path, monkeypatch):
    usearch = tmp_path / "Lib" / "site-packages" / "usearch"
    usearch.mkdir(parents=True)
    (usearch / "__init__.py").write_text("broken native package", encoding="utf-8")
    results = iter((_completed(3221225477, stderr="illegal instruction"), _completed(0, "audit ok\n")))
    monkeypatch.setattr(finalize_install, "APP_DIR", tmp_path)
    monkeypatch.setattr(finalize_install, "_run", lambda _command: next(results))

    assert finalize_install.main() == 0
    state = json.loads((tmp_path / "ann-state.json").read_text(encoding="utf-8"))
    quarantine = Path(state["quarantine_path"])
    assert state["selected_engine"] == "exact"
    assert quarantine.is_dir()
    assert quarantine.is_relative_to(tmp_path / "quarantine")
    assert not usearch.exists()


def test_finalizer_fails_when_dependency_audit_fails(tmp_path, monkeypatch):
    results = iter((_completed(0), _completed(1, "missing dependency")))
    monkeypatch.setattr(finalize_install, "APP_DIR", tmp_path)
    monkeypatch.setattr(finalize_install, "_run", lambda _command: next(results))
    assert finalize_install.main() == 22
