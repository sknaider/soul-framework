"""Instinct data types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Instinct:
    """A learned behavior that forms and decays like a habit."""
    id: int = 0
    agent: str = ""
    trigger_pattern: str = ""
    action: str = ""
    confidence: float = 0.5
    activation_count: int = 0
    last_activated: str = ""
    created_at: str = ""
