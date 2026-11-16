"""Keep score: log each recommendation when it is first made, then check it
against what the market went on to do.

The check is a counterfactual, so it is a guide rather than proof. "Sold at
the price" asks whether, after the recommendation, at least the suggested
quantity changed hands at (or within 3% of) the suggested sell price. For a
FLIP that is strict: the play assumes you bought out the cheap listings, and
if nobody did, buyers kept taking those instead. "Enough buyers" is the
looser test - did that many units sell at any price. "Net of competition" is
the strictest: listings other sellers posted at the same price afterwards sell
before yours (newest first), so they are subtracted. Measured 14-16 Nov 2026
it is what separates the plays: crafts "sold at the price" 93% of the time but
only 48% net, because other crafters keep posting at the same price.

The scorecard also tunes the model: recalibrate() adjusts how far each play's
"sells in" is stretched, from how late its past trades actually ran.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timedelta

from dataclasses import replace

from forever_ah.config import Settings, Tuning

log = logging.getLogger(__name__)
TIME_FMT = "%Y-%m-%d %H:%M:%S"
AT_PRICE = 0.97            # a sale within 3% of the target counts as "at the price"
REPEAT_AFTER_HOURS = 24    # a recommendation that persists is logged again once a day
KEEP_DAYS = 14
SUMMARY_DAYS = 3           # the dashboard's hit rates cover this long, so they reflect the current rules
STRETCH_PLAYS = ("flip", "dip", "craft", "export")
STRETCH_RANGE = (1.0, 3.0)
STRETCH_MIN_SAMPLE = 100


def _time(ts: str) -> datetime:
    return datetime.strptime(ts, TIME_FMT)


def window_hours(days_to_sell: float) -> tuple[int, int]:
    """(hours the sale was predicted to take, hours we watch before calling it missed)."""
    predicted = max(1, math.ceil(days_to_sell * 24))
    return predicted, max(predicted, min(max(2 * predicted, 6), 48))


def log_signals(conn: sqlite3.Connection, settings: Settings) -> int:
    """Record current market recommendations that are not already being tracked."""
    blackwater = settings.houses.get("blackwater", 0)
    latest = dict(conn.execute("SELECT house_id, MAX(published_at) FROM snapshots GROUP BY house_id"))
    added = 0
    rows = conn.execute(
        "SELECT house_id, variant_id, strategy, buy_qty, sell_price, days_to_sell, risk_level "
        "FROM signals WHERE strategy NOT IN ('VENDOR', 'DISENCHANT', 'CRAFTVEND')").fetchall()
    for house_id, variant_id, strategy, qty, sell_price, days, risk in rows:
        issued_at = latest.get(house_id)
        if not issued_at:
            continue
        recent = (_time(issued_at) - timedelta(hours=REPEAT_AFTER_HOURS)).strftime(TIME_FMT)
        if conn.execute(
                "SELECT 1 FROM signal_log WHERE house_id = ? AND variant_id = ? AND strategy = ? AND issued_at > ?",
                (house_id, variant_id, strategy, recent)).fetchone():
            continue
        conn.execute(
            "INSERT INTO signal_log (house_id, sell_house_id, variant_id, strategy, issued_at, buy_qty, "
            "sell_price, days_to_sell, risk_level) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (house_id, blackwater if strategy == "EXPORT" else house_id, variant_id, strategy, issued_at,
             qty, sell_price, days or 0.0, risk))
        added += 1
    conn.commit()
    return added


def judge(issued_at: str, buy_qty: int, sell_price: int, days_to_sell: float,
          sales: list[tuple[str, float, str | None]]) -> tuple[str, float, int, float | None]:
    """Score one recommendation. sales: (published_at, units sold, sold_levels JSON) per later snapshot.

    Returns (outcome, share of the quantity sold at the price, enough buyers at any price, hours to fill).
    """
    predicted, _ = window_hours(days_to_sell)
    start = _time(issued_at)
    at_price = any_price = 0.0
    hours_to_fill = None
    for published_at, sold_qty, levels in sales:
        any_price += sold_qty or 0
        for price, qty in json.loads(levels) if levels else ():
            if price >= sell_price * AT_PRICE:
                at_price += qty
        if hours_to_fill is None and at_price >= buy_qty:
            hours_to_fill = (_time(published_at) - start).total_seconds() / 3600
    if hours_to_fill is None:
        outcome = "MISSED"
    else:
        outcome = "ON_TIME" if hours_to_fill <= predicted + 0.25 else "LATE"
    return outcome, round(min(1.0, at_price / buy_qty), 3), int(any_price >= buy_qty), hours_to_fill


def net_fill(buy_qty: int, sell_price: int, sales: list[tuple]) -> int:
    """1 if the quantity still sold at the price after subtracting every listing
    that other sellers posted at that price (up to 3% under it) during the
    window. At one price the newest listing sells first, so those went before
    yours. sales: (published_at, units sold, sold_levels JSON, new_levels JSON).
    """
    sold = new = 0.0
    for _, _, sold_levels, new_levels in sales:
        for price, qty in json.loads(sold_levels) if sold_levels else ():
            if price >= sell_price * AT_PRICE:
                sold += qty
        for price, qty in json.loads(new_levels) if new_levels else ():
            if sell_price * AT_PRICE <= price <= sell_price:
                new += qty
    return int(sold - new >= buy_qty)


def _window_sales(conn: sqlite3.Connection, house: int, variant_id: int, issued_at: str, until: str) -> list[tuple]:
    return [tuple(r) for r in conn.execute(
        "SELECT s.published_at, i.sold_qty, i.sold_levels, i.new_levels FROM item_stats i "
        "JOIN snapshots s ON s.snapshot_id = i.snapshot_id "
        "WHERE i.house_id = ? AND i.variant_id = ? AND s.published_at > ? AND s.published_at <= ? "
        "ORDER BY s.published_at", (house, variant_id, issued_at, until))]


def score(conn: sqlite3.Connection) -> int:
    """Score every logged recommendation whose watch window has now passed."""
    latest = dict(conn.execute("SELECT house_id, MAX(published_at) FROM snapshots GROUP BY house_id"))
    done = 0
    open_rows = conn.execute(
        "SELECT log_id, sell_house_id, variant_id, issued_at, buy_qty, sell_price, days_to_sell "
        "FROM signal_log WHERE outcome IS NULL").fetchall()
    for log_id, sell_house, variant_id, issued_at, qty, sell_price, days in open_rows:
        _, watch = window_hours(days)
        until = (_time(issued_at) + timedelta(hours=watch)).strftime(TIME_FMT)
        if latest.get(sell_house, "") < until:
            continue  # still inside its window
        seen = conn.execute(
            "SELECT COUNT(*) FROM snapshots WHERE house_id = ? AND published_at > ? AND published_at <= ?",
            (sell_house, issued_at, until)).fetchone()[0]
        if seen < watch / 2:   # the collector missed too much of the window to judge fairly
            conn.execute("UPDATE signal_log SET outcome = 'NO_DATA' WHERE log_id = ?", (log_id,))
            continue
        sales = _window_sales(conn, sell_house, variant_id, issued_at, until)
        outcome, share, any_ok, hours = judge(issued_at, qty, sell_price, days, [r[:3] for r in sales])
        conn.execute(
            "UPDATE signal_log SET outcome = ?, filled_share = ?, any_price_ok = ?, hours_to_fill = ?, net_ok = ? "
            "WHERE log_id = ?", (outcome, share, any_ok, hours, net_fill(qty, sell_price, sales), log_id))
        done += 1
    # Rows judged before the net test existed: fill it in while their hourly data is still held.
    for log_id, sell_house, variant_id, issued_at, qty, sell_price, days in conn.execute(
            "SELECT log_id, sell_house_id, variant_id, issued_at, buy_qty, sell_price, days_to_sell FROM signal_log "
            "WHERE outcome IN ('ON_TIME', 'LATE', 'MISSED') AND net_ok IS NULL").fetchall():
        _, watch = window_hours(days)
        until = (_time(issued_at) + timedelta(hours=watch)).strftime(TIME_FMT)
        conn.execute("UPDATE signal_log SET net_ok = ? WHERE log_id = ?",
                     (net_fill(qty, sell_price, _window_sales(conn, sell_house, variant_id, issued_at, until)), log_id))
    newest = max(latest.values(), default=None)
    if newest:
        cutoff = (_time(newest) - timedelta(days=KEEP_DAYS)).strftime(TIME_FMT)
        conn.execute("DELETE FROM signal_log WHERE issued_at < ?", (cutoff,))
    conn.commit()
    if done:
        log.info("Scorecard: %d recommendation(s) scored", done)
    return done


def summary(conn: sqlite3.Connection) -> dict:
    """Hit rates per play and risk level over the last SUMMARY_DAYS, for the dashboard."""
    newest = conn.execute("SELECT MAX(published_at) FROM snapshots").fetchone()[0]
    since = (_time(newest) - timedelta(days=SUMMARY_DAYS)).strftime(TIME_FMT) if newest else ""
    rows = [
        {"strategy": r[0], "risk_level": r[1], "checked": r[2], "on_time": r[3], "late": r[4],
         "missed": r[5], "enough_buyers": r[6], "net": r[7] or 0}
        for r in conn.execute(
            "SELECT strategy, risk_level, COUNT(*), SUM(outcome = 'ON_TIME'), SUM(outcome = 'LATE'), "
            "SUM(outcome = 'MISSED'), SUM(any_price_ok), SUM(net_ok) FROM signal_log "
            "WHERE outcome IN ('ON_TIME', 'LATE', 'MISSED') AND issued_at >= ? GROUP BY strategy, risk_level "
            "ORDER BY strategy, risk_level", (since,))
    ]
    pending = conn.execute("SELECT COUNT(*) FROM signal_log WHERE outcome IS NULL").fetchone()[0]
    stretch = {r[0][8:]: r[1] for r in conn.execute("SELECT key, value FROM calibration WHERE key LIKE 'stretch_%'")}
    return {"rows": rows, "pending": pending, "keep_days": KEEP_DAYS, "days": SUMMARY_DAYS, "stretch": stretch}


def calibrated(conn: sqlite3.Connection, tun: Tuning) -> Tuning:
    """The tuning with each play's "sells in" stretch replaced by its self-tuned value, if any."""
    if not tun.auto_stretch:
        return tun
    stored = {r[0]: r[1] for r in conn.execute("SELECT key, value FROM calibration WHERE key LIKE 'stretch_%'")}
    return replace(tun, **{k: v for k, v in stored.items() if hasattr(tun, k)})


