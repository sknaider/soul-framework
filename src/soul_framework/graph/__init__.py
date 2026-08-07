"""Graph — Neo4j connectome and entity management."""

from soul_framework.graph.types import ConnectomeStats, Edge, Entity

__all__ = ["Entity", "Edge", "ConnectomeStats"]

# Neo4jBackend and ConnectomeBuilder are optional — require neo4j driver
try:
    from soul_framework.graph.neo4j import Neo4jBackend  # noqa: F401
    __all__.append("Neo4jBackend")
except ImportError:
    pass

# ConnectomeBuilder can be imported independently (uses TYPE_CHECKING for Neo4j)
from soul_framework.graph.connectome import ConnectomeBuilder, extract_entities, classify_domain, VALID_DOMAINS  # noqa: F401, E402
__all__ += ["ConnectomeBuilder", "extract_entities", "classify_domain", "VALID_DOMAINS"]
