"""Optional vector indexes for SQLite memory retrieval.

``ExactVectorIndex`` is the dependency-free correctness fallback.
``HnswMemoryIndex`` and ``USearchMemoryIndex`` use native ANN engines and persist
byte-bound sidecars so a stale/corrupt index fails closed instead of serving wrong labels.

This module deliberately does not mutate the SQLite schema or MemoryStore API.
The integration layer may rebuild from SQLite and switch back to exact search
whenever loading the ANN artifact fails.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

INDEX_FORMAT_VERSION = 1


class StaleVectorIndexError(RuntimeError):
    """The persisted index does not match its sidecar or source fingerprint."""


@dataclass(frozen=True, slots=True)
class VectorHit:
    memory_id: int
    similarity: float


@runtime_checkable
class VectorIndex(Protocol):
    @property
    def dimensions(self) -> int: ...

    @property
    def count(self) -> int: ...

    def build(self, ids: Sequence[int], vectors: Sequence[Sequence[float]]) -> None: ...

    def add(self, memory_id: int, vector: Sequence[float]) -> None: ...

    def remove(self, memory_id: int) -> bool: ...

    def search(self, query: Sequence[float], limit: int) -> list[VectorHit]: ...


def _validate_vector(vector: Sequence[float], dimensions: int) -> list[float]:
    if len(vector) != dimensions:
        raise ValueError(f"expected {dimensions} dimensions, got {len(vector)}")
    values = [float(value) for value in vector]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("vectors must contain only finite values")
    return values


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


class ExactVectorIndex:
    """Dependency-free exact cosine scan used as the safe fallback."""

    def __init__(self, dimensions: int) -> None:
        if dimensions < 1:
            raise ValueError("dimensions must be >= 1")
        self._dimensions = dimensions
        self._vectors: dict[int, list[float]] = {}

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def count(self) -> int:
        return len(self._vectors)

    def build(self, ids: Sequence[int], vectors: Sequence[Sequence[float]]) -> None:
        if len(ids) != len(vectors):
            raise ValueError("ids and vectors must have the same length")
        if len(set(ids)) != len(ids):
            raise ValueError("memory ids must be unique")
        self._vectors = {
            int(memory_id): _validate_vector(vector, self._dimensions)
            for memory_id, vector in zip(ids, vectors, strict=True)
        }

    def add(self, memory_id: int, vector: Sequence[float]) -> None:
        self._vectors[int(memory_id)] = _validate_vector(vector, self._dimensions)

    def remove(self, memory_id: int) -> bool:
        return self._vectors.pop(int(memory_id), None) is not None

    def search(self, query: Sequence[float], limit: int) -> list[VectorHit]:
        if limit < 1 or not self._vectors:
            return []
        query_values = _validate_vector(query, self._dimensions)
        ranked = sorted(
            (
                VectorHit(memory_id, _cosine(query_values, vector))
                for memory_id, vector in self._vectors.items()
            ),
            key=lambda hit: (-hit.similarity, hit.memory_id),
        )
        return ranked[:limit]


def _load_hnsw_dependencies() -> tuple[Any, Any]:
    try:
        import hnswlib
        import numpy
    except ImportError as exc:  # pragma: no cover - controlled through monkeypatch
        raise ImportError(
            "HNSW retrieval requires hnswlib and numpy. Install the ANN extra, "
            "or keep the dependency-free exact SQLite fallback."
        ) from exc
    return hnswlib, numpy


def _load_usearch_dependencies() -> tuple[Any, Any]:
    try:
        import numpy
        from usearch.index import Index
    except ImportError as exc:  # pragma: no cover - controlled through monkeypatch
        raise ImportError(
            "USearch ANN retrieval requires usearch and numpy. Install the ANN "
            "extra, or keep the dependency-free exact SQLite fallback."
        ) from exc
    return Index, numpy


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class HnswMemoryIndex:
    """Cosine HNSW index keyed by stable SQLite memory ids."""

    def __init__(
        self,
        dimensions: int,
        *,
        m: int = 16,
        ef_construction: int = 200,
        ef_search: int = 400,
        random_seed: int = 100,
    ) -> None:
        if dimensions < 1:
            raise ValueError("dimensions must be >= 1")
        if m < 2 or ef_construction < 2 or ef_search < 1:
            raise ValueError("invalid HNSW parameters")
        self._hnswlib, self._numpy = _load_hnsw_dependencies()
        self._dimensions = dimensions
        self._m = m
        self._ef_construction = ef_construction
        self._ef_search = ef_search
        self._random_seed = random_seed
        self._index: Any | None = None
        self._labels: set[int] = set()
        self._capacity = 0
        self._lock = threading.RLock()

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def count(self) -> int:
        return len(self._labels)

    def build(self, ids: Sequence[int], vectors: Sequence[Sequence[float]]) -> None:
        if len(ids) != len(vectors):
            raise ValueError("ids and vectors must have the same length")
        labels = [int(memory_id) for memory_id in ids]
        if len(set(labels)) != len(labels):
            raise ValueError("memory ids must be unique")
        matrix = self._matrix(vectors)
        capacity = max(1, len(labels))
        with self._lock:
            index = self._hnswlib.Index(space="cosine", dim=self._dimensions)
            index.init_index(
                max_elements=capacity,
                ef_construction=self._ef_construction,
                M=self._m,
                random_seed=self._random_seed,
                allow_replace_deleted=True,
            )
            if labels:
                index.add_items(
                    matrix, self._numpy.asarray(labels, dtype="int64"), num_threads=1
                )
            index.set_ef(self._ef_search)
            self._index = index
            self._labels = set(labels)
            self._capacity = capacity

    def add(self, memory_id: int, vector: Sequence[float]) -> None:
        label = int(memory_id)
        row = self._matrix([vector])
        with self._lock:
            if self._index is None:
                self.build([label], [vector])
                return
            if label in self._labels:
                self._index.add_items(
                    row,
                    self._numpy.asarray([label], dtype="int64"),
                    num_threads=1,
                )
                return
            if len(self._labels) >= self._capacity:
                self._capacity = max(self._capacity * 2, len(self._labels) + 1)
                self._index.resize_index(self._capacity)
            self._index.add_items(
                row,
                self._numpy.asarray([label], dtype="int64"),
                num_threads=1,
                replace_deleted=True,
            )
            self._labels.add(label)

    def remove(self, memory_id: int) -> bool:
        label = int(memory_id)
        with self._lock:
            if self._index is None or label not in self._labels:
                return False
            self._index.mark_deleted(label)
            self._labels.remove(label)
            return True

    def search(self, query: Sequence[float], limit: int) -> list[VectorHit]:
        if limit < 1:
            return []
        row = self._matrix([query])
        with self._lock:
            if self._index is None or not self._labels:
                return []
            k = min(limit, len(self._labels))
            self._index.set_ef(max(self._ef_search, k))
            labels, distances = self._index.knn_query(row, k=k, num_threads=1)
        return [
            VectorHit(int(label), max(-1.0, min(1.0, 1.0 - float(distance))))
            for label, distance in zip(labels[0], distances[0], strict=True)
        ]

    def save(self, path: str | Path, *, source_fingerprint: str) -> None:
        """Atomically persist index + byte-bound metadata sidecar."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        metadata_path = self.metadata_path(destination)
        with self._lock:
            if self._index is None:
                raise RuntimeError("cannot save an index before build()")
            with tempfile.NamedTemporaryFile(
                prefix=f".{destination.name}.", dir=destination.parent, delete=False
            ) as handle:
                temp_index = Path(handle.name)
            temp_metadata = temp_index.with_suffix(temp_index.suffix + ".json")
            try:
                self._index.save_index(str(temp_index))
                metadata = {
                    "format_version": INDEX_FORMAT_VERSION,
                    "dimensions": self._dimensions,
                    "count": len(self._labels),
                    "labels": sorted(self._labels),
                    "source_fingerprint": source_fingerprint,
                    "index_sha256": _sha256(temp_index),
                    "space": "cosine",
                    "m": self._m,
                    "ef_construction": self._ef_construction,
                    "ef_search": self._ef_search,
                    "random_seed": self._random_seed,
                }
                temp_metadata.write_text(
                    json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                os.replace(temp_index, destination)
                os.replace(temp_metadata, metadata_path)
            finally:
                temp_index.unlink(missing_ok=True)
                temp_metadata.unlink(missing_ok=True)

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        source_fingerprint: str,
    ) -> HnswMemoryIndex:
        """Load only when bytes, dimensions and source fingerprint all match."""
        source = Path(path)
        metadata_path = cls.metadata_path(source)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StaleVectorIndexError("missing or invalid HNSW metadata") from exc
        required = {
            "format_version",
            "dimensions",
            "count",
            "labels",
            "source_fingerprint",
            "index_sha256",
            "space",
            "m",
            "ef_construction",
            "ef_search",
            "random_seed",
        }
        if not required.issubset(metadata):
            raise StaleVectorIndexError("incomplete HNSW metadata")
        if metadata["format_version"] != INDEX_FORMAT_VERSION:
            raise StaleVectorIndexError("unsupported HNSW metadata version")
        if metadata["source_fingerprint"] != source_fingerprint:
            raise StaleVectorIndexError("HNSW source fingerprint mismatch")
        if metadata["space"] != "cosine":
            raise StaleVectorIndexError("HNSW distance-space mismatch")
        try:
            actual_sha = _sha256(source)
        except OSError as exc:
            raise StaleVectorIndexError("missing HNSW index bytes") from exc
        if actual_sha != metadata["index_sha256"]:
            raise StaleVectorIndexError("HNSW index checksum mismatch")

        labels = [int(label) for label in metadata["labels"]]
        if len(labels) != metadata["count"] or len(set(labels)) != len(labels):
            raise StaleVectorIndexError("HNSW label manifest is inconsistent")
        instance = cls(
            int(metadata["dimensions"]),
            m=int(metadata["m"]),
            ef_construction=int(metadata["ef_construction"]),
            ef_search=int(metadata["ef_search"]),
            random_seed=int(metadata["random_seed"]),
        )
        capacity = max(1, len(labels))
        try:
            instance._index = instance._hnswlib.Index(
                space="cosine", dim=instance._dimensions
            )
            instance._index.load_index(
                str(source),
                max_elements=capacity,
                allow_replace_deleted=True,
            )
            instance._index.set_ef(instance._ef_search)
        except Exception as exc:
            raise StaleVectorIndexError("HNSW index bytes could not be loaded") from exc
        instance._labels = set(labels)
        instance._capacity = capacity
        return instance

    @staticmethod
    def metadata_path(path: str | Path) -> Path:
        source = Path(path)
        return source.with_name(source.name + ".json")

    def _matrix(self, vectors: Iterable[Sequence[float]]) -> Any:
        rows = [_validate_vector(vector, self._dimensions) for vector in vectors]
        return self._numpy.asarray(rows, dtype="float32")


