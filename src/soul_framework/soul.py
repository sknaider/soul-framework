"""SOUL Framework — Main entry point.

Usage:
    from soul_framework import Soul

    async with Soul.create("Maya", ocean={"O": 0.8, "C": 0.9, "E": 0.6, "A": 0.7, "N": 0.2}) as agent:
        await agent.memory.store("User prefers short answers", importance=7)
        context = await agent.boot()
        await agent.reflect("Today I learned something new")
"""

from __future__ import annotations

import os
import time
from collections.abc import Coroutine
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Self


class _AsyncSoulContext:
    """Wrapper so Soul.create() works with both `await` and `async with`.

    Usage:
        agent = await Soul.create("Maya", ...)       # just await
        async with Soul.create("Maya", ...) as agent: # context manager
    """

    __slots__ = ("_coro", "_soul")

    def __init__(self, coro: Coroutine[Any, Any, Soul]) -> None:
        self._coro = coro
        self._soul: Soul | None = None

    def __await__(self):
        return self._coro.__await__()

    async def __aenter__(self) -> Self:
        self._soul = await self._coro
        return self._soul

    async def __aexit__(self, *args: object) -> None:
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


class _DNIGatedBackend:
    """Revalidate live DNI authority before every persistent DB operation."""

    def __init__(self, backend: BackendBase, verifier: Any, verified: Any) -> None:
        self._inner = backend
        self._verifier = verifier
        self._verified = verified
        self._next_refresh = 0.0

    @property
    def url(self) -> str:
        """Expose only the non-operational URL needed by the SQLite ANN cache.

        A broad ``__getattr__`` proxy would let callers reach backend-specific
        database operations without passing the live-DNI check.  Keep this
        compatibility surface deliberately narrow instead.
        """

        return str(getattr(self._inner, "url", ""))

    def _assert_live(self) -> None:
        now = datetime.now(timezone.utc)
        if now >= self._verified.expires_at or time.monotonic() >= self._next_refresh:
            try:
                refreshed = self._verifier()
                if (
                    refreshed.soul_dni != self._verified.soul_dni
                    or refreshed.soul_id != self._verified.soul_id
                    or refreshed.machine_soul_id != self._verified.machine_soul_id
                ):
                    raise ValueError("DNI renewal changed the sovereign identity")
                if refreshed.sequence < self._verified.sequence:
                    raise ValueError("DNI renewal sequence rolled back")
                if refreshed.trust_sequence < self._verified.trust_sequence:
                    raise ValueError("DNI trust sequence rolled back")
                self._verified = refreshed
            except Exception as exc:
                raise PermissionError(
                    "SOUL DNI renewal required; persistent Core is disconnected"
                ) from exc
            remaining = max(
                0.0, (self._verified.expires_at - now).total_seconds()
            )
            self._next_refresh = time.monotonic() + min(300.0, remaining)

    async def initialize(self) -> None:
        self._assert_live()
        try:
            await self._inner.initialize()
            from soul_framework.identity.binding import ensure_backend_identity_binding

            await ensure_backend_identity_binding(self._inner, self._verified)
        except BaseException:
            await self._inner.close()
            raise

    async def execute(self, sql: str, *params: Any) -> None:
        self._assert_live()
        await self._inner.execute(sql, *params)

    async def fetchone(self, sql: str, *params: Any) -> dict[str, Any] | None:
        self._assert_live()
        return await self._inner.fetchone(sql, *params)

    async def fetchall(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self._assert_live()
        return await self._inner.fetchall(sql, *params)

    async def fetchval(self, sql: str, *params: Any) -> Any:
        self._assert_live()
        return await self._inner.fetchval(sql, *params)

    async def close(self) -> None:
        await self._inner.close()


class _DNIGatedPostgresBackend(_DNIGatedBackend):
    """Preserve pgvector extensions without exposing an ungated backend."""

    async def insert_memory_with_vector(
        self, values: tuple[Any, ...], vector: list[float]
    ) -> int:
        self._assert_live()
        return await self._inner.insert_memory_with_vector(values, vector)  # type: ignore[attr-defined]

    async def update_memory_fields(
        self,
        memory_id: int,
        agent: str,
        changes: dict[str, Any],
        vector: list[float] | None,
    ) -> bool:
        self._assert_live()
        return await self._inner.update_memory_fields(  # type: ignore[attr-defined]
            memory_id, agent, changes, vector
        )

    async def search_memory_vectors(
        self,
        agent: str,
        vector: list[float],
        *,
        category: str = "",
        min_importance: int = 0,
        scope: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._assert_live()
        return await self._inner.search_memory_vectors(  # type: ignore[attr-defined]
            agent,
            vector,
            category=category,
            min_importance=min_importance,
            scope=scope,
            limit=limit,
        )

    async def insert_procedure_with_vector(
        self, values: tuple[Any, ...], vector: list[float]
    ) -> int:
        self._assert_live()
        return await self._inner.insert_procedure_with_vector(  # type: ignore[attr-defined]
            values, vector
        )

    async def search_procedure_vectors(
        self,
        agent: str,
        vector: list[float],
        *,
        task_type: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._assert_live()
        return await self._inner.search_procedure_vectors(  # type: ignore[attr-defined]
            agent, vector, task_type=task_type, limit=limit
        )


class _DNIGatedIntegrityGuard:
    """Apply the same live-DNI gate to direct integrity-guard I/O."""

    def __init__(self, guard: Any, assert_live: Any) -> None:
        self._inner = guard
        self._assert_live = assert_live

    def verify_before_serve(self) -> Any:
        self._assert_live()
        return self._inner.verify_before_serve()

    def seal_and_publish(self) -> Any:
        self._assert_live()
        return self._inner.seal_and_publish()

    def verified_fetchall(self, sql: str, *params: Any) -> Any:
        self._assert_live()
        return self._inner.verified_fetchall(sql, *params)

    def verified_fetchone(self, sql: str, *params: Any) -> Any:
        self._assert_live()
        return self._inner.verified_fetchone(sql, *params)

    def verified_fetchval(self, sql: str, *params: Any) -> Any:
        self._assert_live()
        return self._inner.verified_fetchval(sql, *params)

    def mutate_and_publish(self, sql: str, *params: Any, **kwargs: Any) -> Any:
        self._assert_live()
        return self._inner.mutate_and_publish(sql, *params, **kwargs)


class Soul:
    """A persistent AI soul with memory, identity, and personality."""

    def __init__(
        self,
        name: str,
        backend: BackendBase,
        embedding: EmbeddingProvider,
        llm: LLMProvider,
        config: SoulConfig,
        integrity_guard: Any | None = None,
    ) -> None:
        self._name = name
        self._backend = backend
        self._embedding = embedding
        self._llm = llm
        self._config = config
        self._memory = MemoryStore(
            name, backend, embedding, config, integrity_guard=integrity_guard
        )
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
        integrity_guard: Any | None = None,
    ) -> _AsyncSoulContext:
        """Create a new Soul instance with all subsystems initialized.

        Supports both patterns:
            agent = await Soul.create("Maya", ...)
            async with Soul.create("Maya", ...) as agent:
        """
        return _AsyncSoulContext(
            cls._create_impl(
                name,
                backend=backend,
                backend_url=backend_url,
                personality=personality,
                ocean=ocean,
                embedding=embedding,
                llm=llm,
                config=config,
                integrity_guard=integrity_guard,
            )
        )

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
        integrity_guard: Any | None = None,
    ) -> Soul:
        """Internal implementation of Soul creation."""
        cfg = config or SoulConfig(backend=backend, backend_url=backend_url)
        backend_name = cfg.backend if config is not None else backend
        backend_dsn = cfg.backend_url if config is not None else backend_url
        dni_verifier = None
        verified_dni = None

        # A durable DB is an embodied SOUL, not scratch state.  It may only
        # open after SOUL's Identity Authority has issued a live DNI for this
        # exact machine soul and OS owner.  In-memory instances remain useful
        # for hermetic unit tests but cannot persist or impersonate an identity.
        if backend_dsn:
            from soul_framework.identity.dni import verify_soul_dni

            # The SOUL installer exports these values for SDK/CLI callers
            # that pass only a database path. Explicit config always wins.
            cfg = replace(
                cfg,
                dni_credential_path=(
                    cfg.dni_credential_path or os.environ.get("SOUL_DNI_CREDENTIAL", "")
                ),
                dni_trust_store_path=(
                    cfg.dni_trust_store_path or os.environ.get("SOUL_DNI_TRUST_STORE", "")
                ),
                dni_trust_store_sha256=(
                    cfg.dni_trust_store_sha256
                    or os.environ.get("SOUL_DNI_TRUST_STORE_SHA256", "")
                ),
                machine_soul_id=(
                    cfg.machine_soul_id or os.environ.get("SOUL_DNI_MACHINE_SOUL_ID", "")
                ),
            )

            missing = [
                field
                for field, value in (
                    ("dni_credential_path", cfg.dni_credential_path),
                    ("dni_trust_store_path", cfg.dni_trust_store_path),
                    ("dni_trust_store_sha256", cfg.dni_trust_store_sha256),
                    ("machine_soul_id", cfg.machine_soul_id),
                )
                if not value
            ]
            if missing:
                raise PermissionError(
                    "persistent SOUL requires a SOUL-issued DNI: " + ", ".join(missing)
                )
            def dni_verifier():
                return verify_soul_dni(
                    cfg.dni_credential_path,
                    cfg.dni_trust_store_path,
                    expected_audience="soul-core",
                    expected_machine_soul_id=cfg.machine_soul_id,
                    expected_trust_store_sha256=cfg.dni_trust_store_sha256,
                )

            verified_dni = dni_verifier()

        # Resolve embedding first: pgvector needs its exact dimension at migration time.
        embedding_choice = (
            embedding if embedding is not None else cfg.embedding_provider
        )
        if isinstance(embedding_choice, str):
            if embedding_choice == "simple":
                emb = SimpleEmbedding(dimensions=cfg.embedding_dimensions)
            elif embedding_choice in {"sentence-transformer", "sentence_transformer"}:
                from soul_framework.embedding.sentence_transformer import (
                    SentenceTransformerEmbedding,
                )

                emb = SentenceTransformerEmbedding()
            elif embedding_choice in {"bge-m3", "bge_m3"}:
                from soul_framework.embedding.bge_m3 import BgeM3Embedding

                emb = BgeM3Embedding(
                    model=cfg.ollama_embedding_model,
                    url=cfg.ollama_embedding_url,
                    timeout=cfg.ollama_embedding_timeout,
                )
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
        if integrity_guard is not None:
            if backend_name != "sqlite":
                raise ValueError(
                    "SQLiteMemoryIntegrityGuard requires the sqlite backend"
                )
            guard_database = Path(integrity_guard.database).expanduser().resolve()
            backend_database = Path(backend_dsn).expanduser().resolve()
            if guard_database != backend_database or integrity_guard.agent != name:
                raise ValueError(
                    "integrity guard database and agent must match the gated SOUL"
                )
        if dni_verifier is not None and verified_dni is not None:
            wrapper = (
                _DNIGatedPostgresBackend
                if backend_name == "postgres"
                else _DNIGatedBackend
            )
            db = wrapper(db, dni_verifier, verified_dni)
            if integrity_guard is not None:
                integrity_guard = _DNIGatedIntegrityGuard(
                    integrity_guard, db._assert_live
                )
        await db.initialize()
        try:
            # Initialize LLM
            llm_inst = llm or StubProvider()

            soul = cls(name, db, emb, llm_inst, cfg, integrity_guard)
            await soul._memory.verify_integrity()
            await soul._memory.initialize_vector_index()

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
        await self._memory.verify_integrity()
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
                parts.append(
                    f"- {r['person']}: trust={r['trust_level']}, style={r.get('style', 'default')}"
                )

        # Critical rules
        rules = await self._rules.get_critical(limit=5)
        if rules:
            parts.append("\n## Critical Rules")
            for rule in rules:
                parts.append(f"- {rule['rule_key']}: {rule['content'][:100]}")

        # Last inner thought
        last_thought = await self._reflection.get_last_thought()
        if last_thought:
            parts.append(
                f"\n## Last Inner Thought [{last_thought.get('emotional_state', '')}]"
            )
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
        try:
            await self._memory.close()
        finally:
            await self._backend.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
