"""SOUL Framework — Main entry point.

Usage:
    from soul_framework import Soul

    async with Soul.create("Maya", ocean={"O": 0.8, "C": 0.9, "E": 0.6, "A": 0.7, "N": 0.2}) as agent:
        await agent.memory.store("User prefers short answers", importance=7)
        context = await agent.boot()
        await agent.reflect("Today I learned something new")
"""

from __future__ import annotations

from typing import Any, Coroutine


class _AsyncSoulContext:
    """Wrapper so Soul.create() works with both `await` and `async with`.

    Usage:
        agent = await Soul.create("Maya", ...)       # just await
        async with Soul.create("Maya", ...) as agent: # context manager
    """

    __slots__ = ("_coro", "_soul")

    def __init__(self, coro: Coroutine[Any, Any, "Soul"]) -> None:
        self._coro = coro
        self._soul: Soul | None = None

    def __await__(self):
        return self._coro.__await__()

    async def __aenter__(self) -> "Soul":
        self._soul = await self._coro
        return self._soul

    async def __aexit__(self, *args: Any) -> None:
        if self._soul is not None:
            await self._soul.close()

from soul_framework.backend.base import BackendBase
from soul_framework.backend.sqlite import SqliteBackend
from soul_framework.config import SoulConfig
from soul_framework.consolidation.sleep_gate import SleepGate
from soul_framework.dmem.gate import DMemGate
from soul_framework.embedding.base import EmbeddingProvider
from soul_framework.embedding.simple import SimpleEmbedding
from soul_framework.identity.manager import IdentityManager
from soul_framework.identity.ocean import ocean_to_narrative
from soul_framework.instincts.manager import InstinctManager
from soul_framework.llm.base import LLMProvider
from soul_framework.llm.stub import StubProvider
from soul_framework.memory.store import MemoryStore
from soul_framework.procedures.store import ProceduralStore
from soul_framework.reflection.reflect import ReflectionManager
from soul_framework.rules.manager import RuleManager


