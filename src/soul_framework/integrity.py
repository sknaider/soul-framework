"""Opt-in integrity barrier for the SQLite memory store.

This module is deliberately isolated from :mod:`soul_framework.memory.store`.
Nothing changes unless an application explicitly constructs a
``SQLiteMemoryIntegrityGuard`` and calls ``seal_and_publish`` / ``verify_before_serve``.

Security model
--------------

* every SQLite memory snapshot is committed by a deterministic SHA-256 chain;
* the checkpoint is signed with Ed25519 by a key outside the database;
* a ``MonotonicWitness`` independently remembers the newest sequence + digest;
* ``verify_before_serve`` fails closed unless database, signature, checkpoint chain,
  and witness all agree.

The witness interface is the trust boundary.  A file or object on the same host as
the database is useful for tests, but is *not* an external anchor and cannot support
a strong integrity claim after complete host compromise.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

CHECKPOINT_DOMAIN = "SOUL-SQLITE-MEMORY-CHECKPOINT-V1"
GENESIS_DIGEST = "0" * 64


class IntegrityVerificationError(RuntimeError):
    """The memory store must not be served because integrity is unverified."""


class WitnessConflictError(IntegrityVerificationError):
    """A monotonic witness rejected a rollback, fork, or stale update."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_checkpoint(checkpoint: MemoryCheckpoint) -> None:
    if checkpoint.domain != CHECKPOINT_DOMAIN or (
        isinstance(checkpoint.version, bool) or checkpoint.version != 1
    ):
        raise IntegrityVerificationError("unsupported checkpoint domain/version")
    if not all(
        isinstance(value, str) and value
        for value in (checkpoint.stream_id, checkpoint.agent)
    ):
        raise IntegrityVerificationError("checkpoint identity/stream is invalid")
    if (
        isinstance(checkpoint.sequence, bool)
        or not isinstance(checkpoint.sequence, int)
        or checkpoint.sequence < 1
    ):
        raise IntegrityVerificationError(
            "checkpoint sequence must be a positive integer"
        )
    if (
        isinstance(checkpoint.memory_count, bool)
        or not isinstance(checkpoint.memory_count, int)
        or checkpoint.memory_count < 0
    ):
        raise IntegrityVerificationError("checkpoint memory_count is invalid")
    if not all(
        _valid_sha256(value)
        for value in (
            checkpoint.previous_checkpoint_digest,
            checkpoint.memory_head,
            checkpoint.schema_digest,
            checkpoint.digest,
        )
    ):
        raise IntegrityVerificationError("checkpoint contains an invalid digest")


def _encode_sqlite_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"$bytes_b64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise IntegrityVerificationError(
        f"unsupported SQLite value in memory commitment: {type(value).__name__}"
    )


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    """Logical commitment to all memory rows for one agent."""

    agent: str
    memory_count: int
    memory_head: str
    schema_digest: str


@dataclass(frozen=True, slots=True)
class MemoryCheckpoint:
    """Signed, monotonically sequenced memory commitment."""

    domain: str
    version: int
    stream_id: str
    sequence: int
    previous_checkpoint_digest: str
    agent: str
    memory_count: int
    memory_head: str
    schema_digest: str

    def payload(self) -> bytes:
        return _canonical_bytes(asdict(self))

    @property
    def digest(self) -> str:
        return _sha256(self.payload())


