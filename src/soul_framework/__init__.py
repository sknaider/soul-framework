"""SOUL Framework — Persistent AI souls with memory, personality, and identity."""

from soul_framework.consolidation import ConsolidationReport, PhaseResult, SleepGate
from soul_framework.dmem import DMemGate, DMemResult, DMemRoute
from soul_framework.procedures import Procedure, ProceduralStore, ProcedureSearchResult
from soul_framework.soul import Soul
from soul_framework.trees import (
    BoundingBox,
    FenwickStats,
    MerkleSoul,
    RSpatialIndex,
    SpatialEntry,
    SplayCache,
    TrieIndex,
)

__version__ = "0.5.0.dev1"
__all__ = [
    # Core
    "Soul",
    # Consolidation (Phase 2)
    "SleepGate",
    "ConsolidationReport",
    "PhaseResult",
    # Procedures (Phase 2)
    "ProceduralStore",
    "Procedure",
    "ProcedureSearchResult",
    # D-MEM (Phase 2)
    "DMemGate",
    "DMemResult",
    "DMemRoute",
    # Trees (Phase 1)
    "MerkleSoul",
    "SplayCache",
    "TrieIndex",
    "FenwickStats",
    "RSpatialIndex",
    "BoundingBox",
    "SpatialEntry",
]
