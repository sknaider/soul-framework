"""Tests for soul_framework.trees — all 5 tree data structures."""

import pytest

from soul_framework.trees import (
    BoundingBox,
    FenwickStats,
    MerkleSoul,
    RSpatialIndex,
    SpatialEntry,
    SplayCache,
    TrieIndex,
)


# =============================================================================
# MerkleSoul
# =============================================================================


class TestMerkleSoul:
    def test_empty_tree_root_is_none(self):
        tree = MerkleSoul()
        assert tree.root_hash is None

    def test_update_leaf_returns_hash(self):
        tree = MerkleSoul()
        h = tree.update_leaf("ocean", {"O": 0.8})
        assert isinstance(h, str) and len(h) == 64

    def test_root_hash_deterministic(self):
        t1, t2 = MerkleSoul(), MerkleSoul()
        t1.update_leaf("a", [1, 2])
        t2.update_leaf("a", [1, 2])
        assert t1.root_hash == t2.root_hash

    def test_root_hash_changes_on_modification(self):
        tree = MerkleSoul()
        tree.update_leaf("ocean", {"O": 0.8})
        h1 = tree.root_hash
        tree.update_leaf("ocean", {"O": 0.9})
        assert tree.root_hash != h1

    def test_sign_and_verify_integrity(self):
        tree = MerkleSoul()
        tree.update_leaf("ocean", {"O": 0.8})
        tree.update_leaf("rules", ["rule1"])
        tree.sign_checkpoint()
        result = tree.verify_integrity()
        assert result["valid"] is True
        assert result["drift_detected"] is False

    def test_detect_drift_after_modification(self):
        tree = MerkleSoul()
        tree.update_leaf("ocean", {"O": 0.8})
        tree.sign_checkpoint()
        tree.update_leaf("ocean", {"O": 0.1})  # unauthorized change
        result = tree.verify_integrity()
        assert result["valid"] is False
        assert result["drift_detected"] is True

    def test_remove_leaf(self):
        tree = MerkleSoul()
        tree.update_leaf("a", 1)
        tree.update_leaf("b", 2)
        assert tree.remove_leaf("a") is True
        assert tree.remove_leaf("nonexistent") is False

    def test_get_proof(self):
        tree = MerkleSoul()
        tree.update_leaf("ocean", {"O": 0.8})
        tree.update_leaf("rules", ["r1"])
        proof = tree.get_proof("ocean")
        assert proof is not None
        assert isinstance(proof, list)

    def test_verify_proof(self):
        tree = MerkleSoul()
        data = {"O": 0.8, "C": 1.0}
        tree.update_leaf("ocean", data)
        tree.update_leaf("rules", ["r1"])
        proof = tree.get_proof("ocean")
        assert tree.verify_proof("ocean", data, proof) is True

    def test_checkpoint_history(self):
        tree = MerkleSoul()
        tree.update_leaf("a", 1)
        tree.sign_checkpoint(metadata={"session": "test"})
        tree.update_leaf("b", 2)
        tree.sign_checkpoint()
        assert tree.checkpoint_count == 2
        assert tree.last_checkpoint is not None
        assert tree.last_checkpoint["leaf_count"] == 2

    def test_multiple_leaves_root_hash(self):
        tree = MerkleSoul()
        for i in range(5):
            tree.update_leaf(f"cat_{i}", {"val": i})
        assert tree.root_hash is not None


# =============================================================================
# SplayCache
# =============================================================================


