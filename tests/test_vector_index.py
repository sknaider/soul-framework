from __future__ import annotations

import json

import pytest

from soul_framework.memory import vector_index as mod
from soul_framework.memory.vector_index import (
    ExactVectorIndex,
    HnswMemoryIndex,
    StaleVectorIndexError,
    USearchMemoryIndex,
    create_vector_index,
)


def _corpus():
    return [10, 20, 30, 40], [
        [1.0, 0.0, 0.0],
        [0.9, 0.1, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]


def test_exact_fallback_search_update_and_remove():
    ids, vectors = _corpus()
    index = ExactVectorIndex(3)
    index.build(ids, vectors)
    assert [hit.memory_id for hit in index.search([1.0, 0.0, 0.0], 2)] == [10, 20]
    index.add(20, [0.0, 1.0, 0.0])
    assert index.remove(10) is True
    assert index.remove(10) is False
    assert index.search([1.0, 0.0, 0.0], 1)[0].memory_id in {20, 30, 40}


def test_exact_validation_rejects_bad_vectors_and_duplicate_ids():
    index = ExactVectorIndex(3)
    with pytest.raises(ValueError, match="same length"):
        index.build([1], [])
    with pytest.raises(ValueError, match="unique"):
        index.build([1, 1], [[1, 0, 0], [0, 1, 0]])
    with pytest.raises(ValueError, match="3 dimensions"):
        index.add(1, [1, 0])
    with pytest.raises(ValueError, match="finite"):
        index.add(1, [1, 0, float("nan")])


def test_factory_falls_back_when_optional_dependency_is_missing(monkeypatch):
    def missing():
        raise ImportError("not installed")

    monkeypatch.setattr(mod, "_load_hnsw_dependencies", missing)
    monkeypatch.setattr(mod, "_load_usearch_dependencies", missing)
    assert isinstance(create_vector_index(3), ExactVectorIndex)


def test_explicit_missing_engine_fails_closed(monkeypatch):
    monkeypatch.setattr(
        mod, "_load_usearch_dependencies", lambda: (_ for _ in ()).throw(ImportError())
    )
    with pytest.raises(ImportError):
        create_vector_index(3, engine="usearch")


def test_usearch_round_trip_update_remove_and_fingerprint(tmp_path):
    pytest.importorskip("usearch")
    ids, vectors = _corpus()
    index = USearchMemoryIndex(3, expansion_search=20)
    index.build(ids, vectors)
    assert index.search([1.0, 0.0, 0.0], 1)[0].memory_id == 10
    index.add(20, [0.0, 1.0, 0.0])
    assert index.remove(40) is True

    path = tmp_path / "memory.usearch"
    index.save(path, source_fingerprint="db-state-1")
    loaded = USearchMemoryIndex.load(path, source_fingerprint="db-state-1")
    assert loaded.count == 3
    assert loaded.search([0.0, 1.0, 0.0], 2)[0].memory_id in {20, 30}
    with pytest.raises(StaleVectorIndexError, match="fingerprint"):
        USearchMemoryIndex.load(path, source_fingerprint="different")


def test_usearch_corrupt_bytes_fail_closed(tmp_path):
    pytest.importorskip("usearch")
    ids, vectors = _corpus()
    path = tmp_path / "memory.usearch"
    index = USearchMemoryIndex(3)
    index.build(ids, vectors)
    index.save(path, source_fingerprint="db-state-1")
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(StaleVectorIndexError, match="checksum"):
        USearchMemoryIndex.load(path, source_fingerprint="db-state-1")


def test_hnsw_round_trip_and_source_fingerprint(tmp_path):
    pytest.importorskip("hnswlib")
    ids, vectors = _corpus()
    index = HnswMemoryIndex(3, ef_search=20)
    index.build(ids, vectors)
    assert index.count == 4
    assert index.search([1.0, 0.0, 0.0], 1)[0].memory_id == 10

    path = tmp_path / "memory.hnsw"
    index.save(path, source_fingerprint="db-state-1")
    loaded = HnswMemoryIndex.load(path, source_fingerprint="db-state-1")
    assert loaded.count == 4
    assert loaded.search([0.0, 0.0, 1.0], 1)[0].memory_id == 40
    assert loaded.remove(40) is True
    loaded.add(50, [0.0, 0.0, 1.0])
    assert loaded.search([0.0, 0.0, 1.0], 1)[0].memory_id == 50

    with pytest.raises(StaleVectorIndexError, match="fingerprint"):
        HnswMemoryIndex.load(path, source_fingerprint="different-db-state")


def test_hnsw_corrupt_bytes_and_metadata_fail_closed(tmp_path):
    pytest.importorskip("hnswlib")
    ids, vectors = _corpus()
    path = tmp_path / "memory.hnsw"
    index = HnswMemoryIndex(3)
    index.build(ids, vectors)
    index.save(path, source_fingerprint="db-state-1")

    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(StaleVectorIndexError, match="checksum"):
        HnswMemoryIndex.load(path, source_fingerprint="db-state-1")

    index.save(path, source_fingerprint="db-state-1")
    meta_path = HnswMemoryIndex.metadata_path(path)
    metadata = json.loads(meta_path.read_text())
    metadata["labels"].append(metadata["labels"][0])
    meta_path.write_text(json.dumps(metadata))
    with pytest.raises(StaleVectorIndexError, match="label manifest"):
        HnswMemoryIndex.load(path, source_fingerprint="db-state-1")


def test_hnsw_add_remove_and_resize():
    pytest.importorskip("hnswlib")
    index = HnswMemoryIndex(3, ef_search=20)
    index.build([1], [[1.0, 0.0, 0.0]])
    index.add(2, [0.0, 1.0, 0.0])
    assert index.count == 2
    assert index.search([0.0, 1.0, 0.0], 1)[0].memory_id == 2
    assert index.remove(2) is True
    assert [hit.memory_id for hit in index.search([0.0, 1.0, 0.0], 2)] == [1]
