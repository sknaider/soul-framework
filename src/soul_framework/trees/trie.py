"""TrieIndex — Prefix lookup tree for procedures, tools, commands.

O(L) search where L = key length. Supports prefix search, autocomplete, delete.

Original: ADA (Team SEAL), Proposal: JARVIS
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _TrieNode:
    children: dict[str, _TrieNode] = field(default_factory=dict)
    is_end: bool = False
    value: Any = None
    count: int = 0


class TrieIndex:
    """Trie for O(L) prefix search of procedures, MCP tools, commands.

    Usage:
        trie = TrieIndex()
        trie.insert("memory_search", {"tool": "memory_search"})
        trie.insert("memory_store", {"tool": "memory_store"})
        results = trie.search_prefix("memory_")  # Both
        exact = trie.search("memory_search")  # One
    """

    def __init__(self) -> None:
        self._root = _TrieNode()
        self._size = 0

    def insert(self, key: str, value: Any = None) -> None:
        """Insert a key with optional associated value."""
        node = self._root
        for char in key:
            if char not in node.children:
                node.children[char] = _TrieNode()
            node = node.children[char]
            node.count += 1
        node.is_end = True
        node.value = value
        self._size += 1

    def search(self, key: str) -> Any | None:
        """Exact search. Returns value if found, None otherwise."""
        node = self._traverse(key)
        if node and node.is_end:
            return node.value
        return None

    def has_key(self, key: str) -> bool:
        """Check if exact key exists."""
        node = self._traverse(key)
        return node is not None and node.is_end

    def search_prefix(self, prefix: str) -> list[tuple[str, Any]]:
        """Find all entries matching a prefix. Returns list of (key, value)."""
        node = self._traverse(prefix)
        if not node:
            return []
        results: list[tuple[str, Any]] = []
        self._collect(node, prefix, results)
        return results

    def delete(self, key: str) -> bool:
        """Delete a key. Returns True if found and removed."""
        if not self.has_key(key):
            return False
        self._delete(self._root, key, 0)
        return True

    def autocomplete(self, prefix: str, limit: int = 10) -> list[str]:
        """Return up to `limit` keys matching prefix."""
        results = self.search_prefix(prefix)
        return [k for k, _ in results[:limit]]

    def _traverse(self, prefix: str) -> _TrieNode | None:
        node = self._root
        for char in prefix:
            if char not in node.children:
                return None
            node = node.children[char]
        return node

    def _collect(self, node: _TrieNode, prefix: str, results: list[tuple[str, Any]]) -> None:
        if node.is_end:
            results.append((prefix, node.value))
        for char in sorted(node.children.keys()):
            self._collect(node.children[char], prefix + char, results)

    def _delete(self, node: _TrieNode, key: str, depth: int) -> bool:
        if depth == len(key):
            if not node.is_end:
                return False
            node.is_end = False
            node.value = None
            self._size -= 1
            return len(node.children) == 0
        char = key[depth]
        if char not in node.children:
            return False
        should_delete = self._delete(node.children[char], key, depth + 1)
        if should_delete:
            del node.children[char]
            node.count -= 1
            return not node.is_end and len(node.children) == 0
        node.count -= 1
        return False

    def count_prefix(self, prefix: str) -> int:
        """Count how many keys have this prefix."""
        node = self._traverse(prefix)
        return node.count if node else 0

    @property
    def size(self) -> int:
        return self._size
