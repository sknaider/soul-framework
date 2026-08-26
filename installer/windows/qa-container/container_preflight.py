from __future__ import annotations

import base64
import json
import sys
import urllib.request
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


payload = Path(sys.argv[1])
ollama_url = sys.argv[2].rstrip("/")
ollama_model = sys.argv[3]
templates_dir = payload / "templates"
trust = set(
    json.loads((payload / "official_trust_keys.json").read_text(encoding="utf-8"))[
        "official_public_keys"
    ]
)
paths = sorted(templates_dir.glob("*.soul-template.json"))
expected = {"asistente", "programador", "investigador", "companero"}
if {path.name.removesuffix(".soul-template.json") for path in paths} != expected:
    raise SystemExit("catalogo de plantillas inesperado")
if any(not path.name.isascii() for path in paths):
    raise SystemExit("nombre de plantilla no portable")

verified: list[str] = []
for path in paths:
    template = json.loads(path.read_text(encoding="utf-8"))
    signature = template["signature"]
    public_key_b64 = signature["public_key"]
    if public_key_b64 not in trust:
        raise SystemExit(f"raiz no confiable: {path.name}")
    unsigned = {key: value for key, value in template.items() if key != "signature"}
    canonical = b"SOUL-TEMPLATE-V1\0" + json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    key.verify(base64.b64decode(signature["sig"]), canonical)
    verified.append(template["template_id"])

tampered = json.loads((templates_dir / "programador.soul-template.json").read_text(encoding="utf-8"))
tampered["ocean"]["openness"] = 0.71
unsigned = {key: value for key, value in tampered.items() if key != "signature"}
canonical = b"SOUL-TEMPLATE-V1\0" + json.dumps(
    unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
).encode("utf-8")
try:
    Ed25519PublicKey.from_public_bytes(
        base64.b64decode(tampered["signature"]["public_key"])
    ).verify(base64.b64decode(tampered["signature"]["sig"]), canonical)
except InvalidSignature:
    pass
else:
    raise SystemExit("la plantilla alterada conservo una firma valida")

with urllib.request.urlopen(ollama_url + "/api/tags", timeout=10) as response:
    tags = json.load(response)
models = {item.get("name") for item in tags.get("models", [])}
if ollama_model not in models:
    raise SystemExit(f"modelo no anunciado por Ollama: {ollama_model}")
if "__soul_qa_missing_model__" in models:
    raise SystemExit("el control negativo de modelo dejo de ser negativo")

print("SIGNED_TEMPLATES_OK=" + str(len(verified)))
print("OLLAMA_TAGS_OK=" + str(len(models)))
