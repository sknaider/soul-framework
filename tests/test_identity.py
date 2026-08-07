"""Tests for IdentityManager — OCEAN, personality, relationships."""

from __future__ import annotations

import pytest

from soul_framework.identity.manager import IdentityManager
from soul_framework.identity.ocean import ocean_to_narrative
from soul_framework.identity.types import OceanScores


class TestOceanScores:

    def test_to_dict(self):
        s = OceanScores(O=0.8, C=0.9, E=0.6, A=0.7, N=0.2)
        d = s.to_dict()
        assert d == {"O": 0.8, "C": 0.9, "E": 0.6, "A": 0.7, "N": 0.2}

    def test_from_dict(self):
        s = OceanScores.from_dict({"O": 0.1, "C": 0.2, "E": 0.3, "A": 0.4, "N": 0.5})
        assert s.O == 0.1
        assert s.N == 0.5

    def test_from_dict_defaults(self):
        s = OceanScores.from_dict({})
        assert s.O == 0.5


class TestOceanNarrative:

    def test_high_c_high_o(self):
        narrative = ocean_to_narrative("Test", {"O": 0.8, "C": 0.9, "E": 0.6, "A": 0.7, "N": 0.1})
        assert "meticuloso" in narrative
        assert "abierto" in narrative

    def test_low_everything(self):
        narrative = ocean_to_narrative("Test", {"O": 0.3, "C": 0.3, "E": 0.2, "A": 0.3, "N": 0.5})
        assert "pragmatico" in narrative
        assert "introvertido" in narrative

    def test_returns_spanish(self):
        narrative = ocean_to_narrative("Test", {"O": 0.5, "C": 0.5, "E": 0.5, "A": 0.5, "N": 0.5})
        assert narrative.startswith("Soy ")


class TestIdentityManager:

    @pytest.fixture
    async def mgr(self, backend, config):
        return IdentityManager("TestAgent", backend, config)

    async def test_get_empty(self, mgr):
        result = await mgr.get()
        assert result is None

    async def test_set_and_get_ocean(self, mgr):
        await mgr.set_ocean({"O": 0.8, "C": 0.9, "E": 0.6, "A": 0.7, "N": 0.2})
        ocean = await mgr.get_ocean()
        assert ocean is not None
        assert ocean["O"] == 0.8
        assert ocean["N"] == 0.2

    async def test_set_ocean_validates(self, mgr):
        with pytest.raises(ValueError, match="must be in"):
            await mgr.set_ocean({"O": 1.5, "C": 0.5, "E": 0.5, "A": 0.5, "N": 0.5})

    async def test_update_ocean_with_drift_cap(self, mgr):
        await mgr.set_ocean({"O": 0.5, "C": 0.5, "E": 0.5, "A": 0.5, "N": 0.5})
        # Try to push O by +0.3, but cap is 0.05
        new = await mgr.update_ocean({"O": 0.3})
        assert new["O"] == pytest.approx(0.55, abs=0.001)  # capped at +0.05

    async def test_update_ocean_clamps_to_bounds(self, mgr):
        await mgr.set_ocean({"O": 0.98, "C": 0.5, "E": 0.5, "A": 0.5, "N": 0.02})
        new = await mgr.update_ocean({"O": 0.05, "N": -0.05})
        assert new["O"] <= 1.0
        assert new["N"] >= 0.0

    async def test_set_personality(self, mgr):
        await mgr.set_personality({"personality": "Strategic thinker", "philosophy": "Plan first"})
        ident = await mgr.get()
        assert ident is not None
        assert ident["personality"] == "Strategic thinker"
        assert ident["philosophy"] == "Plan first"

    async def test_update_personality(self, mgr):
        await mgr.set_personality({"personality": "Original"})
        await mgr.set_personality({"personality": "Updated"})
        ident = await mgr.get()
        assert ident["personality"] == "Updated"

    async def test_relationships_empty(self, mgr):
        rels = await mgr.get_relationships()
        assert rels == []

    async def test_set_relationship(self, mgr):
        await mgr.set_relationship("William", trust_level=0.95, style="respectful")
        rels = await mgr.get_relationships()
        assert len(rels) == 1
        assert rels[0]["person"] == "William"
        assert rels[0]["trust_level"] == 0.95

    async def test_update_relationship(self, mgr):
        await mgr.set_relationship("ADA", trust_level=0.8)
        await mgr.set_relationship("ADA", trust_level=0.95, style="direct, brotherly")
        rels = await mgr.get_relationships()
        assert len(rels) == 1
        assert rels[0]["trust_level"] == 0.95
        assert rels[0]["style"] == "direct, brotherly"

    async def test_multiple_relationships_sorted(self, mgr):
        await mgr.set_relationship("Low", trust_level=0.3)
        await mgr.set_relationship("High", trust_level=0.9)
        await mgr.set_relationship("Mid", trust_level=0.6)
        rels = await mgr.get_relationships()
        assert rels[0]["person"] == "High"
        assert rels[2]["person"] == "Low"
