"""SOUL Framework — Configuration via stdlib dataclasses (zero external deps)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SoulConfig:
    """Configuration for a Soul instance."""

    # Backend (SQLite is zero-config; PostgreSQL requires the ``postgres`` extra)
    backend: str = "sqlite"  # "sqlite" | "postgres"
    backend_url: str = ""  # empty = in-memory SQLite; or "path/to/soul.db"
    postgres_schema: str = "soul_framework"
    postgres_auto_migrate: bool = True
    postgres_pool_min_size: int = 1
    postgres_pool_max_size: int = 10

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
    memory_search_candidate_limit: int = 100
    memory_vector_cache: bool = True  # cache embeddings in memory for SQLite search
