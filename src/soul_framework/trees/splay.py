"""SplayCache — Adaptive L1 cache using a Splay Tree.

Frequently accessed elements move to the root.
Natural Zipf distribution: 20% of memories = 80% of accesses.

Original: ADA (Team SEAL), Proposal: JARVIS
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class _SplayNode:
    key: Any
    value: Any
    left: Optional[_SplayNode] = None
    right: Optional[_SplayNode] = None
    access_count: int = 0
    last_access: float = 0.0


class SplayCache:
    """Splay Tree as adaptive L1 cache for memories.

    Usage:
        cache = SplayCache(max_size=500)
        cache.put("ocean_scores", {...})
        result = cache.get("ocean_scores")  # O(1) amortized if hot
        stats = cache.stats()
    """

    def __init__(self, max_size: int = 500) -> None:
        self._root: _SplayNode | None = None
        self._size: int = 0
        self._max_size: int = max_size
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0

    def _rotate_right(self, node: _SplayNode) -> _SplayNode:
        left = node.left
        node.left = left.right
        left.right = node
        return left

    def _rotate_left(self, node: _SplayNode) -> _SplayNode:
        right = node.right
        node.right = right.left
        right.left = node
        return right

    @staticmethod
    def _cmp_key(k: Any) -> tuple:
        """Normalize key for safe cross-type comparison."""
        return (0, k) if isinstance(k, str) else (1, str(k))

    def _lt(self, a: Any, b: Any) -> bool:
        return self._cmp_key(a) < self._cmp_key(b)

    def _gt(self, a: Any, b: Any) -> bool:
        return self._cmp_key(a) > self._cmp_key(b)

    def _splay(self, root: _SplayNode | None, key: Any) -> _SplayNode | None:
        if root is None or root.key == key:
            return root
        if self._lt(key, root.key):
            if root.left is None:
                return root
            if self._lt(key, root.left.key):
                root.left.left = self._splay(root.left.left, key)
                root = self._rotate_right(root)
            elif self._gt(key, root.left.key):
                root.left.right = self._splay(root.left.right, key)
                if root.left.right:
                    root.left = self._rotate_left(root.left)
            return self._rotate_right(root) if root.left else root
        else:
            if root.right is None:
                return root
            if self._gt(key, root.right.key):
                root.right.right = self._splay(root.right.right, key)
                root = self._rotate_left(root)
            elif self._lt(key, root.right.key):
                root.right.left = self._splay(root.right.left, key)
                if root.right.left:
                    root.right = self._rotate_right(root.right)
            return self._rotate_left(root) if root.right else root

    def get(self, key: Any) -> Any | None:
        """Get value by key. Moves accessed node to root (splay)."""
        self._root = self._splay(self._root, key)
        if self._root and self._root.key == key:
            self._hits += 1
            self._root.access_count += 1
            self._root.last_access = time.time()
            return self._root.value
        self._misses += 1
        return None

    def put(self, key: Any, value: Any) -> None:
        """Insert or update a key-value pair."""
        if self._root is None:
            self._root = _SplayNode(key=key, value=value, last_access=time.time())
            self._size = 1
            return

        self._root = self._splay(self._root, key)
        if self._root.key == key:
            self._root.value = value
            self._root.access_count += 1
            self._root.last_access = time.time()
            return

        node = _SplayNode(key=key, value=value, last_access=time.time())
        if self._lt(key, self._root.key):
            node.right = self._root
            node.left = self._root.left
            self._root.left = None
        else:
            node.left = self._root
            node.right = self._root.right
            self._root.right = None
        self._root = node
        self._size += 1

        if self._size > self._max_size:
            self._evict_coldest()

    def delete(self, key: Any) -> bool:
        """Remove a key. Returns True if found and removed."""
        if self._root is None:
            return False
        self._root = self._splay(self._root, key)
        if self._root.key != key:
            return False
        if self._root.left is None:
            self._root = self._root.right
        else:
            right = self._root.right
            self._root = self._splay(self._root.left, key)
            self._root.right = right
        self._size -= 1
        return True

    def _evict_coldest(self) -> None:
        """Remove the least recently accessed node."""
        if self._root is None:
            return
        coldest_key = None
        coldest_time = float("inf")
        stack = [self._root]
        while stack:
            node = stack.pop()
            if node is None:
                continue
            if node.last_access < coldest_time:
                coldest_time = node.last_access
                coldest_key = node.key
            stack.append(node.left)
            stack.append(node.right)
        if coldest_time < float("inf"):
            self._root = self._splay(self._root, coldest_key)
            if self._root and self._root.key == coldest_key:
                if self._root.left is None:
                    self._root = self._root.right
                else:
                    right = self._root.right
                    self._root = self._splay(self._root.left, coldest_key)
                    self._root.right = right
                self._size -= 1
                self._evictions += 1

    def contains(self, key: Any) -> bool:
        """Check if key exists without splaying."""
        node = self._root
        while node:
            if key == node.key:
                return True
            elif self._lt(key, node.key):
                node = node.left
            else:
                node = node.right
        return False

    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "size": self._size,
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0.0,
            "evictions": self._evictions,
        }

    def clear(self) -> None:
        self._root = None
        self._size = 0

    @property
    def size(self) -> int:
        return self._size
