# Architecture

> **Draft** — this document is a working draft awaiting final review. Content and structure may change before publication.

SOUL gives AI agents a persistent identity, memory, and personality that survive across sessions. This document explains how the framework is organized, the core abstractions, and how to extend it.

## Design Principles

1. **Persistence is the default.** Memories, identity, rules, and emotional state all live in a database — not in the model's context window. The LLM is stateless; the soul is not.

2. **Separation of soul and model.** The framework is LLM-agnostic. Your agent can be backed by GPT, Claude, Llama, Qwen, or any other model. SOUL doesn't care — it only provides the substrate (memory, personality, continuity).

3. **Semantic over keyword.** Memories are stored with embeddings and retrieved by meaning, ranked by similarity, importance, and recency.

4. **Zero-config by default.** `Soul.create("Maya")` works out of the box with SQLite and a local embedding provider. No database setup, no API keys, no cloud.

5. **Extensible at every layer.** Backend, embedding, and LLM are swappable interfaces — implement the interface to bring your own storage, your own sentence transformer, or your own inference server.

## Module Overview

```
soul_framework/
├── soul.py              # Main entry point — the Soul class
├── config.py            # SoulConfig (dataclass for options)
│
├── backend/             # Persistent storage (abstract + implementations)
│   ├── base.py          #   BackendBase — the storage contract
│   ├── sqlite.py        #   SqliteBackend — default, zero-config
│   └── schema.py        #   Shared SQL schema definitions
│
├── embedding/           # Turn text into vectors
│   ├── base.py          #   EmbeddingProvider interface
│   ├── simple.py        #   SimpleEmbedding — hash-based, no dependencies
│   └── sentence_transformer.py  # Real semantic embeddings
│
├── llm/                 # Optional LLM integration (for reflection, summaries)
│   ├── base.py          #   LLMProvider interface
│   ├── stub.py          #   StubProvider — no-op default
│   └── ollama.py        #   OllamaProvider — local inference
│
├── memory/              # Episodic memory — facts, events, preferences
│   └── store.py         #   MemoryStore: store, search, list, invalidate
│
├── identity/            # Who the agent is
│   ├── manager.py       #   IdentityManager: personality, OCEAN, relationships
│   └── ocean.py         #   OCEAN → first-person narrative
│
├── rules/               # Behavioral constraints the agent always follows
│   └── manager.py       #   RuleManager: critical/high/normal priority
│
├── instincts/           # Trigger-response patterns (auto-learned reflexes)
│   └── manager.py       #   InstinctManager: activate, evolve, promote
│
├── reflection/          # Inner monologue — private self-reflection
│   └── reflect.py       #   ReflectionManager: add_thought, get_last_thought
│
├── consolidation/       # Memory consolidation during idle periods
│   └── sleep_gate.py    #   SleepGate: mood retrieval, importance rescoring
│
├── dmem/                # Dual-memory routing (fast vs durable)
│   └── gate.py          #   DMemGate: importance-based tier selection
│
├── procedures/          # Multi-step procedural memory
│   └── store.py         #   ProceduralStore: store and search how-to knowledge
│
├── graph/               # Optional graph memory (relationships, entities)
│   ├── connectome.py    #   Connectome: typed edges between memories
│   └── neo4j.py         #   Neo4j backend for graph queries
│
└── trees/               # Merkle trees for integrity verification
```

## Core Flow

The five operations that matter most in a typical session:

```
 ┌──────────────────────────────────────────────────────────────┐
 │                                                              │
 │    ┌─────────────┐                                           │
 │    │ Soul.create │──── creates Soul instance                 │
 │    │  (name)     │     initializes backend, embedding, LLM   │
 │    └──────┬──────┘                                           │
 │           │                                                  │
 │           ▼                                                  │
 │    ┌─────────────┐     ┌──────────────────────────────┐      │
 │    │   boot()    │────►│  load identity + OCEAN +     │      │
 │    │             │     │  relationships + rules +     │      │
 │    │             │     │  last inner thought          │      │
 │    │             │◄────│  → system prompt string      │      │
 │    └─────────────┘     └──────────────────────────────┘      │
 │           │                                                  │
 │           ▼                                                  │
 │    ┌─────────────┐     ┌──────────────────────────────┐      │
 │    │ memory.store│────►│  embed content               │      │
 │    │             │     │  persist with category,      │      │
 │    │             │     │  importance, scope, VAD      │      │
 │    └─────────────┘     └──────────────────────────────┘      │
 │           │                                                  │
 │           ▼                                                  │
 │    ┌─────────────┐     ┌──────────────────────────────┐      │
 │    │memory.search│────►│  embed query                 │      │
 │    │             │     │  cosine similarity           │      │
 │    │             │     │  rank by sim × importance    │      │
 │    │             │◄────│          × temporal decay    │      │
 │    └─────────────┘     └──────────────────────────────┘      │
 │           │                                                  │
 │           ▼                                                  │
 │    ┌─────────────┐     ┌──────────────────────────────┐      │
 │    │  reflect()  │────►│  record inner thought        │      │
 │    │             │     │  with emotional_state        │      │
 │    │             │     │  → continuity between        │      │
 │    │             │     │    sessions                  │      │
 │    └─────────────┘     └──────────────────────────────┘      │
 │                                                              │
 └──────────────────────────────────────────────────────────────┘
```

## The Soul Class

`Soul` is the main entry point. It owns references to every subsystem but delegates all real work to managers. You never construct managers directly — access them as properties:

