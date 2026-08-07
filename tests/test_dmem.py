"""Tests for DMemGate — dopamine-gated memory routing."""

import pytest

from soul_framework import Soul, DMemGate, DMemResult, DMemRoute


@pytest.fixture
async def soul():
    s = await Soul.create("TestAgent", backend="sqlite")
    yield s
    await s.close()


class TestDMemTypes:
    """Test D-MEM data types."""

    def test_route_enum(self):
        assert DMemRoute.FAST_PATH == "fast_path"
        assert DMemRoute.FULL_PROCESSING == "full_processing"

    def test_result_defaults(self):
        r = DMemResult(route=DMemRoute.FAST_PATH)
        assert r.surprise == 0.0
        assert r.utility == 0.5
        assert r.rpe == 0.0


class TestDMemGateEmpty:
    """Test D-MEM gate with no existing memories."""

    async def test_novel_content_full_processing(self, soul):
        result = await soul.dmem.evaluate("completely new content")
        assert result.route == DMemRoute.FULL_PROCESSING
        assert result.surprise == 1.0
        assert result.max_similarity == 0.0
        assert "novel" in result.reason

    async def test_empty_returns_full_processing(self, soul):
        result = await soul.dmem.evaluate("anything")
        assert result.route == DMemRoute.FULL_PROCESSING


class TestDMemGateRouting:
    """Test routing decisions based on surprise."""

    async def test_redundant_content_fast_path(self, soul):
        # Store a memory first
        await soul.memory.store("the weather is sunny today")
        # Very similar content should route to fast_path
        result = await soul.dmem.evaluate(
            "the weather is sunny today",
            utility=0.3,  # low utility
        )
        assert result.route == DMemRoute.FAST_PATH
        assert result.surprise < 0.3
        assert result.max_similarity > 0.7

    async def test_novel_content_full_processing(self, soul):
        await soul.memory.store("the weather is sunny today")
        result = await soul.dmem.evaluate(
            "quantum entanglement breaks locality assumptions",
            utility=0.5,
        )
        assert result.route == DMemRoute.FULL_PROCESSING
        assert result.surprise > 0.3

    async def test_high_utility_overrides_low_surprise(self, soul):
        await soul.memory.store("database migration steps")
        # Similar content but high utility → full processing
        result = await soul.dmem.evaluate(
            "database migration steps",
            utility=0.8,  # high utility forces full processing
        )
        assert result.route == DMemRoute.FULL_PROCESSING
        assert "utility" in result.reason

    async def test_custom_thresholds(self, soul):
        await soul.memory.store("testing memory")
        result = await soul.dmem.evaluate(
            "testing memory",
            utility=0.1,
            threshold_surprise=0.99,  # very high bar → almost everything is fast_path
            threshold_utility=0.99,
        )
        assert result.route == DMemRoute.FAST_PATH

    async def test_rpe_calculation(self, soul):
        await soul.memory.store("existing context")
        result = await soul.dmem.evaluate("brand new topic", utility=0.8)
        # RPE = surprise * utility
        assert result.rpe == pytest.approx(result.surprise * result.utility, abs=0.01)


class TestDMemGateMultipleMemories:
    """Test with multiple existing memories."""

    async def test_similarity_against_most_similar(self, soul):
        await soul.memory.store("python programming language")
        await soul.memory.store("javascript frontend framework")
        await soul.memory.store("rust memory safety")

        result = await soul.dmem.evaluate("python code review")
        # Should find python memory as most similar
        assert result.max_similarity > 0.0
        assert result.surprise < 1.0

    async def test_lookback_limit(self, soul):
        # Store more memories than lookback
        for i in range(5):
            await soul.memory.store(f"memory content number {i}")

        gate = DMemGate(
            "TestAgent", soul._backend, soul._embedding,
            lookback=3,  # only look at last 3
        )
        result = await gate.evaluate("memory content")
        assert isinstance(result, DMemResult)


class TestDMemIntegration:
    """Integration tests."""

    async def test_soul_property_access(self, soul):
        assert isinstance(soul.dmem, DMemGate)

    async def test_evaluate_then_store(self, soul):
        """Typical workflow: evaluate → decide → store."""
        content = "new important finding"
        result = await soul.dmem.evaluate(content)

        if result.route == DMemRoute.FULL_PROCESSING:
            # Full A-MEM pipeline
            mid = await soul.memory.store(content, importance=8)
            assert mid > 0
        else:
            # Fast path — store directly, lower importance
            mid = await soul.memory.store(content, importance=5)
            assert mid > 0
