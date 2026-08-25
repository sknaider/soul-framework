#!/usr/bin/env python3
"""Diagnóstico no destructivo de la instalación de escritorio."""

from __future__ import annotations

import json
import platform
import sys
from importlib.metadata import version
from pathlib import Path

from setup_soul import check_ollama


def main() -> int:
    soul_dir = Path.home() / ".soul"
    configs = sorted(soul_dir.glob("*.config.json")) if soul_dir.is_dir() else []
    print(f"Python privado: {platform.python_version()} ({sys.executable})")
    print(f"SOUL Core: {version('soul-framework')}")
    print(f"Directorio de almas: {soul_dir}")
    print(f"Almas configuradas: {len(configs)}")
    failures = 0
    for config_path in configs:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        database = Path(config["database"])
        db_ok = database.is_file() and database.stat().st_size > 0
        ollama_ok, message = check_ollama(config.get("ollama_base_url", ""), config.get("model", ""))
        print(f"- {config.get('name')}: DB={'OK' if db_ok else 'FALTA'}; {message}")
        failures += int(not db_ok or not ollama_ok)
    if not configs:
        print("Aún no hay un alma. Abre 'Configurar mi alma'.")
    print("RESULTADO: " + ("OK" if failures == 0 else f"{failures} problema(s)"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
