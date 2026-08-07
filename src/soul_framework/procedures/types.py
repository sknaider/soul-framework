"""Procedural memory data types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Procedure:
    """A stored procedural memory (task + workflow)."""
    id: int = 0
    agent: str = ""
    task_type: str = "general"
    task_description: str = ""
    workflow: str = ""
    facts: str = ""
    hit_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    source_task: str = ""
    build_policy: str = "direct"
    reflection: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count
        return self.success_count / total if total > 0 else 0.0


@dataclass
class ProcedureSearchResult:
    """A procedure with search relevance score."""
    procedure: Procedure
    score: float = 0.0
    similarity: float = 0.0
    match_type: str = "semantic"  # "prefix" or "semantic"
