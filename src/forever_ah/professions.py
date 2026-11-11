"""Reference data for the crafting and disenchanting plays, and for "how do I get this item".

Recipes and item sources are fetched per item from Wowhead's Forever database
(see blizzard.parse_wowhead_facts). The disenchant table below is Classic's:
Forever uses the same enchanting materials, but Blizzard has published no
table for it, so treat disenchant values as an estimate until checked in game.
"""

from __future__ import annotations

# Wowhead skill-line ids on a recipe -> profession name.
PROFESSIONS = {
    129: "First Aid", 164: "Blacksmithing", 165: "Leatherworking", 171: "Alchemy", 185: "Cooking",
    186: "Mining", 197: "Tailoring", 202: "Engineering", 333: "Enchanting", 356: "Fishing",
    755: "Jewelcrafting", 773: "Inscription",
}

# Wowhead "source" codes -> how a player gets the item. Order = which label to show first.
SOURCES = {
    17: "Herbalism", 19: "Mining", 23: "Skinning", 16: "Fishing", 15: "Disenchanting", 20: "Prospecting",
    18: "Milling", 21: "Pickpocketing", 2: "Drops", 1: "Crafted", 4: "Quest", 5: "Vendor",
}
FARMABLE = {17, 19, 23, 16, 21, 2}     # you can go out and get these without buying anything
VENDOR_ONLY = [5]                      # sold by NPCs and obtainable no other way: unlimited at a fixed price


def how_obtained(sources: list[int] | None) -> str | None:
    """Readable label such as "Mining, Drops" (at most two ways), or None when unknown."""
    if not sources:
        return None
    names = [name for code, name in SOURCES.items() if code in sources]
    return ", ".join(names[:2]) or None


def is_farmable(sources: list[int] | None) -> bool:
    return bool(sources) and bool(FARMABLE.intersection(sources))


# Enchanting materials (item ids are the original game's and are on Forever's auction house).
STRANGE, SOUL, VISION, DREAM, ILLUSION = 10940, 11083, 11137, 11176, 16204
L_MAGIC, G_MAGIC, L_ASTRAL, G_ASTRAL = 10938, 10939, 10998, 11082
L_MYSTIC, G_MYSTIC, L_NETHER, G_NETHER = 11134, 11135, 11174, 11175
L_ETERNAL, G_ETERNAL = 16202, 16203
S_GLIMMERING, L_GLIMMERING, S_GLOWING, L_GLOWING = 10978, 11084, 11138, 11139
S_RADIANT, L_RADIANT, S_BRILLIANT, L_BRILLIANT = 11177, 11178, 14343, 14344

# Uncommon (green) gear by item level: (highest item level, dust, dust count, essence, essence count, shard).
# Armour gives dust 75% / essence 20% / shard 5% of the time; weapons swap dust and essence.
_GREEN = [
    (15, STRANGE, 1.5, L_MAGIC, 1.5, None),
    (20, STRANGE, 2.5, G_MAGIC, 1.5, S_GLIMMERING),
    (25, STRANGE, 5.0, L_ASTRAL, 1.5, S_GLIMMERING),
    (30, SOUL, 1.5, G_ASTRAL, 1.5, L_GLIMMERING),
    (35, SOUL, 3.5, L_MYSTIC, 1.5, S_GLOWING),
    (40, VISION, 1.5, G_MYSTIC, 1.5, L_GLOWING),
    (45, VISION, 3.5, L_NETHER, 1.5, S_RADIANT),
    (50, DREAM, 1.5, G_NETHER, 1.5, L_RADIANT),
    (55, DREAM, 3.5, L_ETERNAL, 1.5, S_BRILLIANT),
    (60, ILLUSION, 1.5, G_ETERNAL, 1.5, L_BRILLIANT),
    (65, ILLUSION, 3.5, G_ETERNAL, 2.5, L_BRILLIANT),
]
# Rare (blue) gear gives one shard.
_BLUE = [(25, S_GLIMMERING), (30, L_GLIMMERING), (35, S_GLOWING), (40, L_GLOWING), (45, S_RADIANT),
         (50, L_RADIANT), (55, S_BRILLIANT), (65, L_BRILLIANT)]


def disenchant_yield(quality: str | None, item_class: str | None, item_level: int | None) -> list[tuple[int, float]]:
    """Expected materials from disenchanting one item: [(material item id, average units)].

    Empty when the item cannot be disenchanted or falls outside the table.
    """
    if item_class not in ("Armor", "Weapon") or not item_level:
        return []
    if quality == "Rare":
        if item_level < 11:
            return []
        return next(([(shard, 1.0)] for top, shard in _BLUE if item_level <= top), [])
    if quality != "Uncommon" or item_level < 5:
        return []
    for top, dust, dust_n, essence, essence_n, shard in _GREEN:
        if item_level <= top:
            dust_p, essence_p = (0.75, 0.20) if item_class == "Armor" else (0.20, 0.75)
            if shard is None:   # the lowest bracket has no shard: dust takes its 5%
                dust_p, essence_p = (0.80, 0.20) if item_class == "Armor" else (0.20, 0.80)
            out = [(dust, dust_p * dust_n), (essence, essence_p * essence_n)]
            if shard is not None:
                out.append((shard, 0.05))
            return out
    return []
