"""Rule data types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Rule:
    """A behavioral rule or guardrail."""
    id: int = 0
    agent: str = ""
    rule_key: str = ""
    content: str = ""
    set_by: str = "system"
    priority: str = "normal"  # "normal" | "critical"
    active: bool = True
    created_at: str = ""
