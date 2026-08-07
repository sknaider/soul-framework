"""Graph data types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Entity:
    """An extracted entity from memory content."""
    name: str
    entity_type: str = "unknown"  # person, place, concept, tool, etc.
    memory_ids: list[int] = field(default_factory=list)
    mention_count: int = 0


@dataclass
class Edge:
    """A relationship between two memories or entities."""
    source_id: int
    target_id: int
    edge_type: str = "EXCITES"  # EXCITES, INHIBITS, MENTIONS
    weight: float = 0.0
    valid_from: str = ""


@dataclass
class Conflict:
    """A detected contradiction between two memories."""
    memory_id_a: int
    memory_id_b: int
    conflict_type: str  # "edge_contradiction", "semantic_contradiction"
    similarity: float = 0.0
    detail: str = ""


@dataclass
class ConflictReport:
    """Results from conflict detection scan."""
    agent: str
    conflicts: list[Conflict] = field(default_factory=list)
    memories_scanned: int = 0
    edges_scanned: int = 0

    @property
    def conflict_count(self) -> int:
        return len(self.conflicts)

    def summary(self) -> str:
        if not self.conflicts:
            return f"Conflict scan [{self.agent}]: 0 conflicts ({self.memories_scanned} memories, {self.edges_scanned} edges scanned)"
        return (
            f"Conflict scan [{self.agent}]: {self.conflict_count} conflicts found "
            f"({self.memories_scanned} memories, {self.edges_scanned} edges scanned)"
        )


@dataclass
class ConnectomeStats:
    """Statistics from a connectome build/entity extraction."""
    agent: str
    nodes_created: int = 0
    edges_created: int = 0
    entities_extracted: int = 0
    dry_run: bool = True

    def summary(self) -> str:
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        return (
            f"Connectome [{mode}] — {self.agent}: "
            f"{self.nodes_created} nodes, {self.edges_created} edges, "
            f"{self.entities_extracted} entities"
        )
