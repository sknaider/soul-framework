"""LLM provider protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Abstract LLM provider for text generation."""

    async def generate(self, prompt: str, *, max_tokens: int = 500, temperature: float = 0.3) -> str:
        """Generate text from a prompt."""
        ...
