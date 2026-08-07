"""Storage backends: SQLite (zero-config) and optional PostgreSQL + pgvector."""

from soul_framework.backend.base import BackendBase
from soul_framework.backend.sqlite import SqliteBackend

__all__ = ["BackendBase", "SqliteBackend"]
