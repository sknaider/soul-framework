from __future__ import annotations

import pytest

from soul_framework.memory.query import contextualize_query


def test_contextualize_query_preserves_legacy_input_without_context():
    assert contextualize_query("  what happened?  ") == "what happened?"


def test_contextualize_query_includes_recent_conversation_context():
    result = contextualize_query(
        "¿cómo la tomo?",
        ["Estamos hablando del café.", "William pregunta por su bebida."],
    )
    assert "Estamos hablando del café." in result
    assert result.endswith("\n¿cómo la tomo?")


def test_contextualize_query_bounds_context_from_the_recent_end():
    result = contextualize_query("q", "OLD-newest", max_context_chars=6)
    assert "OLD-" not in result
    assert "newest" in result


def test_contextualize_query_rejects_negative_bound():
    with pytest.raises(ValueError, match="non-negative"):
        contextualize_query("q", "context", max_context_chars=-1)
