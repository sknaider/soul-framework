"""Tests for Soul class — create, boot, snapshot, reflect, context manager."""

from __future__ import annotations

import pytest

from soul_framework import Soul


class TestSoulCreate:

    async def test_create_minimal(self):
        async with await Soul.create("Maya") as s:
            assert s.name == "Maya"

    async def test_create_with_ocean(self):
        async with await Soul.create(
            "Maya", ocean={"O": 0.8, "C": 0.9, "E": 0.6, "A": 0.7, "N": 0.2}
        ) as s:
            ocean = await s.identity.get_ocean()
            assert ocean["O"] == 0.8

    async def test_create_with_personality(self):
        async with await Soul.create(
            "Maya",
            personality={"personality": "Wise and calm", "philosophy": "Think first"},
        ) as s:
            ident = await s.identity.get()
            assert ident["personality"] == "Wise and calm"

    async def test_create_invalid_backend(self):
        with pytest.raises(ValueError, match="Unsupported backend"):
            await Soul.create("Maya", backend="redis")


class TestSoulBoot:

    async def test_boot_returns_string(self, soul):
        ctx = await soul.boot()
        assert isinstance(ctx, str)
        assert "TestAgent" in ctx

    async def test_boot_contains_ocean(self, soul):
        ctx = await soul.boot()
        assert "OCEAN" in ctx
        assert "0.800" in ctx or "0.8" in ctx

    async def test_boot_contains_rules(self, soul):
        await soul.rules.set("greet", "Always greet warmly", priority="critical")
        ctx = await soul.boot()
        assert "greet" in ctx

    async def test_boot_contains_last_thought(self, soul):
        await soul.reflect("I feel ready")
        ctx = await soul.boot()
        assert "I feel ready" in ctx


class TestSoulReflect:

    async def test_reflect_returns_id(self, soul):
        tid = await soul.reflect("Deep thought", "contemplative")
        assert tid > 0


class TestSoulSnapshot:

    async def test_snapshot_structure(self, soul):
        await soul.memory.store("A memory", importance=7)
        await soul.rules.set("r1", "A rule")
        await soul.reflect("A thought")

        snap = await soul.snapshot()
        assert snap["name"] == "TestAgent"
        assert snap["ocean"] is not None
        assert len(snap["recent_memories"]) >= 1
        assert len(snap["rules"]) >= 1
        assert snap["last_thought"] is not None


class TestSoulMemory:

    async def test_store_and_search(self, soul):
        await soul.memory.store("Python is my favorite language", importance=8)
        await soul.memory.store("I enjoy hiking in the mountains", importance=6)
        results = await soul.memory.search("programming language")
        assert len(results) > 0
        assert "Python" in results[0].memory.content
