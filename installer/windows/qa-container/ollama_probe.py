from __future__ import annotations

import json
import sys
import urllib.request


base_url, model = sys.argv[1:3]
payload = json.dumps(
    {"model": model, "prompt": "Responde solo: SOUL_QA_OK", "stream": False}
).encode("utf-8")
request = urllib.request.Request(
    base_url.rstrip("/") + "/api/generate",
    data=payload,
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=300) as response:
    result = json.load(response)
if not isinstance(result.get("response"), str) or not result["response"].strip():
    raise SystemExit("Ollama respondio sin texto")
print("OLLAMA_GENERATE_OK model=" + result.get("model", model))
