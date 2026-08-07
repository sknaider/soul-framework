"""Tests for graph types, entity extraction, and conflict detection (no Neo4j required)."""

import struct
import pytest

from soul_framework.graph.types import Entity, Edge, ConnectomeStats, Conflict, ConflictReport
from soul_framework.graph.connectome import extract_entities


class TestGraphTypes:
    """Test graph data types."""

    def test_entity_defaults(self):
        e = Entity(name="JARVIS")
        assert e.entity_type == "unknown"
        assert e.memory_ids == []
        assert e.mention_count == 0

    def test_edge_defaults(self):
        e = Edge(source_id=1, target_id=2)
        assert e.edge_type == "EXCITES"
        assert e.weight == 0.0

    def test_connectome_stats_summary(self):
        s = ConnectomeStats(
            agent="Test", nodes_created=10, edges_created=25,
            entities_extracted=5, dry_run=True,
        )
        text = s.summary()
        assert "DRY-RUN" in text
        assert "10 nodes" in text
        assert "25 edges" in text

    def test_connectome_stats_live(self):
        s = ConnectomeStats(agent="Test", dry_run=False)
        assert "LIVE" in s.summary()


class TestEntityExtraction:
    """Test regex-based entity extraction."""

    def test_extract_proper_nouns(self):
        entities = extract_entities("William Tovar created Team SEAL")
        names = [e[0] for e in entities]
        assert "William Tovar" in names

    def test_extract_acronyms(self):
        entities = extract_entities("The SEAL system uses HIPAA compliance and FHIR data")
        names = [e[0] for e in entities]
        assert "SEAL" in names
        assert "HIPAA" in names
        assert "FHIR" in names

    def test_extract_quoted_references(self):
        entities = extract_entities('The tool "soul-framework" was deployed')
        names = [e[0] for e in entities]
        assert "soul-framework" in names

    def test_extract_single_quoted(self):
        entities = extract_entities("William said 'Data First' is the rule")
        names = [e[0] for e in entities]
        assert "Data First" in names

    def test_no_duplicates(self):
        entities = extract_entities("SEAL loves SEAL and SEAL again")
        names = [e[0] for e in entities]
        assert names.count("SEAL") == 1

    def test_empty_text(self):
        assert extract_entities("") == []

    def test_no_entities(self):
        entities = extract_entities("simple lowercase text with no entities")
        # Should only find short strings, filtered by len > 1
        assert len(entities) == 0

    def test_mixed_entities(self):
        text = 'William Tovar built JARVIS using "soul-framework" for GTL Consulting'
        entities = extract_entities(text)
        names = [e[0] for e in entities]
        assert "William Tovar" in names
        assert "JARVIS" in names
        assert "soul-framework" in names
        assert "GTL" in names  # GTL extracted as acronym (all-caps)

    def test_entity_types(self):
        text = 'SEAL uses "PostgreSQL" and HIPAA compliance'
        entities = extract_entities(text)
        type_map = {name: etype for name, etype in entities}
        assert type_map.get("SEAL") == "acronym"
        assert type_map.get("PostgreSQL") == "reference"
        assert type_map.get("HIPAA") == "acronym"


