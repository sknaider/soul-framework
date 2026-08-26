#!/usr/bin/env bash
set -euo pipefail

installer="/artifacts/SOUL-Core-0.4.3-Windows-x64.exe"
expected_installer_sha256="${SOUL_INSTALLER_SHA256:?SOUL_INSTALLER_SHA256 es obligatorio}"
test -f "$installer"
observed_installer_sha256="$(sha256sum "$installer" | awk '{print $1}')"
test "$observed_installer_sha256" = "$expected_installer_sha256"
echo "INSTALLER_SHA256_OK=$observed_installer_sha256"

python3 - <<'PY'
import json
from pathlib import Path

payload = Path('/payload')
required_files = (
    'python.exe', 'setup_soul.py', 'finalize_install.py', 'ann_probe.py',
    'dependency_audit.py', 'ann-state.json', 'DEPENDENCIES.txt',
    'Lib/site-packages/torch/lib/msvcp140.dll',
    'Lib/site-packages/torch/lib/vcruntime140.dll',
    'Lib/site-packages/torch/lib/vcruntime140_1.dll',
)
missing_files = [name for name in required_files if not (payload / name).is_file()]
if missing_files:
    raise SystemExit(f'missing payload files: {missing_files}')

template_names = sorted(path.name for path in (payload / 'templates').glob('*.json'))
expected_templates = [
    'asistente.soul-template.json', 'companero.soul-template.json',
    'investigador.soul-template.json', 'programador.soul-template.json',
]
if template_names != expected_templates or not all(name.isascii() for name in template_names):
    raise SystemExit(f'non-portable templates: {template_names}')

manifest = (payload / 'DEPENDENCIES.txt').read_text(encoding='utf-8-sig').lower()
if 'file:///' in manifest or 'c:/users/' in manifest or 'c:\\users\\' in manifest:
    raise SystemExit('dependency manifest leaks a local build path')
required_distributions = (
    'soul-framework==', 'aiosqlite==', 'sentence-transformers==', 'numpy==',
    'cryptography==', 'asyncpg==', 'pgvector==', 'httpx==', 'neo4j==',
    'torch==', 'usearch==',
)
missing_distributions = [name for name in required_distributions if name not in manifest]
if missing_distributions:
    raise SystemExit(f'missing distributions: {missing_distributions}')

direct_urls = list((payload / 'Lib/site-packages').glob('soul_framework-*.dist-info/direct_url.json'))
if direct_urls:
    raise SystemExit(f'local wheel provenance leaked into payload: {direct_urls}')

ann_state = json.loads((payload / 'ann-state.json').read_text(encoding='utf-8-sig'))
if ann_state.get('selected_engine') != 'usearch' or ann_state.get('build_validation_only') is not True:
    raise SystemExit(f'invalid build ANN state: {ann_state}')
print('WINDOWS_PAYLOAD_MANIFEST_OK')
PY

python3 -m venv /qa/venv
/qa/venv/bin/python -m pip install --disable-pip-version-check --quiet '/source[dev]'
/qa/venv/bin/python -m pytest -q /source/tests
echo "CONTAINER_SOURCE_SUITE_OK"
echo "SOUL_WINDOWS_CONTAINER_GATE_OK"
