"""Price maths. Pure functions, all money in copper."""

from __future__ import annotations

import math
from collections.abc import Iterable

Level = tuple[int, int]  # (unit price, quantity available at that price)


def price_levels(pairs: Iterable[tuple[int, int]]) -> list[Level]:
    """Collapse (unit_price, quantity) listings into an ascending order book."""
    book: dict[int, int] = {}
    for price, qty in pairs:
        book[price] = book.get(price, 0) + qty
    return sorted(book.items())


def market_price(levels: list[Level], fraction: float = 0.15) -> int | None:
    """Quantity-weighted mean price of the cheapest `fraction` of units.

    Sturdier than the single cheapest listing: one lowball stack of 1 in a
    market of 10,000 barely moves it, yet it still tracks what buyers pay.
    """
    total = sum(q for _, q in levels)
    if total <= 0:
        return None
    want = max(1, math.ceil(total * fraction))
    taken = value = 0
    for price, qty in levels:
        take = min(qty, want - taken)
        value += take * price
        taken += take
        if taken >= want:
            break
    return round(value / taken)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def format_money(copper: float | None) -> str:
    if copper is None:
        return "-"
    copper = int(round(copper))
    sign = "-" if copper < 0 else ""
    copper = abs(copper)
    g, rem = divmod(copper, 10000)
    s, c = divmod(rem, 100)
    parts = []
    if g:
        parts.append(f"{g:,}g")
    if s or (g and c):
        parts.append(f"{s}s")
    if c or not parts:
        parts.append(f"{c}c")
    return sign + " ".join(parts)