class USearchMemoryIndex:
    """Portable graph ANN index with CPython 3.13 Windows wheels.

    hnswlib has no binary wheel for the Python 3.13 Windows runtime used by the
    SOUL laptop. USearch provides the same cosine graph-ANN contract without
    requiring a compiler, while the exact index remains the fail-safe fallback.
    """

    def __init__(
        self,
        dimensions: int,
        *,
        connectivity: int = 16,
        expansion_add: int = 200,
        expansion_search: int = 400,
    ) -> None:
        if dimensions < 1:
            raise ValueError("dimensions must be >= 1")
        if connectivity < 2 or expansion_add < 2 or expansion_search < 1:
            raise ValueError("invalid USearch parameters")
        self._Index, self._numpy = _load_usearch_dependencies()
        self._dimensions = int(dimensions)
        self._connectivity = int(connectivity)
        self._expansion_add = int(expansion_add)
        self._expansion_search = int(expansion_search)
        self._index: Any | None = None
        self._labels: set[int] = set()
        self._lock = threading.RLock()

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def count(self) -> int:
        return len(self._labels)

    def _new_index(self) -> Any:
        return self._Index(
            ndim=self._dimensions,
            metric="cos",
            dtype="f32",
            connectivity=self._connectivity,
            expansion_add=self._expansion_add,
            expansion_search=self._expansion_search,
            enable_key_lookups=True,
        )

    def _matrix(self, vectors: Iterable[Sequence[float]]) -> Any:
        rows = [_validate_vector(vector, self._dimensions) for vector in vectors]
        return self._numpy.asarray(rows, dtype="float32")

    def build(self, ids: Sequence[int], vectors: Sequence[Sequence[float]]) -> None:
        if len(ids) != len(vectors):
            raise ValueError("ids and vectors must have the same length")
        labels = [int(memory_id) for memory_id in ids]
        if len(set(labels)) != len(labels):
            raise ValueError("memory ids must be unique")
        matrix = self._matrix(vectors)
        with self._lock:
            index = self._new_index()
            if labels:
                index.add(
                    self._numpy.asarray(labels, dtype="uint64"),
                    matrix,
                    threads=1,
                )
            self._index = index
            self._labels = set(labels)

    def add(self, memory_id: int, vector: Sequence[float]) -> None:
        label = int(memory_id)
        row = self._matrix([vector])[0]
        with self._lock:
            if self._index is None:
                self.build([label], [vector])
                return
            if label in self._labels:
                self._index.remove(label, threads=1)
            self._index.add(label, row, threads=1)
            self._labels.add(label)

    def remove(self, memory_id: int) -> bool:
        label = int(memory_id)
        with self._lock:
            if self._index is None or label not in self._labels:
                return False
            removed = bool(self._index.remove(label, threads=1))
            if removed:
                self._labels.remove(label)
            return removed

    def search(self, query: Sequence[float], limit: int) -> list[VectorHit]:
        if limit < 1:
            return []
        row = self._matrix([query])[0]
        with self._lock:
            if self._index is None or not self._labels:
                return []
            matches = self._index.search(
                row, count=min(limit, len(self._labels)), threads=1
            )
            keys = list(matches.keys)
            distances = list(matches.distances)
        return [
            VectorHit(int(label), max(-1.0, min(1.0, 1.0 - float(distance))))
            for label, distance in zip(keys, distances, strict=True)
        ]

    def save(self, path: str | Path, *, source_fingerprint: str) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        metadata_path = self.metadata_path(destination)
        with self._lock:
            if self._index is None:
                raise RuntimeError("cannot save an index before build()")
            with tempfile.NamedTemporaryFile(
                prefix=f".{destination.name}.", dir=destination.parent, delete=False
            ) as handle:
                temp_index = Path(handle.name)
            temp_metadata = temp_index.with_suffix(temp_index.suffix + ".json")
            try:
                self._index.save(temp_index)
                metadata = {
                    "format_version": INDEX_FORMAT_VERSION,
                    "engine": "usearch",
                    "dimensions": self._dimensions,
                    "count": len(self._labels),
                    "labels": sorted(self._labels),
                    "source_fingerprint": source_fingerprint,
                    "index_sha256": _sha256(temp_index),
                    "space": "cosine",
                    "connectivity": self._connectivity,
                    "expansion_add": self._expansion_add,
                    "expansion_search": self._expansion_search,
                }
                temp_metadata.write_text(
                    json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                os.replace(temp_index, destination)
                os.replace(temp_metadata, metadata_path)
            finally:
                temp_index.unlink(missing_ok=True)
                temp_metadata.unlink(missing_ok=True)

    @classmethod
    def load(
        cls, path: str | Path, *, source_fingerprint: str
    ) -> USearchMemoryIndex:
        source = Path(path)
        metadata_path = cls.metadata_path(source)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StaleVectorIndexError("missing or invalid USearch metadata") from exc
        required = {
            "format_version", "engine", "dimensions", "count", "labels",
            "source_fingerprint", "index_sha256", "space", "connectivity",
            "expansion_add", "expansion_search",
        }
        if not required.issubset(metadata):
            raise StaleVectorIndexError("incomplete USearch metadata")
        if metadata["format_version"] != INDEX_FORMAT_VERSION or metadata["engine"] != "usearch":
            raise StaleVectorIndexError("unsupported USearch metadata version")
        if metadata["source_fingerprint"] != source_fingerprint:
            raise StaleVectorIndexError("USearch source fingerprint mismatch")
        if metadata["space"] != "cosine":
            raise StaleVectorIndexError("USearch distance-space mismatch")
        try:
            actual_sha = _sha256(source)
        except OSError as exc:
            raise StaleVectorIndexError("missing USearch index bytes") from exc
        if actual_sha != metadata["index_sha256"]:
            raise StaleVectorIndexError("USearch index checksum mismatch")
        labels = [int(label) for label in metadata["labels"]]
        if len(labels) != metadata["count"] or len(set(labels)) != len(labels):
            raise StaleVectorIndexError("USearch label manifest is inconsistent")
        instance = cls(
            int(metadata["dimensions"]),
            connectivity=int(metadata["connectivity"]),
            expansion_add=int(metadata["expansion_add"]),
            expansion_search=int(metadata["expansion_search"]),
        )
        try:
            instance._index = instance._new_index()
            instance._index.load(source)
        except Exception as exc:
            raise StaleVectorIndexError("USearch index bytes could not be loaded") from exc
        instance._labels = set(labels)
        return instance

    @staticmethod
    def metadata_path(path: str | Path) -> Path:
        source = Path(path)
        return source.with_name(source.name + ".json")


def create_vector_index(
    dimensions: int,
    *,
    engine: str = "auto",
    prefer_hnsw: bool | None = None,
    **hnsw_options: Any,
) -> VectorIndex:
    """Choose a portable ANN engine, or preserve exact-search correctness.

    ``prefer_hnsw`` is retained for source compatibility with the 0.4.0
    candidate API. New callers should use ``engine``.
    """
    if prefer_hnsw is False:
        engine = "exact"
    if engine not in {"auto", "usearch", "hnsw", "exact"}:
        raise ValueError(f"unsupported vector index engine: {engine}")
    if engine in {"auto", "usearch"}:
        try:
            return USearchMemoryIndex(dimensions)
        except ImportError:
            if engine == "usearch":
                raise
    if engine in {"auto", "hnsw"}:
        try:
            return HnswMemoryIndex(dimensions, **hnsw_options)
        except ImportError:
            if engine == "hnsw":
                raise
    return ExactVectorIndex(dimensions)