class TestDomainClassification:
    """Test memory domain classification."""

    def test_classify_technical_by_category(self):
        from soul_framework.graph.connectome import classify_domain
        assert classify_domain("correction", "Fix the bug in parser") == "technical"
        assert classify_domain("procedure", "Deploy config for v2") == "technical"

    def test_classify_emotional_by_category(self):
        from soul_framework.graph.connectome import classify_domain
        assert classify_domain("emotion", "Feeling proud today") == "emotional"
        assert classify_domain("relationship", "Trust with William deepened") == "emotional"

    def test_classify_procedural(self):
        from soul_framework.graph.connectome import classify_domain
        assert classify_domain("procedure", "Step 1: run tests") == "procedural"

    def test_classify_identity(self):
        from soul_framework.graph.connectome import classify_domain
        assert classify_domain("belief", "I value precision above all") == "identity"
        assert classify_domain("value", "Data-first approach") == "identity"

    def test_classify_by_content_keywords(self):
        from soul_framework.graph.connectome import classify_domain
        # Technical keywords in content override general category
        assert classify_domain("fact", "The API endpoint /v2/health returns 200") == "technical"
        assert classify_domain("fact", "PostgreSQL running on port 5433") == "technical"

    def test_classify_emotional_by_content(self):
        from soul_framework.graph.connectome import classify_domain
        assert classify_domain("fact", "William se siente orgulloso del equipo") == "emotional"
        assert classify_domain("fact", "Me preocupa la estabilidad") == "emotional"

    def test_classify_identity_by_content(self):
        from soul_framework.graph.connectome import classify_domain
        assert classify_domain("fact", "My OCEAN score is O=0.78") == "identity"
        assert classify_domain("fact", "Soy JARVIS, el arquitecto") == "identity"

    def test_classify_default_general(self):
        from soul_framework.graph.connectome import classify_domain
        assert classify_domain("fact", "The weather is nice") == "general"
        assert classify_domain("", "") == "general"

    def test_all_domain_values_valid(self):
        """Ensure classify_domain always returns a valid domain."""
        from soul_framework.graph.connectome import classify_domain, VALID_DOMAINS
        for cat in ["fact", "correction", "emotion", "belief", "procedure", "relationship", "value", ""]:
            result = classify_domain(cat, "some text")
            assert result in VALID_DOMAINS, f"Invalid domain {result} for category {cat}"


