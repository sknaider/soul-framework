#!/usr/bin/env python3
"""Onboarding local en español para el instalador autónomo de SOUL Core."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from soul_framework import Soul

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
TRUST_FILE = APP_DIR / "official_trust_keys.json"
SOUL_DIR = Path.home() / ".soul"
OCEAN_MAP = {
    "openness": "O",
    "conscientiousness": "C",
    "extraversion": "E",
    "agreeableness": "A",
    "neuroticism": "N",
}
ALLOWED_FIELDS = {
    "schema", "template_id", "name", "description", "role", "ocean",
    "base_rules", "version", "author", "created_at", "signature",
}
SECRET_PATTERN = re.compile(
    r"(?:gsk_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})"
)


class SetupError(ValueError):
    pass


def _canonical_signing_bytes(template: dict) -> bytes:
    unsigned = {key: value for key, value in template.items() if key != "signature"}
    payload = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return b"SOUL-TEMPLATE-V1\0" + payload


def load_official_template(template_name: str) -> dict:
    template_path = TEMPLATES_DIR / f"{template_name}.soul-template.json"
    if template_path.parent != TEMPLATES_DIR or not template_path.is_file():
        raise SetupError(f"plantilla inexistente: {template_name}")
    if template_path.stat().st_size > 64 * 1024:
        raise SetupError("plantilla sobredimensionada")
    template = json.loads(template_path.read_text(encoding="utf-8"))
    if set(template) - ALLOWED_FIELDS:
        raise SetupError("la plantilla contiene campos desconocidos")
    if template.get("schema") != "soul.template.v1":
        raise SetupError("schema de plantilla inválido")
    ocean = template.get("ocean")
    if not isinstance(ocean, dict) or set(ocean) != set(OCEAN_MAP):
        raise SetupError("perfil OCEAN inválido")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in ocean.values()):
        raise SetupError("valor OCEAN fuera de rango")
    rules = template.get("base_rules")
    if not isinstance(rules, list) or not rules or not all(isinstance(rule, str) for rule in rules):
        raise SetupError("reglas base inválidas")
    if SECRET_PATTERN.search(json.dumps(template, ensure_ascii=False)):
        raise SetupError("la plantilla contiene un posible secreto")

    signature = template.get("signature")
    if not isinstance(signature, dict) or signature.get("alg") != "Ed25519":
        raise SetupError("firma ausente o algoritmo inválido")
    trust = json.loads(TRUST_FILE.read_text(encoding="utf-8"))["official_public_keys"]
    public_key_b64 = signature.get("public_key", "")
    if public_key_b64 not in trust:
        raise SetupError("la firma no pertenece a una raíz oficial")
    try:
        public_key = base64.b64decode(public_key_b64, validate=True)
        signature_bytes = base64.b64decode(signature.get("sig", ""), validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature_bytes, _canonical_signing_bytes(template)
        )
    except (ValueError, InvalidSignature) as exc:
        raise SetupError("firma de plantilla inválida") from exc
    return template


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip("-")
    return slug or "soul"


def normalize_ollama_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        value = "http://" + value
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SetupError("URL de Ollama inválida")
    if parsed.username or parsed.password:
        raise SetupError("la URL de Ollama no debe incluir credenciales")
    path = parsed.path.rstrip("/")
    for suffix in ("/api/generate", "/api/tags", "/v1"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")).rstrip("/")


def check_ollama(base_url: str, model: str, timeout: float = 5.0) -> tuple[bool, str]:
    if not base_url:
        return False, "Ollama no configurado"
    request = urllib.request.Request(base_url + "/api/tags", headers={"User-Agent": "SOUL-Core-Installer"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return False, f"Ollama no responde: {type(exc).__name__}"
    models = {
        item.get("name", "") for item in payload.get("models", []) if isinstance(item, dict)
    }
    if model and model not in models:
        return False, f"Ollama responde, pero no anunció el modelo {model!r}"
    return True, f"Ollama conectado; {len(models)} modelo(s) disponible(s)"


async def create_soul(name: str, template: dict, db_path: Path) -> None:
    ocean = {short: float(template["ocean"][long]) for long, short in OCEAN_MAP.items()}
    personality = {
        "personality": f"{template['description']} Rol inicial: {template['role']}."
    }
    async with Soul.create(
        name, backend_url=str(db_path), ocean=ocean, personality=personality
    ) as soul:
        for index, rule in enumerate(template["base_rules"], start=1):
            await soul.rules.set(
                f"template-{index}", rule, priority="normal", set_by=template["template_id"]
            )


def _ask(prompt: str, default: str) -> str:
    answer = input(f"{prompt} [{default}]: ").strip()
    return answer or default


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configura un alma local de SOUL Core")
    parser.add_argument("--name", default="")
    parser.add_argument("--template", choices=("asistente", "programador", "investigador", "compañero"), default="")
    parser.add_argument("--ollama-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--skip-ollama-check", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print("SOUL Core — crea tu alma\n")
    if args.non_interactive:
        if not args.name or not args.template:
            raise SetupError("--name y --template son obligatorios en modo no interactivo")
        name, template_name = args.name, args.template
    else:
        name = args.name or _ask("Nombre de tu alma", "Maya")
        print("\nPlantillas oficiales: 1) Asistente  2) Programador  3) Investigador  4) Compañero")
        selected = args.template or _ask("Elige 1, 2, 3 o 4", "1")
        template_name = {"1": "asistente", "2": "programador", "3": "investigador", "4": "compañero"}.get(selected, selected.lower())
        if template_name not in {"asistente", "programador", "investigador", "compañero"}:
            raise SetupError("selección de plantilla inválida")

    template = load_official_template(template_name)
    SOUL_DIR.mkdir(parents=True, exist_ok=True)
    db_path = SOUL_DIR / f"{_slug(name)}.db"
    if db_path.exists():
        raise SetupError(f"ya existe un alma llamada {name!r}; no se sobrescribió")

    env_url = os.environ.get("OLLAMA_HOST") or os.environ.get("SOUL_UPSTREAM_URL") or "http://127.0.0.1:11434"
    env_model = os.environ.get("SOUL_MODEL") or "qwen2.5:7b"
    ollama_url = normalize_ollama_url(args.ollama_url or (env_url if args.non_interactive else _ask("Dirección de Ollama", env_url)))
    model = args.model or (env_model if args.non_interactive else _ask("Modelo de Ollama", env_model))
    ollama_ok, ollama_message = check_ollama(ollama_url, model)
    print(f"\n{ollama_message}")
    if not ollama_ok and not args.skip_ollama_check:
        raise SetupError("no se creó el alma; corrige Ollama o usa --skip-ollama-check")

    asyncio.run(create_soul(name, template, db_path))
    config = {
        "schema": "soul.desktop.v1",
        "name": name,
        "database": str(db_path),
        "template_id": template["template_id"],
        "template_verified": True,
        "ollama_base_url": ollama_url,
        "ollama_generate_url": ollama_url + "/api/generate" if ollama_url else "",
        "model": model,
    }
    config_path = SOUL_DIR / f"{_slug(name)}.config.json"
    temporary = config_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(config_path)
    print(f"Plantilla oficial verificada: {template['name']} ✓")
    print(f"Alma creada: {name} -> {db_path}")
    print(f"Prueba ahora: soul boot {name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SetupError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
