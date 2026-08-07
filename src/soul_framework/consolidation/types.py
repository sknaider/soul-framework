"""Consolidation data types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PhaseResult:
    """Result from a single consolidation phase."""
    phase: str
    affected: int = 0
    details: str = ""


@dataclass
class ConsolidationReport:
    """Full report from a sleep gate run."""
    agent: str
    dry_run: bool = True
    phases: list[PhaseResult] = field(default_factory=list)
    total_affected: int = 0

    def summary(self) -> str:
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        lines = [f"Sleep Gate [{mode}] — {self.agent}"]
        for p in self.phases:
            lines.append(f"  {p.phase}: {p.affected} affected — {p.details}")
        lines.append(f"  Total: {self.total_affected}")
        return "\n".join(lines)
