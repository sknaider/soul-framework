#!/usr/bin/env python3
"""Falla si el runtime completo no contiene todas las dependencias declaradas."""

from __future__ import annotations

import importlib
import json
import sys
from importlib.metadata import PackageNotFoundError, version

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

    for module in (*REQUIRED_IMPORTS, ann_distribution):
        try:
            importlib.import_module(module)
        except Exception as exc:  # importa DLLs reales; cualquier fallo invalida el runtime
            failures.append(f"import_failed:{module}:{type(exc).__name__}:{exc}")

    print(json.dumps({"python": sys.version.split()[0], "versions": versions, "failures": failures}, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