```python
agent.memory       # MemoryStore
agent.identity     # IdentityManager
agent.rules        # RuleManager
agent.instincts    # InstinctManager
agent.sleep_gate   # SleepGate
agent.procedures   # ProceduralStore
agent.dmem         # DMemGate
```

Top-level convenience methods:

- `boot()` — returns a context string with identity, OCEAN narrative, relationships, critical rules, and last inner thought. Inject into your LLM's system prompt.
- `reflect(thought, emotional_state)` — record a private inner thought.
- `snapshot()` — full dict of soul state (identity, OCEAN, recent memories, rules, instincts, last thought).
- `close()` — clean shutdown (also handled by `async with`).

## Memory Model

Every memory has:

| Field | Purpose |
|-------|---------|
| `content` | The text itself |
| `embedding` | Vector for semantic search |
| `category` | `fact`, `preference`, `decision`, `insight`, `correction`, `milestone`, `pattern`, `emotion`, `trust`, `humor`, `dynamic` |
| `importance` | 1–10 scale — affects ranking and consolidation |
| `scope` | `private`, `shared`, `team` — controls visibility across agents |
| `valence`, `arousal`, `dominance` | VAD affective state |
| `confidence` | How sure the agent is of this memory |
| `utility` | Measured usefulness (updated by feedback) |
| `event_time` | When the event happened (vs when it was recorded) |
| `episode_context` | Links related memories into episodes |

Search is ranked by a composite score: `similarity × importance × temporal_decay`. High-importance memories decay slower than low-importance ones.

## Identity and OCEAN

Identity is more than a name. SOUL models personality with the Big Five (OCEAN):

- **O**penness — curiosity, creativity
- **C**onscientiousness — organization, reliability
- **E**xtraversion — social energy
- **A**greeableness — cooperation, empathy
- **N**euroticism — emotional sensitivity

Scores are stored as floats 0.0–1.0. The `ocean_to_narrative()` function converts them into a first-person behavioral sentence that gets injected into the boot context. This is how personality actually shapes responses — the LLM sees its own character description and stays in character.

Identity also tracks:

- **Relationships** — per-person trust, interaction style, history
- **Personality descriptors** — free-form text beyond OCEAN

## Rules and Instincts

**Rules** are explicit behavioral constraints (like "always verify sources before citing"). They have priorities (`critical`, `high`, `normal`) and get injected into the boot context so the agent sees them every session.

**Instincts** are trigger-response patterns — faster than rules, automatic. An instinct looks like "when X happens, do Y" and has a confidence score. High-confidence instincts (≥0.7) show up in boot context. Instincts can be auto-learned from interactions, consolidated, and promoted.

## Reflection and Continuity

`reflect()` records inner thoughts — private self-reflection between turns. Each thought has an `emotional_state` field (curious, frustrated, proud, uncertain, …). The most recent thought is loaded into `boot()`, so a new session starts with continuity: the agent remembers how it felt at the end of the last session.

This is the difference between a chatbot that resets every conversation and an agent with genuine continuity.

## Consolidation (SleepGate)

During idle periods, the SleepGate does what sleep does for humans: it reviews recent memories, adjusts importance scores based on retrieval patterns, and consolidates related memories. This keeps the memory store from bloating with low-value noise and surfaces emotionally salient patterns.

## D-MEM Gate

Dual-memory routing. Important memories get routed to durable storage immediately; trivial ones go to fast-access tier and may be pruned. This is an importance-based write path — you can think of it as analogous to hippocampus vs cortex.

## Procedures

Some knowledge is how-to, not what-is: multi-step procedures, recipes, workflows. `ProceduralStore` is a separate space for that, searchable by intent rather than content.

## Extending SOUL

### Custom Backend

Implement `BackendBase` and pass your instance to `Soul.create(..., backend=my_backend)`. You need to handle the schema defined in `backend/schema.py` — migrations for memory, identity, rules, instincts, inner_monologue, and the rest.

### Custom Embedding

Implement `EmbeddingProvider`:

```python
class EmbeddingProvider(Protocol):
    dimensions: int
    async def embed(self, text: str) -> list[float]: ...
```

Pass an instance to `Soul.create(..., embedding=my_embedder)`. SOUL ships with:
- `SimpleEmbedding` — hash-based, zero dependencies, good for tests
- `SentenceTransformerEmbedding` — real semantic embeddings, requires `sentence-transformers`

### Custom LLM

Implement `LLMProvider`. The LLM is optional — SOUL works without it (reflection, summaries, and consolidation gracefully degrade). When present, SOUL can use it for memory summarization, instinct generation, and reflection assistance.

Ships with:
- `StubProvider` — no-op, default
- `OllamaProvider` — local inference via Ollama

## Design Decisions

- **Async everywhere.** Every operation that touches storage is async. This makes SOUL compatible with any async framework (FastAPI, aiohttp, etc.) and keeps hot paths non-blocking.
- **Context managers by default.** `async with Soul.create(...) as agent` handles clean shutdown. A plain `await Soul.create(...)` also works if you manage the lifecycle manually.
- **No hidden state.** Every memory, rule, thought, and relationship is in the database. What you see is what persists.
- **Importance is the knob.** Importance drives ranking, decay, consolidation, and D-MEM routing. It's the single most important parameter when you `store()` a memory — take the time to set it meaningfully.

## Status

This is a draft. Final structure may be reorganized before publication.
