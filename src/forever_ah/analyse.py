"""Rebuild the `market` summary and `signals` for a house from what is in the database."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from itertools import groupby

from forever_ah.config import FACTIONS, Settings
from forever_ah.pricing import junk_ceiling, market_price
from forever_ah.professions import PROFESSIONS, VENDOR_ONLY, disenchant_yield
from forever_ah.signals import (
    build_history,
    craft_signal,
    disenchant_signal,
    evaluate,
    export_signal,
    house_fees,
)

log = logging.getLogger(__name__)
TIME_FMT = "%Y-%m-%d %H:%M:%S"
MAX_VENDOR_SUPPLY = 5000   # copper: the dearest vendor-only material trusted as always available


def _hours(ts: str) -> float:
    return datetime.strptime(ts, TIME_FMT).replace(tzinfo=UTC).timestamp() / 3600


def load_ladders(conn: sqlite3.Connection, house_id: int) -> dict[int, tuple[int, list[tuple[int, int]], list[int]]]:
    """variant_id -> (is_commodity, ascending [(unit price, quantity)], listings at each price)."""
    rows = conn.execute(
        "SELECT variant_id, MAX(is_commodity), unit_price, SUM(quantity), COUNT(*) FROM listings "
        "WHERE house_id = ? GROUP BY variant_id, unit_price ORDER BY variant_id, unit_price", (house_id,))
    out = {}
    for variant_id, group in groupby(rows, key=lambda r: r[0]):
        group = list(group)
        out[variant_id] = (group[0][1], [(r[2], r[3]) for r in group], [r[4] for r in group])
    return out


def without_junk(ladders: dict, history: dict, tun) -> dict:
    """The same ladders with junk-priced listings removed (see pricing.junk_ceiling)."""
    out = {}
    for variant_id, (is_commodity, levels, counts) in ladders.items():
        hist = history.get(variant_id)
        ceiling = junk_ceiling(levels, hist.ref_price if hist else None, tun.junk_multiple, tun.market_fraction)
        if ceiling is not None and levels[-1][0] > ceiling:
            keep = sum(1 for price, _ in levels if price <= ceiling)
            levels, counts = levels[:keep], counts[:keep]
        out[variant_id] = (is_commodity, levels, counts)
    return out


def load_history(conn: sqlite3.Connection, house_id: int, settings: Settings):
    """(variant_id -> History, hours observed, first snapshot id) for the lookback window, or None."""
    since = (datetime.now(UTC) - timedelta(days=settings.tuning.lookback_days)).strftime(TIME_FMT)
    snaps = conn.execute(
        "SELECT snapshot_id, published_at FROM snapshots WHERE house_id = ? AND published_at >= ? "
        "ORDER BY published_at", (house_id, since)).fetchall()
    if not snaps:
        return None
    snap_hours = {s[0]: _hours(s[1]) for s in snaps}
    span_hours = _hours(snaps[-1][1]) - _hours(snaps[0][1])

    # Sale and new-listing prices have only been recorded since the first tallied diff.
    first_levels = conn.execute(
        "SELECT MIN(snapshot_id) FROM rate_counts WHERE house_id = ? AND snapshot_id >= ?",
        (house_id, snaps[0][0])).fetchone()[0]
    level_days = None
    if first_levels in snap_hours:   # that diff covered the hour before its snapshot
        level_days = (_hours(snaps[-1][1]) - snap_hours[first_levels] + 1) / 24

    history = {}
    rows = conn.execute(
        "SELECT variant_id, snapshot_id, market_price, sold_qty, sold_levels, new_levels FROM item_stats "
        "WHERE house_id = ? AND snapshot_id >= ? ORDER BY variant_id, snapshot_id", (house_id, snaps[0][0]))
    for variant_id, group in groupby(rows, key=lambda r: r[0]):
        points, sold, new = [], {}, {}
        for r in group:
            if r[1] not in snap_hours:
                continue
            points.append((snap_hours[r[1]], r[2], r[3]))
            for column, into in ((r[4], sold), (r[5], new)):
                if column:
                    for price, qty in json.loads(column):
                        into[price] = into.get(price, 0) + qty
        history[variant_id] = build_history(points, span_hours, sold_levels=sold, new_levels=new,
                                            level_days=level_days,
                                            junk_multiple=settings.tuning.junk_multiple)
    return history, span_hours, snaps[0][0]


def _vendor_prices(conn: sqlite3.Connection) -> dict[int, tuple[int, str | None]]:
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT v.variant_id, i.vendor_sell, i.source FROM variants v JOIN items i ON i.item_id = v.item_id "
        "WHERE i.vendor_sell > 0")}


def analyse_house(conn: sqlite3.Connection, house_id: int, settings: Settings) -> int:
    tun = settings.tuning
    house = conn.execute("SELECT * FROM houses WHERE house_id = ?", (house_id,)).fetchone()
    fees = house_fees(settings.fees, house["faction"])

    loaded = load_history(conn, house_id, settings)
    if loaded is None:
        return 0
    history, span_hours, _ = loaded

    vendor = _vendor_prices(conn)
    ladders = without_junk(load_ladders(conn, house_id), history, tun)

    # What each item is (for disenchanting) and the plain, unmodified variant of each item (for materials).
    item_of, base_variant, item_name = {}, {}, {}
    for variant_id, item_id, bonus, quality, item_class, level, name in conn.execute(
            "SELECT v.variant_id, v.item_id, v.bonus, i.quality, i.item_class, i.level, i.name FROM variants v "
            "LEFT JOIN items i ON i.item_id = v.item_id"):
        item_of[variant_id] = (item_id, quality, item_class, level)
        item_name[item_id] = name
        if not bonus:
            base_variant[item_id] = variant_id
    facts = {r[0]: r[1:] for r in conn.execute(
        "SELECT item_id, name, sources, buy_price, skill, makes, reagents FROM item_facts WHERE ok = 1")}

    def unit_price(item_id: int) -> tuple[int | None, bool]:
        """(what one unit costs to obtain, bought from a vendor). Vendor-only goods cost their
        vendor price; everything else is priced at this house's market price."""
        f = facts.get(item_id)
        # Only everyday supplies (thread, flux, vials): a dear "vendor-only" item is more
        # likely a limited or beta-only vendor than a dependable source.
        if f and f[2] and f[2] <= MAX_VENDOR_SUPPLY and f[1] and json.loads(f[1]) == VENDOR_ONLY:
            return f[2], True
        listed = ladders.get(base_variant.get(item_id))
        return (market_price(listed[1], tun.market_fraction), False) if listed else (None, False)

    def sale_value(item_id: int) -> int | None:
        """What a material fetches here: the lower of its market price and its typical price."""
        variant = base_variant.get(item_id)
        listed = ladders.get(variant)
        if not listed:
            return None
        now = market_price(listed[1], tun.market_fraction)
        typical = history[variant].ref_price if variant in history else None
        return min(now, typical) if typical else now

    def material_name(item_id: int) -> str:
        return (facts.get(item_id) or (None,))[0] or item_name.get(item_id) or f"Item #{item_id}"

    # Crafting and disenchanting are judged in the faction houses only: Blackwater's
    # asking prices are too wishful to value materials by.
    makes_things = house["faction"] != "blackwater"

    def craft(variant_id: int, levels: list, hist, is_commodity: bool):
        item_id = item_of.get(variant_id, (None,))[0]
        f = facts.get(item_id)
        if not f or not f[5] or base_variant.get(item_id) != variant_id:
            return None
        materials = []
        for reagent_id, count, name in json.loads(f[5]):
            price, from_vendor = unit_price(reagent_id)
            if price is None:
                return None   # a material is neither sold by a vendor nor listed here
            materials.append((name, count, price, from_vendor))
        return craft_signal(levels, hist, materials, f[4], PROFESSIONS.get(f[3]),
                            vendor.get(variant_id, (None, None))[0], is_commodity, fees, tun)

    def keep(variant_id: int, sig) -> None:
        signal_rows.append((
            house_id, variant_id, sig.strategy, sig.buy_qty, sig.buy_max_price, sig.buy_cost,
            sig.sell_price, sig.profit, sig.roi, sig.days_to_sell, sig.risk_score, sig.risk_level,
            json.dumps(sig.reasons), sig.list_hours,
        ))

    market_rows, signal_rows = [], []
    empty = build_history([], span_hours)
    for variant_id, (is_commodity, levels, counts) in ladders.items():
        n_listings = sum(counts)
        hist = history.get(variant_id, empty)
        market_rows.append((
            house_id, variant_id, is_commodity, levels[0][0], market_price(levels, tun.market_fraction),
            sum(q for _, q in levels), n_listings, hist.ref_price, hist.trend_per_day, hist.cv,
            hist.sold_per_day, hist.total_sold, hist.n_snaps,
        ))
        vendor_price, source = vendor.get(variant_id, (None, None))
        verified = bool(source and "forever" in source)
        for sig in evaluate(levels, hist, vendor_price, verified, bool(is_commodity), fees, tun):
            keep(variant_id, sig)
        if not makes_things:
            continue
        sig = craft(variant_id, levels, hist, bool(is_commodity))
        if sig:
            keep(variant_id, sig)
        _, quality, item_class, level = item_of.get(variant_id, (None, None, None, None))
        breaks_into = disenchant_yield(quality, item_class, level)
        if breaks_into:
            sig = disenchant_signal(
                levels, [(material_name(m), units, sale_value(m)) for m, units in breaks_into], fees, tun)
            if sig:
                keep(variant_id, sig)

    # Craftable items with a sales history here but nothing listed right now: an open goal.
    for item_id in facts if makes_things else ():
        variant_id = base_variant.get(item_id)
        if variant_id in history and variant_id not in ladders:
            sig = craft(variant_id, [], history[variant_id], False)
            if sig:
                keep(variant_id, sig)

    conn.execute("DELETE FROM market WHERE house_id = ?", (house_id,))
    conn.execute("DELETE FROM signals WHERE house_id = ?", (house_id,))
    conn.executemany("INSERT INTO market VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", market_rows)
    conn.executemany("INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", signal_rows)
    conn.commit()
    log.info("House %s: %d items priced, %d signals", house_id, len(market_rows), len(signal_rows))
    return len(signal_rows)