class TestSplayCache:
    def test_put_and_get(self):
        cache = SplayCache()
        cache.put("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing_returns_none(self):
        cache = SplayCache()
        assert cache.get("nope") is None

    def test_update_existing_key(self):
        cache = SplayCache()
        cache.put("k", "v1")
        cache.put("k", "v2")
        assert cache.get("k") == "v2"
        assert cache.size == 1

    def test_delete(self):
        cache = SplayCache()
        cache.put("k", "v")
        assert cache.delete("k") is True
        assert cache.get("k") is None
        assert cache.size == 0

    def test_delete_nonexistent(self):
        cache = SplayCache()
        assert cache.delete("nope") is False

    def test_contains_without_splaying(self):
        cache = SplayCache()
        cache.put("a", 1)
        cache.put("b", 2)
        assert cache.contains("a") is True
        assert cache.contains("z") is False

    def test_eviction_when_full(self):
        cache = SplayCache(max_size=3)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        cache.put("d", 4)  # triggers eviction
        assert cache.size == 3

    def test_stats(self):
        cache = SplayCache()
        cache.put("a", 1)
        cache.get("a")  # hit
        cache.get("b")  # miss
        s = cache.stats()
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert s["hit_rate"] == 0.5

    def test_clear(self):
        cache = SplayCache()
        cache.put("a", 1)
        cache.clear()
        assert cache.size == 0
        assert cache.get("a") is None

    def test_mixed_key_types(self):
        cache = SplayCache()
        cache.put("str_key", 1)
        cache.put(42, 2)
        cache.put(100, 3)
        assert cache.get("str_key") == 1
        assert cache.get(42) == 2

    def test_many_inserts(self):
        cache = SplayCache(max_size=50)
        for i in range(100):
            cache.put(f"key_{i}", i)
        assert cache.size == 50
        assert cache.stats()["evictions"] == 50


# =============================================================================
# TrieIndex
# =============================================================================


class TestTrieIndex:
    def test_insert_and_search(self):
        trie = TrieIndex()
        trie.insert("hello", {"tool": "hello"})
        assert trie.search("hello") == {"tool": "hello"}

    def test_search_missing(self):
        trie = TrieIndex()
        assert trie.search("nope") is None

    def test_has_key(self):
        trie = TrieIndex()
        trie.insert("abc")
        assert trie.has_key("abc") is True
        assert trie.has_key("ab") is False

    def test_search_prefix(self):
        trie = TrieIndex()
        trie.insert("memory_search", 1)
        trie.insert("memory_store", 2)
        trie.insert("boot_context", 3)
        results = trie.search_prefix("memory_")
        assert len(results) == 2
        keys = {k for k, _ in results}
        assert keys == {"memory_search", "memory_store"}

    def test_autocomplete(self):
        trie = TrieIndex()
        for word in ["apple", "app", "application", "banana"]:
            trie.insert(word)
        completions = trie.autocomplete("app", limit=2)
        assert len(completions) == 2
        assert all(c.startswith("app") for c in completions)

    def test_delete(self):
        trie = TrieIndex()
        trie.insert("abc", 1)
        trie.insert("abd", 2)
        assert trie.delete("abc") is True
        assert trie.search("abc") is None
        assert trie.search("abd") == 2

    def test_delete_nonexistent(self):
        trie = TrieIndex()
        assert trie.delete("nope") is False

    def test_count_prefix(self):
        trie = TrieIndex()
        trie.insert("memory_search")
        trie.insert("memory_store")
        trie.insert("memory_list")
        assert trie.count_prefix("memory_") == 3
        assert trie.count_prefix("boot") == 0

    def test_size(self):
        trie = TrieIndex()
        assert trie.size == 0
        trie.insert("a")
        trie.insert("b")
        assert trie.size == 2

    def test_empty_prefix_returns_all(self):
        trie = TrieIndex()
        trie.insert("x", 1)
        trie.insert("y", 2)
        results = trie.search_prefix("")
        assert len(results) == 2


# =============================================================================
# FenwickStats
# =============================================================================


class TestFenwickStats:
    def test_update_and_prefix_sum(self):
        ft = FenwickStats(10)
        ft.update(3, 5)
        ft.update(7, 3)
        assert ft.prefix_sum(3) == 5
        assert ft.prefix_sum(7) == 8
        assert ft.prefix_sum(10) == 8

    def test_range_sum(self):
        ft = FenwickStats(10)
        ft.update(2, 4)
        ft.update(5, 6)
        ft.update(8, 2)
        assert ft.range_sum(2, 5) == 10
        assert ft.range_sum(5, 8) == 8
        assert ft.range_sum(1, 10) == 12

    def test_range_sum_inverted_returns_zero(self):
        ft = FenwickStats(10)
        ft.update(3, 5)
        assert ft.range_sum(5, 3) == 0

    def test_point_query(self):
        ft = FenwickStats(10)
        ft.update(5, 7)
        assert ft.point_query(5) == 7
        assert ft.point_query(4) == 0

    def test_find_kth(self):
        ft = FenwickStats(10)
        ft.update(1, 3)
        ft.update(3, 5)
        ft.update(7, 2)
        assert ft.find_kth(3) == 1   # prefix_sum(1) = 3 >= 3
        assert ft.find_kth(4) == 3   # prefix_sum(3) = 8 >= 4
        assert ft.find_kth(9) == 7   # prefix_sum(7) = 10 >= 9

    def test_from_array(self):
        arr = [1, 2, 3, 4, 5]
        ft = FenwickStats.from_array(arr)
        assert ft.capacity == 5
        assert ft.total == 15
        assert ft.prefix_sum(3) == 6
        assert ft.range_sum(2, 4) == 9

    def test_out_of_range_raises(self):
        ft = FenwickStats(5)
        with pytest.raises(IndexError):
            ft.update(0, 1)
        with pytest.raises(IndexError):
            ft.update(6, 1)

    def test_prefix_sum_negative_returns_zero(self):
        ft = FenwickStats(5)
        assert ft.prefix_sum(-1) == 0

    def test_prefix_sum_clamped(self):
        ft = FenwickStats(5)
        ft.update(5, 10)
        assert ft.prefix_sum(100) == 10  # clamped to n

    def test_total_property(self):
        ft = FenwickStats(10)
        ft.update(1, 3)
        ft.update(10, 7)
        assert ft.total == 10

    def test_negative_delta(self):
        ft = FenwickStats(5)
        ft.update(3, 10)
        ft.update(3, -4)
        assert ft.point_query(3) == 6


# =============================================================================
# RSpatialIndex
# =============================================================================


class TestRSpatialIndex:
    def test_insert_and_search(self):
        rtree = RSpatialIndex()
        rtree.insert(BoundingBox(0, 0, 10, 10), "A")
        rtree.insert(BoundingBox(5, 5, 15, 15), "B")
        results = rtree.search(BoundingBox(3, 3, 7, 7))
        assert len(results) == 2

    def test_search_no_results(self):
        rtree = RSpatialIndex()
        rtree.insert(BoundingBox(0, 0, 1, 1), "A")
        results = rtree.search(BoundingBox(10, 10, 20, 20))
        assert len(results) == 0

    def test_search_point(self):
        rtree = RSpatialIndex()
        rtree.insert(BoundingBox(0, 0, 10, 10), "A")
        rtree.insert(BoundingBox(20, 20, 30, 30), "B")
        results = rtree.search_point(5, 5)
        assert len(results) == 1
        assert results[0].data == "A"

    def test_nearest(self):
        rtree = RSpatialIndex()
        rtree.insert(BoundingBox(0, 0, 2, 2), "close")
        rtree.insert(BoundingBox(100, 100, 102, 102), "far")
        nearest = rtree.nearest(0, 0, k=1)
        assert len(nearest) == 1
        assert nearest[0][1].data == "close"

    def test_nearest_k(self):
        rtree = RSpatialIndex()
        for i in range(5):
            rtree.insert(BoundingBox(i * 10, 0, i * 10 + 2, 2), f"item_{i}")
        nearest = rtree.nearest(0, 0, k=3)
        assert len(nearest) == 3
        assert nearest[0][0] <= nearest[1][0] <= nearest[2][0]

    def test_size(self):
        rtree = RSpatialIndex()
        assert rtree.size == 0
        rtree.insert(BoundingBox(0, 0, 1, 1))
        rtree.insert(BoundingBox(2, 2, 3, 3))
        assert rtree.size == 2

    def test_clear(self):
        rtree = RSpatialIndex()
        rtree.insert(BoundingBox(0, 0, 1, 1))
        rtree.clear()
        assert rtree.size == 0

    def test_many_inserts_trigger_splits(self):
        rtree = RSpatialIndex(max_entries=3)
        for i in range(20):
            rtree.insert(BoundingBox(i, i, i + 1, i + 1), f"item_{i}")
        assert rtree.size == 20
        results = rtree.search(BoundingBox(0, 0, 25, 25))
        assert len(results) == 20

    def test_bounding_box_area(self):
        bb = BoundingBox(0, 0, 10, 5)
        assert bb.area() == 50

    def test_bounding_box_contains(self):
        outer = BoundingBox(0, 0, 10, 10)
        inner = BoundingBox(2, 2, 8, 8)
        assert outer.contains(inner) is True
        assert inner.contains(outer) is False

    def test_bounding_box_intersects(self):
        a = BoundingBox(0, 0, 5, 5)
        b = BoundingBox(3, 3, 8, 8)
        c = BoundingBox(6, 6, 10, 10)
        assert a.intersects(b) is True
        assert a.intersects(c) is False

    def test_bounding_box_from_point(self):
        bb = BoundingBox.from_point(3.0, 4.0)
        assert bb.min_x == bb.max_x == 3.0
        assert bb.min_y == bb.max_y == 4.0
        assert bb.area() == 0

    def test_expansion_area(self):
        a = BoundingBox(0, 0, 10, 10)
        b = BoundingBox(5, 5, 15, 15)
        expansion = a.expansion_area(b)
        assert expansion > 0

    def test_mining_use_case(self):
        """Simulate INGEMMET concession queries."""
        rtree = RSpatialIndex()
        rtree.insert(BoundingBox(-6.77, -79.84, -6.75, -79.82), {"name": "Concesión A"})
        rtree.insert(BoundingBox(-6.80, -79.90, -6.78, -79.88), {"name": "Concesión B"})
        rtree.insert(BoundingBox(-7.00, -80.00, -6.98, -79.98), {"name": "Concesión C"})
        results = rtree.search(BoundingBox(-6.85, -79.95, -6.70, -79.75))
        names = {r.data["name"] for r in results}
        assert "Concesión A" in names
        assert "Concesión B" in names
        assert "Concesión C" not in names
