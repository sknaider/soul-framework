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
```

No database to set up — SQLite by default.

## What you get

- **Persistent identity + OCEAN personality** — the agent is the *same* agent across runs.
- **Memory that survives restarts** — store facts with importance; recall them on boot.
- **Boot context** — one call returns a system-prompt block with the agent's identity, traits, and salient memories.
- **Self-reflection** — the agent records what it learned and its emotional state.

### A note on memory search (honest by design)

- The **base install** ranks memories with **lexical (TF-IDF) matching** — zero downloads.
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

Alpha (v0.2.0) — extracted from Team SEAL's production system. API may still shift before 1.0.

## License

Apache-2.0 — see [LICENSE](LICENSE).
