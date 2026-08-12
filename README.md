# soul-framework

**Persistent AI souls — memory, personality, and identity for any LLM agent.**

Most agents forget everything between runs. `soul-framework` gives yours a *soul*: a
persistent identity with an OCEAN personality, a memory that survives restarts, and the
ability to reflect on what it learned — in ~2 minutes, zero configuration.

```python
import asyncio
from soul_framework import Soul

async def main():
    async with Soul.create("Maya", ocean={"O": 0.8, "C": 0.9, "E": 0.6, "A": 0.7, "N": 0.2}) as agent:
        await agent.memory.store("User prefers concise technical answers", importance=7)
        context = await agent.boot()        # ready-to-use system-prompt context
        print(context)                      # -> "## Identity: Maya  OCEAN Profile: ..."
        await agent.reflect("First session went well; the user values brevity")

asyncio.run(main())
```

## Command line

Prefer a terminal? `soul-framework` ships a `soul` CLI. Each soul lives in `~/.soul/<name>.db`.

```bash
soul create Maya --ocean "0.8,0.9,0.6,0.7,0.2"   # give Maya a personality
soul remember Maya "William prefers short answers" --importance 8
soul recall Maya "how should I answer?"           # lexical by default (see note below)
soul boot Maya                                    # print the system-prompt context
soul reflect Maya "the session went well" --mood satisfied
soul snapshot Maya                                # compact view of the soul's state
```

## Install

```bash
pip install soul-framework              # base: identity + memory + boot + reflect, SQLite, zero config
pip install soul-framework[embeddings]  # add TRUE semantic memory search (sentence-transformers)
pip install soul-framework[ann]         # add persistent HNSW search for large SQLite souls
pip install soul-framework[integrity]   # add signed integrity checkpoints
pip install soul-framework[postgres]    # add PostgreSQL + indexed pgvector storage
```

No database to set up — SQLite by default.

### PostgreSQL + pgvector

For a larger persistent store, install both production extras and pass the DSN
at runtime (never commit it):

```bash
pip install 'soul-framework[postgres,embeddings]'
```

```python
import os
from soul_framework import Soul
from soul_framework.config import SoulConfig

config = SoulConfig(
    backend="postgres",
    backend_url=os.environ["SOUL_POSTGRES_DSN"],
    embedding_provider="sentence-transformer",
)

async with Soul.create("Maya", config=config) as agent:
    await agent.memory.store("The user enjoys astronomy")
    matches = await agent.memory.search("favorite stargazing hobby")
```

The database administrator must enable `CREATE EXTENSION vector` once. SOUL
then applies an idempotent schema migration and uses a cosine HNSW index. The
embedding model defines meaning: pgvector scales retrieval, while the default
`simple` provider remains lexical by design. At scale, PostgreSQL first takes
the nearest `memory_search_candidate_limit` vectors and then applies SOUL's
importance/recency scoring; raise that limit when those secondary signals must
consider a wider candidate set.

The `soul` CLI intentionally remains the zero-config SQLite path in v0.4.1;
PostgreSQL is configured through the Python API shown above.

### Five-year SQLite path (local and sovereign)

For a large local soul, BGE-M3 runs through the loopback-only Ollama API and
HNSW avoids scanning every memory:

```bash
ollama pull bge-m3
pip install 'soul-framework[ann]'
```

```python
from soul_framework import Soul
from soul_framework.config import SoulConfig

config = SoulConfig(
    backend_url="maya.db",
    embedding_provider="bge-m3",
    embedding_dimensions=1024,
    memory_vector_index="hnsw",
)

async with Soul.create("Maya", config=config) as agent:
    matches = await agent.memory.search(
        "¿qué medicina debo evitar?",
        context="Estoy revisando mis antecedentes médicos",
    )
```

Existing 128-dimensional SQLite souls are migrated into a separate candidate;
the source is never overwritten and the checkpoint supports resume/rollback:

```bash
python -m soul_framework.embedding_migration run maya.db \
  --candidate maya.bge-m3.db --source-dim 128 --target-dim 1024 \
  --provider bge-m3
```

The five-year engineering gate used 54,750 synthetic memories: all 8 fixed
contextual anchors appeared in the top 5 (7/8 ranked first), and end-to-end
retrieval measured 280 ms p50 on the test host. This validates the candidate path, not a universal "never
forgets" claim; natural corpora and broader probes remain application gates.
The HNSW sidecar is bound to the SQLite source fingerprint and is rebuilt
fail-closed if stale or corrupt.

Signed Ed25519 checkpoints are available through
`soul_framework.integrity`. Strong rollback protection additionally requires
an external monotonic witness; an in-process witness is useful for tests but is
not a security boundary.

## What you get

- **Persistent identity + OCEAN personality** — the agent is the *same* agent across runs.
- **Memory that survives restarts** — store facts with importance; recall them on boot.
- **Boot context** — one call returns a system-prompt block with the agent's identity, traits, and salient memories.
- **Self-reflection** — the agent records what it learned and its emotional state.

### A note on memory search (honest by design)

- The **base install** ranks memories with **lexical token-hash matching** — zero downloads.
  It's strong when the query shares words with the memory (e.g. `"short answers"` → high),
  but a purely *semantic* query with no shared words (e.g. `"what does the user like?"`)
  scores near **0.00**. It's word-overlap search, not meaning search.
- **True *semantic* search — "find by meaning, not keywords"** — needs the embeddings extra:
  `pip install soul-framework[embeddings]` and `SoulConfig(embedding_provider="sentence-transformer")`.

We'd rather tell you this up front than have you discover a `0.00` similarity on your first
meaning-based query.

## Why soul-framework vs a general agent framework

| | soul-framework | typical agent framework |
|---|---|---|
| Persistent identity across runs | ✅ built-in (OCEAN) | ✗ / bring-your-own |
| Memory that survives restarts | ✅ SQLite by default | usually external store |
| Boot-context for the system prompt | ✅ one call | ✗ |
| Self-reflection / learning trace | ✅ | ✗ |
| Zero-config to first run | ✅ ~2 min | varies |
| Memory search — lexical (base) / semantic (`[embeddings]`) | ✅ both | varies |

`soul-framework` is not a full agent orchestrator — it's the **soul layer** you drop into any
LLM loop (LangChain, your own, whatever). It answers one question well: *how does this agent
remember who it is and what it learned?*

## Docs

- [Quickstart](docs/quickstart.md)
- [Architecture](docs/architecture.md)

## Status

Alpha (v0.4.1) — local BGE-M3 + HNSW + reversible embedding migration,
with optional signed integrity checkpoints. API may still shift before 1.0.

## License

Apache-2.0 — see [LICENSE](LICENSE).
