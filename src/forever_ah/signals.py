"""The recommendation engine: what to buy, what to sell it for, and how risky. Pure functions.

Three strategies, all inside ONE auction house:

VENDOR  Listings priced below what an NPC vendor pays. Buy, walk to a vendor,
        sell. No market risk at all.
FLIP    The cheapest listings sit well under the next price up. Buy them and
        relist just under that next price (never above the item's typical
        price), so you are the cheapest seller again.
DIP     The whole market is well below its typical price. Buy the cheap units
        and relist near typical. Slower and riskier: it needs the price to recover.

Every metric has exactly one definition, here, so the dashboard (and any later
consumer such as an AI agent) can never disagree about what "profit" means.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from forever_ah.config import Fees, Tuning
from forever_ah.pricing import Level, median


@dataclass(frozen=True)
class History:
    n_snaps: int = 0                    # snapshots in which the item had a price
    ref_price: int | None = None        # typical price: median of hourly market prices
    trend_per_day: float | None = None  # +0.10 = rising 10% a day
    cv: float | None = None             # volatility: stdev / mean of hourly market prices
    sold_per_day: float | None = None   # units, inferred (see lifecycle.py)
    total_sold: int = 0


@dataclass(frozen=True)
class HouseFees:
    cut: float
    deposit_rate: float
    min_deposit: int
    listing_hours: int


@dataclass(frozen=True)
class Signal:
    strategy: str
    buy_qty: int
    buy_max_price: int
    buy_cost: int
    sell_price: int
    profit: int
    roi: float
    days_to_sell: float | None
    risk_score: int
    risk_level: str
    reasons: list[str]

    def as_dict(self) -> dict:
        return asdict(self)


def house_fees(fees: Fees, faction: str) -> HouseFees:
    neutral = faction == "blackwater"
    return HouseFees(
        cut=fees.neutral_cut if neutral else fees.faction_cut,
        deposit_rate=fees.neutral_deposit_rate if neutral else fees.deposit_rate,
        min_deposit=fees.min_deposit,
        listing_hours=fees.listing_hours,
    )


def build_history(points: list[tuple[float, int | None, int]], span_hours: float,
                  min_span_hours: float = 6.0) -> History:
    """Summarise an item's hourly rows.

    points: (time in hours, market price or None, units sold), oldest first.
    span_hours: how long the HOUSE has been observed in the window - an item
    missing from a snapshot was out of stock, which still counts as time.
    """
    priced = [(t, p) for t, p, _ in points if p]
    total_sold = sum(s for _, _, s in points)
    sold_per_day = total_sold / (span_hours / 24) if span_hours >= min_span_hours else None
    if not priced:
        return History(sold_per_day=sold_per_day, total_sold=total_sold)

    prices = [p for _, p in priced]
    ref = median(prices)
    mean = sum(prices) / len(prices)
    cv = None
    if len(prices) >= 3 and mean > 0:
        cv = math.sqrt(sum((p - mean) ** 2 for p in prices) / (len(prices) - 1)) / mean

    # Trend: the last 6 hours against a 12-hour window a day earlier (or, with
    # less history, against the first 6 hours seen), scaled to a daily rate.
    trend = None
    t_last = priced[-1][0]
    recent = [p for t, p in priced if t > t_last - 6]
    base = [(t, p) for t, p in priced if t_last - 36 <= t <= t_last - 24]
    if not base:
        t_first = priced[0][0]
        base = [(t, p) for t, p in priced if t < t_first + 6 and t <= t_last - 6]
    if recent and base:
        base_mid = sum(t for t, _ in base) / len(base)
        days = (t_last - 3 - base_mid) / 24
        base_price = median([p for _, p in base])
        if days >= 0.25 and base_price:
            trend = (median(recent) / base_price - 1) / days

    return History(n_snaps=len(priced), ref_price=round(ref), trend_per_day=trend, cv=cv,
                   sold_per_day=sold_per_day, total_sold=total_sold)


def _deposit(vendor: int | None, qty: int, is_commodity: bool, fees: HouseFees) -> int:
    """Deposit put at risk when relisting: one stack for commodities, one listing per item for gear."""
    if is_commodity:
        return max(fees.min_deposit, round(fees.deposit_rate * (vendor or 0) * qty))
    return qty * max(fees.min_deposit, round(fees.deposit_rate * (vendor or 0)))


def assess_risk(strategy: str, days_to_sell: float, hist: History) -> tuple[int, str, list[str]]:
    """0-100 score from four things: how fast it sells, how much evidence we
    have, how jumpy the price is, and which way it is heading."""
    score = 0
    reasons: list[str] = []

    if days_to_sell <= 0.25:
        reasons.append("Sells within hours")
    elif days_to_sell <= 1:
        score += 10
        reasons.append("About a day to sell")
    elif days_to_sell <= 3:
        score += 25
        reasons.append(f"About {days_to_sell:.0f} days to sell")
    else:
        score += 40
        reasons.append(f"Slow seller: about {days_to_sell:.0f} days")

    if hist.n_snaps < 24:
        score += 20
        reasons.append(f"Only {hist.n_snaps}h of price history")
    elif hist.n_snaps < 72:
        score += 10
        reasons.append("Under 3 days of price history")
    if hist.total_sold < 10:
        score += 15
        reasons.append(f"Few observed sales ({hist.total_sold})")

    if hist.cv is not None:
        if hist.cv > 0.5:
            score += 15
            reasons.append("Very volatile price")
        elif hist.cv > 0.25:
            score += 8
            reasons.append("Volatile price")

    if hist.trend_per_day is not None:
        if hist.trend_per_day < -0.15:
            score += 30
            reasons.append(f"Price falling {abs(hist.trend_per_day):.0%} a day")
        elif hist.trend_per_day < -0.05:
            score += 10
            reasons.append(f"Price drifting down {abs(hist.trend_per_day):.0%} a day")

    if strategy == "DIP":
        score += 15
        reasons.append("Relies on the price recovering")

    score = min(score, 100)
    level = "LOW" if score < 30 else "MEDIUM" if score < 55 else "HIGH"
    return score, level, reasons


def _finish(strategy: str, qty: int, max_price: int, cost: int, target: int, days: float,
            hist: History, vendor: int | None, is_commodity: bool, fees: HouseFees,
            tun: Tuning) -> Signal | None:
    # A deposit is refunded on sale and lost on expiry; charge one per listing
    # period we expect to sit through before the stock clears.
    expiries = max(0, math.ceil(days * 24 / fees.listing_hours) - 1)
    lost_deposits = _deposit(vendor, qty, is_commodity, fees) * expiries
    profit = math.floor(qty * target * (1 - fees.cut)) - cost - lost_deposits
    if cost <= 0 or profit < tun.min_profit or profit / cost < tun.min_roi:
        return None
    score, level, reasons = assess_risk(strategy, days, hist)
    return Signal(strategy, qty, max_price, cost, target, profit, round(profit / cost, 4),
                  round(days, 2), score, level, reasons)


def vendor_signal(levels: list[Level], vendor: int | None, verified: bool, tun: Tuning) -> Signal | None:
    if not vendor:
        return None
    qty = cost = max_price = 0
    for price, q in levels:
        if price >= vendor:
            break
        qty += q
        cost += price * q
        max_price = price
    profit = qty * vendor - cost
    if qty == 0 or profit < tun.min_profit:
        return None
    reasons = ["Sell to any NPC vendor - no auction needed"]
    if not verified:
        reasons.append("Vendor price is from Classic data: check the tooltip first")
    return Signal("VENDOR", qty, max_price, cost, vendor, profit, round(profit / cost, 4) if cost else 0.0,
                  0.0, 0 if verified else 10, "NONE" if verified else "LOW", reasons)


def flip_signal(levels: list[Level], hist: History, vendor: int | None, is_commodity: bool,
                fees: HouseFees, tun: Tuning) -> Signal | None:
    if hist.ref_price is None or hist.n_snaps < tun.min_history_snaps or not hist.sold_per_day:
        return None
    max_units = max(1, int(hist.sold_per_day * tun.horizon_days))
    best: Signal | None = None
    qty = cost = 0
    for i in range(min(len(levels) - 1, tun.max_levels)):
        price, q = levels[i]
        qty += q
        cost += price * q
        if qty > max_units:
            break  # buying part of a price level leaves the rest undercutting you
        target = min(levels[i + 1][0] - 1, hist.ref_price)
        if target <= price:
            continue
        sig = _finish("FLIP", qty, price, cost, target, qty / hist.sold_per_day, hist,
                      vendor, is_commodity, fees, tun)
        if sig and (best is None or sig.profit > best.profit):
            best = sig
    return best


def dip_signal(levels: list[Level], hist: History, vendor: int | None, is_commodity: bool,
               fees: HouseFees, tun: Tuning) -> Signal | None:
    if hist.ref_price is None or hist.n_snaps < 24 or not hist.sold_per_day or not levels:
        return None
    ceiling = hist.ref_price * (1 - tun.dip_threshold)
    if levels[0][0] > ceiling:
        return None
    target = int(hist.ref_price * tun.dip_target)
    max_units = max(1, int(hist.sold_per_day * tun.horizon_days))
    qty = cost = max_price = 0
    for price, q in levels[: tun.max_levels]:
        if price > ceiling or qty >= max_units:
            break
        take = min(q, max_units - qty) if is_commodity else q
        if not is_commodity and qty + take > max_units and qty > 0:
            break
        qty += take
        cost += take * price
        max_price = price
    if qty == 0:
        return None
    # Everything still listed under our relist price has to sell before ours does.
    ahead = sum(q for p, q in levels if p < target) - qty
    days = (max(ahead, 0) + qty) / hist.sold_per_day
    return _finish("DIP", qty, max_price, cost, target, days, hist, vendor, is_commodity, fees, tun)


def evaluate(levels: list[Level], hist: History, vendor: int | None, vendor_verified: bool,
             is_commodity: bool, fees: HouseFees, tun: Tuning) -> list[Signal]:
    found = (
        vendor_signal(levels, vendor, vendor_verified, tun),
        flip_signal(levels, hist, vendor, is_commodity, fees, tun),
        dip_signal(levels, hist, vendor, is_commodity, fees, tun),
    )
    return [s for s in found if s is not None]
