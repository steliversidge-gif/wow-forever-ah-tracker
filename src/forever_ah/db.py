"""SQLite schema and connection handling.

Storage model (the fix for the retail tracker's 9.7 GB database): a listing is
stored ONCE while it is live and deleted when it closes; what happened to it is
tallied into one small summary row per item per hourly snapshot.

  listings    live auctions only - the "previous snapshot" each run diffs against
  item_stats  per house / item variant / snapshot: prices, depth, and the flows
              inferred from the diff (sold, cancelled, expired, new)
  market      latest per-item summary, rebuilt each run   } what the dashboard
  signals     buy/sell recommendations, rebuilt each run  } export reads
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS houses (
    house_id      INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    faction       TEXT NOT NULL,          -- alliance | horde | blackwater
    slot          INTEGER NOT NULL,       -- position within its faction in the API index
    last_modified TEXT                    -- raw HTTP Last-Modified of the last ingested snapshot
);

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    house_id      INTEGER NOT NULL REFERENCES houses(house_id),
    published_at  TEXT NOT NULL,          -- UTC, when Blizzard generated the data
    fetched_at    TEXT NOT NULL,
    n_auctions    INTEGER NOT NULL,
    n_commodities INTEGER NOT NULL,
    gap_hours     REAL,                   -- hours since the previous snapshot (NULL for the first)
    UNIQUE (house_id, published_at)
);

-- An item variant: item id plus its bonus list (random-suffix gear such as
-- "of the Monkey" shares an item id but is a different thing to price).
CREATE TABLE IF NOT EXISTS variants (
    variant_id INTEGER PRIMARY KEY,
    item_id    INTEGER NOT NULL,
    bonus      TEXT NOT NULL DEFAULT '',
    UNIQUE (item_id, bonus)
);

CREATE TABLE IF NOT EXISTS items (
    item_id        INTEGER PRIMARY KEY,
    name           TEXT,                  -- NULL when no namespace knows the item
    quality        TEXT,
    item_class     TEXT,
    item_subclass  TEXT,
    level          INTEGER,
    required_level INTEGER,
    vendor_sell    INTEGER,               -- copper
    source         TEXT,                  -- namespace that answered
    fetched_at     TEXT NOT NULL,
    attempts       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS listings (
    house_id     INTEGER NOT NULL,
    auction_id   INTEGER NOT NULL,
    variant_id   INTEGER NOT NULL,
    is_commodity INTEGER NOT NULL,
    unit_price   INTEGER NOT NULL,        -- copper per unit (buyout)
    quantity     INTEGER NOT NULL,
    time_left    TEXT NOT NULL,
    first_seen   TEXT NOT NULL,
    PRIMARY KEY (house_id, auction_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_listings_ladder ON listings (house_id, variant_id, unit_price);

CREATE TABLE IF NOT EXISTS item_stats (
    house_id      INTEGER NOT NULL,
    variant_id    INTEGER NOT NULL,
    snapshot_id   INTEGER NOT NULL,
    min_price     INTEGER,                -- NULL when nothing is listed
    market_price  INTEGER,
    quantity      INTEGER NOT NULL DEFAULT 0,
    listings      INTEGER NOT NULL DEFAULT 0,
    sold_qty      INTEGER NOT NULL DEFAULT 0,
    sold_value    INTEGER NOT NULL DEFAULT 0,
    cancelled_qty INTEGER NOT NULL DEFAULT 0,
    expired_qty   INTEGER NOT NULL DEFAULT 0,
    new_qty       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (house_id, variant_id, snapshot_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS market (
    house_id       INTEGER NOT NULL,
    variant_id     INTEGER NOT NULL,
    is_commodity   INTEGER NOT NULL,
    min_price      INTEGER NOT NULL,
    market_price   INTEGER NOT NULL,
    quantity       INTEGER NOT NULL,
    listings       INTEGER NOT NULL,
    ref_price      INTEGER,               -- typical price: median hourly market price
    trend_per_day  REAL,
    cv             REAL,                  -- price volatility (stdev / mean)
    sold_per_day   REAL,
    total_sold     INTEGER NOT NULL DEFAULT 0,
    n_snaps        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (house_id, variant_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS signals (
    house_id      INTEGER NOT NULL,
    variant_id    INTEGER NOT NULL,
    strategy      TEXT NOT NULL,          -- VENDOR | FLIP | DIP
    buy_qty       INTEGER NOT NULL,
    buy_max_price INTEGER NOT NULL,
    buy_cost      INTEGER NOT NULL,
    sell_price    INTEGER NOT NULL,
    profit        INTEGER NOT NULL,
    roi           REAL NOT NULL,
    days_to_sell  REAL,
    risk_score    INTEGER NOT NULL,
    risk_level    TEXT NOT NULL,          -- NONE | LOW | MEDIUM | HIGH
    reasons       TEXT NOT NULL,          -- JSON list of plain-English reasons
    PRIMARY KEY (house_id, variant_id, strategy)
) WITHOUT ROWID;
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"Database schema v{version} is newer than this code (v{SCHEMA_VERSION}).")
    if version < SCHEMA_VERSION:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    return conn


def checkpoint(conn: sqlite3.Connection) -> None:
    """Fold the WAL back into the main file so the .db can be copied/uploaded on its own."""
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
