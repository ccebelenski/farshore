"""Frontier city-name generation (cosmetic; spec has no bearing on play).

Names are assigned once at map assembly, from a seeded RNG, so a given map seed
always yields the same names (reproducible games, stable saves). The flavor is
"FARSHORE — Terra Incognita": landfalls, capes, and coastal outposts. A large
standalone pool plus head-and-tail compounds gives far more unique names than any
map needs; a numbered fallback guarantees we never run dry.
"""

from __future__ import annotations

import random

# Evocative standalone names — used first, in shuffled order.
_STANDALONE: tuple[str, ...] = (
    "Landfall", "Saltmarsh", "Farewell", "Longreach", "Driftwood", "Wayrest",
    "Stormhold", "Greywater", "Blacksand", "Kingsport", "Highwater", "Deepwater",
    "Fairwind", "Weatherly", "Ironwood", "Tidewater", "Ravenroost", "Coldharbor",
    "Anchorage", "Redcliff", "Whitewater", "Thornbury", "Barrowick", "Mistport",
    "Duskwall", "Rimewatch", "Gullscry", "Windward", "Sablecross", "Marrowfen",
)

# Compound names: HEAD + " " + TAIL. Most read naturally (Cape Mercy, Fort
# Harlow, Cold Harbor, Storm Point); a few are quirkier, which suits frontier
# nomenclature.
_HEADS: tuple[str, ...] = (
    "Cape", "Fort", "Port", "New", "Mount", "Cross", "Cold", "Bleak", "Salt",
    "Grey", "Far", "Iron", "Storm", "Last", "North", "South", "East", "West",
    "Old", "Low",
)
_TAILS: tuple[str, ...] = (
    "Mercy", "Harlow", "Haven", "Harbor", "Hollow", "Reach", "Point", "Watch",
    "Landing", "Rock", "Bay", "Cove", "Ridge", "Hope", "Cross", "End", "Barrow",
    "Marsh", "Bristol", "Ferry", "Bend", "Crossing", "Anchor", "Hearth", "Gate",
    "Fell", "Moor", "Vale", "Wick", "Shoal",
)


def generate_city_names(count: int, rng: random.Random) -> list[str]:
    """`count` unique frontier place-names, drawn deterministically from `rng`.

    Standalone names first (shuffled), then shuffled head-and-tail compounds, then a
    numbered fallback — so the result is always exactly `count` distinct names,
    for any map size."""
    if count <= 0:
        return []
    names: list[str] = []
    seen: set[str] = set()

    def take(candidates: list[str]) -> bool:
        for name in candidates:
            if name not in seen:
                names.append(name)
                seen.add(name)
                if len(names) == count:
                    return True
        return False

    standalone = list(_STANDALONE)
    rng.shuffle(standalone)
    if take(standalone):
        return names

    compounds = [f"{h} {t}" for h in _HEADS for t in _TAILS]
    rng.shuffle(compounds)
    if take(compounds):
        return names

    i = 1
    while len(names) < count:  # pragma: no cover — beyond any real map's cities
        candidate = f"Outpost {i}"
        i += 1
        if candidate not in seen:
            names.append(candidate)
            seen.add(candidate)
    return names