@dataclass(frozen=True, slots=True)
class SignedCheckpoint:
    checkpoint: MemoryCheckpoint
    key_id: str
    signature_b64: str

    def as_dict(self) -> dict[str, object]:
        return {
            "checkpoint": asdict(self.checkpoint),
            "key_id": self.key_id,
            "signature_b64": self.signature_b64,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> SignedCheckpoint:
        try:
            raw_checkpoint = value["checkpoint"]
            if not isinstance(raw_checkpoint, Mapping):
                raise TypeError("checkpoint must be an object")
            checkpoint = MemoryCheckpoint(**dict(raw_checkpoint))
            key_id = value["key_id"]
            signature_b64 = value["signature_b64"]
            if not isinstance(key_id, str) or not key_id:
                raise TypeError("key_id must be a non-empty string")
            if not isinstance(signature_b64, str) or not signature_b64:
                raise TypeError("signature_b64 must be a non-empty string")
        except (KeyError, TypeError, ValueError) as exc:
            raise IntegrityVerificationError(
                f"invalid checkpoint envelope: {exc}"
            ) from exc
        return cls(checkpoint, key_id, signature_b64)


@dataclass(frozen=True, slots=True)
class WitnessState:
    """Newest checkpoint observed by the independent monotonic witness."""

    stream_id: str
    sequence: int
    previous_checkpoint_digest: str
    checkpoint_digest: str


@runtime_checkable
class MonotonicWitness(Protocol):
    """Interface implemented by an independently protected witness service.

    ``advance`` must be an atomic compare-and-set.  It must reject a lower sequence,
    a different digest at the same sequence, and an unexpected previous state.
    Transport authentication and durable append-only storage belong to the external
    implementation, not this Core-side client interface.
    """

    def read(self, stream_id: str) -> WitnessState | None: ...

    def advance(
        self,
        stream_id: str,
        *,
        expected: WitnessState | None,
        proposed: WitnessState,
    ) -> WitnessState: ...


class InMemoryMonotonicWitness:
    """Deterministic test adapter; never an external production anchor."""

    def __init__(self) -> None:
        self._states: dict[str, WitnessState] = {}

    def read(self, stream_id: str) -> WitnessState | None:
        return self._states.get(stream_id)

    def advance(
        self,
        stream_id: str,
        *,
        expected: WitnessState | None,
        proposed: WitnessState,
    ) -> WitnessState:
        current = self._states.get(stream_id)
        if current != expected:
            raise WitnessConflictError("witness compare-and-set conflict")
        if proposed.stream_id != stream_id:
            raise WitnessConflictError("witness stream mismatch")
        if current is None:
            if proposed.sequence != 1:
                raise WitnessConflictError("first witnessed sequence must be 1")
            if proposed.previous_checkpoint_digest != GENESIS_DIGEST:
                raise WitnessConflictError("first witnessed state must link to genesis")
        else:
            if proposed.sequence != current.sequence + 1:
                raise WitnessConflictError(
                    "witness sequence must advance by exactly one"
                )
            if proposed.previous_checkpoint_digest != current.checkpoint_digest:
                raise WitnessConflictError("witness checkpoint chain is broken")
            if proposed.checkpoint_digest == current.checkpoint_digest:
                raise WitnessConflictError("new witness state reused the prior digest")
        self._states[stream_id] = proposed
        return proposed


class Ed25519CheckpointSigner:
    """Checkpoint signer backed by a caller-supplied Ed25519 private key."""

    def __init__(self, key_id: str, private_key: object) -> None:
        if not key_id:
            raise ValueError("key_id must not be empty")
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )
        except ImportError as exc:
            raise IntegrityVerificationError(
                "Ed25519 integrity requires the optional 'cryptography' package"
            ) from exc
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("private_key must be an Ed25519PrivateKey")
        self.key_id = key_id
        self._private_key = private_key

    def sign(self, checkpoint: MemoryCheckpoint) -> SignedCheckpoint:
        _validate_checkpoint(checkpoint)
        try:
            signature = self._private_key.sign(checkpoint.payload())
        except (AttributeError, TypeError, ValueError) as exc:
            raise IntegrityVerificationError(f"Ed25519 signing failed: {exc}") from exc
        return SignedCheckpoint(
            checkpoint=checkpoint,
            key_id=self.key_id,
            signature_b64=base64.b64encode(signature).decode("ascii"),
        )