class Soul:
    """A persistent AI soul with memory, identity, and personality."""

    def __init__(
        self,
        name: str,
        backend: BackendBase,
        embedding: EmbeddingProvider,
        llm: LLMProvider,
        config: SoulConfig,
    ) -> None:
        self._name = name
        self._backend = backend
        self._embedding = embedding
        self._llm = llm
        self._config = config
        self._memory = MemoryStore(name, backend, embedding, config)
        self._identity = IdentityManager(name, backend, config)
        self._rules = RuleManager(name, backend)
        self._instincts = InstinctManager(name, backend)
        self._reflection = ReflectionManager(name, backend)
        self._sleep_gate = SleepGate(name, backend)
        self._procedures = ProceduralStore(name, backend, embedding)
        self._dmem = DMemGate(name, backend, embedding)

    @classmethod
    def create(
        cls,
        name: str,
        *,
        backend: str = "sqlite",
        backend_url: str = "",
        personality: dict[str, Any] | None = None,
        ocean: dict[str, float] | None = None,
        embedding: EmbeddingProvider | str | None = None,
        llm: LLMProvider | None = None,
        config: SoulConfig | None = None,
    ) -> _AsyncSoulContext:
        """Create a new Soul instance with all subsystems initialized.

        Supports both patterns:
            agent = await Soul.create("Maya", ...)
            async with Soul.create("Maya", ...) as agent:
        """
        return _AsyncSoulContext(cls._create_impl(
            name, backend=backend, backend_url=backend_url,
            personality=personality, ocean=ocean,
            embedding=embedding, llm=llm, config=config,
        ))

    @classmethod
    async def _create_impl(
        cls,
        name: str,
        *,
        backend: str = "sqlite",
        backend_url: str = "",
        personality: dict[str, Any] | None = None,
        ocean: dict[str, float] | None = None,
        embedding: EmbeddingProvider | str | None = None,
        llm: LLMProvider | None = None,
        config: SoulConfig | None = None,
    ) -> Soul:
        """Internal implementation of Soul creation."""
        cfg = config or SoulConfig(backend=backend, backend_url=backend_url)
        backend_name = cfg.backend if config is not None else backend
        backend_dsn = cfg.backend_url if config is not None else backend_url

        # Resolve embedding first: pgvector needs its exact dimension at migration time.
        embedding_choice = embedding if embedding is not None else cfg.embedding_provider
        if isinstance(embedding_choice, str):
            if embedding_choice == "simple":
                emb = SimpleEmbedding(dimensions=cfg.embedding_dimensions)
            elif embedding_choice in {"sentence-transformer", "sentence_transformer"}:
                from soul_framework.embedding.sentence_transformer import (
                    SentenceTransformerEmbedding,
                )

                emb = SentenceTransformerEmbedding()
            else:
                raise ValueError(f"Unknown embedding provider: {embedding_choice}")
        else:
            emb = embedding_choice

        # Initialize backend only after embedding dimensions are known.
        if backend_name == "sqlite":
            db: BackendBase = SqliteBackend(backend_dsn or ":memory:")
        elif backend_name == "postgres":
            from soul_framework.backend.postgres import PostgresBackend

            db = PostgresBackend(
                backend_dsn,
                dimensions=emb.dimensions,
                schema=cfg.postgres_schema,
                auto_migrate=cfg.postgres_auto_migrate,
                min_size=cfg.postgres_pool_min_size,
                max_size=cfg.postgres_pool_max_size,
            )
        else:
            raise ValueError(
                f"Unsupported backend: {backend_name}. Use 'sqlite' or 'postgres'."
            )
        await db.initialize()
        try:
            # Initialize LLM
            llm_inst = llm or StubProvider()

            soul = cls(name, db, emb, llm_inst, cfg)

            # Set initial identity if ocean provided
            if ocean:
                await soul._identity.set_ocean(ocean)
            if personality:
                await soul._identity.set_personality(personality)

            return soul
        except BaseException:
            # A failed constructor must not strand SQLite handles or a PG pool.
            await db.close()
            raise

    @property
    def name(self) -> str:
        return self._name

    @property
    def memory(self) -> MemoryStore:
        return self._memory

    @property
    def identity(self) -> IdentityManager:
        return self._identity

    @property
    def rules(self) -> RuleManager:
        return self._rules

    @property
    def instincts(self) -> InstinctManager:
        return self._instincts

    @property
    def sleep_gate(self) -> SleepGate:
        return self._sleep_gate

    @property
    def procedures(self) -> ProceduralStore:
        return self._procedures

    @property
    def dmem(self) -> DMemGate:
        return self._dmem

    async def boot(self) -> str:
        """Generate boot context string for any LLM system prompt.

        Loads: identity, OCEAN narrative, relationships, critical rules, last thought.
        """
        parts: list[str] = []

        # Identity
        ident = await self._identity.get()
        if ident:
            parts.append(f"## Identity: {self._name}")
            if ident.get("personality"):
                parts.append(ident["personality"])

        # OCEAN
        ocean = await self._identity.get_ocean()
        if ocean:
            narrative = ocean_to_narrative(self._name, ocean)
            scores_str = ", ".join(f"{k}={v:.3f}" for k, v in sorted(ocean.items()))
            parts.append(f"OCEAN Profile: {scores_str}")
            parts.append(f"OCEAN Narrative: {narrative}")

        # Relationships
        rels = await self._identity.get_relationships()
        if rels:
            parts.append("\n## Relationships")
            for r in rels:
                parts.append(f"- {r['person']}: trust={r['trust_level']}, style={r.get('style', 'default')}")

        # Critical rules
        rules = await self._rules.get_critical(limit=5)
        if rules:
            parts.append("\n## Critical Rules")
            for rule in rules:
                parts.append(f"- {rule['rule_key']}: {rule['content'][:100]}")

        # Last inner thought
        last_thought = await self._reflection.get_last_thought()
        if last_thought:
            parts.append(f"\n## Last Inner Thought [{last_thought.get('emotional_state', '')}]")
            parts.append(f"({last_thought['created_at']}): {last_thought['thought']}")

        parts.append("\n## Boot Protocol")
        parts.append(
            f"You are {self._name}. Use memory search for deeper recall. "
            "Your memories live in the database, always accessible."
        )

        return "\n".join(parts)

    async def reflect(self, thought: str, emotional_state: str = "") -> int:
        """Record an inner thought / self-reflection. Returns thought ID."""
        return await self._reflection.add_thought(thought, emotional_state)

    async def snapshot(self) -> dict[str, Any]:
        """Full soul state: identity, OCEAN, recent memories, rules, instincts."""
        ocean = await self._identity.get_ocean()
        ident = await self._identity.get()
        recent = await self._memory.list(limit=10)
        rules = await self._rules.list()
        instincts = await self._instincts.list()
        last_thought = await self._reflection.get_last_thought()

        return {
            "name": self._name,
            "identity": ident,
            "ocean": ocean,
            "recent_memories": recent,
            "rules": rules,
            "instincts": instincts,
            "last_thought": last_thought,
        }

    async def close(self) -> None:
        """Clean shutdown of backend connections."""
        await self._backend.close()

    async def __aenter__(self) -> Soul:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
