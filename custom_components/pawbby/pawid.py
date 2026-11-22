"""PawID — attribute a litter-box visit to a cat by weight.

Ported from the PawID block in Pawbby-Reborn's ``tuya-listener.ts``: the visit
is attributed to the cat whose profile weight is closest to the measured
weight, within a 300 g threshold. Quick peeks (no DP 107 confirmation) are
deliberately NOT attributed — half a heavy cat leaning in looks identical to
a small cat fully inside.
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import PAWID_MATCH_THRESHOLD_KG


@dataclass(frozen=True)
class CatProfile:
    id: str
    name: str
    weight_kg: float


def match_cat(
    weight_kg: float,
    cats: list[CatProfile],
    threshold_kg: float = PAWID_MATCH_THRESHOLD_KG,
) -> CatProfile | None:
    """Return the closest cat within ``threshold_kg`` of ``weight_kg``, else None."""
    if weight_kg <= 0:
        return None
    best: CatProfile | None = None
    best_diff = threshold_kg
    for cat in cats:
        diff = abs(cat.weight_kg - weight_kg)
        if diff <= best_diff:
            best_diff = diff
            best = cat
    return best
