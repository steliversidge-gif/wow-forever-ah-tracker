"""Rebuild the `market` summary and `signals` for a house from what is in the database."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from itertools import groupby

from forever_ah.config import Settings
from forever_ah.pricing import market_price
from forever_ah.signals import build_history, evaluate, house_fees

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


def analyse_house(conn: sqlite3.Connection, house_id: int, settings: Settings) -> int:
    tun = settings.tuning
    house = conn.execute("SELECT * FROM houses WHERE house_id = ?", (house_id,)).fetchone()
    fees = house_fees(settings.fees, house["faction"])

    since = (datetime.now(UTC) - timedelta(days=tun.lookback_days)).strftime(TIME_FMT)
    snaps = conn.execute(
        "SELECT snapshot_id, published_at FROM snapshots WHERE house_id = ? AND published_at >= ? "
        "ORDER BY published_at", (house_id, since)).fetchall()
    if not snaps:
        return 0
    snap_hours = {s[0]: _hours(s[1]) for s in snaps}
    span_hours = _hours(snaps[-1][1]) - _hours(snaps[0][1])

    history = {}
    rows = conn.execute(
        "SELECT variant_id, snapshot_id, market_price, sold_qty FROM item_stats "
        "WHERE house_id = ? AND snapshot_id >= ? ORDER BY variant_id, snapshot_id", (house_id, snaps[0][0]))
    for variant_id, group in groupby(rows, key=lambda r: r[0]):
        points = [(snap_hours[r[1]], r[2], r[3]) for r in group if r[1] in snap_hours]
        history[variant_id] = build_history(points, span_hours)

    vendor = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT v.variant_id, i.vendor_sell, i.source FROM variants v JOIN items i ON i.item_id = v.item_id "
        "WHERE i.vendor_sell > 0")}

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
