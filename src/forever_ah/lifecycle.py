"""Work out what happened to listings between two snapshots. Pure functions.

Blizzard's API never reports a sale, so sales are inferred. Treating every
disappearance as a sale is badly wrong: measured on the Horde house on
2026-11-11, a third of all listings with 30 minutes to 2 hours left vanished
within the hour whether or not anyone could have wanted them - they expired -
and most expired gear came straight back at the same price.

The evidence used, strongest first:

1. Partial fills. A stack whose quantity shrinks was bought from: you cannot
   partially cancel. This is a certain sale.
2. The buying queue. Stackable goods are bought cheapest first and, at the
   same price, NEWEST listing first (checked: 3,600 of 3,600 part-bought
   stacks were newer than the untouched stacks beside them). So a vanished
   stack that sat ahead of a part-bought one was bought, and one that sat
   behind an untouched or part-bought stack was not.
3. Reposts. A vanished listing with an identical fresh one (same item,
   quantity and price) was withdrawn or expired and put back, not sold.
4. Time left. A listing cannot expire with more time left than the gap between
   snapshots. One that could have expired is only PARTLY counted as sold, in
   proportion to how likely a sale was against expiry or a quiet withdrawal.

Gear (one-per-listing auctions) has no strict queue - buyers pick by stats -
so rule 2 becomes "within 20% of the cheapest survivor".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

# Hours a listing can have left in each time_left bucket: (least, most).
REMAINING = {"SHORT": (0.0, 0.5), "MEDIUM": (0.5, 2.0), "LONG": (2.0, 12.0), "VERY_LONG": (12.0, 24.0)}
MIN_REMAINING = {k: v[0] for k, v in REMAINING.items()}


class Listing(NamedTuple):
    variant_id: int
    is_commodity: int
    unit_price: int
    quantity: int
    time_left: str


class Rates(NamedTuple):
    """Hourly base rates for a listing at the front of its queue, measured on
    the Horde house, 2026-11-11 (see the module docstring). They only matter
    for a disappearance that nothing else explains."""
    stack_sale: float = 0.17     # chance a front-of-queue stack is bought out in an hour
    gear_sale: float = 0.02      # the same for a competitively priced piece of gear
    stack_cancel: float = 0.024  # chance it is withdrawn without an identical repost
    gear_cancel: float = 0.01
    stack_repost: float = 0.60   # share of expired listings that come back identical
    gear_repost: float = 0.70


@dataclass(slots=True)
class Flow:
    sold_qty: float = 0
    sold_value: float = 0
    cancelled_qty: float = 0
    expired_qty: float = 0
    new_qty: int = 0
    sold_levels: dict[int, float] = field(default_factory=dict)   # price -> units sold at it
    new_levels: dict[int, int] = field(default_factory=dict)      # price -> units newly listed at it

    def sold(self, qty: float, price: int) -> None:
        self.sold_qty += qty
        self.sold_value += qty * price
        self.sold_levels[price] = self.sold_levels.get(price, 0) + qty


@dataclass(slots=True)
class RateCounts:
    """Tallies from one diff that the base rates are re-measured from."""
    behind_n: int = 0
    behind_gone: int = 0
    front_n: int = 0
    front_gone: int = 0
    lapsed_n: int = 0
    lapsed_reposted: int = 0


@dataclass(slots=True)
class Diff:
    new: list[int] = field(default_factory=list)
    changed: list[int] = field(default_factory=list)   # quantity or time_left moved
    gone: list[int] = field(default_factory=list)
    flows: dict[int, Flow] = field(default_factory=dict)
    counts: dict[str, RateCounts] = field(default_factory=dict)   # "stack" / "gear"; empty unless ~hourly


def rates_from_counts(stack: RateCounts | None, gear: RateCounts | None, default: Rates = Rates(),
                      min_sample: int = 2000) -> Rates:
    """Base rates measured from pooled tallies, keeping the default wherever the sample is thin."""
    out = default._asdict()
    for kind, c in (("stack", stack), ("gear", gear)):
        if c is None:
            continue
        cancel = out[f"{kind}_cancel"]
        if c.behind_n >= min_sample:
            cancel = out[f"{kind}_cancel"] = min(0.5, max(0.001, c.behind_gone / c.behind_n))
        if c.front_n >= min_sample:
            out[f"{kind}_sale"] = min(0.9, max(0.002, c.front_gone / c.front_n - cancel))
        if c.lapsed_n >= min_sample // 4:
            out[f"{kind}_repost"] = min(0.95, c.lapsed_reposted / c.lapsed_n)
    return Rates(**out)


def expiry_chance(time_left: str, gap_hours: float) -> float:
    """Chance a listing last seen in this time_left bucket ran out during the gap."""
    least, most = REMAINING.get(time_left, REMAINING["LONG"])
    return min(1.0, max(0.0, (gap_hours - least) / (most - least)))


def sale_weight(listing: Listing, gap_hours: float, rates: Rates = Rates()) -> float:
    """How much of an unexplained front-of-queue disappearance to count as sold (0-1).

    Three things could have happened: it sold, it expired and was not reposted
    identically, or it was quietly withdrawn. The weight is the sale's share.
    """
    if listing.is_commodity:
        sale, cancel, repost = rates.stack_sale, rates.stack_cancel, rates.stack_repost
    else:
        sale, cancel, repost = rates.gear_sale, rates.gear_cancel, rates.gear_repost
    expiry = expiry_chance(listing.time_left, gap_hours)
    alive = 1.0 - expiry
    sold = alive * (1 - (1 - sale) ** gap_hours)
    withdrawn = alive * (1 - (1 - cancel) ** gap_hours)
    lapsed = expiry * (1 - repost)
    total = sold + withdrawn + lapsed
    return sold / total if total > 0 else 0.0


def classify_gone(listing: Listing, floor: int | None, gap_hours: float | None,
                  max_gap_hours: float, tolerance: float) -> str:
    """SOLD | CANCELLED | EXPIRED | UNKNOWN for a listing that disappeared, on price alone.

    `floor` is the cheapest price among same-item listings that survived (or,
    if none survived, among listings that newly appeared). SOLD here means
    "priced where a buyer would have taken it"; diff_snapshot then discounts
    it for reposts and possible expiry.
    """
    if gap_hours is None or gap_hours > max_gap_hours:
        return "UNKNOWN"  # collector was down too long to say anything honest
    if listing.time_left == "SHORT":
        return "EXPIRED"  # under 30 minutes left: expiry is the likeliest end
    if floor is None:
        competitive = True
    elif listing.is_commodity:
        competitive = listing.unit_price <= floor
    else:
        competitive = listing.unit_price <= floor * tolerance
    if competitive:
        return "SOLD"
    could_expire = gap_hours >= MIN_REMAINING.get(listing.time_left, 0.0)
    return "EXPIRED" if could_expire else "CANCELLED"


def diff_snapshot(prev: dict[int, Listing], curr: dict[int, Listing], gap_hours: float | None,
                  *, first: bool = False, max_gap_hours: float = 3.0, tolerance: float = 1.2,
                  rates: Rates = Rates()) -> Diff:
    """Compare the previous live listings with the current ones (both keyed by auction id)."""
    diff = Diff()
    flows = diff.flows

    def flow(variant_id: int) -> Flow:
        f = flows.get(variant_id)
        if f is None:
            f = flows[variant_id] = Flow()
        return f

    # A stack's place in the buying queue: cheapest first, then newest (highest id) first.
    survivor_floor: dict[int, int] = {}
    untouched_floor: dict[int, int] = {}              # cheapest survivor nobody bought from
    new_floor: dict[int, int] = {}
    frontier: dict[int, tuple[int, int]] = {}         # deepest queue place that was bought from
    first_untouched: dict[int, tuple[int, int]] = {}  # first surviving stack nobody bought from
    fresh: dict[tuple[int, int, int], int] = {}       # (item, quantity, price) -> new listings not yet matched
    for auction_id, now in curr.items():
        was = prev.get(auction_id)
        if was is None:
            diff.new.append(auction_id)
            if not first:
                f = flow(now.variant_id)
                f.new_qty += now.quantity
                f.new_levels[now.unit_price] = f.new_levels.get(now.unit_price, 0) + now.quantity
            p = new_floor.get(now.variant_id)
            if p is None or now.unit_price < p:
                new_floor[now.variant_id] = now.unit_price
            key = (now.variant_id, now.quantity, now.unit_price)
            fresh[key] = fresh.get(key, 0) + 1
            continue
        variant = was.variant_id
        p = survivor_floor.get(variant)
        if p is None or was.unit_price < p:
            survivor_floor[variant] = was.unit_price
        place = (was.unit_price, -auction_id)
        bought = was.quantity - now.quantity
        if bought > 0:
            flow(variant).sold(bought, was.unit_price)
            if variant not in frontier or place > frontier[variant]:
                frontier[variant] = place
        else:
            p = untouched_floor.get(variant)
            if p is None or was.unit_price < p:
                untouched_floor[variant] = was.unit_price
            if was.is_commodity and (variant not in first_untouched or place < first_untouched[variant]):
                first_untouched[variant] = place
        if now.quantity != was.quantity or now.time_left != was.time_left:
            diff.changed.append(auction_id)

    known = gap_hours is not None and gap_hours <= max_gap_hours
    relisted = dict(fresh)   # untouched copy: `fresh` is used up as reposts are matched below
    for auction_id, was in prev.items():
        if auction_id in curr:
            continue
        diff.gone.append(auction_id)
        variant, qty = was.variant_id, was.quantity
        f = flow(variant)
        if not known:
            continue  # collector was down too long to say anything honest
        place = (was.unit_price, -auction_id)

        outcome = None
        if was.is_commodity:
            if variant in frontier:
                if place < frontier[variant]:        # buyers worked past it: certainly bought
                    f.sold(qty, was.unit_price)
                    continue
                outcome = "BEHIND"                   # a part-bought stack was ahead of it
            elif variant in first_untouched and place > first_untouched[variant]:
                outcome = "BEHIND"                   # an untouched stack was ahead of it
        if outcome is None:
            floor = survivor_floor.get(variant, new_floor.get(variant))
            outcome = classify_gone(was, floor, gap_hours, max_gap_hours, tolerance)
        if outcome == "BEHIND":
            could_expire = gap_hours >= MIN_REMAINING.get(was.time_left, 0.0)
            outcome = "EXPIRED" if could_expire else "CANCELLED"

        if outcome == "SOLD":
            key = (variant, qty, was.unit_price)
            if fresh.get(key):                       # an identical listing appeared: reposted, not sold
                fresh[key] -= 1
                f.cancelled_qty += qty
                continue
            weight = sale_weight(was, gap_hours, rates)
            if weight > 0:
                f.sold(qty * weight, was.unit_price)
            if expiry_chance(was.time_left, gap_hours) > 0:
                f.expired_qty += qty * (1 - weight)
            else:
                f.cancelled_qty += qty * (1 - weight)
        elif outcome == "CANCELLED":
            f.cancelled_qty += qty
        elif outcome == "EXPIRED":
            f.expired_qty += qty

    if known and 0.75 <= gap_hours <= 1.5:   # the base rates are per hour, so only tally hourly diffs
        counts = diff.counts = {"stack": RateCounts(), "gear": RateCounts()}
        for auction_id, was in prev.items():
            c = counts["stack" if was.is_commodity else "gear"]
            floor = untouched_floor.get(was.variant_id)
            reach = floor if was.is_commodity or floor is None else floor * tolerance
            behind = floor is not None and was.unit_price > reach
            gone = auction_id not in curr
            reposted = gone and (was.variant_id, was.quantity, was.unit_price) in relisted
            if was.time_left in ("LONG", "VERY_LONG"):          # cannot have expired
                if behind:
                    c.behind_n += 1
                    c.behind_gone += gone
                else:
                    c.front_n += 1
                    c.front_gone += gone and not reposted
            elif was.time_left == "SHORT" and not behind:       # certain to have run out
                c.lapsed_n += 1
                c.lapsed_reposted += reposted
    return diff
