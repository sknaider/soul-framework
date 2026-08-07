"""Tests for InstinctManager."""

from __future__ import annotations

import pytest

from soul_framework.instincts.manager import InstinctManager


class TestInstinctManager:

    @pytest.fixture
    async def mgr(self, backend):
        return InstinctManager("TestAgent", backend)

    async def test_create_returns_id(self, mgr):
        iid = await mgr.create("user says hello", "greet warmly")
        assert iid > 0

    async def test_get_instinct(self, mgr):
        iid = await mgr.create("user asks time", "check clock", confidence=0.7)
        inst = await mgr.get(iid)
        assert inst is not None
        assert inst["trigger_pattern"] == "user asks time"
        assert inst["action"] == "check clock"
        assert inst["confidence"] == 0.7

    async def test_get_nonexistent(self, mgr):
        assert await mgr.get(9999) is None

    async def test_list_all(self, mgr):
        await mgr.create("trigger1", "action1", confidence=0.3)
        await mgr.create("trigger2", "action2", confidence=0.8)
        instincts = await mgr.list()
        assert len(instincts) == 2
        # Sorted by confidence DESC
        assert instincts[0]["confidence"] >= instincts[1]["confidence"]

    async def test_list_min_confidence(self, mgr):
        await mgr.create("low", "action", confidence=0.2)
        await mgr.create("high", "action", confidence=0.8)
        filtered = await mgr.list(min_confidence=0.5)
        assert len(filtered) == 1
        assert filtered[0]["trigger_pattern"] == "high"

    async def test_activate_boosts_confidence(self, mgr):
        iid = await mgr.create("trigger", "action", confidence=0.5)
        ok = await mgr.activate(iid)
        assert ok is True
        inst = await mgr.get(iid)
        assert inst["confidence"] > 0.5
        assert inst["activation_count"] == 1
        assert inst["last_activated"] != ""

    async def test_activate_approaches_one(self, mgr):
        """Repeated activations should approach 1.0 but never exceed."""
        iid = await mgr.create("trigger", "action", confidence=0.5)
        for _ in range(50):
            await mgr.activate(iid)
        inst = await mgr.get(iid)
        assert inst["confidence"] <= 1.0
        assert inst["confidence"] > 0.99
        assert inst["activation_count"] == 50

    async def test_activate_nonexistent(self, mgr):
        ok = await mgr.activate(9999)
        assert ok is False

    async def test_delete(self, mgr):
        iid = await mgr.create("trigger", "action")
        ok = await mgr.delete(iid)
        assert ok is True
        assert await mgr.get(iid) is None

    async def test_delete_nonexistent(self, mgr):
        ok = await mgr.delete(9999)
        assert ok is False
