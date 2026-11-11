"""Pull snapshots from Blizzard, diff them against the live listings, store the summary."""

from __future__ import annotations

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from forever_ah.blizzard import WOWHEAD_CLASSES, BlizzardClient, http_date_to_utc
from forever_ah.config import FACTIONS, Settings
from forever_ah.lifecycle import Listing, diff_snapshot
from forever_ah.pricing import market_price, price_levels

log = logging.getLogger(__name__)
TIME_FMT = "%Y-%m-%d %H:%M:%S"


def utcnow() -> str:
    return datetime.now(UTC).strftime(TIME_FMT)


def faction_of(name: str) -> str:
    lowered = name.lower()
    for faction in FACTIONS:
        if faction in lowered:
            return faction
    return "unknown"


def sync_houses(conn: sqlite3.Connection, index: list[dict]) -> None:
    slots: dict[str, int] = {}
    for house in index:
        faction = faction_of(house["name"])
        slots[faction] = slots.get(faction, 0) + 1
        conn.execute(
            "INSERT INTO houses (house_id, name, faction, slot) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(house_id) DO UPDATE SET name = excluded.name, faction = excluded.faction, "
            "slot = excluded.slot",
            (house["id"], house["name"], faction, slots[faction]),
        )
    conn.commit()


def tracked_houses(conn: sqlite3.Connection, settings: Settings) -> list[sqlite3.Row]:
    """Pinned house per faction; every candidate while a faction is unidentified (0); none if -1."""
    out = []
    for row in conn.execute("SELECT * FROM houses ORDER BY house_id"):
        choice = settings.houses.get(row["faction"], -1)
        if choice == 0 or choice == row["house_id"]:
            out.append(row)
    return out


