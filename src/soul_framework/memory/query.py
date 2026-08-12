"""Query preparation helpers for durable, long-horizon recall.

Semantic embeddings cannot infer context that is absent from the query.  These
helpers make the caller-provided conversation context explicit and bounded so
the same input produces the same embedding text across backends.
"""

from __future__ import annotations

from collections.abc import Iterable


def contextualize_query(
    query: str,
    context: str | Iterable[str] | None = None,
    *,
    max_context_chars: int = 2_000,
) -> str:
    """Return deterministic embedding text containing query and recent context.

    Context is opt-in: callers that do not have trusted conversation context keep
    the historical behavior.  The most recent context is retained when the bound
    is exceeded, preventing unbounded prompt/history growth.
    """
    clean_query = query.strip()
    if not context:
        return clean_query
    if max_context_chars < 0:
        raise ValueError("max_context_chars must be non-negative")

    if isinstance(context, str):
        context_text = context.strip()
    else:
        context_text = "\n".join(
            part.strip() for part in context if isinstance(part, str) and part.strip()
        )
    if not context_text or max_context_chars == 0:
        return clean_query
    if len(context_text) > max_context_chars:
        context_text = context_text[-max_context_chars:]
    # Keep the embedding text natural and language-neutral.  Artificial English
    # labels degraded multilingual BGE-M3 recall in the five-year gate.
    return f"{context_text}\n{clean_query}"
