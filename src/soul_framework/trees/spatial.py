"""RSpatialIndex — Simplified R-Tree for spatial indexing.

Supports: insert, range search, point query, nearest neighbor.
Use cases: Mining (INGEMMET concessions), Medical (anatomical localization).

Original: ADA (Team SEAL), Proposal: JARVIS
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class BoundingBox:
    """Minimum Bounding Rectangle (MBR) for spatial objects."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def area(self) -> float:
        return max(0, self.max_x - self.min_x) * max(0, self.max_y - self.min_y)

    def contains(self, other: BoundingBox) -> bool:
        return (
            self.min_x <= other.min_x
            and self.min_y <= other.min_y
            and self.max_x >= other.max_x
            and self.max_y >= other.max_y
        )

    def intersects(self, other: BoundingBox) -> bool:
        return not (
            self.max_x < other.min_x
            or other.max_x < self.min_x
            or self.max_y < other.min_y
            or other.max_y < self.min_y
        )

    def expand_to_include(self, other: BoundingBox) -> BoundingBox:
        return BoundingBox(
            min(self.min_x, other.min_x),
            min(self.min_y, other.min_y),
            max(self.max_x, other.max_x),
            max(self.max_y, other.max_y),
        )

    def expansion_area(self, other: BoundingBox) -> float:
        """How much area increases if we include other."""
        expanded = self.expand_to_include(other)
        return expanded.area() - self.area()

    def contains_point(self, x: float, y: float) -> bool:
        return self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y

    @classmethod
    def from_point(cls, x: float, y: float) -> BoundingBox:
        return cls(x, y, x, y)


@dataclass
class SpatialEntry:
    """An entry in the R-Tree: a bounding box + associated data."""

    bbox: BoundingBox
    data: Any = None


@dataclass
class _RNode:
    entries: list = field(default_factory=list)
    children: list[_RNode] = field(default_factory=list)
    is_leaf: bool = True
    bbox: Optional[BoundingBox] = None

    @property
    def is_full(self) -> bool:
        if self.is_leaf:
            return len(self.entries) >= 4
        return len(self.children) >= 4


class RSpatialIndex:
    """Simplified R-Tree for spatial indexing.

    Usage:
        rtree = RSpatialIndex()
        rtree.insert(BoundingBox(-6.77, -79.84, -6.75, -79.82), {"name": "Concesión A"})
        results = rtree.search(BoundingBox(-7.0, -80.0, -6.5, -79.5))
    """

    def __init__(self, max_entries: int = 4) -> None:
        self._root = _RNode()
        self._size = 0
        self._max_entries = max_entries

    def insert(self, bbox: BoundingBox, data: Any = None) -> None:
        """Insert a spatial entry."""
        entry = SpatialEntry(bbox=bbox, data=data)
        split_result = self._insert_recursive(self._root, entry)
        if split_result is not None:
            old_root = self._root
            self._root = _RNode(is_leaf=False, children=[old_root, split_result])
            self._update_bbox(self._root)
        self._size += 1

    def search(self, query_bbox: BoundingBox) -> list[SpatialEntry]:
        """Range query: find all entries intersecting the query box."""
        results: list[SpatialEntry] = []
        self._search_recursive(self._root, query_bbox, results)
        return results

    def search_point(self, x: float, y: float) -> list[SpatialEntry]:
        """Find all entries containing the given point."""
        point_bbox = BoundingBox.from_point(x, y)
        return self.search(point_bbox)

    def nearest(self, x: float, y: float, k: int = 1) -> list[tuple[float, SpatialEntry]]:
        """Find k nearest entries to a point. Returns list of (distance, entry)."""
        all_entries: list[SpatialEntry] = []
        self._collect_all(self._root, all_entries)

        def dist_to_entry(entry: SpatialEntry) -> float:
            cx = (entry.bbox.min_x + entry.bbox.max_x) / 2
            cy = (entry.bbox.min_y + entry.bbox.max_y) / 2
            return ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5

        scored = [(dist_to_entry(e), e) for e in all_entries]
        scored.sort(key=lambda t: t[0])
        return scored[:k]

    def _insert_recursive(self, node: _RNode, entry: SpatialEntry) -> _RNode | None:
        """Insert entry into subtree. Returns new sibling if split occurred."""
        if node.is_leaf:
            node.entries.append(entry)
            self._update_bbox(node)
            if len(node.entries) > self._max_entries:
                return self._split_node(node)
            return None

        best_child = node.children[0]
        best_expansion = float("inf")
        for child in node.children:
            if child.bbox:
                expansion = child.bbox.expansion_area(entry.bbox)
                if expansion < best_expansion:
                    best_expansion = expansion
                    best_child = child

        split_result = self._insert_recursive(best_child, entry)
        self._update_bbox(node)

        if split_result is not None:
            node.children.append(split_result)
            self._update_bbox(node)
            if len(node.children) > self._max_entries:
                return self._split_node(node)
        return None

    def _update_bbox(self, node: _RNode) -> None:
        boxes: list[BoundingBox] = []
        for entry in node.entries:
            boxes.append(entry.bbox)
        for child in node.children:
            if child.bbox:
                boxes.append(child.bbox)
        if boxes:
            node.bbox = boxes[0]
            for b in boxes[1:]:
                node.bbox = node.bbox.expand_to_include(b)

    def _split_node(self, node: _RNode) -> _RNode:
        """Split a node and return the new sibling."""
        if node.is_leaf:
            entries = node.entries
            entries.sort(key=lambda e: e.bbox.min_x)
            mid = len(entries) // 2
            node.entries = entries[:mid]
            new_node = _RNode(entries=entries[mid:])
        else:
            children = node.children
            children.sort(key=lambda c: c.bbox.min_x if c.bbox else 0)
            mid = len(children) // 2
            node.children = children[:mid]
            new_node = _RNode(is_leaf=False, children=children[mid:])
        self._update_bbox(node)
        self._update_bbox(new_node)
        return new_node

    def _search_recursive(self, node: _RNode, query: BoundingBox, results: list) -> None:
        if node.bbox and not node.bbox.intersects(query):
            return
        for entry in node.entries:
            if entry.bbox.intersects(query):
                results.append(entry)
        for child in node.children:
            self._search_recursive(child, query, results)

    def _collect_all(self, node: _RNode, results: list) -> None:
        results.extend(node.entries)
        for child in node.children:
            self._collect_all(child, results)

    @property
    def size(self) -> int:
        return self._size

    def clear(self) -> None:
        self._root = _RNode()
        self._size = 0
