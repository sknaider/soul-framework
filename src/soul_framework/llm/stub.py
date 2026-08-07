"""Stub LLM provider — returns empty string. For when no LLM is needed."""

from __future__ import annotations


class StubProvider:
    """No-op LLM provider. Memory store/search work without LLM; enrichment is skipped."""

    async def generate(self, prompt: str, *, max_tokens: int = 500, temperature: float = 0.3) -> str:
        return ""
