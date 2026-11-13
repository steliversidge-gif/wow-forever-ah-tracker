"""Pull snapshots from Blizzard, diff them against the live listings, store the summary."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from forever_ah.blizzard import WOWHEAD_CLASSES, BlizzardClient, http_date_to_utc, parse_wowhead_facts
from forever_ah.lifecycle import RateCounts, Rates, rates_from_counts
from forever_ah.pricing import squash_levels
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


def measured_rates(conn: sqlite3.Connection, house_id: int) -> tuple[Rates, dict[str, RateCounts]]:
    """This house's sale / withdrawal / repost base rates, re-measured from every
    hourly diff still in the database (defaults fill in where the sample is thin)."""
    pooled = {
        r[0]: RateCounts(*r[1:]) for r in conn.execute(
            "SELECT kind, SUM(behind_n), SUM(behind_gone), SUM(front_n), SUM(front_gone), SUM(lapsed_n), "
            "SUM(lapsed_reposted) FROM rate_counts WHERE house_id = ? GROUP BY kind", (house_id,))
    }
    return rates_from_counts(pooled.get("stack"), pooled.get("gear")), pooled


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
    rates, _ = measured_rates(conn, house_id)
    diff = diff_snapshot(prev, curr, gap_hours, first=first, max_gap_hours=tun.max_gap_hours,
                         tolerance=tun.noncommodity_tolerance, rates=rates)

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
            squash_levels(f.sold_levels) if f else None, squash_levels(f.new_levels) if f else None,
        ))
    conn.executemany("INSERT INTO item_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.executemany(
        "INSERT INTO rate_counts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ((house_id, snapshot_id, kind, c.behind_n, c.behind_gone, c.front_n, c.front_gone, c.lapsed_n,
          c.lapsed_reposted) for kind, c in diff.counts.items()))
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


def resolve_facts(conn: sqlite3.Connection, client: BlizzardClient, limit: int = 120,
                  pause: float = 0.5) -> int:
    """Fetch from Wowhead how items are obtained and what crafted ones are made of.

    One request per item, ever (misses are retried after a day). Wowhead
    blocks an address that asks too fast (a burst of ~800 lookups got one
    blocked), so each run does a small batch, one at a time with a pause, and
    stops at the first refusals; the first full pass takes two to three days.
    Materials of known recipes go first, then auctioned items, most traded first.
    """
    retry_before = (datetime.now(UTC) - timedelta(days=1)).strftime(TIME_FMT)
    known = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT item_id, ok, fetched_at FROM item_facts")}

    def due(item_id: int) -> bool:
        seen = known.get(item_id)
        return seen is None or (not seen[0] and seen[1] < retry_before)

    todo: list[int] = []
    for (reagents,) in conn.execute("SELECT reagents FROM item_facts WHERE reagents IS NOT NULL"):
        todo.extend(r[0] for r in json.loads(reagents) if due(r[0]))
    todo.extend(r[0] for r in conn.execute(
        "SELECT v.item_id FROM variants v LEFT JOIN market m ON m.variant_id = v.variant_id "
        "GROUP BY v.item_id ORDER BY MAX(COALESCE(m.sold_per_day, 0) * m.market_price) DESC, v.item_id") if due(r[0]))
    todo = list(dict.fromkeys(todo))[:limit]
    if not todo:
        return 0
    log.info("Looking up %d item(s) on Wowhead...", len(todo))
    now = utcnow()
    done = 0
    refused = 0
    for item_id in todo:
        xml = client.wowhead_xml(item_id)
        if xml is None:
            refused += 1
            if refused >= 3:   # throttled or unreachable: leave the rest for a later run
                log.warning("Wowhead is refusing lookups; stopping after %d this run", done)
                break
            continue
        refused = 0
        f = parse_wowhead_facts(xml) or {}
        conn.execute(
            "INSERT OR REPLACE INTO item_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, f.get("name"), json.dumps(f["sources"]) if f.get("sources") else None, f.get("buy_price"),
             f.get("skill"), f.get("spell_name"), f.get("makes"),
             json.dumps(f["reagents"]) if f.get("reagents") else None, now, int(bool(f))))
        done += 1
        time.sleep(pause)
    conn.commit()
    return done


def roll_up_days(conn: sqlite3.Connection) -> int:
    """Summarise the hourly rows into one row per item per UTC day (and one per house).

    Run before prune. The two newest days are rebuilt every time (today is
    still filling; yesterday may have gained a late snapshot); older days are
    only filled in if missing, so a day keeps its full summary after its
    hourly rows start to be pruned. Items are kept for the days they sold at
    least one unit: unsold one-off gear would be most of the rows.
    """
    days = [r[0] for r in conn.execute("SELECT DISTINCT date(published_at) FROM snapshots ORDER BY 1")]
    have = {r[0] for r in conn.execute("SELECT DISTINCT day FROM house_daily")}
    todo = [d for d in days if d not in have or d in days[-2:]]
    for day in todo:
        snaps = [r[0] for r in conn.execute("SELECT snapshot_id FROM snapshots WHERE date(published_at) = ?", (day,))]
        marks = ",".join("?" * len(snaps))
        conn.execute("DELETE FROM item_daily WHERE day = ?", (day,))
        conn.execute("DELETE FROM house_daily WHERE day = ?", (day,))
        conn.execute(
            "INSERT INTO item_daily SELECT house_id, variant_id, ?, ROUND(AVG(market_price)), MIN(min_price), "
            f"SUM(sold_qty), SUM(sold_value), ROUND(AVG(quantity)), COUNT(*) FROM item_stats "
            f"WHERE snapshot_id IN ({marks}) GROUP BY house_id, variant_id HAVING SUM(sold_qty) >= 1",
            (day, *snaps))
        conn.execute(
            "INSERT INTO house_daily SELECT house_id, ?, SUM(sold_qty), SUM(sold_value), COUNT(DISTINCT snapshot_id) "
            f"FROM item_stats WHERE snapshot_id IN ({marks}) GROUP BY house_id", (day, *snaps))
    conn.commit()
    return len(todo)


def prune(conn: sqlite3.Connection, settings: Settings) -> None:
    """Drop history past retention, and all data for houses we no longer track."""
    dropped_house = False
    day_cutoff = (datetime.now(UTC) - timedelta(days=settings.tuning.daily_retention_days)).strftime("%Y-%m-%d")
    conn.execute("DELETE FROM item_daily WHERE day < ?", (day_cutoff,))
    conn.execute("DELETE FROM house_daily WHERE day < ?", (day_cutoff,))
    cutoff = (datetime.now(UTC) - timedelta(days=settings.tuning.stats_retention_days)).strftime(TIME_FMT)
    old = [r[0] for r in conn.execute("SELECT snapshot_id FROM snapshots WHERE published_at < ?", (cutoff,))]
    if old:
        conn.execute("DELETE FROM item_stats WHERE snapshot_id <= ?", (max(old),))
        conn.execute("DELETE FROM rate_counts WHERE snapshot_id <= ?", (max(old),))
        conn.execute("DELETE FROM snapshots WHERE snapshot_id <= ?", (max(old),))
    tracked = {h["house_id"] for h in tracked_houses(conn, settings)}
    for (house_id,) in conn.execute("SELECT DISTINCT house_id FROM snapshots").fetchall():
        if house_id not in tracked:
            log.info("House %s is no longer tracked: removing its data", house_id)
            for table in ("listings", "item_stats", "market", "signals", "snapshots", "rate_counts", "signal_log",
                          "item_daily", "house_daily"):
                conn.execute(f"DELETE FROM {table} WHERE house_id = ?", (house_id,))
            conn.execute("UPDATE houses SET last_modified = NULL WHERE house_id = ?", (house_id,))
            dropped_house = True
    conn.commit()
    if dropped_house:
        conn.execute("VACUUM")  # hand the freed space back so the stored database shrinks
