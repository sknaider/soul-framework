"""Tree data structures for soul-framework.

- MerkleSoul: Integrity verification via Merkle tree
- SplayCache: Adaptive L1 cache (splay tree)
- TrieIndex: O(L) prefix search for commands/tools
- FenwickStats: O(log n) range queries (Binary Indexed Tree)
- RSpatialIndex: Spatial indexing (simplified R-Tree)
"""

from soul_framework.trees.fenwick import FenwickStats
from soul_framework.trees.merkle import MerkleSoul
from soul_framework.trees.spatial import BoundingBox, RSpatialIndex, SpatialEntry
from soul_framework.trees.splay import SplayCache
from soul_framework.trees.trie import TrieIndex

__all__ = [
    "MerkleSoul",
    "SplayCache",
    "TrieIndex",
    "FenwickStats",
    "RSpatialIndex",
    "BoundingBox",
    "SpatialEntry",
]
