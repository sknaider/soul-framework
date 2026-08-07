"""OCEAN personality scoring and narrative generation.

Exact copy of ocean_to_narrative from proyecto-seal/memory/soul/core/scoring.py.
"""

from __future__ import annotations


def ocean_to_narrative(agent: str, ocean: dict) -> str:
    """Convert OCEAN scores to a first-person behavioral narrative sentence.

    Args:
        agent: Agent name (unused, kept for API compatibility)
        ocean: Dict with keys O, C, E, A, N — each float in [0.0, 1.0]

    Returns:
        Spanish first-person sentence describing personality.
    """
    o = ocean.get("O", 0.5)
    c = ocean.get("C", 0.5)
    e = ocean.get("E", 0.5)
    a = ocean.get("A", 0.5)
    n = ocean.get("N", 0.5)

    traits: list[str] = []

    if c >= 0.8:   traits.append("extremadamente meticuloso y organizado")
    elif c >= 0.6: traits.append("organizado y confiable")
    else:          traits.append("flexible con la estructura")

    if o >= 0.7:   traits.append("abierto a nuevas ideas")
    elif o >= 0.5: traits.append("moderadamente curioso")
    else:          traits.append("pragmatico y convencional")

    if n <= 0.15:  traits.append("muy estable emocionalmente")
    elif n <= 0.3: traits.append("emocionalmente estable bajo presion")
    else:          traits.append("sensible emocionalmente")

    if a >= 0.7:   traits.append("cooperativo con el equipo")
    elif a >= 0.5: traits.append("equilibrado entre autonomia y colaboracion")
    else:          traits.append("independiente en sus juicios")

    if e >= 0.7:   traits.append("energizado por la interaccion directa")
    elif e >= 0.4: traits.append("selectivo en sus interacciones")
    else:          traits.append("introvertido — prefiere el pensamiento profundo")

    return "Soy " + ", ".join(traits[:-1]) + f", y {traits[-1]}."
