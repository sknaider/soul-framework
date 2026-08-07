"""Tests for RuleManager."""

from __future__ import annotations

import pytest

from soul_framework.rules.manager import RuleManager


class TestRuleManager:

    @pytest.fixture
    async def mgr(self, backend):
        return RuleManager("TestAgent", backend)

    async def test_set_returns_id(self, mgr):
        rid = await mgr.set("always_greet", "Greet the user warmly")
        assert rid > 0

    async def test_get_rule(self, mgr):
        await mgr.set("always_greet", "Greet the user warmly", priority="critical")
        rule = await mgr.get("always_greet")
        assert rule is not None
        assert rule["content"] == "Greet the user warmly"
        assert rule["priority"] == "critical"

    async def test_get_nonexistent(self, mgr):
        assert await mgr.get("nope") is None

    async def test_set_updates_existing(self, mgr):
        await mgr.set("r1", "Old content")
        await mgr.set("r1", "New content")
        rule = await mgr.get("r1")
        assert rule["content"] == "New content"

    async def test_list_rules(self, mgr):
        await mgr.set("r1", "Rule 1")
        await mgr.set("r2", "Rule 2", priority="critical")
        rules = await mgr.list()
        assert len(rules) == 2

    async def test_get_critical(self, mgr):
        await mgr.set("r1", "Normal rule", priority="normal")
        await mgr.set("r2", "Critical rule", priority="critical")
        await mgr.set("r3", "Another critical", priority="critical")
        critical = await mgr.get_critical()
        assert len(critical) == 2
        assert all(r["priority"] == "critical" for r in critical)

    async def test_deactivate(self, mgr):
        await mgr.set("r1", "Rule 1")
        ok = await mgr.deactivate("r1")
        assert ok is True
        # Not in active list
        rules = await mgr.list()
        assert len(rules) == 0
        # But in include_inactive list
        all_rules = await mgr.list(include_inactive=True)
        assert len(all_rules) == 1

    async def test_deactivate_nonexistent(self, mgr):
        ok = await mgr.deactivate("nope")
        assert ok is False

    async def test_reactivate_via_set(self, mgr):
        await mgr.set("r1", "Rule 1")
        await mgr.deactivate("r1")
        await mgr.set("r1", "Rule 1 reactivated")
        rules = await mgr.list()
        assert len(rules) == 1
        assert rules[0]["content"] == "Rule 1 reactivated"
