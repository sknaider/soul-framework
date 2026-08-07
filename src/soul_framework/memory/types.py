"""Memory data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Memory:
    """A single memory entry."""
    id: int = 0
    agent: str = ""
    category: str = "fact"
    content: str = ""
    importance: int = 5
    valence: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0
    source: str = "conversation"
    scope: str = "private"
    confidence_score: float = 1.0
    utility_score: float = 0.5
    relevance_score: float = 1.0
    last_activation: str = ""
    identity_defining: bool = False
    event_time: str = ""
    episode_context: str = ""
    metadata: dict = field(default_factory=dict)
    valid_from: str = ""
    invalid_at: str = ""
    created_at: str = ""


@dataclass
class SearchResult:
    """A memory with similarity score."""
    memory: Memory
    score: float = 0.0
    similarity: float = 0.0
