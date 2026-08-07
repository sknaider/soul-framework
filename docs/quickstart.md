# Quickstart

Give your AI agent a persistent soul in under 5 minutes.

## Install

```bash
pip install soul-framework
```

No database setup needed. SOUL uses SQLite by default -- zero configuration.

## From the command line

The package ships a `soul` command, so you can try it without writing any code. State
persists at `~/.soul/<name>.db` (override with `--db` or `$SOUL_DB`):

```bash
soul create Maya --ocean 0.8,0.9,0.6,0.7,0.2
soul remember Maya "User prefers concise technical answers" --importance 7
soul recall Maya "what does the user like?"
soul boot Maya          # the system-prompt context block
soul snapshot Maya
```

Run `soul --help` for all commands. The Python API below gives you the same soul
programmatically.

## Create Your First Agent

```python
import asyncio
from soul_framework import Soul

async def main():
    # Create an agent with a personality (OCEAN model)
    async with Soul.create("Maya", ocean={
        "O": 0.8,   # Openness — curious, creative
        "C": 0.9,   # Conscientiousness — organized, reliable
        "E": 0.6,   # Extraversion — moderately social
        "A": 0.7,   # Agreeableness — cooperative
        "N": 0.2,   # Neuroticism — emotionally stable
    }) as agent:

        # Store a memory
        await agent.memory.store(
            "User prefers concise technical answers",
            category="preference",
            importance=7,
        )

        # Search memories semantically
        results = await agent.memory.search("what does the user like?")
        for r in results:
            print(f"[{r.similarity:.2f}] {r.memory.content}")

        # Generate boot context for your LLM system prompt
        context = await agent.boot()
        print(context)

        # Record a self-reflection
        await agent.reflect(
            "First session went well, user values brevity",
            emotional_state="satisfied",
        )

asyncio.run(main())

```

## What Just Happened?

1. **`Soul.create("Maya", ocean={...})`** -- created an agent named Maya with an OCEAN personality profile. This is stored persistently.

2. **`agent.memory.store(...)`** -- stored a memory with semantic embedding. Memories are searchable by meaning, not just keywords.

3. **`agent.memory.search(...)`** -- found relevant memories using cosine similarity, ranked by relevance, importance, and recency.

4. **`agent.boot()`** -- generated a context string you can inject into any LLM system prompt. Includes identity, personality narrative, relationships, rules, and last emotional state.

5. **`agent.reflect(...)`** -- recorded an inner thought with emotional state. This creates continuity between sessions.

## OCEAN Personality Model

SOUL uses the Big Five (OCEAN) personality model to give agents consistent behavioral tendencies:

| Trait | Low (0.0) | High (1.0) |
|-------|-----------|------------|
| **O**penness | Pragmatic, conventional | Curious, creative |
| **C**onscientiousness | Flexible, spontaneous | Organized, disciplined |
| **E**xtraversion | Reflective, reserved | Energetic, social |
| **A**greeableness | Independent, direct | Cooperative, empathetic |
| **N**euroticism | Emotionally stable | Sensitive, reactive |

The `boot()` method automatically converts OCEAN scores into a first-person behavioral narrative that shapes agent responses.

## Persistent Storage

By default, SOUL stores everything in an in-memory SQLite database. For persistence across sessions, provide a file path:

```python
agent = await Soul.create("Maya",
    backend="sqlite",
    backend_url="maya_soul.db",
    ocean={"O": 0.8, "C": 0.9, "E": 0.6, "A": 0.7, "N": 0.2},
)
```

## Memory Categories

Organize memories by type:

```python
await agent.memory.store("Always validate inputs",      category="correction", importance=9)
await agent.memory.store("Deployed v2.1 to production", category="milestone",  importance=8)
await agent.memory.store("User seems frustrated today",  category="emotion",    importance=6)
await agent.memory.store("Prefers Python over JS",       category="preference", importance=5)
```

Available categories: `fact`, `preference`, `decision`, `insight`, `correction`, `milestone`, `pattern`, `emotion`, `trust`, `humor`, `dynamic`.

## Memory Scopes

Control who can see each memory:

```python
await agent.memory.store("Internal architecture note", scope="private")   # Only this agent
await agent.memory.store("Shared team decision",       scope="shared")    # Visible to peers
await agent.memory.store("Company-wide announcement",  scope="team")      # All agents
```

## Full Agent Snapshot

Get the complete state of an agent's soul:

```python
state = await agent.snapshot()
print(state["ocean"])           # OCEAN scores
print(state["recent_memories"]) # Last 10 memories
print(state["rules"])           # Active rules
print(state["instincts"])       # Learned instincts
print(state["last_thought"])    # Most recent reflection
```

## Next Steps

- **Rules**: Set behavioral rules with `agent.rules` -- constraints the agent always follows.
- **Instincts**: Auto-learned trigger-response patterns with `agent.instincts`.
- **Sleep Gate**: Memory consolidation during idle periods with `agent.sleep_gate`.
- **Procedures**: Store and search multi-step procedures with `agent.procedures`.
- **D-MEM**: Dual-memory gating for importance-based routing with `agent.dmem`.

For advanced embeddings, the graph layer, and architecture details, see the [full documentation](./architecture.md).
