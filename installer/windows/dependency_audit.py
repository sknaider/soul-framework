#!/usr/bin/env python3
"""Falla si el runtime completo no contiene todas las dependencias declaradas."""

from __future__ import annotations

import importlib
import json
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

REQUIRED_DISTRIBUTIONS = (
    "soul-framework",
    "aiosqlite",
    "sentence-transformers",
    "numpy",
    "cryptography",
    "asyncpg",
    "pgvector",
    "httpx",
    "neo4j",
    "torch",
)
REQUIRED_IMPORTS = (
    "soul_framework",
    "aiosqlite",
    "sentence_transformers",
    "numpy",
    "cryptography",
    "asyncpg",
    "pgvector",
    "httpx",
    "neo4j",
    "torch",
)


def main() -> int:
    failures: list[str] = []
    versions: dict[str, str] = {}
    app_dir = Path(__file__).resolve().parent
    # The autonomous EXE installs app files directly in {app}; the uv design
    # uses {install_root}/app. Accept both layouts, but never search outside
    # these two explicit locations.
    state_candidates = (app_dir / "ann-state.json", app_dir.parent / "ann-state.json")
    ann_state_path = next((path for path in state_candidates if path.is_file()), state_candidates[0])
    install_root = ann_state_path.parent
    ann_state: dict[str, object] = {}
    try:
        ann_state = json.loads(ann_state_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        failures.append(f"ann_state_invalid:{type(exc).__name__}:{exc}")
    for distribution in REQUIRED_DISTRIBUTIONS:
        try:
            versions[distribution] = version(distribution)
        except PackageNotFoundError:
            failures.append(f"missing_distribution:{distribution}")

    ann_distribution = "usearch" if sys.version_info >= (3, 13) else "hnswlib"
    try:
        versions[ann_distribution] = version(ann_distribution)
    except PackageNotFoundError:
        failures.append(f"missing_distribution:{ann_distribution}")

    selected_ann = str(ann_state.get("selected_engine", ""))
    if selected_ann == "exact":
        quarantine = Path(str(ann_state.get("quarantine_path", "")))
        quarantine_root = (install_root / "quarantine").resolve()
        try:
            quarantine.resolve().relative_to(quarantine_root)
        except (OSError, ValueError):
            failures.append("ann_quarantine_outside_install")
        if not quarantine.is_dir():
            failures.append("ann_quarantine_missing")
    elif selected_ann != "usearch":
        failures.append(f"ann_engine_invalid:{selected_ann}")

    imports = list(REQUIRED_IMPORTS)
    if selected_ann == "usearch":
        imports.append(ann_distribution)
    for module in imports:
        try:
            importlib.import_module(module)
        except Exception as exc:  # importa DLLs reales; cualquier fallo invalida el runtime
            failures.append(f"import_failed:{module}:{type(exc).__name__}:{exc}")

    print(json.dumps({"python": sys.version.split()[0], "versions": versions, "ann_engine": selected_ann, "failures": failures}, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
