"""SOUL Framework — Configuration via stdlib dataclasses (zero external deps)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SoulConfig:
    """Configuration for a Soul instance."""

    # Backend (v0.2: sqlite only — zero-config)
    backend: str = "sqlite"  # "sqlite"
    backend_url: str = ""  # empty = in-memory SQLite; or "path/to/soul.db"

    # Embedding
    embedding_provider: str = "simple"  # "simple" | "sentence-transformer"
    embedding_dimensions: int = 128  # simple=128, sentence-transformer=768

    # LLM (optional, for enrichment)
    llm_provider: str = "stub"  # "stub" | "ollama"
    ollama_url: str = "http://localhost:11434/api/generate"
    ollama_model: str = "qwen2.5:7b"

    # OCEAN drift protection
    ocean_drift_cap: float = 0.05  # max drift per session

    # Memory search
    memory_search_default_limit: int = 10
    memory_vector_cache: bool = True  # cache embeddings in memory for SQLite search
