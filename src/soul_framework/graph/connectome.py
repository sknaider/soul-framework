"""ConnectomeBuilder — Build knowledge graph from memories.

Two operations ported from mcp_server_v3.py:
1. build_similarity() — EXCITES/INHIBITS edges from embedding cosine similarity
2. build_entities() — MENTIONS edges from entity extraction (regex, no LLM)
"""

from __future__ import annotations

import re
import struct
from typing import Any

from typing import TYPE_CHECKING

from soul_framework.backend.base import BackendBase
from soul_framework.embedding.simple import cosine_similarity
from soul_framework.graph.types import Conflict, ConflictReport, ConnectomeStats

if TYPE_CHECKING:
    from soul_framework.graph.neo4j import Neo4jBackend


def _unpack_embedding(data: bytes) -> list[float]:
    n = len(data) // 4
    return list(struct.unpack(f"<{n}f", data))


# Simple entity extraction patterns (no LLM needed)
_ENTITY_PATTERNS = [
    # Capitalized words (2+ consecutive) — likely proper nouns
    (r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", "person_or_place"),
    # ALL CAPS words (3+ chars) — acronyms/identifiers
    (r"\b([A-Z]{3,})\b", "acronym"),
    # Quoted strings — explicit references
    (r'"([^"]{2,50})"', "reference"),
    (r"'([^']{2,50})'", "reference"),
]


# ── Domain classification ──

VALID_DOMAINS = frozenset({"technical", "emotional", "procedural", "identity", "general"})

_CATEGORY_DOMAIN: dict[str, str] = {
    "correction": "technical",
    "bug": "technical",
    "emotion": "emotional",
    "relationship": "emotional",
    "belief": "identity",
    "value": "identity",
    "personality": "identity",
    "workflow": "procedural",
}

_CONTENT_DOMAIN_PATTERNS: list[tuple[list[str], str]] = [
    (["API", "endpoint", "PostgreSQL", "port", "bug", "deploy", "error", "fix", "crash",
      "server", "database", "query", "schema", "test", "código", "función"], "technical"),
    (["orgulloso", "preocupa", "siento", "feliz", "triste", "angry", "trust", "confianza",
      "proud", "worried", "happy", "emoción", "miedo", "love", "afraid"], "emotional"),
    (["OCEAN", "personality", "identidad", "soy JARVIS", "soy ADA", "mi rol",
      "mi personalidad", "belief", "value", "principio"], "identity"),
    (["step", "paso", "workflow", "pipeline", "sequence", "procedimiento"], "procedural"),
]


def classify_domain(category: str, content: str) -> str:
    """Classify a memory into a domain based on category and content.

    Priority: category mapping > content keywords > default 'general'.
    Special case: 'procedure' category checks content for workflow keywords.
    """
    cat_lower = category.lower().strip()

    # Special: procedure can be procedural or technical
    if cat_lower == "procedure":
        content_lower = content.lower()
        for kw in ["step", "paso", "workflow", "sequence", "procedimiento"]:
            if kw in content_lower:
                return "procedural"
        return "technical"

    # Direct category mapping
    if cat_lower in _CATEGORY_DOMAIN:
        return _CATEGORY_DOMAIN[cat_lower]

    # Content keyword scanning
    content_lower = content.lower()
    for keywords, domain in _CONTENT_DOMAIN_PATTERNS:
        for kw in keywords:
            if kw.lower() in content_lower:
                return domain

    return "general"


def extract_entities(text: str) -> list[tuple[str, str]]:
    """Extract entities from text using regex patterns.

    Returns list of (entity_name, entity_type) tuples.
    """
    entities: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pattern, etype in _ENTITY_PATTERNS:
        for match in re.finditer(pattern, text):
            name = match.group(1).strip()
            key = name.lower()
            if key not in seen and len(name) > 1:
                entities.append((name, etype))
                seen.add(key)
    return entities


class ConnectomeBuilder:
    """Build knowledge graph edges from memory similarity and entities.

    Usage:
        builder = ConnectomeBuilder(agent, db_backend, neo4j_backend)
        stats = await builder.build_similarity(dry_run=True)
        stats = await builder.build_entities(dry_run=True)
    """

    def __init__(
        self,
        agent: str,
        backend: BackendBase,
        graph: "Neo4jBackend",
    ) -> None:
        self._agent = agent
        self._db = backend
        self._graph = graph

    async def build_similarity(
        self,
        *,
        dry_run: bool = True,
        similarity_threshold: float = 0.70,
        max_neighbors: int = 20,
        domain: str = "",
    ) -> ConnectomeStats:
        """Build EXCITES/INHIBITS edges from embedding cosine similarity.

        For each memory, finds top-N similar memories above threshold.
        correction+correction or correction+other → INHIBITS edge.
        If domain is set, only process memories matching that domain.
        """
        stats = ConnectomeStats(agent=self._agent, dry_run=dry_run)

        # Load all valid memories with embeddings
        rows = await self._db.fetchall(
            """SELECT id, category, content, embedding FROM memories
               WHERE agent = $1 AND invalid_at IS NULL AND embedding IS NOT NULL""",
            self._agent,
        )

        entries: list[dict[str, Any]] = []
        for row in rows:
            emb_data = row.get("embedding")
            if not emb_data or len(emb_data) < 8:
                continue
            # Domain filter
            if domain:
                mem_domain = classify_domain(
                    row.get("category", "fact"),
                    row.get("content", ""),
                )
                if mem_domain != domain:
                    continue
            entries.append({
                "id": row["id"],
                "category": row.get("category", "fact"),
                "vec": _unpack_embedding(emb_data),
            })

        if not dry_run:
            # Clear existing edges for this agent
            await self._graph.run_write(
                "MATCH (m:Memory {agent: $agent})-[r]-() DELETE r",
                agent=self._agent,
            )

        # Build edges
        for i, entry in enumerate(entries):
            # Ensure node exists
            if not dry_run:
                await self._graph.run_write(
                    "MERGE (m:Memory {memory_id: $mid, agent: $agent})",
                    mid=entry["id"], agent=self._agent,
                )
                stats.nodes_created += 1

            # Find similar neighbors
            similarities: list[tuple[int, str, float]] = []
            for j, other in enumerate(entries):
                if i == j:
                    continue
                sim = cosine_similarity(entry["vec"], other["vec"])
                if sim >= similarity_threshold:
                    similarities.append((other["id"], other["category"], sim))

            # Sort by similarity, keep top-N
            similarities.sort(key=lambda x: x[2], reverse=True)
            for neighbor_id, neighbor_cat, weight in similarities[:max_neighbors]:
                # Determine edge type
                is_correction = entry["category"] == "correction" or neighbor_cat == "correction"
                edge_type = "INHIBITS" if is_correction else "EXCITES"

                if not dry_run:
                    await self._graph.run_write(
                        f"""MERGE (a:Memory {{memory_id: $aid, agent: $agent}})
                            MERGE (b:Memory {{memory_id: $bid, agent: $agent}})
                            MERGE (a)-[r:{edge_type}]->(b)
                            SET r.weight = $weight""",
                        aid=entry["id"], bid=neighbor_id,
                        agent=self._agent, weight=weight,
                    )
                stats.edges_created += 1

        return stats

    async def detect_conflicts(
        self,
        *,
        similarity_threshold: float = 0.85,
        contradiction_keywords: tuple[str, ...] = (
            "no ", "nunca", "incorrecto", "error", "falso", "wrong",
            "not ", "never", "incorrect", "false", "don't", "doesn't",
            "ya no", "antes ", "cambió", "reemplaz", "obsolet",
        ),
        domain: str = "",
    ) -> ConflictReport:
        """Detect contradictions in the connectome.

        Two types of conflicts:
        1. Edge contradictions: same node pair has both EXCITES and INHIBITS
        2. Semantic contradictions: memories with high embedding similarity
           (>threshold) but content containing negation/contradiction signals

        If domain is set, only scan memories matching that domain.
        """
        report = ConflictReport(agent=self._agent)

        # Load all valid memories with embeddings and content
        rows = await self._db.fetchall(
            """SELECT id, category, content, embedding FROM memories
               WHERE agent = $1 AND invalid_at IS NULL AND embedding IS NOT NULL""",
            self._agent,
        )

        entries: list[dict[str, Any]] = []
        for row in rows:
            emb_data = row.get("embedding")
            if not emb_data or len(emb_data) < 8:
                continue
            # Domain filter
            if domain:
                mem_domain = classify_domain(
                    row.get("category", "fact"),
                    row.get("content", ""),
                )
                if mem_domain != domain:
                    continue
            entries.append({
                "id": row["id"],
                "category": row.get("category", "fact"),
                "content": row.get("content", ""),
                "vec": _unpack_embedding(emb_data),
            })

        report.memories_scanned = len(entries)

        # --- Type 1: Edge contradictions (EXCITES vs INHIBITS between same nodes) ---
        try:
            edge_rows = await self._graph.run_read(
                """MATCH (a:Memory {agent: $agent})-[r]->(b:Memory {agent: $agent})
                   RETURN a.memory_id AS src, b.memory_id AS dst,
                          type(r) AS rel, r.weight AS weight""",
                agent=self._agent,
            )

            # Group edges by (src, dst) pair
            edge_map: dict[tuple[int, int], list[tuple[str, float]]] = {}
            for erow in edge_rows:
                pair = (erow["src"], erow["dst"])
                edge_map.setdefault(pair, []).append(
                    (erow["rel"], erow.get("weight", 0.0))
                )
                report.edges_scanned += 1

            for (src, dst), edges in edge_map.items():
                rel_types = {e[0] for e in edges}
                if "EXCITES" in rel_types and "INHIBITS" in rel_types:
                    report.conflicts.append(Conflict(
                        memory_id_a=src,
                        memory_id_b=dst,
                        conflict_type="edge_contradiction",
                        similarity=max(e[1] for e in edges),
                        detail=f"Both EXCITES and INHIBITS between memories {src} and {dst}",
                    ))
        except Exception:
            # Neo4j may not have edges yet — skip edge check
            pass

        # --- Type 2: Semantic contradictions (high similarity + negation signals) ---
        for i, entry_a in enumerate(entries):
            for j, entry_b in enumerate(entries):
                if j <= i:
                    continue
                sim = cosine_similarity(entry_a["vec"], entry_b["vec"])
                if sim < similarity_threshold:
                    continue

                # Check for contradiction signals in content
                content_a = entry_a["content"].lower()
                content_b = entry_b["content"].lower()

                has_contradiction = False
                matched_keyword = ""
                for kw in contradiction_keywords:
                    a_has = kw in content_a
                    b_has = kw in content_b
                    # One has negation, the other doesn't → potential contradiction
                    if a_has != b_has:
                        has_contradiction = True
                        matched_keyword = kw.strip()
                        break

                # Also flag if one is a "correction" category
                if entry_a["category"] == "correction" or entry_b["category"] == "correction":
                    has_contradiction = True
                    matched_keyword = matched_keyword or "correction_category"

                if has_contradiction:
                    report.conflicts.append(Conflict(
                        memory_id_a=entry_a["id"],
                        memory_id_b=entry_b["id"],
                        conflict_type="semantic_contradiction",
                        similarity=round(sim, 4),
                        detail=f"High similarity ({sim:.3f}) with contradiction signal: '{matched_keyword}'",
                    ))

        return report

    async def build_entities(
        self,
        *,
        dry_run: bool = True,
        batch_size: int = 100,
        domain: str = "",
    ) -> ConnectomeStats:
        """Build MENTIONS edges from entity extraction.

        Extracts entities from memory content using regex patterns,
        creates Entity nodes and MENTIONS edges.
        If domain is set, only process memories matching that domain.
        """
        stats = ConnectomeStats(agent=self._agent, dry_run=dry_run)

        rows = await self._db.fetchall(
            """SELECT id, content, category FROM memories
               WHERE agent = $1 AND invalid_at IS NULL""",
            self._agent,
        )

        for row in rows:
            if domain:
                mem_domain = classify_domain(
                    row.get("category", "fact"),
                    row.get("content", ""),
                )
                if mem_domain != domain:
                    continue
            entities = extract_entities(row.get("content", ""))
            for entity_name, entity_type in entities:
                if not dry_run:
                    await self._graph.run_write(
                        """MERGE (e:Entity {name: $name})
                           ON CREATE SET e.entity_type = $etype
                           MERGE (m:Memory {memory_id: $mid, agent: $agent})
                           MERGE (m)-[:MENTIONS]->(e)""",
                        name=entity_name, etype=entity_type,
                        mid=row["id"], agent=self._agent,
                    )
                stats.entities_extracted += 1

        return stats
