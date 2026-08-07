"""Tests for ProceduralStore — procedural memory with trie + semantic search."""

import pytest

from soul_framework import Soul, ProceduralStore, Procedure, ProcedureSearchResult


@pytest.fixture
async def soul():
    s = await Soul.create("TestAgent", backend="sqlite")
    yield s
    await s.close()


class TestProcedureTypes:
    """Test procedure data types."""

    def test_procedure_defaults(self):
        p = Procedure()
        assert p.task_type == "general"
        assert p.hit_count == 0
        assert p.success_rate == 0.0

    def test_success_rate(self):
        p = Procedure(success_count=7, fail_count=3)
        assert p.success_rate == pytest.approx(0.7)

    def test_success_rate_zero_total(self):
        p = Procedure(success_count=0, fail_count=0)
        assert p.success_rate == 0.0

    def test_search_result(self):
        r = ProcedureSearchResult(
            procedure=Procedure(id=1),
            score=0.85,
            similarity=0.85,
            match_type="semantic",
        )
        assert r.match_type == "semantic"


class TestProceduralStore:
    """Test store and retrieve operations."""

    async def test_store_returns_id(self, soul):
        pid = await soul.procedures.store(
            "deploy to production",
            "1. Run tests\n2. Build\n3. Deploy",
        )
        assert pid > 0

    async def test_get_by_id(self, soul):
        pid = await soul.procedures.store(
            "fix database migration",
            "1. Check schema\n2. Run alembic",
            task_type="bugfix",
            facts="Always backup first",
        )
        proc = await soul.procedures.get(pid)
        assert proc is not None
        assert proc.task_description == "fix database migration"
        assert proc.workflow == "1. Check schema\n2. Run alembic"
        assert proc.task_type == "bugfix"
        assert proc.facts == "Always backup first"

    async def test_get_nonexistent(self, soul):
        proc = await soul.procedures.get(99999)
        assert proc is None

    async def test_store_with_success(self, soul):
        pid = await soul.procedures.store("task", "workflow", success=True)
        proc = await soul.procedures.get(pid)
        assert proc.success_count == 1
        assert proc.fail_count == 0

    async def test_store_with_failure(self, soul):
        pid = await soul.procedures.store("task", "workflow", success=False)
        proc = await soul.procedures.get(pid)
        assert proc.success_count == 0
        assert proc.fail_count == 1

    async def test_count(self, soul):
        assert await soul.procedures.count() == 0
        await soul.procedures.store("task1", "wf1")
        await soul.procedures.store("task2", "wf2", task_type="debug")
        assert await soul.procedures.count() == 2
        assert await soul.procedures.count(task_type="debug") == 1

    async def test_record_outcome_success(self, soul):
        pid = await soul.procedures.store("task", "workflow")
        assert await soul.procedures.record_outcome(pid, success=True)
        proc = await soul.procedures.get(pid)
        assert proc.success_count == 2  # 1 from store + 1 from record

    async def test_record_outcome_failure(self, soul):
        pid = await soul.procedures.store("task", "workflow")
        assert await soul.procedures.record_outcome(pid, success=False)
        proc = await soul.procedures.get(pid)
        assert proc.fail_count == 1

    async def test_record_outcome_nonexistent(self, soul):
        assert not await soul.procedures.record_outcome(99999, success=True)

    async def test_add_reflection(self, soul):
        pid = await soul.procedures.store("task", "workflow")
        assert await soul.procedures.add_reflection(pid, "This works well for small datasets")
        proc = await soul.procedures.get(pid)
        assert proc.reflection == "This works well for small datasets"

    async def test_add_reflection_nonexistent(self, soul):
        assert not await soul.procedures.add_reflection(99999, "no")


class TestProceduralSearch:
    """Test two-tier search: trie prefix + semantic."""

    async def test_semantic_search(self, soul):
        await soul.procedures.store("deploy web application", "1. Build\n2. Push\n3. Deploy")
        await soul.procedures.store("fix login bug", "1. Debug\n2. Fix\n3. Test")
        await soul.procedures.store("setup database", "1. Install\n2. Configure\n3. Migrate")

        results = await soul.procedures.search("deploy web app")
        assert len(results) > 0
        assert results[0].procedure.task_description == "deploy web application"

    async def test_prefix_search(self, soul):
        await soul.procedures.store("deploy to staging", "steps...")
        await soul.procedures.store("deploy to production", "steps...")
        await soul.procedures.store("debug memory leak", "steps...")

        results = await soul.procedures.search("deploy to")
        assert len(results) >= 2
        # Prefix matches should score 1.0
        prefix_results = [r for r in results if r.match_type == "prefix"]
        assert len(prefix_results) >= 1

    async def test_search_with_task_type_filter(self, soul):
        await soul.procedures.store("task a", "wf", task_type="deploy")
        await soul.procedures.store("task b", "wf", task_type="debug")

        results = await soul.procedures.search("task", task_type="deploy")
        for r in results:
            assert r.procedure.task_type == "deploy"

    async def test_search_bumps_hit_count(self, soul):
        pid = await soul.procedures.store("searchable task", "workflow here")
        await soul.procedures.search("searchable")
        proc = await soul.procedures.get(pid)
        assert proc.hit_count >= 1

    async def test_search_top_k(self, soul):
        for i in range(10):
            await soul.procedures.store(f"generic task {i}", f"workflow {i}")

        results = await soul.procedures.search("generic task", top_k=3)
        assert len(results) <= 3

    async def test_trie_invalidation(self, soul):
        await soul.procedures.store("alpha procedure", "steps")
        results1 = await soul.procedures.search("alpha")
        assert len(results1) > 0

        # Invalidate trie, add new procedure, search again
        soul.procedures.invalidate_trie()
        await soul.procedures.store("alpha new procedure", "steps")
        results2 = await soul.procedures.search("alpha")
        assert len(results2) >= 2


class TestProceduralIntegration:
    """Integration tests."""

    async def test_soul_property_access(self, soul):
        assert isinstance(soul.procedures, ProceduralStore)

    async def test_full_lifecycle(self, soul):
        # Store
        pid = await soul.procedures.store(
            "process medical data",
            "1. Load DICOM\n2. Segment\n3. Analyze",
            task_type="medical",
            facts="HIPAA compliant",
            source_task="pipeline_v2",
        )
        # Search
        results = await soul.procedures.search("medical data processing")
        assert len(results) > 0

        # Record outcomes
        await soul.procedures.record_outcome(pid, success=True)
        await soul.procedures.record_outcome(pid, success=True)
        await soul.procedures.record_outcome(pid, success=False)

        # Check rates
        proc = await soul.procedures.get(pid)
        assert proc.success_count == 3  # 1 from store + 2 from record
        assert proc.fail_count == 1
        assert proc.success_rate == pytest.approx(0.75)

        # Add reflection
        await soul.procedures.add_reflection(pid, "Works better with normalized data")
        proc = await soul.procedures.get(pid)
        assert "normalized" in proc.reflection