class Ed25519TrustStore:
    """Pinned Ed25519 public keys used to authenticate checkpoint bytes."""

    def __init__(self, keys: Mapping[str, object]) -> None:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )
        except ImportError as exc:
            raise IntegrityVerificationError(
                "Ed25519 integrity requires the optional 'cryptography' package"
            ) from exc
        invalid = [
            key_id
            for key_id, key in keys.items()
            if not key_id or not isinstance(key, Ed25519PublicKey)
        ]
        if invalid:
            raise TypeError(f"trust store contains non-Ed25519 keys: {invalid!r}")
        self._keys = dict(keys)

    def verify(self, envelope: SignedCheckpoint) -> None:
        key = self._keys.get(envelope.key_id)
        if key is None:
            raise IntegrityVerificationError(
                f"checkpoint key is not trusted: {envelope.key_id!r}"
            )
        try:
            signature = base64.b64decode(envelope.signature_b64, validate=True)
            key.verify(signature, envelope.checkpoint.payload())
        except Exception as exc:  # cryptography raises InvalidSignature without detail
            raise IntegrityVerificationError(
                "checkpoint signature verification failed"
            ) from exc


class CheckpointFile:
    """Atomic local envelope cache.  The witness, not this file, supplies freshness."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def read(self) -> SignedCheckpoint:
        try:
            raw = self.path.read_bytes()
        except (FileNotFoundError, OSError) as exc:
            raise IntegrityVerificationError(f"checkpoint unavailable: {exc}") from exc
        if not raw.endswith(b"\n"):
            raise IntegrityVerificationError("checkpoint is not newline-terminated")
        try:
            parsed = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrityVerificationError(
                f"checkpoint is invalid JSON: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise IntegrityVerificationError("checkpoint envelope must be an object")
        envelope = SignedCheckpoint.from_dict(parsed)
        if raw != _canonical_bytes(envelope.as_dict()) + b"\n":
            raise IntegrityVerificationError(
                "checkpoint envelope is not canonical JSON"
            )
        return envelope

    def write(self, envelope: SignedCheckpoint) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = _canonical_bytes(envelope.as_dict()) + b"\n"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                if handle.write(raw) != len(raw):
                    raise OSError("short checkpoint write")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _snapshot_connection(connection: sqlite3.Connection, agent: str) -> MemorySnapshot:
    """Commit logical memory bytes from the caller's current transaction."""

    try:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(memories)")]
        if not columns:
            raise IntegrityVerificationError("memories table does not exist")
        schema_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories'"
        ).fetchone()
        if schema_sql_row is None or not schema_sql_row[0]:
            raise IntegrityVerificationError("memories table schema is unavailable")
        schema_digest = _sha256(schema_sql_row[0].encode("utf-8"))
        head = hashlib.sha256(
            b"SOUL-SQLITE-MEMORY-CHAIN-V1\x00" + bytes.fromhex(schema_digest)
        ).digest()
        count = 0
        quoted_columns = ",".join(
            '"' + name.replace('"', '""') + '"' for name in columns
        )
        for row in connection.execute(
            f"SELECT {quoted_columns} FROM memories WHERE agent=? ORDER BY id",
            (agent,),
        ):
            payload = {name: _encode_sqlite_value(row[name]) for name in columns}
            head = hashlib.sha256(head + _canonical_bytes(payload)).digest()
            count += 1
        return MemorySnapshot(agent, count, head.hex(), schema_digest)
    except sqlite3.Error as exc:
        raise IntegrityVerificationError(f"SQLite snapshot failed: {exc}") from exc


