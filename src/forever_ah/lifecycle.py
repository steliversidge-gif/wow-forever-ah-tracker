"""Work out what happened to listings between two snapshots. Pure functions.

Blizzard's API never reports a sale, so sales are inferred. The retail tracker
treated "disappeared" as "sold", which cancel-and-relist scans inflate badly.
This version uses three stronger signals:

1. Partial fills. A commodity listing whose quantity shrinks was bought from:
   you cannot partially cancel. This is a certain sale.
2. Cheapest-first buying. Commodities are bought cheapest first, so a listing
   that vanished while a CHEAPER one survived untouched was not bought - it was
   cancelled or expired. Gear (one-per-listing auctions) gets the same test with
   a tolerance, since buyers there do not always take the very cheapest.
3. Time left. A listing last seen with more time remaining than the gap between
   snapshots cannot have expired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

# Least time a listing can have left, in hours, for each time_left bucket.
MIN_REMAINING = {"SHORT": 0.0, "MEDIUM": 0.5, "LONG": 2.0, "VERY_LONG": 12.0}


class Listing(NamedTuple):
    variant_id: int
    is_commodity: int
    unit_price: int
    quantity: int
    time_left: str


@dataclass(slots=True)
class Flow:
    sold_qty: int = 0
    sold_value: int = 0
    cancelled_qty: int = 0
    expired_qty: int = 0
    new_qty: int = 0


@dataclass(slots=True)
class Diff:
    new: list[int] = field(default_factory=list)
    changed: list[int] = field(default_factory=list)   # quantity or time_left moved
    gone: list[int] = field(default_factory=list)
    flows: dict[int, Flow] = field(default_factory=dict)


def classify_gone(listing: Listing, floor: int | None, gap_hours: float | None,
                  max_gap_hours: float, tolerance: float) -> str:
    """SOLD | CANCELLED | EXPIRED | UNKNOWN for a listing that disappeared.

    `floor` is the cheapest price among same-item listings that survived (or,
    if none survived, among listings that newly appeared).
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
                  *, first: bool = False, max_gap_hours: float = 3.0, tolerance: float = 1.2) -> Diff:
    """Compare the previous live listings with the current ones (both keyed by auction id)."""
    diff = Diff()
    flows = diff.flows

    def flow(variant_id: int) -> Flow:
        f = flows.get(variant_id)
        if f is None:
            f = flows[variant_id] = Flow()
        return f

    survivor_floor: dict[int, int] = {}
    new_floor: dict[int, int] = {}
    for auction_id, now in curr.items():
        was = prev.get(auction_id)
        if was is None:
            diff.new.append(auction_id)
            if not first:
                flow(now.variant_id).new_qty += now.quantity
            p = new_floor.get(now.variant_id)
            if p is None or now.unit_price < p:
                new_floor[now.variant_id] = now.unit_price
            continue
        p = survivor_floor.get(was.variant_id)
        if p is None or was.unit_price < p:
            survivor_floor[was.variant_id] = was.unit_price
        if now.quantity != was.quantity or now.time_left != was.time_left:
            diff.changed.append(auction_id)
            bought = was.quantity - now.quantity
            if bought > 0:
                f = flow(was.variant_id)
                f.sold_qty += bought
                f.sold_value += bought * was.unit_price

    for auction_id, was in prev.items():
        if auction_id in curr:
            continue
        diff.gone.append(auction_id)
        floor = survivor_floor.get(was.variant_id, new_floor.get(was.variant_id))
        outcome = classify_gone(was, floor, gap_hours, max_gap_hours, tolerance)
        f = flow(was.variant_id)
        if outcome == "SOLD":
            f.sold_qty += was.quantity
            f.sold_value += was.quantity * was.unit_price
        elif outcome == "CANCELLED":
            f.cancelled_qty += was.quantity
        elif outcome == "EXPIRED":
            f.expired_qty += was.quantity
    return diff
