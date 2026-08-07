"""Shared fixtures for soul-framework tests."""

from __future__ import annotations

import pytest

from soul_framework import Soul
from soul_framework.backend.sqlite import SqliteBackend
from soul_framework.config import SoulConfig
from soul_framework.embedding.simple import SimpleEmbedding


@pytest.fixture
async def backend():
    """SQLite in-memory backend, initialized with schema."""
    db = SqliteBackend(":memory:")
    await db.initialize()
    yield db
    await db.close()


@pytest.fixture
def embedding():
    """Simple hash-based embedding provider."""
    return SimpleEmbedding(dimensions=128)


@pytest.fixture
def config():
    """Default config for tests."""
    return SoulConfig()


@pytest.fixture
async def soul():
    """Full Soul instance with SQLite in-memory backend."""
    async with await Soul.create(
        "TestAgent",
        backend="sqlite",
        ocean={"O": 0.8, "C": 0.9, "E": 0.6, "A": 0.7, "N": 0.2},
    ) as s:
        yield s
