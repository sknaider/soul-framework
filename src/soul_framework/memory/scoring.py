"""SEAL SOUL — Scoring helpers.

Adapted from Team SEAL internals for the public SOUL Framework API.
Used by: MemoryStore.search() for ranking results.

Includes:
- temporal_decay_score(): HALO half-life + MemRL utility + Hindsight confidence
- ocean_to_narrative(): OCEAN scores -> first-person behavioral narrative
"""
from __future__ import annotations

import math

# -- HALO half-life decay constants (arxiv 2505.07509) --
# Different memory types decay at different rates based on their nature
HALF_LIFE_BY_CATEGORY: dict[str, float] = {
    "emotion": 1.0,         # 1 day — emotional states are transient
    "humor": 3.0,           # 3 days
    "dynamic": 7.0,         # 1 week
    "pattern": 14.0,        # 2 weeks
    "insight": 30.0,        # 1 month
    "fact": 60.0,           # 2 months
    "preference": 90.0,     # 3 months
    "decision": 120.0,      # 4 months — decisions are load-bearing
    "trust": 180.0,         # 6 months — trust changes slowly
    "correction": 365.0,    # 1 year — lessons learned persist
    "milestone": 730.0,     # 2 years — team history is nearly permanent
}
HALF_LIFE_DEFAULT: float = 30.0  # 1 month default


def temporal_decay_score(
    similarity: float,
    days_old: float,
    importance: int,
    valence: float = 0.0,
    arousal: float = 0.0,
    category: str = "",
    utility: float = 0.5,
    confidence: float = 1.0,
) -> float:
    """HALO half-life decay + MemRL utility + Hindsight confidence.

    References: arxiv 2505.07509 (HALO) + 2601.03192 (MemRL) + 2512.12818 (Hindsight)

    Formula:
        score = (semantic * 0.5 + utility * 0.3 + confidence * 0.2)
                * half_life_decay * importance_weight

    Key behaviors:
    - importance >= 10: immortal (half-life = infinity)
    - importance >= 8: 2x category half-life (floor 180 days)
    - emotional memories decay slower (amygdala-hippocampus interaction)
    """
    # Determine half-life for this category + importance
    if importance >= 10:
        half_life = 99999.0  # immortal
    elif importance >= 8:
        half_life = max(HALF_LIFE_BY_CATEGORY.get(category, HALF_LIFE_DEFAULT) * 2.0, 180.0)
    else:
        half_life = HALF_LIFE_BY_CATEGORY.get(category, HALF_LIFE_DEFAULT)

    # Emotional modulation: high intensity doubles half-life
    emotional_intensity = max(abs(valence), arousal)
    if emotional_intensity > 0.5:
        emotion_factor = 1.0 + min(emotional_intensity, 1.0)  # 1.5x to 2.0x half-life
        half_life *= emotion_factor

    # HALO decay: relevance = 0.5 ^ (days_elapsed / half_life)
    decay = math.pow(0.5, days_old / half_life) if half_life > 0 else 0.0

    # MemRL + Hindsight blended score
    conf = max(0.0, min(1.0, confidence))
    blended = similarity * 0.5 + utility * 0.3 + conf * 0.2

    imp_weight = 0.5 + (importance / 20.0)  # 0.55 at imp=1, 1.0 at imp=10
    return blended * decay * imp_weight
