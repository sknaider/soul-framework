"""Tests for SleepGate — 4-phase memory consolidation."""

import pytest
from datetime import datetime, timezone, timedelta

from soul_framework import Soul, SleepGate, ConsolidationReport, PhaseResult


@pytest.fixture
async def soul():
    s = await Soul.create("TestAgent", backend="sqlite")
    yield s
    await s.close()


class TestSleepGateTypes:
    """Test consolidation data types."""

    def test_phase_result_fields(self):
        pr = PhaseResult(phase="REPLAY", affected=5, details="test")
        assert pr.phase == "REPLAY"
        assert pr.affected == 5

    def test_report_summary(self):
        report = ConsolidationReport(
            agent="Test",
            dry_run=True,
            phases=[PhaseResult(phase="REPLAY", affected=3, details="boosted")],
            total_affected=3,
        )
        s = report.summary()
        assert "DRY-RUN" in s
        assert "REPLAY" in s
        assert "3" in s

    def test_report_live_mode(self):
        report = ConsolidationReport(agent="Test", dry_run=False)
        assert "LIVE" in report.summary()


class TestSleepGateDryRun:
    """Test sleep gate in dry_run mode (no mutations)."""

    async def test_empty_memories(self, soul):
        report = await soul.sleep_gate.run(dry_run=True)
        assert isinstance(report, ConsolidationReport)
        assert len(report.phases) == 4
        assert report.total_affected == 0

    async def test_all_phases_present(self, soul):
        report = await soul.sleep_gate.run(dry_run=True)
        phase_names = [p.phase for p in report.phases]
        assert phase_names == ["REPLAY", "FORGET", "PRUNE", "CONSOLIDATE"]

    async def test_dry_run_no_mutations(self, soul):
        # Store some memories
        for i in range(5):
            await soul.memory.store(f"memory {i}", importance=3)

        count_before = await soul.memory.count()
        await soul.sleep_gate.run(dry_run=True)
        count_after = await soul.memory.count()
        assert count_before == count_after


class TestReplayPhase:
    """Test Phase 1: REPLAY — boost recently activated memories."""

    async def test_replay_boosts_activated(self, soul):
        mid = await soul.memory.store("important memory", importance=7)
        # Set last_activation to now
        now = datetime.now(timezone.utc).isoformat()
        await soul._backend.execute(
            "UPDATE memories SET last_activation = $1, relevance_score = 0.5 WHERE id = $2",
            now, mid,
        )

        report = await soul.sleep_gate.run(dry_run=False, replay_boost=0.10)
        replay = report.phases[0]
        assert replay.phase == "REPLAY"
        assert replay.affected == 1

        # Check relevance was boosted
        row = await soul._backend.fetchone(
            "SELECT relevance_score FROM memories WHERE id = $1", mid
        )
        assert row["relevance_score"] == pytest.approx(0.60, abs=0.01)

    async def test_replay_caps_at_one(self, soul):
        mid = await soul.memory.store("max relevance", importance=7)
        now = datetime.now(timezone.utc).isoformat()
        await soul._backend.execute(
            "UPDATE memories SET last_activation = $1, relevance_score = 0.95 WHERE id = $2",
            now, mid,
        )

        await soul.sleep_gate.run(dry_run=False, replay_boost=0.10)
        row = await soul._backend.fetchone(
            "SELECT relevance_score FROM memories WHERE id = $1", mid
        )
        assert row["relevance_score"] == 1.0


class TestForgetPhase:
    """Test Phase 2: FORGET — decay stale, low-importance memories."""

    async def test_forget_decays_stale(self, soul):
        mid = await soul.memory.store("stale memory", importance=5)
        # Make it stale — no activation, old
        await soul._backend.execute(
            "UPDATE memories SET last_activation = NULL, relevance_score = 0.8 WHERE id = $1",
            mid,
        )

        report = await soul.sleep_gate.run(dry_run=False, stale_days=0)
        forget = report.phases[1]
        assert forget.phase == "FORGET"
        assert forget.affected >= 1

        row = await soul._backend.fetchone(
            "SELECT relevance_score FROM memories WHERE id = $1", mid
        )
        assert row["relevance_score"] < 0.8

    async def test_forget_spares_high_importance(self, soul):
        mid = await soul.memory.store("critical memory", importance=9)
        await soul._backend.execute(
            "UPDATE memories SET relevance_score = 0.8 WHERE id = $1", mid
        )

        await soul.sleep_gate.run(dry_run=False, stale_days=0)
        row = await soul._backend.fetchone(
            "SELECT relevance_score FROM memories WHERE id = $1", mid
        )
        # importance=9 > 7, so should NOT be decayed
        assert row["relevance_score"] == pytest.approx(0.8, abs=0.01)

    async def test_emotional_resistance(self, soul):
        # Memory with high emotion should decay slower
        mid_neutral = await soul.memory.store("neutral fact", importance=5)
        mid_emotional = await soul.memory.store("emotional memory", importance=5, valence=0.9, arousal=0.8)

        for mid in [mid_neutral, mid_emotional]:
            await soul._backend.execute(
                "UPDATE memories SET last_activation = NULL, relevance_score = 0.8 WHERE id = $1",
                mid,
            )

        await soul.sleep_gate.run(dry_run=False, stale_days=0, forget_decay=0.7)

        row_n = await soul._backend.fetchone("SELECT relevance_score FROM memories WHERE id = $1", mid_neutral)
        row_e = await soul._backend.fetchone("SELECT relevance_score FROM memories WHERE id = $1", mid_emotional)

        # Emotional memory should have higher relevance (decayed less)
        assert row_e["relevance_score"] > row_n["relevance_score"]


