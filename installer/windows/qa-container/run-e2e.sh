#!/usr/bin/env bash
set -euo pipefail

installer="/artifacts/SOUL-Core-0.4.3-Windows-x64.exe"
expected_sha256="${SOUL_INSTALLER_SHA256:?SOUL_INSTALLER_SHA256 es obligatorio}"
ollama_url="${SOUL_OLLAMA_URL:-http://host.docker.internal:11434}"
ollama_model="${SOUL_OLLAMA_MODEL:-richardyoung/gemma-4-12b-coder-abliterated:Q8_0}"

test -f "$installer"
observed_sha256="$(sha256sum "$installer" | awk '{print $1}')"
test "$observed_sha256" = "$expected_sha256"
echo "INSTALLER_SHA256_OK=$observed_sha256"

app="/payload"
test -x "$app/python.exe"
test -f "$app/setup_soul.py"
test -f "$app/templates/companero.soul-template.json"
if find "$app/templates" -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C grep -P '[^\x00-\x7F]'; then
    echo "ERROR: nombre de plantilla no portable" >&2
    exit 1
fi
echo "INSTALLER_PAYLOAD_MOUNT_OK"
python3 /qa/container_preflight.py "$app" "$ollama_url" "$ollama_model"

if [[ "${SOUL_QA_SKIP_GENERATE:-0}" != "1" ]]; then
    python3 /qa/ollama_probe.py "$ollama_url" "$ollama_model"
fi

echo "SOUL_WINDOWS_CONTAINER_PREFLIGHT_OK"
