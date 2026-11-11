"""The recommendation engine: what to buy, what to sell it for, and how risky. Pure functions.

Three strategies inside ONE auction house, and one that crosses to Blackwater:

VENDOR  Listings priced below what an NPC vendor pays. Buy, walk to a vendor,
        sell. No market risk at all.
FLIP    The cheapest listings sit well under the next price up. Buy them and
        relist AT that next price (never above the item's typical price). At
        one price the newest listing sells first, so matching the price puts
        you at the front without giving away a copper.
DIP     The whole market is well below its typical price. Buy the cheap units
        and relist near typical. Slower and riskier: it needs the price to recover.
CRAFT   Buy the materials, make the item, list it. Priced at the cheapest
        current listing (or the typical price when none is listed).
DISENCHANT  Gear listed for less than the enchanting materials it breaks into.
        Buy it, disenchant it, sell the materials.
EXPORT  Buy in a faction house and list on the neutral Blackwater house, where
        the other faction can buy it. Only suggested for items that have
        actually been selling on Blackwater, priced no higher than the buyers
        pay on their own house, and with profit to spare after a lost deposit.

Every metric has exactly one definition, here, so the dashboard (and any later
consumer such as an AI agent) can never disagree about what "profit" means.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace

from forever_ah.config import Fees, Tuning
from forever_ah.pricing import Level, format_money, median


AT_PRICE = 0.97        # "at this price" means within 3% under it
MIN_LEVEL_DAYS = 0.25  # price-level data is ignored until 6 hours of it exist
SHORT_LISTING = 8      # hours; the fallback when a trade may need repricing


@dataclass(frozen=True)
class History:
    n_snaps: int = 0                    # snapshots in which the item had a price
    ref_price: int | None = None        # typical price: median of hourly market prices
    trend_per_day: float | None = None  # +0.10 = rising 10% a day
    cv: float | None = None             # volatility: stdev / mean of hourly market prices
    sold_per_day: float | None = None   # units, inferred (see lifecycle.py)
    total_sold: float = 0               # expected units: uncertain disappearances count in part
    sold_levels: tuple = ()             # ((price, units sold at it), ...) over the window
    new_levels: tuple = ()              # ((price, units newly listed at it), ...)
    level_days: float | None = None     # how long those two have been recorded

    def share_sold_at(self, price: int) -> float | None:
        """Share of observed sales that went for `price` or more (within 3%). None = not enough to say."""
        total = sum(q for _, q in self.sold_levels)
        if not self.level_days or total < 1:
            return None
        return sum(q for p, q in self.sold_levels if p >= price * AT_PRICE) / total

    def new_per_day_at(self, price: int) -> float:
        """Units a day that other sellers newly list AT `price` (within 3% under it).

        At one price the newest listing sells first, so these jump the queue.
        """
        if not self.level_days:
            return 0.0
        return sum(q for p, q in self.new_levels if price * AT_PRICE <= p <= price) / self.level_days


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
    list_hours: int | None = None       # suggested listing duration; None for a vendor sale

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
                  min_span_hours: float = 6.0, sold_levels: dict | None = None,
                  new_levels: dict | None = None, level_days: float | None = None,
                  junk_multiple: float | None = None) -> History:
    """Summarise an item's hourly rows.

    points: (time in hours, market price or None, units sold), oldest first.
    span_hours: how long the HOUSE has been observed in the window - an item
    missing from a snapshot was out of stock, which still counts as time.
    """
    priced = [(t, p) for t, p, _ in points if p]
    total_sold = sum(s for _, _, s in points)
    sold_per_day = total_sold / (span_hours / 24) if span_hours >= min_span_hours else None
    levels = {}
    if level_days and level_days >= MIN_LEVEL_DAYS:
        levels = {"sold_levels": tuple(sorted((sold_levels or {}).items())),
                  "new_levels": tuple(sorted((new_levels or {}).items())), "level_days": level_days}
    if not priced:
        return History(sold_per_day=sold_per_day, total_sold=total_sold, **levels)

    prices = [p for _, p in priced]
    ref = median(prices)
    if junk_multiple and levels:
        # A junk listing that ran out can look like a sale: discard "sales" far above the typical price.
        kept = tuple((p, q) for p, q in levels["sold_levels"] if p <= ref * junk_multiple)
        dropped = sum(q for _, q in levels["sold_levels"]) - sum(q for _, q in kept)
        if dropped > 0:
            levels["sold_levels"] = kept
            total_sold = max(0.0, total_sold - dropped)
            if sold_per_day is not None:
                sold_per_day = total_sold / (span_hours / 24)
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
                   sold_per_day=sold_per_day, total_sold=total_sold, **levels)


def demand_at(hist: History, price: int) -> tuple[float, float | None, float]:
    """What to expect when listing at `price`: (units a day you sell, share of
    sales that reach that price, undercut pressure).

    Sales at lower prices do not help you, so only those at `price` or above
    count. Of that demand, listings other sellers post at the same price after
    you take theirs first (newest sells first); pressure is their share of it.
    A tenth of the demand is kept as a floor: a buried listing still sells
    occasionally.
    """
    share = hist.share_sold_at(price)
    reach = (hist.sold_per_day or 0.0) * (1.0 if share is None else share)
    if reach <= 0:
        return 0.0, share, 0.0
    incoming = hist.new_per_day_at(price)
    return max(reach - incoming, reach * 0.1), share, incoming / reach


def _deposit(vendor: int | None, qty: int, is_commodity: bool, fees: HouseFees,
             hours: int | None = None) -> int:
    """Deposit put at risk for one listing of `hours` (default: the full
    duration): one stack for stackable goods, one auction per item for gear.

    Checked in game (2026-11-11, Bronze Shortsword at 2h/8h/24h): the deposit
    is a share of vendor value that grows in step with the duration. The 1
    silver minimum is charged PER AUCTION - five swords posted together for 2h
    cost 5s, although the posting screen quoted 2s 84c.
    """
    rate = fees.deposit_rate * (hours or fees.listing_hours) / fees.listing_hours
    if is_commodity:
        return max(fees.min_deposit, round(rate * (vendor or 0) * qty))
    return qty * max(fees.min_deposit, round(rate * (vendor or 0)))


def choose_listing_hours(vendor: int | None, qty: int, is_commodity: bool, fees: HouseFees,
                         hist: History, pressure: float) -> int:
    """How long to list for.

    The deposit costs the same per hour at every duration and comes back when
    the item sells, so the longest listing loses the least on average - a
    short one simply expires more often. The exception is a trade you may
    have to reprice: cancelling forfeits the deposit, so a shorter listing
    caps what you can lose. Where the 1s minimum applies either way, longer
    is free.
    """
    full = fees.listing_hours
    if full <= SHORT_LISTING:
        return full
    if _deposit(vendor, qty, is_commodity, fees, SHORT_LISTING) == _deposit(vendor, qty, is_commodity, fees):
        return full
    falling = hist.trend_per_day is not None and hist.trend_per_day < -0.05
    jumpy = hist.cv is not None and hist.cv > 0.25
    return SHORT_LISTING if falling or jumpy or pressure >= 0.5 else full


def expected_expiries(days_to_sell: float, listing_hours: float) -> float:
    """How many times a listing is expected to run out before it sells.

    Treats the sale as equally likely in any hour (so `days_to_sell` is an
    average, not a promise): a listing expected to take a day still has a 37%
    chance of outlasting a 24h listing.
    """
    if days_to_sell <= 0:
        return 0.0
    survive = math.exp(-listing_hours / (days_to_sell * 24))
    return survive / (1 - survive)


def assess_risk(strategy: str, days_to_sell: float, hist: History, share: float | None = None,
                pressure: float = 0.0) -> tuple[int, str, list[str]]:
    """0-100 score from how fast it sells, how much evidence we have, how jumpy
    the price is, which way it is heading, how often buyers pay this much, and
    how much competing stock is posted at the same price."""
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
        reasons.append(f"Few observed sales ({hist.total_sold:.0f})")

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

    if share is not None and share < 0.25:
        score += 10
        reasons.append(f"Only {share:.0%} of sales reach this price")
    if pressure >= 1:
        score += 25
        reasons.append("New listings at this price arrive faster than it sells")
    elif pressure >= 0.5:
        score += 10
        reasons.append("Steady stream of new listings at this price")

    if strategy == "DIP":
        score += 15
        reasons.append("Relies on the price recovering")

    if strategy == "EXPORT":
        score += 10
        reasons.append("Blackwater is a thin market")

    score = min(score, 100)
    return score, _risk_level(score), reasons


def _risk_level(score: int) -> str:
    return "LOW" if score < 30 else "MEDIUM" if score < 55 else "HIGH"


def _finish(strategy: str, qty: int, max_price: int, cost: int, target: int, days: float,
            hist: History, vendor: int | None, is_commodity: bool, fees: HouseFees,
            tun: Tuning, share: float | None = None, pressure: float = 0.0) -> Signal | None:
    # A deposit is refunded on sale and lost on expiry; charge the expected
    # number of expiries before the stock clears.
    hours = choose_listing_hours(vendor, qty, is_commodity, fees, hist, pressure)
    lost_deposits = round(_deposit(vendor, qty, is_commodity, fees, hours) * expected_expiries(days, hours))
    profit = math.floor(qty * target * (1 - fees.cut)) - cost - lost_deposits
    if cost <= 0 or profit < tun.min_profit or profit / cost < tun.min_roi:
        return None
    score, level, reasons = assess_risk(strategy, days, hist, share, pressure)
    if hours < fees.listing_hours:
        reasons.append(f"List for {hours}h, not {fees.listing_hours}h: it may need repricing, "
                       "and cancelling forfeits the deposit")
    return Signal(strategy, qty, max_price, cost, target, profit, round(profit / cost, 4),
                  round(days, 2), score, level, reasons, hours)


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
    most = max(1, int(hist.sold_per_day * tun.horizon_days))
    best: Signal | None = None
    qty = cost = 0
    for i in range(min(len(levels) - 1, tun.max_levels)):
        price, q = levels[i]
        qty += q
        cost += price * q
        if qty > most:
            break  # buying part of a price level leaves the rest undercutting you
        target = min(levels[i + 1][0], hist.ref_price)   # match it: the newest listing sells first
        if target <= price:
            continue
        rate, share, pressure = demand_at(hist, target)   # what sells AT the relist price
        if rate <= 0 or qty > max(1, int(rate * tun.horizon_days)):
            continue
        sig = _finish("FLIP", qty, price, cost, target, qty / rate, hist,
                      vendor, is_commodity, fees, tun, share, pressure)
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
    rate, share, pressure = demand_at(hist, target)
    if rate <= 0:
        return None
    max_units = max(1, int(rate * tun.horizon_days))
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
    # Everything listed under our relist price has to sell before ours does
    # (stock AT our price is behind us: the newest listing sells first).
    ahead = sum(q for p, q in levels if p < target) - qty
    days = max(ahead, 0) / hist.sold_per_day + qty / rate
    return _finish("DIP", qty, max_price, cost, target, days, hist, vendor, is_commodity, fees, tun,
                   share, pressure)


def export_signal(levels: list[Level], dest_levels: list[Level], dest_hist: History,
                  dest_sold_price: float | None, buyer_price: int | None, buyer: str | None,
                  vendor: int | None, is_commodity: bool, dest_fees: HouseFees, tun: Tuning) -> Signal | None:
    """Buy from `levels` (a faction house) and list on Blackwater.

    dest_*: the item's Blackwater order book, history and average price of its
    observed sales there. buyer_price: what the other faction pays on their own
    house (None if not listed there); buyer: that faction's name, or None when
    its house is not tracked.
    """
    if not levels or not dest_hist.sold_per_day or dest_hist.total_sold < tun.export_min_sold:
        return None
    # Asking prices on Blackwater are often wishful, so the target needs a real
    # anchor - what it sold for there, or what the buyers pay at home - and is
    # the lowest of every ceiling we know about.
    if not dest_sold_price and not buyer_price:
        return None
    ceilings = [c for c in (
        dest_levels[0][0] if dest_levels else None,       # match the cheapest there: newest sells first
        dest_sold_price,
        buyer_price - 1 if buyer_price else None,         # nobody pays more than at home
        dest_hist.ref_price if dest_hist.n_snaps >= tun.min_history_snaps else None,
    ) if c]
    target = int(min(ceilings))
    most_to_pay = target * (1 - dest_fees.cut) / (1 + tun.min_roi)
    rate, share, pressure = demand_at(dest_hist, target)
    if rate <= 0:
        return None
    max_units = max(1, int(rate * tun.horizon_days))
    qty = cost = max_price = 0
    for price, q in levels[: tun.max_levels]:
        if price > most_to_pay or qty >= max_units:
            break
        take = min(q, max_units - qty)
        qty += take
        cost += take * price
        max_price = price
    if qty == 0:
        return None
    sig = _finish("EXPORT", qty, max_price, cost, target, qty / rate, dest_hist,
                  vendor, is_commodity, dest_fees, tun, share, pressure)
    if sig is None:
        return None
    deposit = _deposit(vendor, qty, is_commodity, dest_fees, sig.list_hours)
    if sig.profit < deposit:   # must survive one listing expiring unsold
        return None
    score, extra = sig.risk_score, []
    if deposit * 2 > sig.profit:
        score = min(score + 10, 100)
        extra.append("One expired listing costs over half the profit")
    reasons = [f"List on Blackwater ({dest_fees.cut:.0%} cut)"]
    if buyer:
        reasons.append(f"{buyer.capitalize()} pays {format_money(buyer_price)} at home" if buyer_price
                       else f"Not listed on the {buyer.capitalize()} house")
    reasons.append(f"Deposit at stake: {format_money(deposit)} per {sig.list_hours}h listing")
    return replace(sig, risk_score=score, risk_level=_risk_level(score),
                   reasons=reasons + sig.reasons + extra)


def craft_signal(levels: list[Level], hist: History, materials: list[tuple[str, int, int, bool]],
                 makes: float, profession: str | None, vendor: int | None, is_commodity: bool,
                 fees: HouseFees, tun: Tuning, cooldown_days: int | None = None) -> Signal | None:
    """Make the item and list it. materials: (name, count per craft, unit price, bought from a vendor).

    `levels` is the crafted item's own order book (may be empty). The buy
    columns of the signal describe the materials: quantity = units to make,
    price = material cost per unit made.
    """
    if not materials or not makes or not hist.sold_per_day or hist.total_sold < tun.export_min_sold:
        return None
    proven = hist.ref_price if hist.n_snaps >= tun.min_history_snaps else None
    ceilings = [c for c in (levels[0][0] if levels else None, proven) if c]
    if not ceilings:
        return None
    target = int(min(ceilings))                      # match the cheapest: the newest listing sells first
    rate, share, pressure = demand_at(hist, target)
    if rate <= 0:
        return None
    cost_each = sum(count * price for _, count, price, _ in materials) / makes
    qty = min(max(1, int(rate * tun.horizon_days)), tun.craft_max)
    if cooldown_days:
        qty = 1                                      # one per cooldown: it must pay as a single craft
    sig = _finish("CRAFT", qty, math.ceil(cost_each), math.ceil(cost_each * qty), target, qty / rate, hist,
                  vendor, is_commodity, fees, tun, share, pressure)
    if sig is None:
        return None
    parts = ", ".join(f"{count}x {name} ({format_money(price)}{' at a vendor' if from_vendor else ''})"
                      for name, count, price, from_vendor in materials)
    recipe = f"{profession or 'Crafted'}: {parts}" + (f" makes {makes:g}" if makes != 1 else "")
    notes = [recipe]
    if cooldown_days:
        every = "day" if cooldown_days == 1 else f"{cooldown_days} days"
        notes.append(f"Cooldown in the original game: one per character every {every}, so this is for one craft")
    return replace(sig, reasons=notes + sig.reasons)


def disenchant_signal(levels: list[Level], materials: list[tuple[str, float, int | None]],
                      fees: HouseFees, tun: Tuning) -> Signal | None:
    """Buy gear listed below what it disenchants into. materials: (name, average units, unit price or None).

    The sell price of the signal is the average material value of one piece.
    """
    if not levels or not materials:
        return None
    if any(price is None and units >= 0.1 for _, units, price in materials):
        return None                                  # a main material has no price here: cannot value it
    worth = sum(units * price for _, units, price in materials if price)
    net = worth * (1 - fees.cut)
    most_to_pay = net / (1 + tun.disenchant_margin)
    qty = cost = max_price = 0
    for price, q in levels:
        if price > most_to_pay or qty >= tun.disenchant_max:
            break
        take = min(q, tun.disenchant_max - qty)
        qty += take
        cost += take * price
        max_price = price
    profit = math.floor(qty * net) - cost
    if qty == 0 or profit < tun.min_profit:
        return None
    reasons = [
        "Disenchants into about " + ", ".join(f"{units:.2g}x {name}" for name, units, _ in materials) + " each",
        "Needs an enchanter; results vary piece to piece",
        "Uses the original game's disenchant table: test a few before buying many",
    ]
    return Signal("DISENCHANT", qty, max_price, cost, round(worth), profit, round(profit / cost, 4), 0.25,
                  20, "LOW", reasons, fees.listing_hours)


def evaluate(levels: list[Level], hist: History, vendor: int | None, vendor_verified: bool,
             is_commodity: bool, fees: HouseFees, tun: Tuning) -> list[Signal]:
    found = (
        vendor_signal(levels, vendor, vendor_verified, tun),
        flip_signal(levels, hist, vendor, is_commodity, fees, tun),
        dip_signal(levels, hist, vendor, is_commodity, fees, tun),
    )
    return [s for s in found if s is not None]
