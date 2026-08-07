"""FenwickStats — Binary Indexed Tree for O(log n) range queries.

Supports: prefix sums, range sums, point updates, kth element.
Use cases: importance/utility statistics over memory ranges.

Original: ADA (Team SEAL), Proposal: JARVIS
"""

from __future__ import annotations


class FenwickStats:
    """Fenwick Tree (BIT) for O(log n) range queries over utility/importance.

    Usage:
        ft = FenwickStats(1000)
        ft.update(42, 8)   # Memory #42 has importance 8
        ft.update(100, 5)  # Memory #100 has importance 5
        total = ft.prefix_sum(100)    # Sum of importance 1..100
        rango = ft.range_sum(42, 100) # Sum of importance 42..100
    """

    def __init__(self, n: int) -> None:
        self._n = n
        self._tree = [0] * (n + 1)  # 1-indexed
        self._count = 0

    def update(self, i: int, delta: int) -> None:
        """Add delta to position i. O(log n)."""
        if i < 1 or i > self._n:
            raise IndexError(f"Index {i} out of range [1, {self._n}]")
        while i <= self._n:
            self._tree[i] += delta
            i += i & (-i)
        self._count += 1

    def prefix_sum(self, i: int) -> int:
        """Sum of elements [1..i]. O(log n)."""
        if i < 0:
            return 0
        i = min(i, self._n)
        total = 0
        while i > 0:
            total += self._tree[i]
            i -= i & (-i)
        return total

    def range_sum(self, left: int, right: int) -> int:
        """Sum of elements [left..right]. O(log n)."""
        if left > right:
            return 0
        return self.prefix_sum(right) - self.prefix_sum(left - 1)

    def point_query(self, i: int) -> int:
        """Get value at position i. O(log n)."""
        return self.range_sum(i, i)

    def find_kth(self, k: int) -> int:
        """Find smallest index with prefix sum >= k. O(log^2 n)."""
        lo, hi = 1, self._n
        while lo < hi:
            mid = (lo + hi) // 2
            if self.prefix_sum(mid) >= k:
                hi = mid
            else:
                lo = mid + 1
        return lo

    @classmethod
    def from_array(cls, arr: list[int]) -> FenwickStats:
        """Build from array in O(n)."""
        n = len(arr)
        ft = cls(n)
        for i in range(1, n + 1):
            ft._tree[i] = arr[i - 1]
        for i in range(1, n + 1):
            j = i + (i & (-i))
            if j <= n:
                ft._tree[j] += ft._tree[i]
        ft._count = n
        return ft

    @property
    def capacity(self) -> int:
        return self._n

    @property
    def total(self) -> int:
        return self.prefix_sum(self._n)