class TestPrunePhase:
    """Test Phase 3: PRUNE — soft-invalidate low-relevance memories."""

    async def test_prune_invalidates_low_relevance(self, soul):
        mid = await soul.memory.store("forgettable", importance=3)
        await soul._backend.execute(
            "UPDATE memories SET relevance_score = 0.01 WHERE id = $1", mid
        )

        report = await soul.sleep_gate.run(dry_run=False, prune_threshold=0.05)
        prune = report.phases[2]
        assert prune.phase == "PRUNE"
        assert prune.affected >= 1

        row = await soul._backend.fetchone(
            "SELECT invalid_at FROM memories WHERE id = $1", mid
        )
        assert row["invalid_at"] is not None

    async def test_prune_protects_identity_defining(self, soul):
        mid = await soul.memory.store("I am JARVIS", importance=3)
        await soul._backend.execute(
            "UPDATE memories SET relevance_score = 0.01, identity_defining = 1 WHERE id = $1",
            mid,
        )

        await soul.sleep_gate.run(dry_run=False, prune_threshold=0.05)
        row = await soul._backend.fetchone(
            "SELECT invalid_at FROM memories WHERE id = $1", mid
        )
        assert row["invalid_at"] is None  # protected!

    async def test_prune_respects_max_prune(self, soul):
        for i in range(10):
            mid = await soul.memory.store(f"low {i}", importance=2)
            await soul._backend.execute(
                "UPDATE memories SET relevance_score = 0.001 WHERE id = $1", mid
            )

        report = await soul.sleep_gate.run(dry_run=False, prune_threshold=0.05, max_prune=3)
        prune = report.phases[2]
        assert prune.affected <= 3


class TestConsolidatePhase:
    """Test Phase 4: CONSOLIDATE — merge near-duplicate memories."""

    async def test_consolidate_merges_duplicates(self, soul):
        # Store two identical memories
        mid1 = await soul.memory.store("the sky is blue", importance=7)
        mid2 = await soul.memory.store("the sky is blue", importance=5)

        report = await soul.sleep_gate.run(dry_run=False, consolidation_similarity=0.90)
        consolidate = report.phases[3]
        assert consolidate.phase == "CONSOLIDATE"
        assert consolidate.affected >= 1

        # The lower importance one should be invalidated
        row = await soul._backend.fetchone("SELECT invalid_at FROM memories WHERE id = $1", mid2)
        assert row["invalid_at"] is not None

    async def test_consolidate_keeps_higher_importance(self, soul):
        mid1 = await soul.memory.store("exact duplicate text here", importance=3)
        mid2 = await soul.memory.store("exact duplicate text here", importance=8)

        await soul.sleep_gate.run(dry_run=False, consolidation_similarity=0.90)

        # mid2 (importance=8) should survive
        row2 = await soul._backend.fetchone("SELECT invalid_at FROM memories WHERE id = $1", mid2)
        assert row2["invalid_at"] is None

    async def test_consolidate_different_content_untouched(self, soul):
        mid1 = await soul.memory.store("apples are red fruits", importance=5)
        mid2 = await soul.memory.store("quantum mechanics explains particle behavior", importance=5)

        await soul.sleep_gate.run(dry_run=False, consolidation_similarity=0.95)

        for mid in [mid1, mid2]:
            row = await soul._backend.fetchone("SELECT invalid_at FROM memories WHERE id = $1", mid)
            assert row["invalid_at"] is None


class TestSleepGateIntegration:
    """Integration tests for full sleep gate cycle."""

    async def test_full_cycle(self, soul):
        # Create a mix of memories
        for i in range(10):
            await soul.memory.store(f"memory number {i}", importance=3 + (i % 5))

        report = await soul.sleep_gate.run(dry_run=True)
        assert isinstance(report, ConsolidationReport)
        assert len(report.phases) == 4
        # Dry run should report candidates but not mutate
        count = await soul.memory.count()
        assert count == 10

    async def test_soul_property_access(self, soul):
        assert isinstance(soul.sleep_gate, SleepGate)
