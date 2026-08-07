"""Neo4jBackend — Graph database backend for knowledge connectome.

Optional: requires `pip install soul-framework[graph]` (neo4j driver).
"""

from __future__ import annotations

from typing import Any

try:
    from neo4j import AsyncGraphDatabase, AsyncDriver
except ImportError:
    raise ImportError(
        "Neo4j driver not installed. Install with: pip install soul-framework[graph]"
    )


class Neo4jBackend:
    """Async Neo4j backend for graph operations.

    Usage:
        backend = Neo4jBackend("bolt://localhost:7687", auth=("neo4j", "password"))
        await backend.initialize()
        await backend.run("CREATE (n:Memory {id: $id})", id=123)
        await backend.close()
    """

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        auth: tuple[str, str] = ("neo4j", "neo4j"),
        database: str = "neo4j",
    ) -> None:
        self._uri = uri
        self._auth = auth
        self._database = database
        self._driver: AsyncDriver | None = None

    async def initialize(self) -> None:
        """Create driver and verify connectivity."""
        self._driver = AsyncGraphDatabase.driver(self._uri, auth=self._auth)
        await self._driver.verify_connectivity()

    async def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        """Execute a Cypher query and return results as list of dicts."""
        if not self._driver:
            raise RuntimeError("Neo4jBackend not initialized. Call initialize() first.")
        async with self._driver.session(database=self._database) as session:
            result = await session.run(query, params)
            records = await result.data()
            return records

    async def run_write(self, query: str, **params: Any) -> None:
        """Execute a write Cypher query (CREATE, MERGE, DELETE)."""
        if not self._driver:
            raise RuntimeError("Neo4jBackend not initialized. Call initialize() first.")
        async with self._driver.session(database=self._database) as session:
            await session.run(query, params)

    async def close(self) -> None:
        """Close the driver."""
        if self._driver:
            await self._driver.close()
            self._driver = None
