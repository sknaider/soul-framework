"""Tests for temporal_decay_score — parity with original scoring.py."""

from __future__ import annotations

import math

import pytest

from soul_framework.memory.scoring import (
    HALF_LIFE_BY_CATEGORY,
    HALF_LIFE_DEFAULT,
    temporal_decay_score,
)


class TestTemporalDecayScore:

    def test_fresh_memory_high_score(self):
        """A fresh, relevant, important memory should score high."""
        score = temporal_decay_score(
            similarity=0.9, days_old=0.0, importance=8, category="fact"
        )
        assert score > 0.5

    def test_old_memory_decays(self):
        """Same memory, older = lower score."""
        fresh = temporal_decay_score(similarity=0.8, days_old=0, importance=5)
        old = temporal_decay_score(similarity=0.8, days_old=60, importance=5)
        assert fresh > old

    def test_importance_10_is_immortal(self):
        """importance >= 10 means no decay."""
        score_1day = temporal_decay_score(similarity=0.8, days_old=1, importance=10)
        score_1000days = temporal_decay_score(similarity=0.8, days_old=1000, importance=10)
        assert abs(score_1day - score_1000days) < 0.01

    def test_importance_weight(self):
        """Higher importance = higher score, all else equal."""
        low = temporal_decay_score(similarity=0.8, days_old=0, importance=2)
        high = temporal_decay_score(similarity=0.8, days_old=0, importance=9)
        assert high > low

    def test_emotion_category_decays_fast(self):
        """Emotions have 1-day half-life."""
        score = temporal_decay_score(
            similarity=0.8, days_old=2, importance=5, category="emotion"
        )
        # After 2 days with 1-day half-life, decay = 0.25
        assert score < 0.3

    def test_milestone_decays_slow(self):
        """Milestones have 730-day half-life."""
        score = temporal_decay_score(
            similarity=0.8, days_old=365, importance=5, category="milestone"
        )
        # After 1 year with 2-year half-life, decay = ~0.71
        assert score > 0.2

    def test_emotional_modulation(self):
        """High valence/arousal increases half-life."""
        neutral = temporal_decay_score(
            similarity=0.8, days_old=30, importance=5,
            valence=0.0, arousal=0.0, category="fact"
        )
        emotional = temporal_decay_score(
            similarity=0.8, days_old=30, importance=5,
            valence=0.8, arousal=0.9, category="fact"
        )
        assert emotional > neutral

    def test_utility_and_confidence(self):
        """Higher utility and confidence boost score."""
        low = temporal_decay_score(
            similarity=0.8, days_old=0, importance=5,
            utility=0.1, confidence=0.1,
        )
        high = temporal_decay_score(
            similarity=0.8, days_old=0, importance=5,
            utility=0.9, confidence=0.9,
        )
        assert high > low

    def test_half_life_categories_exist(self):
        """All expected categories are defined."""
        expected = {"emotion", "humor", "dynamic", "pattern", "insight",
                    "fact", "preference", "decision", "trust", "correction", "milestone"}
        assert expected == set(HALF_LIFE_BY_CATEGORY.keys())

    def test_importance_8_floor_180(self):
        """importance >= 8 has minimum 180-day half-life."""
        # emotion normally = 1 day, but with imp=8 → max(1*2, 180) = 180
        score = temporal_decay_score(
            similarity=0.8, days_old=90, importance=8, category="emotion"
        )
        # 90 days with 180-day half-life → decay ~0.71
        assert score > 0.3

    def test_zero_similarity(self):
        """Zero similarity should produce low score."""
        score = temporal_decay_score(similarity=0.0, days_old=0, importance=5)
        assert score < 0.3

    def test_default_half_life(self):
        assert HALF_LIFE_DEFAULT == 30.0
