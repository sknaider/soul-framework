"""SentenceTransformer embedding provider.

Requires: pip install soul-framework[embeddings]
"""

from __future__ import annotations

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None  # type: ignore[assignment,misc]


class SentenceTransformerEmbedding:
    """Embedding provider using sentence-transformers.

    Usage:
        emb = SentenceTransformerEmbedding("all-MiniLM-L6-v2")
        vector = await emb.embed("Hello world")
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        if SentenceTransformer is None:
            raise ImportError(
                "sentence-transformers is required. "
                "Install with: pip install soul-framework[embeddings]"
            )
        self._model = SentenceTransformer(model_name)
        get_dimensions = getattr(self._model, "get_embedding_dimension", None)
        if get_dimensions is None:  # sentence-transformers < 5.0
            get_dimensions = self._model.get_sentence_embedding_dimension
        self._dimensions = get_dimensions()

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        """Embed a single text."""
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in a single batch (efficient)."""
        vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=32)
        return [v.tolist() for v in vectors]