def recalibrate(conn: sqlite3.Connection, tun: Tuning, now: str) -> dict[str, float]:
    """Once a day, nudge each play's stretch towards "three trades in four sell within the predicted time".

    For trades that sold at the price over the last 3 days, take how long each
    took against its own prediction. If the 75th percentile is 1.3, predictions
    were a third too short. The stretch moves half-way there (square root), is
    kept within STRETCH_RANGE, and is left alone on a thin sample.
    """
    if not tun.auto_stretch:
        return {}
    last = conn.execute("SELECT MAX(updated_at) FROM calibration").fetchone()[0]
    if last and _time(now) - _time(last) < timedelta(hours=23):
        return {}
    since = (_time(now) - timedelta(days=3)).strftime(TIME_FMT)
    current = calibrated(conn, tun)
    changed = {}
    for play in STRETCH_PLAYS:
        ratios = sorted(
            hours / window_hours(days)[0] for hours, days in conn.execute(
                "SELECT hours_to_fill, days_to_sell FROM signal_log WHERE strategy = ? AND issued_at >= ? "
                "AND outcome IN ('ON_TIME', 'LATE')", (play.upper(), since)))
        key = f"stretch_{play}"
        value = getattr(current, key)
        if len(ratios) >= STRETCH_MIN_SAMPLE:
            p75 = ratios[int(0.75 * (len(ratios) - 1))]
            value = round(min(STRETCH_RANGE[1], max(STRETCH_RANGE[0], value * math.sqrt(max(p75, 0.25)))), 2)
        conn.execute("INSERT OR REPLACE INTO calibration VALUES (?, ?, ?)", (key, value, now))
        changed[key] = value
    conn.commit()
    log.info("Scorecard re-tuned the time stretches: %s", changed)
    return changed


def last_outcomes(conn: sqlite3.Connection, house_id: int) -> dict[tuple[int, str], str]:
    """(variant, play) -> how its most recent judged recommendation in this house turned out."""
    return {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT variant_id, strategy, outcome FROM signal_log WHERE house_id = ? "
        "AND outcome IN ('ON_TIME', 'LATE', 'MISSED') ORDER BY issued_at", (house_id,))}