def snapshot_sqlite_memories(
    database: str | os.PathLike[str], agent: str
) -> MemorySnapshot:
    """Commit exact logical bytes from one consistent read transaction."""

    path = Path(database)
    if not path.is_file():
        raise IntegrityVerificationError(f"SQLite database does not exist: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        snapshot = _snapshot_connection(connection, agent)
        connection.commit()
        return snapshot
    finally:
        connection.close()


_PG_PARAM_RE = re.compile(r"\$(\d+)")


def _translate_params(
    sql: str, params: tuple[object, ...]
) -> tuple[str, tuple[object, ...]]:
    refs = [int(match.group(1)) for match in _PG_PARAM_RE.finditer(sql)]
    if not refs:
        return sql, params
    try:
        reordered = tuple(params[index - 1] for index in refs)
    except IndexError as exc:
        raise IntegrityVerificationError("verified query parameter mismatch") from exc
    return _PG_PARAM_RE.sub("?", sql), reordered


class SQLiteMemoryIntegrityGuard:
    """Explicit opt-in seal/publish and verify-before-serve barrier."""

    def __init__(
        self,
        *,
        database: str | os.PathLike[str],
        agent: str,
        stream_id: str,
        checkpoint_file: CheckpointFile,
        witness: MonotonicWitness,
        trust_store: Ed25519TrustStore,
        signer: Ed25519CheckpointSigner | None = None,
    ) -> None:
        if not agent or not stream_id:
            raise ValueError("agent and stream_id must not be empty")
        self.database = Path(database)
        self.agent = agent
        self.stream_id = stream_id
        self.checkpoint_file = checkpoint_file
        self.witness = witness
        self.trust_store = trust_store
        self.signer = signer

    def _verified_checkpoint(self, connection: sqlite3.Connection) -> MemoryCheckpoint:
        """Verify the signed head and the caller's pinned SQLite snapshot."""

        envelope = self.checkpoint_file.read()
        checkpoint = envelope.checkpoint
        _validate_checkpoint(checkpoint)
        if checkpoint.stream_id != self.stream_id or checkpoint.agent != self.agent:
            raise IntegrityVerificationError("checkpoint identity/stream mismatch")
        self.trust_store.verify(envelope)
        witness_state = self.witness.read(self.stream_id)
        expected_state = WitnessState(
            self.stream_id,
            checkpoint.sequence,
            checkpoint.previous_checkpoint_digest,
            checkpoint.digest,
        )
        if witness_state is None:
            raise IntegrityVerificationError("external witness is unavailable")
        if witness_state != expected_state:
            raise IntegrityVerificationError(
                "checkpoint is stale, forked, or not the externally witnessed head"
            )
        live = _snapshot_connection(connection, self.agent)
        if (
            live.memory_count != checkpoint.memory_count
            or live.memory_head != checkpoint.memory_head
            or live.schema_digest != checkpoint.schema_digest
        ):
            raise IntegrityVerificationError(
                "live memory state does not match the witnessed signed checkpoint"
            )
        return checkpoint

    def _verified_query(
        self,
        sql: str,
        params: tuple[object, ...],
        *,
        mode: str,
    ) -> object:
        """Run a SELECT in the exact SQLite snapshot whose bytes were verified.

        Keeping ``BEGIN`` open across verification and fetch removes the
        verify-then-query TOCTOU window: a concurrent writer may commit, but this
        reader continues serving the already-verified snapshot.
        """

        if not sql.lstrip().upper().startswith("SELECT ") or ";" in sql:
            raise IntegrityVerificationError("verified reads accept one SELECT only")
        translated_sql, translated_params = _translate_params(sql, params)
        path = self.database.resolve()
        connection = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=5.0, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN")
            self._verified_checkpoint(connection)
            cursor = connection.execute(translated_sql, translated_params)
            if mode == "all":
                result: object = [dict(row) for row in cursor.fetchall()]
            elif mode == "one":
                row = cursor.fetchone()
                result = None if row is None else dict(row)
            elif mode == "value":
                row = cursor.fetchone()
                result = None if row is None else row[0]
            else:  # pragma: no cover - private contract
                raise ValueError(f"unsupported verified query mode: {mode}")
            connection.commit()
            return result
        except sqlite3.Error as exc:
            raise IntegrityVerificationError(
                f"verified SQLite read failed: {exc}"
            ) from exc
        finally:
            connection.close()

    def verified_fetchall(self, sql: str, *params: object) -> list[dict[str, object]]:
        return self._verified_query(sql, params, mode="all")  # type: ignore[return-value]

    def verified_fetchone(self, sql: str, *params: object) -> dict[str, object] | None:
        return self._verified_query(sql, params, mode="one")  # type: ignore[return-value]

    def verified_fetchval(self, sql: str, *params: object) -> object:
        return self._verified_query(sql, params, mode="value")

    def _publish_snapshot(self, snapshot: MemorySnapshot) -> SignedCheckpoint:
        if self.signer is None:
            raise IntegrityVerificationError("this guard has no checkpoint signer")
        previous = self.witness.read(self.stream_id)
        sequence = 1 if previous is None else previous.sequence + 1
        previous_digest = (
            GENESIS_DIGEST if previous is None else previous.checkpoint_digest
        )
        checkpoint = MemoryCheckpoint(
            domain=CHECKPOINT_DOMAIN,
            version=1,
            stream_id=self.stream_id,
            sequence=sequence,
            previous_checkpoint_digest=previous_digest,
            agent=self.agent,
            memory_count=snapshot.memory_count,
            memory_head=snapshot.memory_head,
            schema_digest=snapshot.schema_digest,
        )
        envelope = self.signer.sign(checkpoint)
        self.trust_store.verify(envelope)
        proposed = WitnessState(
            self.stream_id,
            sequence,
            previous_digest,
            checkpoint.digest,
        )
        observed = self.witness.advance(
            self.stream_id, expected=previous, proposed=proposed
        )
        if observed != proposed:
            raise WitnessConflictError("witness returned a different state")
        self.checkpoint_file.write(envelope)
        return envelope

    def mutate_and_publish(
        self,
        sql: str,
        *params: object,
        mode: str = "rowcount",
    ) -> object:
        """Verify, mutate, sign and commit while holding one SQLite write lock.

        The witness/checkpoint advance precedes the SQLite commit deliberately.  A
        crash at any intermediate point leaves disagreement and therefore fails
        closed; it never serves an unsigned mutation.
        """

        statement = sql.lstrip().upper()
        if not statement.startswith(("INSERT ", "UPDATE ")) or ";" in sql:
            raise IntegrityVerificationError(
                "guarded mutations accept one INSERT/UPDATE"
            )
        translated_sql, translated_params = _translate_params(sql, params)
        connection = sqlite3.connect(
            self.database.resolve(), timeout=5.0, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._verified_checkpoint(connection)
            cursor = connection.execute(translated_sql, translated_params)
            if mode == "one":
                row = cursor.fetchone()
                result: object = None if row is None else dict(row)
            elif mode == "rowcount":
                result = cursor.rowcount
            else:  # pragma: no cover - private contract
                raise ValueError(f"unsupported guarded mutation mode: {mode}")
            self._publish_snapshot(_snapshot_connection(connection, self.agent))
            connection.commit()
            return result
        except sqlite3.Error as exc:
            connection.rollback()
            raise IntegrityVerificationError(
                f"guarded SQLite mutation failed: {exc}"
            ) from exc
        finally:
            connection.close()

    def seal_and_publish(self) -> SignedCheckpoint:
        """Sign current memory state and atomically advance the external witness.

        Witness advancement happens before the local envelope write.  A crash between
        them leaves a newer witness and an older local file, so reads fail closed.
        """

        connection = sqlite3.connect(
            self.database.resolve(), timeout=5.0, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            envelope = self._publish_snapshot(
                _snapshot_connection(connection, self.agent)
            )
            connection.commit()
            return envelope
        except sqlite3.Error as exc:
            connection.rollback()
            raise IntegrityVerificationError(f"SQLite seal failed: {exc}") from exc
        finally:
            connection.close()

    def verify_before_serve(self) -> MemoryCheckpoint:
        """Fail closed unless signed checkpoint, witness and live DB all agree."""

        path = self.database.resolve()
        connection = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=5.0, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN")
            checkpoint = self._verified_checkpoint(connection)
            connection.commit()
            return checkpoint
        finally:
            connection.close()
