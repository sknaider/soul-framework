from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from soul_framework.identity.dni import verify_soul_dni


TOOL = Path(__file__).resolve().parents[1] / "tools" / "soul_dni_sia.py"
SPEC = importlib.util.spec_from_file_location("soul_dni_sia", TOOL)
assert SPEC and SPEC.loader
sia = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sia)


def _key(path: Path) -> str:
    private = Ed25519PrivateKey.generate()
    path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return hashlib.sha256(public).hexdigest()


def test_external_sia_issues_bytes_that_core_accepts(tmp_path):
    key = tmp_path / "sia.pem"
    key_pin = _key(key)
    out = tmp_path / "issued"
    machine_soul_id = str(uuid.uuid4())
    receipt = sia.issue(
        private_key_file=key,
        out_dir=out,
        machine_soul_id=machine_soul_id,
        machine_binding_sha256="c" * 64,
        sequence=1,
        state_dir=tmp_path / "state",
    )
    verified = verify_soul_dni(
        receipt["credential"],
        receipt["trust_store"],
        expected_audience="soul-platform",
        expected_machine_soul_id=machine_soul_id,
        expected_machine_binding_sha256="c" * 64,
        expected_trust_store_sha256=receipt["trust_store_sha256"],
        trusted_issuer_key_sha256={"soul-sia-root-1": key_pin},
    )
    assert verified.soul_dni == receipt["soul_dni"]
    assert verified.sequence == 1
    marker = "PRIVATE" + " KEY"
    assert key.read_text().startswith("-----BEGIN " + marker + "-----")
    assert marker not in Path(receipt["credential"]).read_text()
    assert marker not in Path(receipt["trust_store"]).read_text()


def test_sia_never_overwrites_existing_issuance(tmp_path):
    key = tmp_path / "sia.pem"
    _key(key)
    out = tmp_path / "issued"
    kwargs = dict(
        private_key_file=key,
        out_dir=out,
        machine_soul_id=str(uuid.uuid4()),
        machine_binding_sha256="d" * 64,
        state_dir=tmp_path / "state",
    )
    sia.issue(**kwargs)
    before = {path.name: path.read_bytes() for path in out.iterdir()}
    with pytest.raises(FileExistsError):
        sia.issue(**kwargs)
    assert {path.name: path.read_bytes() for path in out.iterdir()} == before


def test_sia_rejects_private_key_through_symlinked_parent(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    key = real / "sia.pem"
    _key(key)
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(PermissionError, match="symlinks"):
        sia.issue(
            private_key_file=linked / "sia.pem",
            out_dir=tmp_path / "issued",
            machine_soul_id=str(uuid.uuid4()),
            machine_binding_sha256="e" * 64,
            state_dir=tmp_path / "state",
        )


def test_sia_rejects_output_through_symlinked_parent(tmp_path):
    key = tmp_path / "sia.pem"
    _key(key)
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(PermissionError, match="symlinks"):
        sia.issue(
            private_key_file=key,
            out_dir=linked / "issued",
            machine_soul_id=str(uuid.uuid4()),
            machine_binding_sha256="f" * 64,
            state_dir=tmp_path / "state",
        )


def test_operational_cli_revokes_dni_monotonically_and_core_rejects_it(tmp_path):
    key = tmp_path / "sia.pem"
    key_pin = _key(key)
    machine_soul_id = str(uuid.uuid4())
    issued = sia.issue(
        private_key_file=key,
        out_dir=tmp_path / "generation-1",
        machine_soul_id=machine_soul_id,
        machine_binding_sha256="a" * 64,
        state_dir=tmp_path / "authority-state",
    )
    revoked_path = tmp_path / "generation-2-trust.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "revoke",
            "--private-key",
            str(key),
            "--previous-trust-store",
            issued["trust_store"],
            "--out-file",
            str(revoked_path),
            "--soul-dni",
            issued["soul_dni"],
            "--state-dir",
            str(tmp_path / "authority-state"),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    receipt = json.loads(completed.stdout)
    assert receipt["sequence"] == 2
    assert json.loads(revoked_path.read_text())["revoked_soul_dnis"] == [
        issued["soul_dni"]
    ]
    with pytest.raises(ValueError, match="revoked"):
        verify_soul_dni(
            issued["credential"],
            revoked_path,
            expected_audience="soul-core",
            expected_machine_soul_id=machine_soul_id,
            expected_machine_binding_sha256="a" * 64,
            expected_trust_store_sha256=receipt["trust_store_sha256"],
            trusted_issuer_key_sha256={"soul-sia-root-1": key_pin},
        )
    with pytest.raises(PermissionError, match="revoked"):
        sia.issue(
            private_key_file=key,
            out_dir=tmp_path / "forbidden-generation-3",
            machine_soul_id=machine_soul_id,
            machine_binding_sha256="a" * 64,
            soul_id=issued["soul_dni"].rsplit(":", 1)[1],
            sequence=3,
            previous_trust_store=revoked_path,
            state_dir=tmp_path / "authority-state",
        )


def test_authority_head_rejects_two_forks_from_the_same_generation(tmp_path):
    key = tmp_path / "sia.pem"
    _key(key)
    state = tmp_path / "authority-state"
    first = sia.issue(
        private_key_file=key,
        out_dir=tmp_path / "generation-1",
        machine_soul_id=str(uuid.uuid4()),
        machine_binding_sha256="b" * 64,
        state_dir=state,
    )
    first_dni = first["soul_dni"]
    second_dni = f"urn:soul:agent:{sia.generate_soul_id()}"
    sia.revoke(
        private_key_file=key,
        previous_trust_store=Path(first["trust_store"]),
        out_file=tmp_path / "generation-2-a.json",
        revoke_soul_dnis=(first_dni,),
        state_dir=state,
    )
    with pytest.raises(RuntimeError, match="replay|stale|changed"):
        sia.revoke(
            private_key_file=key,
            previous_trust_store=Path(first["trust_store"]),
            out_file=tmp_path / "generation-2-b.json",
            revoke_soul_dnis=(second_dni,),
            state_dir=state,
        )
    canonical_head = json.loads((state / "trust-head.json").read_text())
    assert canonical_head["revoked_soul_dnis"] == [first_dni]