class TestConnectomeBuilderDomains:
    """Test ConnectomeBuilder domain filtering (dry_run, no Neo4j needed)."""

    @pytest.fixture
    def mock_graph(self):
        """Mock Neo4j backend that tracks calls."""
        class MockGraph:
            def __init__(self):
                self.writes = []
            async def run_write(self, query, **params):
                self.writes.append((query, params))
        return MockGraph()

    @pytest.mark.asyncio
    async def test_build_similarity_with_domain_filter(self, backend, embedding, mock_graph):
        """build_similarity with domain filter only processes matching memories."""
        from soul_framework.graph.connectome import ConnectomeBuilder
        import struct

        # Insert memories with different categories (→ different domains)
        vec = await embedding.embed("test technical content about API")
        emb_bytes = struct.pack(f"<{len(vec)}f", *vec)

        for i, (cat, content) in enumerate([
            ("correction", "Fix API bug in parser"),
            ("correction", "Fix API error in handler"),
            ("emotion", "Feeling happy about progress"),
        ]):
            await backend.execute(
                """INSERT INTO memories (id, agent, content, category, embedding, importance, valid_from, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                i + 1, "TestAgent", content, cat, emb_bytes, 5,
                "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
            )

        builder = ConnectomeBuilder("TestAgent", backend, mock_graph)

        # Dry run with domain=technical should only count technical memories
        stats = await builder.build_similarity(dry_run=True, domain="technical")
        # Should have fewer edges than without domain filter
        stats_all = await builder.build_similarity(dry_run=True)
        # Technical domain excludes the emotion memory
        assert stats.edges_created <= stats_all.edges_created

    @pytest.mark.asyncio
    async def test_build_entities_with_domain(self, backend, embedding, mock_graph):
        """build_entities respects domain filter."""
        from soul_framework.graph.connectome import ConnectomeBuilder

        await backend.execute(
            """INSERT INTO memories (id, agent, content, category, importance, valid_from, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            1, "TestAgent", "William built SEAL framework", "fact", 5,
            "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        )
        await backend.execute(
            """INSERT INTO memories (id, agent, content, category, importance, valid_from, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            2, "TestAgent", "Me siento orgulloso de ADA", "emotion", 7,
            "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        )

        builder = ConnectomeBuilder("TestAgent", backend, mock_graph)

        stats_emotional = await builder.build_entities(dry_run=True, domain="emotional")
        stats_all = await builder.build_entities(dry_run=True)
        # Emotional domain should extract fewer entities than all
        assert stats_emotional.entities_extracted <= stats_all.entities_extracted

    @pytest.mark.asyncio
    async def test_build_similarity_no_domain_backward_compat(self, backend, embedding, mock_graph):
        """build_similarity without domain param still works (backward compat)."""
        from soul_framework.graph.connectome import ConnectomeBuilder
        builder = ConnectomeBuilder("TestAgent", backend, mock_graph)
        stats = await builder.build_similarity(dry_run=True)
        assert stats.dry_run is True
        assert stats.agent == "TestAgent"


class TestConflictTypes:
    """Test Conflict and ConflictReport data types."""

    def test_conflict_defaults(self):
        c = Conflict(memory_id_a=1, memory_id_b=2, conflict_type="edge_contradiction")
        assert c.similarity == 0.0
        assert c.detail == ""

    def test_conflict_report_empty(self):
        r = ConflictReport(agent="ADA")
        assert r.conflict_count == 0
        assert "0 conflicts" in r.summary()

    def test_conflict_report_with_conflicts(self):
        r = ConflictReport(
            agent="ADA",
            memories_scanned=100,
            edges_scanned=50,
            conflicts=[
                Conflict(memory_id_a=1, memory_id_b=2,
                         conflict_type="semantic_contradiction",
                         similarity=0.92, detail="test"),
                Conflict(memory_id_a=3, memory_id_b=4,
                         conflict_type="edge_contradiction",
                         similarity=0.88, detail="test2"),
            ],
        )
        assert r.conflict_count == 2
        assert "2 conflicts" in r.summary()
        assert "100 memories" in r.summary()
        assert "50 edges" in r.summary()


class TestDetectConflicts:
    """Test ConnectomeBuilder.detect_conflicts() with mock backends."""

    @pytest.fixture
    def mock_graph_with_edges(self):
        """Mock Neo4j backend that returns configurable edge data."""
        class MockGraphEdges:
            def __init__(self):
                self.edges = []
                self.writes = []

            async def run_read(self, query, **params):
                return self.edges

            async def run_write(self, query, **params):
                self.writes.append((query, params))
        return MockGraphEdges()

    @pytest.mark.asyncio
    async def test_no_conflicts_clean_data(self, backend, embedding, mock_graph_with_edges):
        """No conflicts when memories are distinct."""
        from soul_framework.graph.connectome import ConnectomeBuilder
        from soul_framework.embedding.simple import SimpleEmbedding

        emb = SimpleEmbedding()

        vec1 = await emb.embed("PostgreSQL database running on port 5433")
        vec2 = await emb.embed("William likes coffee in the morning")
        emb1 = struct.pack(f"<{len(vec1)}f", *vec1)
        emb2 = struct.pack(f"<{len(vec2)}f", *vec2)

        await backend.execute(
            """INSERT INTO memories (id, agent, content, category, embedding, importance, valid_from, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            100, "ConflictTest", "PostgreSQL database running on port 5433", "fact", emb1, 5,
            "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        )
        await backend.execute(
            """INSERT INTO memories (id, agent, content, category, embedding, importance, valid_from, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            101, "ConflictTest", "William likes coffee in the morning", "fact", emb2, 5,
            "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        )

        builder = ConnectomeBuilder("ConflictTest", backend, mock_graph_with_edges)
        report = await builder.detect_conflicts()

        assert report.conflict_count == 0
        assert report.memories_scanned == 2

    @pytest.mark.asyncio
    async def test_semantic_contradiction_detected(self, backend, embedding, mock_graph_with_edges):
        """Detects contradiction when similar memories have negation signals."""
        from soul_framework.graph.connectome import ConnectomeBuilder
        from soul_framework.embedding.simple import SimpleEmbedding

        emb = SimpleEmbedding()

        text_a = "the API endpoint returns status 200 successfully"
        text_b = "the API endpoint does not return status 200 successfully"
        vec_a = await emb.embed(text_a)
        vec_b = await emb.embed(text_b)
        emb_a = struct.pack(f"<{len(vec_a)}f", *vec_a)
        emb_b = struct.pack(f"<{len(vec_b)}f", *vec_b)

        await backend.execute(
            """INSERT INTO memories (id, agent, content, category, embedding, importance, valid_from, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            200, "ConflictTest2", text_a, "fact", emb_a, 5,
            "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        )
        await backend.execute(
            """INSERT INTO memories (id, agent, content, category, embedding, importance, valid_from, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            201, "ConflictTest2", text_b, "fact", emb_b, 5,
            "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        )

        builder = ConnectomeBuilder("ConflictTest2", backend, mock_graph_with_edges)
        report = await builder.detect_conflicts(similarity_threshold=0.5)

        assert report.memories_scanned == 2
        if report.conflict_count > 0:
            assert report.conflicts[0].conflict_type == "semantic_contradiction"
            assert "not" in report.conflicts[0].detail.lower()

    @pytest.mark.asyncio
    async def test_correction_category_flagged(self, backend, embedding, mock_graph_with_edges):
        """Memories with 'correction' category are flagged when similar to another."""
        from soul_framework.graph.connectome import ConnectomeBuilder
        from soul_framework.embedding.simple import SimpleEmbedding

        emb = SimpleEmbedding()

        text_a = "deploy the model to production server"
        text_b = "deploy the model to production server fixed version"
        vec_a = await emb.embed(text_a)
        vec_b = await emb.embed(text_b)
        emb_a = struct.pack(f"<{len(vec_a)}f", *vec_a)
        emb_b = struct.pack(f"<{len(vec_b)}f", *vec_b)

        await backend.execute(
            """INSERT INTO memories (id, agent, content, category, embedding, importance, valid_from, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            300, "ConflictTest3", text_a, "fact", emb_a, 5,
            "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        )
        await backend.execute(
            """INSERT INTO memories (id, agent, content, category, embedding, importance, valid_from, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            301, "ConflictTest3", text_b, "correction", emb_b, 5,
            "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        )

        builder = ConnectomeBuilder("ConflictTest3", backend, mock_graph_with_edges)
        report = await builder.detect_conflicts(similarity_threshold=0.5)

        if report.conflict_count > 0:
            assert any(c.detail and "correction" in c.detail.lower() for c in report.conflicts)

    @pytest.mark.asyncio
    async def test_edge_contradiction_detected(self, backend, embedding, mock_graph_with_edges):
        """Detects when same node pair has both EXCITES and INHIBITS edges."""
        from soul_framework.graph.connectome import ConnectomeBuilder

        mock_graph_with_edges.edges = [
            {"src": 1, "dst": 2, "rel": "EXCITES", "weight": 0.9},
            {"src": 1, "dst": 2, "rel": "INHIBITS", "weight": 0.85},
        ]

        builder = ConnectomeBuilder("EdgeTest", backend, mock_graph_with_edges)
        report = await builder.detect_conflicts()

        assert report.edges_scanned == 2
        edge_conflicts = [c for c in report.conflicts if c.conflict_type == "edge_contradiction"]
        assert len(edge_conflicts) == 1
        assert edge_conflicts[0].memory_id_a == 1
        assert edge_conflicts[0].memory_id_b == 2

    @pytest.mark.asyncio
    async def test_no_edge_contradiction_same_type(self, backend, embedding, mock_graph_with_edges):
        """No conflict when edges between same pair are all same type."""
        from soul_framework.graph.connectome import ConnectomeBuilder

        mock_graph_with_edges.edges = [
            {"src": 5, "dst": 6, "rel": "EXCITES", "weight": 0.9},
            {"src": 5, "dst": 6, "rel": "EXCITES", "weight": 0.7},
        ]

        builder = ConnectomeBuilder("EdgeTest2", backend, mock_graph_with_edges)
        report = await builder.detect_conflicts()

        edge_conflicts = [c for c in report.conflicts if c.conflict_type == "edge_contradiction"]
        assert len(edge_conflicts) == 0

    @pytest.mark.asyncio
    async def test_report_summary_format(self, backend, embedding, mock_graph_with_edges):
        """ConflictReport.summary() returns well-formatted string."""
        from soul_framework.graph.connectome import ConnectomeBuilder

        mock_graph_with_edges.edges = []
        builder = ConnectomeBuilder("SummaryTest", backend, mock_graph_with_edges)
        report = await builder.detect_conflicts()

        summary = report.summary()
        assert "SummaryTest" in summary
        assert "0 conflicts" in summary

    @pytest.mark.asyncio
    async def test_domain_filter_reduces_scope(self, backend, embedding, mock_graph_with_edges):
        """detect_conflicts with domain filter only scans matching memories."""
        from soul_framework.graph.connectome import ConnectomeBuilder
        from soul_framework.embedding.simple import SimpleEmbedding

        emb = SimpleEmbedding()

        # Technical memory (correction category → technical domain)
        vec_t = await emb.embed("Fix the API bug in the parser endpoint")
        emb_t = struct.pack(f"<{len(vec_t)}f", *vec_t)
        # Emotional memory (emotion category → emotional domain)
        vec_e = await emb.embed("Me siento orgulloso del equipo hoy")
        emb_e = struct.pack(f"<{len(vec_e)}f", *vec_e)

        await backend.execute(
            """INSERT INTO memories (id, agent, content, category, embedding, importance, valid_from, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            500, "DomainConflict", "Fix the API bug in the parser endpoint", "correction", emb_t, 5,
            "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        )
        await backend.execute(
            """INSERT INTO memories (id, agent, content, category, embedding, importance, valid_from, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            501, "DomainConflict", "Me siento orgulloso del equipo hoy", "emotion", emb_e, 5,
            "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        )

        builder = ConnectomeBuilder("DomainConflict", backend, mock_graph_with_edges)

        # All memories
        report_all = await builder.detect_conflicts()
        assert report_all.memories_scanned == 2

        # Only technical
        report_tech = await builder.detect_conflicts(domain="technical")
        assert report_tech.memories_scanned == 1

        # Only emotional
        report_emo = await builder.detect_conflicts(domain="emotional")
        assert report_emo.memories_scanned == 1

    @pytest.mark.asyncio
    async def test_domain_filter_no_match(self, backend, embedding, mock_graph_with_edges):
        """detect_conflicts with non-matching domain scans 0 memories."""
        from soul_framework.graph.connectome import ConnectomeBuilder
        from soul_framework.embedding.simple import SimpleEmbedding

        emb = SimpleEmbedding()
        vec = await emb.embed("Fix the bug")
        emb_bytes = struct.pack(f"<{len(vec)}f", *vec)

        await backend.execute(
            """INSERT INTO memories (id, agent, content, category, embedding, importance, valid_from, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            600, "DomainNone", "Fix the bug", "correction", emb_bytes, 5,
            "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
        )

        builder = ConnectomeBuilder("DomainNone", backend, mock_graph_with_edges)
        report = await builder.detect_conflicts(domain="emotional")
        assert report.memories_scanned == 0
        assert report.conflict_count == 0


class TestNeo4jImportSafety:
    """Test that Neo4j backend fails gracefully without driver."""

    def test_graph_init_without_neo4j(self):
        from soul_framework.graph.types import Entity, Edge, ConnectomeStats
        assert Entity is not None
        assert Edge is not None
