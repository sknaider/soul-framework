#!/usr/bin/env python3
"""Validate target hardware and fail closed before the installer succeeds."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _write_log(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8", errors="replace")


def main() -> int:
    probe = _run([sys.executable, "-X", "faulthandler", str(APP_DIR / "ann_probe.py")])
    _write_log(APP_DIR / "ann-probe.stdout.log", probe.stdout)
    _write_log(APP_DIR / "ann-probe.stderr.log", probe.stderr)
    state: dict[str, object] = {
        "schema": "soul.core.ann-state.v1",
        "probe_exit_code": probe.returncode,
        "selected_engine": "usearch",
        "quarantine_path": "",
        "build_validation_only": False,
    }
    if probe.returncode != 0:
        usearch_dir = APP_DIR / "Lib" / "site-packages" / "usearch"
        if not usearch_dir.is_dir():
            print("ANN probe failed and usearch package is missing", file=sys.stderr)
            return 21
        quarantine_root = APP_DIR / "quarantine"
        quarantine_root.mkdir(parents=True, exist_ok=True)
        quarantine = quarantine_root / f"usearch-{uuid.uuid4().hex}"
        shutil.move(str(usearch_dir), str(quarantine))
        state["selected_engine"] = "exact"
        state["quarantine_path"] = str(quarantine)

    state_path = APP_DIR / "ann-state.json"
    temporary = state_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(state_path)

    audit = _run([sys.executable, str(APP_DIR / "dependency_audit.py")])
    _write_log(APP_DIR / "dependency-audit.stdout.log", audit.stdout)
    _write_log(APP_DIR / "dependency-audit.stderr.log", audit.stderr)
    if audit.returncode != 0:
        print(audit.stdout, file=sys.stderr)
        print(audit.stderr, file=sys.stderr)
        return 22
    print(f"SOUL_RUNTIME_FINALIZED ann_engine={state['selected_engine']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
