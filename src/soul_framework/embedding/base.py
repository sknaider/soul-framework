"""Embedding provider protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Abstract embedding provider. Implementations: Simple, SentenceTransformer."""

    @property
    def dimensions(self) -> int:
        """Vector dimensionality."""
        ...

    async def embed(self, text: str) -> list[float]:
        """Embed a single text into a vector."""
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts. Default: sequential."""
        ...