class VariantCache:
    """(item_id, bonus) -> variant_id, creating rows on demand."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.ids = {(r[1], r[2]): r[0] for r in conn.execute("SELECT variant_id, item_id, bonus FROM variants")}

    def get(self, item: dict) -> int:
        bonus = ":".join(str(b) for b in sorted(item.get("bonus_lists") or ()))
        key = (item["id"], bonus)
        vid = self.ids.get(key)
        if vid is None:
            vid = self.conn.execute("INSERT INTO variants (item_id, bonus) VALUES (?, ?)", key).lastrowid
            self.ids[key] = vid
        return vid


def parse_listings(auctions: list[dict], commodities: list[dict], variants: VariantCache) -> dict[int, Listing]:
    curr: dict[int, Listing] = {}
    for a in auctions:
        buyout = a.get("buyout")
        if not buyout:
            continue  # bid-only: cannot be bought on demand, so it is not a price
        qty = max(int(a.get("quantity", 1)), 1)
        curr[a["id"]] = Listing(variants.get(a["item"]), 0, buyout // qty, qty, a.get("time_left", "LONG"))
    for c in commodities:
        price = c.get("unit_price")
        if not price:
            continue
        curr[c["id"]] = Listing(variants.get(c["item"]), 1, price, max(int(c.get("quantity", 1)), 1),
                                c.get("time_left", "LONG"))
    return curr


def ingest_house(conn: sqlite3.Connection, house_id: int, published_at: str, curr: dict[int, Listing],
                 n_auctions: int, n_commodities: int, settings: Settings) -> dict:
    """Apply one snapshot for one house inside a single transaction."""
    tun = settings.tuning
    last = conn.execute(
        "SELECT published_at FROM snapshots WHERE house_id = ? ORDER BY published_at DESC LIMIT 1",
        (house_id,),
    ).fetchone()
    first = last is None
    gap_hours = None
    if last:
        gap = datetime.strptime(published_at, TIME_FMT) - datetime.strptime(last[0], TIME_FMT)
        gap_hours = gap.total_seconds() / 3600
        if gap_hours <= 0:
            return {"skipped": True}

    prev = {
        r[0]: Listing(r[1], r[2], r[3], r[4], r[5])
        for r in conn.execute(
            "SELECT auction_id, variant_id, is_commodity, unit_price, quantity, time_left "
            "FROM listings WHERE house_id = ?", (house_id,))
    }
    diff = diff_snapshot(prev, curr, gap_hours, first=first, max_gap_hours=tun.max_gap_hours,
                         tolerance=tun.noncommodity_tolerance)

    snapshot_id = conn.execute(
        "INSERT INTO snapshots (house_id, published_at, fetched_at, n_auctions, n_commodities, gap_hours) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (house_id, published_at, utcnow(), n_auctions, n_commodities, gap_hours),
    ).lastrowid

    conn.executemany(
        "INSERT INTO listings (house_id, auction_id, variant_id, is_commodity, unit_price, quantity, "
        "time_left, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ((house_id, i, *curr[i], published_at) for i in diff.new),
    )
    conn.executemany(
        "UPDATE listings SET quantity = ?, time_left = ? WHERE house_id = ? AND auction_id = ?",
        ((curr[i].quantity, curr[i].time_left, house_id, i) for i in diff.changed),
    )
    conn.executemany("DELETE FROM listings WHERE house_id = ? AND auction_id = ?",
                     ((house_id, i) for i in diff.gone))

    by_variant: dict[int, list[tuple[int, int]]] = {}
    for listing in curr.values():
        by_variant.setdefault(listing.variant_id, []).append((listing.unit_price, listing.quantity))

    rows = []
    for variant_id in by_variant.keys() | diff.flows.keys():
        pairs = by_variant.get(variant_id, [])
        levels = price_levels(pairs)
        f = diff.flows.get(variant_id)
        rows.append((
            house_id, variant_id, snapshot_id,
            levels[0][0] if levels else None,
            market_price(levels, tun.market_fraction),
            sum(q for _, q in levels), len(pairs),
            round(f.sold_qty, 2) if f else 0, round(f.sold_value) if f else 0,
            round(f.cancelled_qty, 2) if f else 0, round(f.expired_qty, 2) if f else 0, f.new_qty if f else 0,
        ))
    conn.executemany("INSERT INTO item_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return {"snapshot_id": snapshot_id, "listings": len(curr), "new": len(diff.new),
            "gone": len(diff.gone), "sold_units": round(sum(f.sold_qty for f in diff.flows.values())),
            "gap_hours": gap_hours}


def collect(conn: sqlite3.Connection, client: BlizzardClient, settings: Settings) -> tuple[list[int], int]:
    """Fetch every tracked house. Returns (house ids with a new snapshot, failure count)."""
    sync_houses(conn, client.house_index(settings.ruleset))
    variants = VariantCache(conn)
    updated: list[int] = []
    failures = 0
    for house in tracked_houses(conn, settings):
        house_id = house["house_id"]
        try:
            result = client.house_auctions(settings.ruleset, house_id, house["last_modified"])
            if result is None:
                log.info("House %s (%s): unchanged", house_id, house["faction"])
                continue
            last_modified, auctions = result
            commodities = client.house_commodities(settings.ruleset, house_id)
            curr = parse_listings(auctions, commodities, variants)
            has_live = conn.execute("SELECT 1 FROM listings WHERE house_id = ? LIMIT 1", (house_id,)).fetchone()
            if curr or has_live:  # skip the permanently empty houses
                summary = ingest_house(conn, house_id, http_date_to_utc(last_modified), curr,
                                       len(auctions), len(commodities), settings)
                if not summary.get("skipped"):
                    updated.append(house_id)
                    log.info("House %s (%s): %s", house_id, house["faction"], summary)
            conn.execute("UPDATE houses SET last_modified = ? WHERE house_id = ?", (last_modified, house_id))
            conn.commit()
        except Exception:
            conn.rollback()
            failures += 1
            log.exception("House %s failed; continuing with the rest", house_id)
    return updated, failures


def resolve_items(conn: sqlite3.Connection, client: BlizzardClient, limit: int = 4000) -> int:
    """Fetch names / vendor prices for item ids we have not seen (or that failed over a day ago)."""
    # Rows stored before Wowhead's category names were mapped onto Blizzard's.
    conn.executemany("UPDATE items SET item_class = ? WHERE item_class = ? AND source = 'wowhead-forever'",
                     [(ours, theirs) for theirs, ours in WOWHEAD_CLASSES.items()])
    conn.commit()
    retry_before = (datetime.now(UTC) - timedelta(days=1)).strftime(TIME_FMT)
    todo =[r[0] for r in conn.execute(
        "SELECT DISTINCT v.item_id FROM variants v LEFT JOIN items i ON i.item_id = v.item_id "
        # An unnamed item gets one prompt retry, then one more a day later.
        "WHERE i.item_id IS NULL OR (i.name IS NULL AND (i.attempts < 2 OR "
        "(i.attempts < 3 AND i.fetched_at < ?))) LIMIT ?",
        (retry_before, limit))]
    if not todo:
        return 0
    client.token()  # fetch once before fanning out
    log.info("Resolving %d item(s)...", len(todo))

    def fetch(item_id: int):
        try:
            return item_id, *client.item(item_id)
        except Exception as exc:  # network trouble: leave it for the next run
            log.warning("Item %s lookup failed: %s", item_id, exc)
            return item_id, None, "error"

    now = utcnow()
    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for item_id, data, source in pool.map(fetch, todo):
            if source == "error":
                continue
            d = data or {}
            conn.execute(
                "INSERT INTO items (item_id, name, quality, item_class, item_subclass, level, required_level, "
                "vendor_sell, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(item_id) DO UPDATE SET name = excluded.name, quality = excluded.quality, "
                "item_class = excluded.item_class, item_subclass = excluded.item_subclass, "
                "level = excluded.level, required_level = excluded.required_level, "
                "vendor_sell = excluded.vendor_sell, source = excluded.source, "
                "fetched_at = excluded.fetched_at, attempts = items.attempts + 1",
                (item_id, d.get("name"), (d.get("quality") or {}).get("name"),
                 (d.get("item_class") or {}).get("name"), (d.get("item_subclass") or {}).get("name"),
                 d.get("level"), d.get("required_level"), d.get("sell_price"), source, now),
            )
            done += 1
    conn.commit()
    return done


def prune(conn: sqlite3.Connection, settings: Settings) -> None:
    """Drop history past retention, and all data for houses we no longer track."""
    dropped_house = False
    cutoff = (datetime.now(UTC) - timedelta(days=settings.tuning.stats_retention_days)).strftime(TIME_FMT)
    old = [r[0] for r in conn.execute("SELECT snapshot_id FROM snapshots WHERE published_at < ?", (cutoff,))]
    if old:
        conn.execute("DELETE FROM item_stats WHERE snapshot_id <= ?", (max(old),))
        conn.execute("DELETE FROM snapshots WHERE snapshot_id <= ?", (max(old),))
    tracked = {h["house_id"] for h in tracked_houses(conn, settings)}
    for (house_id,) in conn.execute("SELECT DISTINCT house_id FROM snapshots").fetchall():
        if house_id not in tracked:
            log.info("House %s is no longer tracked: removing its data", house_id)
            for table in ("listings", "item_stats", "market", "signals", "snapshots"):
                conn.execute(f"DELETE FROM {table} WHERE house_id = ?", (house_id,))
            conn.execute("UPDATE houses SET last_modified = NULL WHERE house_id = ?", (house_id,))
            dropped_house = True
    conn.commit()
    if dropped_house:
        conn.execute("VACUUM")  # hand the freed space back so the stored database shrinks
