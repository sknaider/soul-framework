"""Reversible SQLite embedding reindexer for SOUL Core.

The source database is never modified.  Reindexing happens in a complete SQLite
backup (the *candidate*) and is resumable through an atomically-written JSON
checkpoint.  Rollback only marks and retains the candidate; it never deletes
the source or migration evidence.

CLI example::

    python -m soul_framework.embedding_migration run soul.db \
      --candidate soul.1024.db --source-dim 128 --target-dim 1024 \
      --provider sentence-transformer --model BAAI/bge-m3
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sqlite3
import struct
import sys
import tempfile
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from soul_framework.embedding.base import EmbeddingProvider
from soul_framework.embedding.simple import SimpleEmbedding

_VECTOR_TABLES = {
    "memories": ("content",),
    "procedural_memories": ("task_description", "workflow"),
}
_CHECKPOINT_VERSION = 1


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _nonvector_hash(path: Path) -> str:
    """Hash every logical value except replaceable embedding blobs."""
    digest = hashlib.sha256()
    with closing(_readonly(path)) as connection:
        for table in sorted(_tables(connection)):
            columns = [
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            ]
            kept = [column for column in columns if column != "embedding"]
            if not kept:
                continue
            quoted = ",".join('"' + column.replace('"', '""') + '"' for column in kept)
            digest.update(f"table:{table}:{','.join(kept)}\n".encode())
            for row in connection.execute(
                f'SELECT {quoted} FROM "{table}" ORDER BY rowid'
            ):
                digest.update(
                    json.dumps(
                        list(row),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ).encode()
                )
                digest.update(b"\n")
    return digest.hexdigest()


def _vector_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with closing(_readonly(path)) as connection:
        available = _tables(connection)
        for table in _VECTOR_TABLES:
            if table not in available:
                continue
            digest.update(f"table:{table}\n".encode())
            for row_id, embedding in connection.execute(
                f'SELECT id,embedding FROM "{table}" ORDER BY id'
            ):
                blob = bytes(embedding) if embedding is not None else b""
                digest.update(str(int(row_id)).encode())
                digest.update(b":")
                digest.update(hashlib.sha256(blob).digest())
    return digest.hexdigest()


def _source_fingerprint(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "nonvector_sha256": _nonvector_hash(path),
        "vector_sha256": _vector_hash(path),
    }


def _dimensions(connection: sqlite3.Connection, table: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for length, count in connection.execute(
        f'SELECT length(embedding), COUNT(*) FROM "{table}" '
        "WHERE embedding IS NOT NULL GROUP BY length(embedding) ORDER BY length(embedding)"
    ):
        label = "invalid" if int(length) % 4 else str(int(length) // 4)
        result[label] = result.get(label, 0) + int(count)
    return result


@dataclass(frozen=True)
class MigrationPlan:
    source: str
    candidate: str
    checkpoint: str
    source_dimensions: int
    target_dimensions: int
    batch_size: int
    rows: dict[str, int]
    observed_dimensions: dict[str, dict[str, int]]
    source_sha256: str
    source_nonvector_sha256: str
    source_vector_sha256: str


def plan_sqlite_migration(
    source: str | Path,
    *,
    candidate: str | Path | None = None,
    checkpoint: str | Path | None = None,
    source_dimensions: int = 128,
    target_dimensions: int = 1024,
    batch_size: int = 256,
) -> MigrationPlan:
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise ValueError(f"source SQLite database does not exist: {source_path}")
    if (
        source_dimensions <= 0
        or target_dimensions <= 0
        or source_dimensions == target_dimensions
    ):
        raise ValueError("source and target dimensions must be positive and different")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    candidate_path = (
        Path(candidate or f"{source_path}.emb-{target_dimensions}.candidate.db")
        .expanduser()
        .resolve()
    )
    checkpoint_path = (
        Path(checkpoint or f"{candidate_path}.checkpoint.json").expanduser().resolve()
    )
    if (
        source_path == candidate_path
        or source_path == checkpoint_path
        or candidate_path == checkpoint_path
    ):
        raise ValueError("source, candidate, and checkpoint paths must be distinct")
    fingerprint = _source_fingerprint(source_path)
    rows: dict[str, int] = {}
    dimensions: dict[str, dict[str, int]] = {}
    with closing(_readonly(source_path)) as connection:
        available = _tables(connection)
        if "memories" not in available:
            raise ValueError(
                "source is not a SOUL SQLite database: memories table missing"
            )
        for table in _VECTOR_TABLES:
            if table not in available:
                continue
            rows[table] = int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE embedding IS NOT NULL'
                ).fetchone()[0]
            )
            dimensions[table] = _dimensions(connection, table)
            unexpected = set(dimensions[table]) - {str(source_dimensions)}
            if unexpected:
                raise ValueError(
                    f"{table} has embedding dimensions other than {source_dimensions}: "
                    f"{dimensions[table]}"
                )
    return MigrationPlan(
        source=str(source_path),
        candidate=str(candidate_path),
        checkpoint=str(checkpoint_path),
        source_dimensions=source_dimensions,
        target_dimensions=target_dimensions,
        batch_size=batch_size,
        rows=rows,
        observed_dimensions=dimensions,
        source_sha256=fingerprint["sha256"],
        source_nonvector_sha256=fingerprint["nonvector_sha256"],
        source_vector_sha256=fingerprint["vector_sha256"],
    )


def _copy_database(source: Path, candidate: Path) -> None:
    candidate.parent.mkdir(parents=True, exist_ok=True)
    source_connection = _readonly(source)
    destination = sqlite3.connect(candidate)
    os.chmod(candidate, 0o600)
    try:
        source_connection.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source_connection.close()


def _new_checkpoint(plan: MigrationPlan, provider_name: str) -> dict[str, Any]:
    return {
        "version": _CHECKPOINT_VERSION,
        "status": "running",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "plan": asdict(plan),
        "provider": provider_name,
        "tables": {
            table: {"last_id": 0, "completed_rows": 0, "total_rows": total}
            for table, total in plan.rows.items()
        },
    }


def _load_checkpoint(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid checkpoint {path}: {exc}") from exc
    if value.get("version") != _CHECKPOINT_VERSION:
        raise ValueError("unsupported checkpoint version")
    return value


def _validate_resume(
    plan: MigrationPlan, state: dict[str, Any], provider_name: str
) -> None:
    stored = state.get("plan", {})
    for field in (
        "source",
        "candidate",
        "checkpoint",
        "source_dimensions",
        "target_dimensions",
    ):
        if stored.get(field) != getattr(plan, field):
            raise ValueError(f"resume mismatch for {field}")
    if state.get("provider") != provider_name:
        raise ValueError("resume provider does not match checkpoint")
    if state.get("status") not in {"running", "paused"}:
        raise ValueError(f"checkpoint is not resumable: {state.get('status')}")
    current = _source_fingerprint(Path(plan.source))
    if (
        current["sha256"] != stored.get("source_sha256")
        or current["nonvector_sha256"] != stored.get("source_nonvector_sha256")
        or current["vector_sha256"] != stored.get("source_vector_sha256")
    ):
        raise ValueError("source database changed since migration started")


async def migrate_sqlite_embeddings(
    source: str | Path,
    provider: EmbeddingProvider,
    *,
    candidate: str | Path | None = None,
    checkpoint: str | Path | None = None,
    source_dimensions: int = 128,
    target_dimensions: int = 1024,
    batch_size: int = 256,
    dry_run: bool = False,
    resume: bool = False,
    max_batches: int | None = None,
    provider_name: str = "custom",
) -> dict[str, Any]:
    """Create/resume a non-destructive reindexed candidate.

    ``max_batches`` intentionally pauses after N committed batches and is useful
    for controlled maintenance windows and deterministic resume tests.
    """
    plan = plan_sqlite_migration(
        source,
        candidate=candidate,
        checkpoint=checkpoint,
        source_dimensions=source_dimensions,
        target_dimensions=target_dimensions,
        batch_size=batch_size,
    )
    if int(provider.dimensions) != target_dimensions:
        raise ValueError(
            f"provider dimensions {provider.dimensions} do not match target {target_dimensions}"
        )
    if dry_run:
        return {"status": "dry-run", "plan": asdict(plan), "writes": 0}
    source_path, candidate_path, checkpoint_path = map(
        Path, (plan.source, plan.candidate, plan.checkpoint)
    )
    source_before = _source_fingerprint(source_path)
    if resume:
        if not candidate_path.is_file() or not checkpoint_path.is_file():
            raise ValueError("resume requires both candidate and checkpoint")
        state = _load_checkpoint(checkpoint_path)
        _validate_resume(plan, state, provider_name)
    else:
        if candidate_path.exists() or checkpoint_path.exists():
            raise ValueError(
                "candidate/checkpoint already exists; use --resume or choose new paths"
            )
        _copy_database(source_path, candidate_path)
        if _source_fingerprint(source_path) != source_before:
            raise RuntimeError("source changed while snapshot was being copied")
        state = _new_checkpoint(plan, provider_name)
        _atomic_json(checkpoint_path, state)

    connection = sqlite3.connect(candidate_path)
    batches = 0
    try:
        for table, text_columns in _VECTOR_TABLES.items():
            if table not in state["tables"]:
                continue
            progress = state["tables"][table]
            while True:
                quoted_text = ",".join(f'"{column}"' for column in text_columns)
                rows = connection.execute(
                    f'SELECT id,{quoted_text} FROM "{table}" '
                    "WHERE embedding IS NOT NULL AND id > ? ORDER BY id LIMIT ?",
                    (int(progress["last_id"]), batch_size),
                ).fetchall()
                if not rows:
                    break
                texts = [
                    " ".join(str(value or "") for value in row[1:]).strip()
                    for row in rows
                ]
                vectors = await provider.embed_batch(texts)
                if len(vectors) != len(rows) or any(
                    len(vector) != target_dimensions for vector in vectors
                ):
                    raise ValueError("provider returned an invalid batch shape")
                if any(
                    not math.isfinite(float(value))
                    for vector in vectors
                    for value in vector
                ):
                    raise ValueError("provider returned a non-finite embedding value")
                packed = [
                    struct.pack(f"<{target_dimensions}f", *vector) for vector in vectors
                ]
                connection.executemany(
                    f'UPDATE "{table}" SET embedding = ? WHERE id = ?',
                    [(blob, int(row[0])) for row, blob in zip(rows, packed)],
                )
                connection.commit()
                progress["last_id"] = int(rows[-1][0])
                progress["completed_rows"] = int(progress["completed_rows"]) + len(rows)
                state["updated_at"] = _utc_now()
                _atomic_json(checkpoint_path, state)
                batches += 1
                if max_batches is not None and batches >= max_batches:
                    state["status"] = "paused"
                    state["updated_at"] = _utc_now()
                    _atomic_json(checkpoint_path, state)
                    return state
    finally:
        connection.close()

    with closing(_readonly(candidate_path)) as check:
        for table, expected in plan.rows.items():
            migrated = int(
                check.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE embedding IS NOT NULL '
                    "AND length(embedding) = ?",
                    (target_dimensions * 4,),
                ).fetchone()[0]
            )
            if migrated != expected:
                raise RuntimeError(
                    f"candidate verification failed for {table}: {migrated}/{expected}"
                )
    candidate_nonvector = _nonvector_hash(candidate_path)
    if candidate_nonvector != plan.source_nonvector_sha256:
        raise RuntimeError("candidate changed non-embedding data")
    if _source_fingerprint(source_path) != source_before:
        raise RuntimeError("source database changed during migration")
    state.update(
        {
            "status": "completed",
            "updated_at": _utc_now(),
            "candidate_sha256": _sha256_file(candidate_path),
            "candidate_nonvector_sha256": candidate_nonvector,
        }
    )
    _atomic_json(checkpoint_path, state)
    return state


def rollback_sqlite_migration(checkpoint: str | Path) -> dict[str, Any]:
    """Mark a candidate rolled back while retaining all files and evidence."""
    checkpoint_path = Path(checkpoint).expanduser().resolve()
    state = _load_checkpoint(checkpoint_path)
    if state.get("status") == "rolled-back":
        return state
    source = Path(state["plan"]["source"])
    current = _source_fingerprint(source)
    if any(
        current[key] != state["plan"][f"source_{key}"]
        for key in ("sha256", "nonvector_sha256", "vector_sha256")
    ):
        raise ValueError("source changed; refusing to assert rollback safety")
    state.update(
        {
            "status": "rolled-back",
            "rolled_back_at": _utc_now(),
            "rollback": "source remained canonical; candidate retained, not deleted",
        }
    )
    _atomic_json(checkpoint_path, state)
    return state


def _provider(args: argparse.Namespace) -> EmbeddingProvider:
    if args.provider == "simple":
        return SimpleEmbedding(dimensions=args.target_dim)
    if args.provider == "bge-m3":
        from soul_framework.embedding.bge_m3 import (
            DEFAULT_BGE_M3_MODEL,
            BgeM3Embedding,
        )

        return BgeM3Embedding(
            model=args.model or DEFAULT_BGE_M3_MODEL, dimensions=args.target_dim
        )
    from soul_framework.embedding.sentence_transformer import (
        SentenceTransformerEmbedding,
    )

    return SentenceTransformerEmbedding(args.model or "BAAI/bge-m3")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Non-destructive SOUL SQLite embedding migration"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="plan or create/resume a reindexed candidate")
    run.add_argument("source")
    run.add_argument("--candidate", required=True)
    run.add_argument("--checkpoint", default="")
    run.add_argument("--source-dim", type=int, default=128)
    run.add_argument("--target-dim", type=int, default=1024)
    run.add_argument("--batch-size", type=int, default=256)
    run.add_argument(
        "--provider",
        choices=("simple", "bge-m3", "sentence-transformer"),
        default="bge-m3",
    )
    run.add_argument("--model", default="")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--resume", action="store_true")
    rollback = sub.add_parser(
        "rollback", help="mark candidate inactive; retain all files"
    )
    rollback.add_argument("checkpoint")
    return parser


async def _async_main(args: argparse.Namespace) -> dict[str, Any]:
    checkpoint = (
        args.checkpoint
        or f"{Path(args.candidate).expanduser().resolve()}.checkpoint.json"
    )
    provider = (
        SimpleEmbedding(dimensions=args.target_dim) if args.dry_run else _provider(args)
    )
    return await migrate_sqlite_embeddings(
        args.source,
        provider,
        candidate=args.candidate,
        checkpoint=checkpoint,
        source_dimensions=args.source_dim,
        target_dimensions=args.target_dim,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        resume=args.resume,
        provider_name=(
            f"{args.provider}:{args.model or 'bge-m3'}"
            if args.provider != "simple"
            else f"simple:{args.target_dim}"
        ),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = (
            rollback_sqlite_migration(args.checkpoint)
            if args.command == "rollback"
            else asyncio.run(_async_main(args))
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
