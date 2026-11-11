"""Rebuild the `market` summary and `signals` for a house from what is in the database."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from itertools import groupby

from forever_ah.config import FACTIONS, Settings
from forever_ah.pricing import market_price
from forever_ah.signals import build_history, evaluate, export_signal, house_fees

log = logging.getLogger(__name__)
TIME_FMT = "%Y-%m-%d %H:%M:%S"


def _hours(ts: str) -> float:
    return datetime.strptime(ts, TIME_FMT).replace(tzinfo=UTC).timestamp() / 3600


def load_ladders(conn: sqlite3.Connection, house_id: int) -> dict[int, tuple[int, list[tuple[int, int]]]]:
    """variant_id -> (is_commodity, ascending [(unit price, quantity)])."""
    rows = conn.execute(
        "SELECT variant_id, MAX(is_commodity), unit_price, SUM(quantity), COUNT(*) FROM listings "
        "WHERE house_id = ? GROUP BY variant_id, unit_price ORDER BY variant_id, unit_price", (house_id,))
    out = {}
    for variant_id, group in groupby(rows, key=lambda r: r[0]):
        group = list(group)
        out[variant_id] = (group[0][1], [(r[2], r[3]) for r in group], sum(r[4] for r in group))
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

    history = {}
    rows = conn.execute(
        "SELECT variant_id, snapshot_id, market_price, sold_qty FROM item_stats "
        "WHERE house_id = ? AND snapshot_id >= ? ORDER BY variant_id, snapshot_id", (house_id, snaps[0][0]))
    for variant_id, group in groupby(rows, key=lambda r: r[0]):
        points = [(snap_hours[r[1]], r[2], r[3]) for r in group if r[1] in snap_hours]
        history[variant_id] = build_history(points, span_hours)
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

    market_rows, signal_rows = [], []
    empty = build_history([], span_hours)
    for variant_id, (is_commodity, levels, n_listings) in load_ladders(conn, house_id).items():
        hist = history.get(variant_id, empty)
        market_rows.append((
            house_id, variant_id, is_commodity, levels[0][0], market_price(levels, tun.market_fraction),
            sum(q for _, q in levels), n_listings, hist.ref_price, hist.trend_per_day, hist.cv,
            hist.sold_per_day, hist.total_sold, hist.n_snaps,
        ))
        vendor_price, source = vendor.get(variant_id, (None, None))
        verified = bool(source and "forever" in source)
        for sig in evaluate(levels, hist, vendor_price, verified, bool(is_commodity), fees, tun):
            signal_rows.append((
                house_id, variant_id, sig.strategy, sig.buy_qty, sig.buy_max_price, sig.buy_cost,
                sig.sell_price, sig.profit, sig.roi, sig.days_to_sell, sig.risk_score, sig.risk_level,
                json.dumps(sig.reasons),
            ))

    conn.execute("DELETE FROM market WHERE house_id = ?", (house_id,))
    conn.execute("DELETE FROM signals WHERE house_id = ?", (house_id,))
    conn.executemany("INSERT INTO market VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", market_rows)
    conn.executemany("INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", signal_rows)
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
    dest_ladders = load_ladders(conn, dest)
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
                levels, dest_ladders.get(variant_id, (0, [], 0))[1], hist, dest_sold_price.get(variant_id),
                buyer_prices.get(variant_id), buyer if buyer_tracked else None,
                vendor.get(variant_id, (None, None))[0], bool(is_commodity), fees, settings.tuning)
            if sig:
                rows.append((
                    pinned[source], variant_id, sig.strategy, sig.buy_qty, sig.buy_max_price, sig.buy_cost,
                    sig.sell_price, sig.profit, sig.roi, sig.days_to_sell, sig.risk_score, sig.risk_level,
                    json.dumps(sig.reasons),
                ))
    conn.executemany("INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    log.info("Exports via Blackwater: %d signals", len(rows))
    return len(rows)
