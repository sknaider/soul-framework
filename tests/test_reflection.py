"""Tests for ReflectionManager — inner monologue and diary."""

from __future__ import annotations

import pytest

from soul_framework.reflection.reflect import ReflectionManager


class TestReflectionManager:

    @pytest.fixture
    async def mgr(self, backend):
        return ReflectionManager("TestAgent", backend)

    async def test_add_thought_returns_id(self, mgr):
        tid = await mgr.add_thought("I am thinking", "contemplative")
        assert tid > 0

    async def test_get_last_thought(self, mgr):
        await mgr.add_thought("First thought", "calm")
        await mgr.add_thought("Second thought", "excited")
        last = await mgr.get_last_thought()
        assert last is not None
        assert last["thought"] == "Second thought"
        assert last["emotional_state"] == "excited"

    async def test_get_last_thought_empty(self, mgr):
        last = await mgr.get_last_thought()
        assert last is None

    async def test_list_thoughts(self, mgr):
        for i in range(5):
            await mgr.add_thought(f"Thought {i}")
        thoughts = await mgr.list_thoughts(limit=3)
        assert len(thoughts) == 3
        # Newest first
        assert "4" in thoughts[0]["thought"]

    async def test_list_thoughts_by_session(self, mgr):
        await mgr.add_thought("Session A", session_id="sess-a")
        await mgr.add_thought("Session B", session_id="sess-b")
        results = await mgr.list_thoughts(session_id="sess-a")
        assert len(results) == 1
        assert results[0]["thought"] == "Session A"

    async def test_add_diary_entry(self, mgr):
        did = await mgr.add_diary_entry("Today was productive", mood="satisfied")
        assert did > 0

    async def test_get_last_diary(self, mgr):
        await mgr.add_diary_entry("Day 1", mood="calm")
        await mgr.add_diary_entry("Day 2", mood="excited")
        last = await mgr.get_last_diary()
        assert last is not None
        assert last["content"] == "Day 2"
        assert last["mood"] == "excited"

    async def test_get_last_diary_empty(self, mgr):
        last = await mgr.get_last_diary()
        assert last is None
