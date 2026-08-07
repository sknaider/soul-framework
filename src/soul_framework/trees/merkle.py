"""MerkleSoul — Integrity verification tree for SOUL state.

Each leaf = hash of a soul category (OCEAN, memories, beliefs, rules, etc.).
If anything is modified without going through legitimate channels, the root hash changes.

Original: ADA (Team SEAL), Proposal: JARVIS
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional


class MerkleSoul:
    """Merkle Tree for SOUL integrity verification.

    Usage:
        tree = MerkleSoul()
        tree.update_leaf("ocean", {"A": 0.505, "C": 1.0})
        tree.update_leaf("rules", [...])
        tree.sign_checkpoint()
        tree.verify_integrity()  # True if nothing changed illegitimately
    """

    def __init__(self) -> None:
        self._leaves: dict[str, str] = {}
        self._signed_root: str | None = None
        self._signed_at: float | None = None
        self._checkpoint_history: list[dict[str, Any]] = []

    @staticmethod
    def _hash(data: str) -> str:
        return hashlib.sha256(data.encode()).hexdigest()

    def update_leaf(self, category: str, data: Any) -> str:
        """Update a leaf node with new data. Returns the leaf hash."""
        serialized = json.dumps(data, sort_keys=True, default=str)
        leaf_hash = self._hash(serialized)
        self._leaves[category] = leaf_hash
        return leaf_hash

    def remove_leaf(self, category: str) -> bool:
        """Remove a leaf node. Returns True if it existed."""
        if category in self._leaves:
            del self._leaves[category]
            return True
        return False

    @property
    def root_hash(self) -> str | None:
        """Compute the Merkle root from all leaves."""
        if not self._leaves:
            return None
        sorted_keys = sorted(self._leaves.keys())
        hashes = [self._leaves[k] for k in sorted_keys]
        while len(hashes) > 1:
            next_level = []
            for i in range(0, len(hashes), 2):
                left = hashes[i]
                right = hashes[i + 1] if i + 1 < len(hashes) else left
                next_level.append(self._hash(left + right))
            hashes = next_level
        return hashes[0]

    def sign_checkpoint(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Sign the current state as legitimate."""
        root = self.root_hash
        checkpoint = {
            "root_hash": root,
            "signed_at": time.time(),
            "leaf_count": len(self._leaves),
            "categories": sorted(self._leaves.keys()),
            "metadata": metadata or {},
        }
        self._signed_root = root
        self._signed_at = checkpoint["signed_at"]
        self._checkpoint_history.append(checkpoint)
        return checkpoint

    def verify_integrity(self) -> dict[str, Any]:
        """Check if current state matches last signed checkpoint."""
        current_root = self.root_hash
        is_valid = current_root == self._signed_root
        return {
            "valid": is_valid,
            "current_root": current_root,
            "signed_root": self._signed_root,
            "signed_at": self._signed_at,
            "drift_detected": not is_valid and self._signed_root is not None,
        }

    def get_proof(self, category: str) -> list[tuple[str, str]] | None:
        """Generate Merkle proof for a specific category."""
        if category not in self._leaves:
            return None
        sorted_keys = sorted(self._leaves.keys())
        hashes = [self._leaves[k] for k in sorted_keys]
        index = sorted_keys.index(category)
        proof = []
        while len(hashes) > 1:
            next_level = []
            for i in range(0, len(hashes), 2):
                left = hashes[i]
                right = hashes[i + 1] if i + 1 < len(hashes) else left
                next_level.append(self._hash(left + right))
            sibling_idx = index ^ 1
            if sibling_idx < len(hashes):
                direction = "right" if index % 2 == 0 else "left"
                proof.append((hashes[sibling_idx], direction))
            index //= 2
            hashes = next_level
        return proof

    def verify_proof(self, category: str, data: Any, proof: list[tuple[str, str]]) -> bool:
        """Verify a Merkle proof for given data."""
        serialized = json.dumps(data, sort_keys=True, default=str)
        current = self._hash(serialized)
        for sibling_hash, direction in proof:
            if direction == "right":
                current = self._hash(current + sibling_hash)
            else:
                current = self._hash(sibling_hash + current)
        return current == self.root_hash

    @property
    def checkpoint_count(self) -> int:
        return len(self._checkpoint_history)

    @property
    def last_checkpoint(self) -> dict[str, Any] | None:
        return self._checkpoint_history[-1] if self._checkpoint_history else None
