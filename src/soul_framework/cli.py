"""SOUL Framework — command-line interface.

A thin, zero-dependency CLI (stdlib argparse) over the Soul library. Every command
operates on a persistent SQLite soul stored at ~/.soul/<name>.db (override with --db
or the SOUL_DB environment variable).

    soul create Maya --ocean 0.8,0.9,0.6,0.7,0.2
    soul remember Maya "William prefers short answers" --importance 8
    soul recall Maya "how should I answer William?"
    soul boot Maya
    soul reflect Maya "Today I learned to be concise" --mood calm
    soul snapshot Maya
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any

from soul_framework.soul import Soul

_OCEAN_KEYS = ("O", "C", "E", "A", "N")


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("soul-framework")
    except Exception:
        return "0.4.2"


def _slug(name: str) -> str:
    """Filesystem-safe slug for a soul name."""
    s = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip("-")
    return s or "soul"


def _db_path(name: str, override: str = "") -> str:
    """Resolve the persistent DB path: --db > SOUL_DB env > ~/.soul/<slug>.db."""
    if override:
        return str(Path(override).expanduser())
    env = os.environ.get("SOUL_DB")
    if env:
        return str(Path(env).expanduser())
    base = Path.home() / ".soul"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / f"{_slug(name)}.db")


def _importance(raw: str) -> int:
    """argparse type: importance must be an integer in 1..10 (documented range)."""
    try:
        v = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("importance must be an integer between 1 and 10")
    if not 1 <= v <= 10:
        raise argparse.ArgumentTypeError("importance must be between 1 and 10")
    return v


def _parse_ocean(raw: str) -> dict[str, float]:
    """Parse "0.8,0.9,0.6,0.7,0.2" into {O,C,E,A,N: float}."""
    parts = [p.strip() for p in raw.split(",") if p.strip() != ""]
    if len(parts) != 5:
        raise ValueError("--ocean needs exactly 5 comma-separated values: O,C,E,A,N")
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        raise ValueError("--ocean values must be numbers between 0 and 1")
    if not all(0.0 <= v <= 1.0 for v in vals):
        raise ValueError("--ocean values must be between 0 and 1")
    return dict(zip(_OCEAN_KEYS, vals))


async def _cmd_create(args: argparse.Namespace) -> int:
    ocean = _parse_ocean(args.ocean) if args.ocean else None
    personality = {"personality": args.personality} if args.personality else None
    async with Soul.create(
        args.name, backend_url=_db_path(args.name, args.db),
        ocean=ocean, personality=personality,
    ) as _:
        pass
    where = _db_path(args.name, args.db)
    print(f"Created soul '{args.name}' at {where}")
    if ocean:
        print("OCEAN: " + ", ".join(f"{k}={ocean[k]}" for k in _OCEAN_KEYS))
    return 0


async def _cmd_remember(args: argparse.Namespace) -> int:
    async with Soul.create(args.name, backend_url=_db_path(args.name, args.db)) as agent:
        mid = await agent.memory.store(
            args.text, importance=args.importance, category=args.category,
        )
    print(f"Remembered (id={mid}, importance={args.importance}): {args.text}")
    return 0


async def _cmd_recall(args: argparse.Namespace) -> int:
    async with Soul.create(args.name, backend_url=_db_path(args.name, args.db)) as agent:
        results = await agent.memory.search(args.query, limit=args.limit)
    if not results:
        print("No memories matched.")
        return 0
    print(f"Top {len(results)} for {args.query!r}:")
    for r in results:
        print(f"  [{r.score:.3f}] (imp={r.memory.importance}) {r.memory.content}")
    return 0


async def _cmd_boot(args: argparse.Namespace) -> int:
    async with Soul.create(args.name, backend_url=_db_path(args.name, args.db)) as agent:
        print(await agent.boot())
    return 0


async def _cmd_reflect(args: argparse.Namespace) -> int:
    async with Soul.create(args.name, backend_url=_db_path(args.name, args.db)) as agent:
        tid = await agent.reflect(args.thought, emotional_state=args.mood or "")
    print(f"Reflected (id={tid}): {args.thought}")
    return 0


async def _cmd_snapshot(args: argparse.Namespace) -> int:
    async with Soul.create(args.name, backend_url=_db_path(args.name, args.db)) as agent:
        snap = await agent.snapshot()
    ocean = snap.get("ocean") or {}
    print(f"Soul: {snap.get('name')}")
    if ocean:
        print("OCEAN: " + ", ".join(f"{k}={ocean.get(k, 0):.2f}" for k in _OCEAN_KEYS))
    print(f"Memories: {len(snap.get('recent_memories') or [])} recent")
    print(f"Rules: {len(snap.get('rules') or [])} | Instincts: {len(snap.get('instincts') or [])}")
    last = snap.get("last_thought")
    if last:
        print(f"Last thought: {last.get('thought', '')}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="soul",
        description="Persistent AI souls — memory, identity, personality — from the command line.",
    )
    p.add_argument("--version", action="version", version=f"soul-framework {_version()}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    def _common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("name", help="Soul name (also selects ~/.soul/<name>.db)")
        sp.add_argument("--db", default="", help="Override the SQLite path for this soul")

    c = sub.add_parser("create", help="Create/initialize a soul (sets identity, OCEAN)")
    _common(c)
    c.add_argument("--ocean", default="", help='OCEAN scores "O,C,E,A,N" (each 0..1)')
    c.add_argument("--personality", default="", help="Freeform personality description")
    c.set_defaults(func=_cmd_create)

    r = sub.add_parser("remember", help="Store a memory")
    _common(r)
    r.add_argument("text", help="What to remember")
    r.add_argument("--importance", type=_importance, default=5, help="1..10 (default 5)")
    r.add_argument("--category", default="fact", help="Memory category (default: fact)")
    r.set_defaults(func=_cmd_remember)

    rc = sub.add_parser(
        "recall",
        help="Search stored memories (lexical by default; semantic needs the [embeddings] extra)",
        description=(
            "Search stored memories. The base install ranks by LEXICAL (word-overlap) match; "
            "install soul-framework[embeddings] and set embedding_provider='sentence-transformer' "
            "for true semantic (meaning-based) search."
        ),
    )
    _common(rc)
    rc.add_argument("query", help="What to recall")
    rc.add_argument("--limit", type=int, default=5, help="Max results (default 5)")
    rc.set_defaults(func=_cmd_recall)

    b = sub.add_parser("boot", help="Print the boot context (identity + OCEAN + rules + last thought)")
    _common(b)
    b.set_defaults(func=_cmd_boot)

    rf = sub.add_parser("reflect", help="Record an inner thought")
    _common(rf)
    rf.add_argument("thought", help="The thought to record")
    rf.add_argument("--mood", default="", help="Emotional state label")
    rf.set_defaults(func=_cmd_reflect)

    s = sub.add_parser("snapshot", help="Show a compact view of the soul's state")
    _common(s)
    s.set_defaults(func=_cmd_snapshot)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return asyncio.run(args.func(args))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