def analyse_exports(conn: sqlite3.Connection, settings: Settings) -> int:
    """Rebuild EXPORT signals: buy in a pinned faction house, list on the pinned Blackwater house.

    Run after analyse_house, which rebuilds each house's `market` rows and
    clears its signals.
    """
    conn.execute("DELETE FROM signals WHERE strategy = 'EXPORT'")
    pinned = {f: settings.houses.get(f, 0) for f in FACTIONS}
    dest = pinned["blackwater"]
    loaded = load_history(conn, dest, settings) if dest > 0 else None
    if loaded is None:
        conn.commit()
        return 0
    dest_hist, _, first_snap = loaded
    dest_ladders = without_junk(load_ladders(conn, dest), dest_hist, settings.tuning)
    dest_sold_price = {r[0]: r[1] for r in conn.execute(
        "SELECT variant_id, SUM(sold_value) * 1.0 / SUM(sold_qty) FROM item_stats "
        "WHERE house_id = ? AND snapshot_id >= ? GROUP BY variant_id HAVING SUM(sold_qty) > 0",
        (dest, first_snap))}
    fees = house_fees(settings.fees, "blackwater")
    vendor = _vendor_prices(conn)

    rows = []
    for source, buyer in (("alliance", "horde"), ("horde", "alliance")):
        if pinned[source] <= 0:
            continue
        buyer_tracked = pinned[buyer] > 0
        buyer_prices = {r[0]: min(r[1], r[2] or r[1]) for r in conn.execute(
            "SELECT variant_id, min_price, ref_price FROM market WHERE house_id = ?", (pinned[buyer],))}
        for variant_id, (is_commodity, levels, _) in load_ladders(conn, pinned[source]).items():
            hist = dest_hist.get(variant_id)
            if hist is None:
                continue
            sig = export_signal(
                levels, dest_ladders.get(variant_id, (0, [], []))[1], hist, dest_sold_price.get(variant_id),
                buyer_prices.get(variant_id), buyer if buyer_tracked else None,
                vendor.get(variant_id, (None, None))[0], bool(is_commodity), fees, settings.tuning)
            if sig:
                rows.append((
                    pinned[source], variant_id, sig.strategy, sig.buy_qty, sig.buy_max_price, sig.buy_cost,
                    sig.sell_price, sig.profit, sig.roi, sig.days_to_sell, sig.risk_score, sig.risk_level,
                    json.dumps(sig.reasons), sig.list_hours,
                ))
    conn.executemany("INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    log.info("Exports via Blackwater: %d signals", len(rows))
    return len(rows)
