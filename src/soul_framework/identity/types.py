"""Identity data types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OceanScores:
    """Big Five personality scores, each in [0.0, 1.0]."""
    O: float = 0.5  # Openness
    C: float = 0.5  # Conscientiousness
    E: float = 0.5  # Extraversion
    A: float = 0.5  # Agreeableness
    N: float = 0.5  # Neuroticism

    def to_dict(self) -> dict[str, float]:
        return {"O": self.O, "C": self.C, "E": self.E, "A": self.A, "N": self.N}

    @classmethod
    def from_dict(cls, d: dict[str, float]) -> OceanScores:
        return cls(
            O=d.get("O", 0.5), C=d.get("C", 0.5), E=d.get("E", 0.5),
            A=d.get("A", 0.5), N=d.get("N", 0.5),
        )


@dataclass
class Relationship:
    """A relationship with another entity."""
    person: str = ""
    trust_level: float = 0.5
    style: str = "default"
    dynamic: str = ""
