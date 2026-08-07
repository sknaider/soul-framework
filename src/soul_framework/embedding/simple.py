"""Simple embedding provider — TF-IDF hashing trick, zero external deps.

Produces fixed-size dense vectors using a hashing trick on word tokens.
Good enough for basic semantic search with <10K memories.
For production, use sentence-transformers: pip install soul-framework[embeddings]
"""

from __future__ import annotations

import hashlib
import math
import re


class SimpleEmbedding:
    """Hash-based embedding with cosine similarity. Zero external dependencies."""

    def __init__(self, dimensions: int = 128) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        """Embed text into a fixed-size vector using hashing trick."""
        return self._hash_embed(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts."""
        return [self._hash_embed(t) for t in texts]

    def _hash_embed(self, text: str) -> list[float]:
        """Create a dense vector via token hashing."""
        tokens = _tokenize(text)
        vec = [0.0] * self._dimensions

        for token in tokens:
            # Hash token to get bucket index and sign
            h = hashlib.md5(token.encode("utf-8")).hexdigest()
            bucket = int(h[:8], 16) % self._dimensions
            sign = 1.0 if int(h[8:10], 16) % 2 == 0 else -1.0
            vec[bucket] += sign

        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]

        return vec


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + lowercase tokenizer."""
    text = text.lower()
    tokens = re.findall(r"\w+", text)
    # Add bigrams for better semantic signal
    bigrams = [f"{tokens[i]}_{tokens[i+1]}" for i in range(len(tokens) - 1)]
    return tokens + bigrams


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
