"""Write the static dashboard: the web assets plus JSON data files it reads.

No web server is involved. The output folder can be opened by GitHub Pages or
by `forever-ah serve` locally.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
from datetime import UTC, datetime
from importlib import resources
from itertools import groupby
from pathlib import Path

from forever_ah import scorecard
from forever_ah.collector import measured_rates, tracked_houses, utcnow
from forever_ah.config import Settings

log = logging.getLogger(__name__)
HISTORY_CHUNKS = 32
TIME_FMT = "%Y-%m-%d %H:%M:%S"

NAME_SQL = ("COALESCE(i.name, 'Item #' || v.item_id) || "
            "CASE WHEN v.bonus != '' THEN ' [variant ' || v.bonus || ']' ELSE '' END")

MARKET_COLS = ["variant_id", "item_id", "name", "quality", "item_class", "is_commodity", "min_price",
               "market_price", "quantity", "listings", "ref_price", "trend_per_day", "sold_per_day",
               "total_sold", "n_snaps", "vendor_sell"]


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def _epoch(ts: str) -> int:
    return int(datetime.strptime(ts, TIME_FMT).replace(tzinfo=UTC).timestamp())


def thin_history(points: list[tuple], recent_hours: int = 48, bucket: int = 3) -> list[tuple]:
    """Keep the last 48h at full resolution; merge older points three at a time.

    points: (epoch, market, cheapest, sold, stock). A merged point keeps the
    bucket's last prices and the SUM of its sales, so totals stay true.
    """
    if not points:
        return points
    cutoff = points[-1][0] - recent_hours * 3600
    old = [p for p in points if p[0] < cutoff]
    out = []
    for i in range(0, len(old), bucket):
        part = old[i:i + bucket]
        out.append((part[-1][0], part[-1][1], part[-1][2], sum(p[3] for p in part), part[-1][4]))
    return out + [p for p in points if p[0] >= cutoff]


def export_house(conn: sqlite3.Connection, house_id: int, data_dir: Path, settings: Settings) -> dict:
    market = conn.execute(
        f"SELECT m.variant_id, v.item_id, {NAME_SQL}, i.quality, i.item_class, m.is_commodity, m.min_price, "
        "m.market_price, m.quantity, m.listings, m.ref_price, ROUND(m.trend_per_day, 4), "
        "ROUND(m.sold_per_day, 2), m.total_sold, m.n_snaps, i.vendor_sell "
        "FROM market m JOIN variants v ON v.variant_id = m.variant_id "
        "LEFT JOIN items i ON i.item_id = v.item_id WHERE m.house_id = ?", (house_id,)).fetchall()
    signals = [
        {**dict(r), "reasons": json.loads(r["reasons"])}
        for r in conn.execute(
            f"SELECT s.variant_id, v.item_id, {NAME_SQL} AS name, i.quality, s.strategy, s.buy_qty, "
            "s.buy_max_price, s.buy_cost, s.sell_price, s.profit, s.roi, s.days_to_sell, s.risk_score, "
            "s.risk_level, s.reasons, s.list_hours, ROUND(m.sold_per_day, 2) AS sold_per_day, m.ref_price "
            "FROM signals s JOIN variants v ON v.variant_id = s.variant_id "
            "LEFT JOIN items i ON i.item_id = v.item_id "
            "LEFT JOIN market m ON m.house_id = s.house_id AND m.variant_id = s.variant_id "
            "WHERE s.house_id = ? ORDER BY s.profit DESC", (house_id,))
    ]
    _write(data_dir / f"house-{house_id}.json",
           {"signals": signals, "market": {"cols": MARKET_COLS, "rows": [list(r) for r in market]}})

    # Per-item history and order book, split into chunks so the item page loads
    # one small file instead of the whole house.
    # Only items that trade get a history series; never-sold one-off gear (most
    # of the rows) keeps just its order book.
    listed = {r[0] for r in market}
    charted = {r[0] for r in market if r[5] or r[13]} | {s["variant_id"] for s in signals}
    snap_time = {r[0]: _epoch(r[1]) for r in conn.execute(
        "SELECT snapshot_id, published_at FROM snapshots WHERE house_id = ?", (house_id,))}
    chunks: list[dict] = [{} for _ in range(HISTORY_CHUNKS)]
    for variant_id in listed:
        chunks[variant_id % HISTORY_CHUNKS][variant_id] = {"t": [], "p": [], "m": [], "s": [], "q": []}
    rows = conn.execute(
        "SELECT variant_id, snapshot_id, market_price, min_price, sold_qty, quantity FROM item_stats "
        "WHERE house_id = ? ORDER BY variant_id, snapshot_id", (house_id,))
    for variant_id, group in groupby(rows, key=lambda r: r[0]):
        if variant_id not in charted:
            continue
        points = thin_history([(snap_time[r[1]], r[2], r[3], r[4], r[5]) for r in group if r[1] in snap_time])
        chunks[variant_id % HISTORY_CHUNKS][variant_id] = {
            "t": [p[0] for p in points], "p": [p[1] for p in points], "m": [p[2] for p in points],
            "s": [p[3] for p in points], "q": [p[4] for p in points],
        }
    depth = settings.tuning.ladder_levels
    ladder_rows = conn.execute(
        "SELECT variant_id, unit_price, SUM(quantity) FROM listings WHERE house_id = ? "
        "GROUP BY variant_id, unit_price ORDER BY variant_id, unit_price", (house_id,))
    for variant_id, group in groupby(ladder_rows, key=lambda r: r[0]):
        entry = chunks[variant_id % HISTORY_CHUNKS].get(variant_id)
        if entry is not None:
            entry["ladder"] = [[r[1], r[2]] for _, r in zip(range(depth), group)]
    for n, chunk in enumerate(chunks):
        _write(data_dir / f"hist-{house_id}-{n}.json", chunk)
    return {"n_items": len(market), "n_signals": len(signals)}


def sales_by_hour(conn: sqlite3.Connection, house_id: int) -> list[dict]:
    """Average value sold per hourly snapshot, by hour of the day (UTC), for the "when things sell" chart.

    Only snapshots scored with the current inference (those with a rate tally) count.
    """
    return [
        {"hour": r[0], "snapshots": r[1], "value": round(r[2] / r[1])}
        for r in conn.execute(
            "SELECT CAST(strftime('%H', s.published_at) AS INTEGER) AS hour, COUNT(DISTINCT s.snapshot_id), "
            "SUM(i.sold_value) FROM snapshots s JOIN item_stats i ON i.house_id = s.house_id "
            "AND i.snapshot_id = s.snapshot_id WHERE s.house_id = ? AND s.snapshot_id IN "
            "(SELECT snapshot_id FROM rate_counts WHERE house_id = ?) GROUP BY hour ORDER BY hour",
            (house_id, house_id))
    ]


def base_rates(conn: sqlite3.Connection, house_id: int) -> dict:
    """The sale / withdrawal / repost base rates in use for a house and the sample behind them."""
    rates, pooled = measured_rates(conn, house_id)
    out = {k: round(v, 4) for k, v in rates._asdict().items()}
    out["sample"] = {kind: c.front_n + c.behind_n for kind, c in pooled.items()}
    return out


def setup_reference(conn: sqlite3.Connection, house_ids: list[int], limit: int = 8) -> list[dict]:
    """The best-stocked commodities whose price clearly differs between candidate houses.

    Looking these up in game tells you which candidate house is yours.
    """
    per_house = {
        h: {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            "SELECT variant_id, min_price, market_price, quantity FROM market "
            "WHERE house_id = ? AND is_commodity = 1 AND quantity >= 20", (h,))}
        for h in house_ids
    }
    per_house = {h: v for h, v in per_house.items() if v}
    if len(per_house) < 2:
        return []
    common = set.intersection(*(set(v) for v in per_house.values()))
    scored = []
    for variant_id in common:
        prices = sorted(per_house[h][variant_id][1] for h in per_house)
        # every house must differ from the next by 15%+, or the item cannot tell them apart
        if all(hi >= lo * 1.15 for lo, hi in zip(prices, prices[1:])):
            scored.append((min(per_house[h][variant_id][2] for h in per_house), variant_id))
    scored.sort(reverse=True)
    out = []
    for _, variant_id in scored[: limit * 4]:
        name = conn.execute(
            "SELECT i.name FROM variants v JOIN items i ON i.item_id = v.item_id WHERE v.variant_id = ?",
            (variant_id,)).fetchone()
        if name and name[0]:
            out.append({"name": name[0], "prices": {str(h): per_house[h][variant_id][0] for h in per_house}})
        if len(out) >= limit:
            break
    return out


def export_site(conn: sqlite3.Connection, settings: Settings) -> Path:
    dist = settings.dist_path
    data_dir = dist / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    web = resources.files("forever_ah") / "web"
    assets = {a.name: a.read_bytes() for a in web.iterdir() if a.is_file()}
    # Stamp the script and stylesheet links with a content hash so browsers
    # pick up a new version instead of serving a stale cached copy.
    page = assets["index.html"].decode("utf-8")
    for name in ("app.js", "style.css"):
        page = page.replace(f'"{name}"', f'"{name}?v={hashlib.sha256(assets[name]).hexdigest()[:10]}"')
    assets["index.html"] = page.encode("utf-8")
    for name, content in assets.items():
        (dist / name).write_bytes(content)
    (dist / ".nojekyll").write_text("", encoding="utf-8")

    houses = []
    undecided: dict[str, list[int]] = {}
    for house in tracked_houses(conn, settings):
        house_id = house["house_id"]
        snap = conn.execute(
            "SELECT COUNT(*), MAX(published_at), MIN(published_at) FROM snapshots WHERE house_id = ?",
            (house_id,)).fetchone()
        if not snap[0]:
            continue
        counts = export_house(conn, house_id, data_dir, settings)
        pinned = settings.houses.get(house["faction"]) == house_id
        if not pinned:
            undecided.setdefault(house["faction"], []).append(house_id)
        houses.append({"id": house_id, "name": house["name"], "faction": house["faction"],
                       "slot": house["slot"], "pinned": pinned, "n_snaps": snap[0],
                       "published_at": _epoch(snap[1]), "first_snapshot": _epoch(snap[2]),
                       "sales_by_hour": sales_by_hour(conn, house_id), "rates": base_rates(conn, house_id),
                       **counts})

    fees = settings.fees
    _write(data_dir / "meta.json", {
        "generated_at": _epoch(utcnow()),
        "region": settings.region, "ruleset": settings.ruleset,
        "history_chunks": HISTORY_CHUNKS,
        "min_history_snaps": settings.tuning.min_history_snaps,
        "fees": {"faction_cut": fees.faction_cut, "neutral_cut": fees.neutral_cut,
                 "min_deposit": fees.min_deposit, "listing_hours": fees.listing_hours},
        "home_faction": settings.home_faction,
        "houses": houses,
        "scorecard": scorecard.summary(conn),
        "setup": {f: setup_reference(conn, ids) for f, ids in undecided.items() if len(ids) > 1},
    })
    log.info("Dashboard written to %s (%d house(s))", dist, len(houses))
    return dist
