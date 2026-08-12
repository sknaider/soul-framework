"""SOUL Framework — Configuration via stdlib dataclasses (zero external deps)."""

from __future__ import annotations

from dataclasses import dataclass


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
    embedding_provider: str = "simple"  # simple | sentence-transformer | bge-m3
    embedding_dimensions: int = 128  # simple=128; provider-specific otherwise

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
    memory_vector_index: str = "auto"  # auto | hnsw | exact | off
    memory_hnsw_m: int = 16
    memory_hnsw_ef_construction: int = 200
    memory_hnsw_ef_search: int = 20_000
    memory_semantic_floor: float = 0.80
    memory_context_max_chars: int = 2_000
    memory_exact_fallback: bool = True

    # Local sovereign BGE-M3 through Ollama.
    ollama_embedding_url: str = "http://127.0.0.1:11434/api/embed"
    ollama_embedding_model: str = "bge-m3"
    ollama_embedding_timeout: float = 60.0
