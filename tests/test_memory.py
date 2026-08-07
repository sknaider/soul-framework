"""Tests for MemoryStore — store, search, list, get, update, invalidate."""

from __future__ import annotations

import pytest

from soul_framework.memory.store import MemoryStore


class TestMemoryStore:

    @pytest.fixture
    async def store(self, backend, embedding, config):
        return MemoryStore("TestAgent", backend, embedding, config)

    async def test_store_returns_id(self, store):
        mid = await store.store("Hello world", importance=7)
        assert mid > 0

    async def test_get_by_id(self, store):
        mid = await store.store("Remember this", category="fact", importance=8)
        mem = await store.get(mid)
        assert mem is not None
        assert mem.content == "Remember this"
        assert mem.category == "fact"
        assert mem.importance == 8

    async def test_get_nonexistent(self, store):
        mem = await store.get(9999)
        assert mem is None

    async def test_list_returns_newest_first(self, store):
        await store.store("First")
        await store.store("Second")
        await store.store("Third")
        mems = await store.list(limit=10)
        assert len(mems) == 3
        assert mems[0].content == "Third"
        assert mems[2].content == "First"

    async def test_list_filter_by_category(self, store):
        await store.store("A fact", category="fact")
        await store.store("An emotion", category="emotion")
        facts = await store.list(category="fact")
        assert len(facts) == 1
        assert facts[0].category == "fact"

    async def test_search_finds_relevant(self, store):
        await store.store("I love Python programming", importance=7)
        await store.store("The weather is sunny today", importance=5)
        await store.store("Python is great for AI", importance=8)
        results = await store.search("Python programming language")
        assert len(results) > 0
        # Python-related memories should rank higher
        contents = [r.memory.content for r in results]
        assert any("Python" in c for c in contents[:2])

    async def test_search_empty_db(self, store):
        results = await store.search("anything")
        assert results == []

    async def test_search_respects_limit(self, store):
        for i in range(20):
            await store.store(f"Memory number {i}")
        results = await store.search("memory", limit=5)
        assert len(results) <= 5

    async def test_search_filters_invalidated(self, store):
        mid = await store.store("Secret memory", importance=9)
        await store.invalidate(mid)
        results = await store.search("secret")
        assert all(r.memory.id != mid for r in results)

    async def test_update_content(self, store):
        mid = await store.store("Old content")
        ok = await store.update(mid, content="New content")
        assert ok is True
        mem = await store.get(mid)
        assert mem.content == "New content"

    async def test_update_importance(self, store):
        mid = await store.store("Important", importance=3)
        await store.update(mid, importance=9)
        mem = await store.get(mid)
        assert mem.importance == 9

    async def test_update_nonexistent(self, store):
        ok = await store.update(9999, content="nope")
        assert ok is False

    async def test_invalidate(self, store):
        mid = await store.store("To delete")
        ok = await store.invalidate(mid)
        assert ok is True
        # Still accessible with include_invalid
        mems = await store.list(include_invalid=True)
        found = [m for m in mems if m.id == mid]
        assert len(found) == 1
        assert found[0].invalid_at != ""

    async def test_invalidate_nonexistent(self, store):
        ok = await store.invalidate(9999)
        assert ok is False

    async def test_count(self, store):
        assert await store.count() == 0
        await store.store("One")
        await store.store("Two", category="emotion")
        assert await store.count() == 2
        assert await store.count(category="emotion") == 1

    async def test_search_importance_affects_score(self, store):
        """Higher importance memories should generally score higher."""
        await store.store("AI memory system", importance=3)
        await store.store("AI memory system", importance=9)
        results = await store.search("AI memory")
        assert len(results) == 2
        # The high-importance one should rank first
        assert results[0].memory.importance > results[1].memory.importance

    async def test_store_with_metadata(self, store):
        mid = await store.store("With meta", metadata={"source": "test", "tags": ["a"]})
        mem = await store.get(mid)
        assert mem.metadata == {"source": "test", "tags": ["a"]}

    async def test_agent_isolation(self, backend, embedding, config):
        """Memories are isolated per agent."""
        store_a = MemoryStore("AgentA", backend, embedding, config)
        store_b = MemoryStore("AgentB", backend, embedding, config)
        await store_a.store("Only A sees this")
        await store_b.store("Only B sees this")
        assert await store_a.count() == 1
        assert await store_b.count() == 1
        results_a = await store_a.search("sees this")
        assert all(r.memory.agent == "AgentA" for r in results_a)
