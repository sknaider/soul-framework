#!/usr/bin/env python3
"""Build a hash-verified Windows CPython 3.13 wheelhouse from uv.lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import tomllib
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse


PLAN_LINE = re.compile(r"^ \+ ([a-zA-Z0-9_.-]+)==([^ ]+)$")


def _score_wheel(filename: str) -> int:
    lower = filename.lower()
    if lower.endswith("-py3-none-any.whl") or lower.endswith("-py2.py3-none-any.whl"):
        return 100
    if not lower.endswith("-win_amd64.whl") or "cp313t" in lower:
        return -1
    if "-cp313-cp313-win_amd64.whl" in lower or "-cp313-none-win_amd64.whl" in lower:
        return 400
    match = re.search(r"-cp(\d{2,3})-abi3-win_amd64\.whl$", lower)
    if match and int(match.group(1)) <= 313:
        return 200 + int(match.group(1))
    return -1


def _windows_plan(uv: str, project: Path) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="soul-uv-plan-") as environment:
        env = os.environ.copy()
        env["UV_PROJECT_ENVIRONMENT"] = environment
        command = [
            uv, "sync", "--project", str(project), "--locked", "--no-dev",
            "--python", "3.13.15", "--python-platform", "x86_64-pc-windows-msvc",
            "--dry-run",
        ]
        result = subprocess.run(command, text=True, capture_output=True, env=env, check=False)
    output = result.stdout + "\n" + result.stderr
    if result.returncode:
        raise RuntimeError(f"uv dry-run fallo ({result.returncode}):\n{output}")
    packages = {
        match.group(1).lower().replace("_", "-"): match.group(2)
        for line in output.splitlines()
        if (match := PLAN_LINE.match(line))
    }
    if len(packages) < 40:
        raise RuntimeError(f"plan Windows vacuo o incompleto: {len(packages)} paquetes")
    return packages


def _download(url: str, destination: Path, expected: str) -> None:
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
        return
    if destination.exists():
        raise RuntimeError(f"archivo existente con hash incorrecto: {destination}")
    temporary = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "SOUL-Core-Wheelhouse/0.4.3"})
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as handle:
                while chunk := response.read(1024 * 1024):
                    handle.write(chunk)
            observed = hashlib.sha256(temporary.read_bytes()).hexdigest()
            if observed != expected:
                raise RuntimeError(f"hash inesperado para {destination.name}: {observed}")
            temporary.replace(destination)
            return
        except Exception:
            if temporary.exists():
                temporary.unlink()
            if attempt == 3:
                raise
            time.sleep(attempt)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()

    project = args.project.resolve()
    lock_path = project / "uv.lock"
    locked = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    by_name = {package["name"]: package for package in locked["package"]}
    plan = _windows_plan(args.uv, project)
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        raise RuntimeError(f"wheelhouse debe empezar vacio: {args.output}")

    manifest: list[dict[str, str]] = []
    for name, version in sorted(plan.items()):
        package = by_name.get(name)
        if not package or package["version"] != version:
            raise RuntimeError(f"{name}=={version} no coincide con uv.lock")
        candidates = []
        for wheel in package.get("wheels", []):
            filename = unquote(Path(urlparse(wheel["url"]).path).name)
            score = _score_wheel(filename)
            if score >= 0:
                candidates.append((score, filename, wheel))
        if not candidates:
            raise RuntimeError(f"sin wheel Windows CPython 3.13 para {name}=={version}")
        _, filename, wheel = max(candidates, key=lambda item: item[0])
        expected = wheel["hash"].removeprefix("sha256:")
        destination = args.output / filename
        print(f"[{len(manifest) + 1}/{len(plan)}] {filename}", flush=True)
        _download(wheel["url"], destination, expected)
        manifest.append({"name": name, "version": version, "file": filename, "sha256": expected})

    sums = "".join(f"{item['sha256']}  {item['file']}\n" for item in manifest)
    requirements = "".join(
        f"{item['name']}=={item['version']} --hash=sha256:{item['sha256']}\n"
        for item in manifest
    )
    (args.output / "WHEELHOUSE-SHA256SUMS").write_text(sums, encoding="ascii")
    (args.output / "requirements-windows-x64.txt").write_text(requirements, encoding="ascii")
    (args.output / "WHEELHOUSE-MANIFEST.json").write_text(
        json.dumps({"schema": "soul.core.wheelhouse.v1", "packages": manifest}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"WHEELHOUSE_OK packages={len(manifest)} path={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
