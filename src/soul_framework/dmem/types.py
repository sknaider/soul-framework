"""D-MEM data types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DMemRoute(str, Enum):
    """Routing decision from D-MEM gate."""
    FAST_PATH = "fast_path"
    FULL_PROCESSING = "full_processing"


@dataclass
class DMemResult:
    """Result of D-MEM gate evaluation."""
    route: DMemRoute
    surprise: float = 0.0
    max_similarity: float = 0.0
    utility: float = 0.5
    rpe: float = 0.0  # reward prediction error
    reason: str = ""
