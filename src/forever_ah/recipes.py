"""What it takes to make each recipe, and the recipe book behind the Professions page.

Skill requirements come from two sources:

* Forever's own client data (the SkillLineAbility table, published per build
  by wago.tools; see collector.resolve_recipe_skills): every recipe's
  profession and the skill levels at which it turns yellow and grey.
* Recipe items (Plans, Patterns, Formulas...) via Wowhead: the exact skill
  needed to learn the recipe, and any specialisation ("Master Axesmith").

Recipes taught by a trainer have no item, and the trainer's requirement is not
in the client, so it is estimated as the yellow level minus ESTIMATE_GAP.
Measured on 40 recipe items on 2026-11-22, the real requirement sat 5 to 44
points below yellow, usually 15 to 25.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from forever_ah.analyse import MAX_VENDOR_SUPPLY
from forever_ah.config import Settings
from forever_ah.professions import COOLDOWN_DAYS, PROFESSIONS, VENDOR_ONLY
from forever_ah.signals import NEW_RECIPE_FROM, house_fees

ESTIMATE_GAP = 20
TIME_FMT = "%Y-%m-%d %H:%M:%S"
RECORD_DAYS = 7
# Recipes made with a tool rather than a profession spell, so absent from the client's
# recipe table: spell -> (skill line, skill needed). Original game's requirement.
TOOL_RECIPES = {19566: (165, 250)}   # Salt Shaker -> Refined Deeprock Salt: Leatherworking 250
# Wowhead source codes for gathering professions, for "what you gather".
GATHERING = {19: "Mining", 17: "Herbalism", 23: "Skinning", 16: "Fishing"}


@dataclass(frozen=True)
class Requirement:
    skill_line: int | None
    need: int | None          # skill needed to learn and make it
    exact: bool               # True when a recipe item states it; False when estimated
    yellow: int | None
    grey: int | None
    spec: str | None          # specialisation it needs, when a recipe item says so
    learned_from: int | None  # the recipe item that teaches it, if any


def requirements(conn: sqlite3.Connection) -> dict[int, Requirement]:
    """recipe spell id -> what making it takes."""
    client = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT spell_id, skill_line, yellow, grey FROM recipe_skills")}
    taught: dict[int, tuple[int, int | None, str | None]] = {}
    for item_id, spell, rank, spec in conn.execute(
            "SELECT item_id, teaches_spell, req_rank, req_spec FROM item_facts WHERE teaches_spell > 0"):
        prev = taught.get(spell)
        if prev is None or (rank or 999) < (prev[1] or 999):   # several items can teach one recipe
            taught[spell] = (item_id, rank, spec)
    out = {}
    for spell in set(client) | set(taught) | set(TOOL_RECIPES):
        line, yellow, grey = client.get(spell, (None, None, None))
        item = taught.get(spell)
        if item and item[1]:
            need, exact = item[1], True
        elif spell in TOOL_RECIPES:
            line, need = TOOL_RECIPES[spell]
            exact = False
        elif yellow:
            need, exact = max(1, yellow - ESTIMATE_GAP), False
        else:
            need, exact = None, False
        out[spell] = Requirement(line, need, exact, yellow, grey, item[2] if item else None, item[0] if item else None)
    return out


def recipe_book(conn: sqlite3.Connection, house_id: int, settings: Settings,
                reqs: dict[int, Requirement] | None = None) -> dict:
    """Everything the Professions page shows for one faction house.

    Prices are the house's own: materials at market price (or the vendor price
    for everyday vendor supplies, as the CRAFT play does), products at the
    lower of the cheapest listing and the typical price.
    """
    tun = settings.tuning
    reqs = requirements(conn) if reqs is None else reqs
    faction = conn.execute("SELECT faction FROM houses WHERE house_id = ?", (house_id,)).fetchone()[0]
    cut = house_fees(settings.fees, faction).cut
    barred = set(settings.unavailable_recipes)

    market = {r[0]: r[1:] for r in conn.execute(
        "SELECT variant_id, min_price, market_price, ref_price, n_snaps, sold_per_day, listings, quantity "
        "FROM market WHERE house_id = ?", (house_id,))}
    base, items = {}, {}
    for variant_id, item_id, bonus in conn.execute("SELECT variant_id, item_id, bonus FROM variants"):
        if not bonus:
            base[item_id] = variant_id
    for item_id, name, quality, item_class, vendor in conn.execute(
            "SELECT item_id, name, quality, item_class, vendor_sell FROM items"):
        items[item_id] = (name, quality, item_class, vendor)
    facts = {r[0]: r[1:] for r in conn.execute(
        "SELECT item_id, name, sources, buy_price, skill, makes, reagents, spell_id FROM item_facts WHERE ok = 1")}

    def listed(item_id: int):
        return market.get(base.get(item_id))

    def unit_price(item_id: int) -> tuple[int | None, bool]:
        f = facts.get(item_id)
        if f and f[2] and f[2] <= MAX_VENDOR_SUPPLY and f[1] and json.loads(f[1]) == VENDOR_ONLY:
            return f[2], True
        m = listed(item_id)
        return (m[1], False) if m and m[1] else (None, False)

    # The strict scorecard (sold net of competition) for each crafted item over the last week.
    newest = conn.execute("SELECT MAX(published_at) FROM snapshots WHERE house_id = ?", (house_id,)).fetchone()[0]
    since = (datetime.strptime(newest, TIME_FMT) - timedelta(days=RECORD_DAYS)).strftime(TIME_FMT) if newest else ""
    record = {r[0]: (r[1], r[2] or 0) for r in conn.execute(
        "SELECT variant_id, COUNT(*), SUM(net_ok) FROM signal_log WHERE house_id = ? AND strategy = 'CRAFT' "
        "AND outcome IN ('ON_TIME', 'LATE', 'MISSED') AND net_ok IS NOT NULL AND issued_at >= ? GROUP BY variant_id",
        (house_id, since))}

    rows, by_prof_record, specs = [], {}, {}
    for item_id, (name, sources, buy, skill, makes, reagents, spell) in facts.items():
        if not reagents or (name or "").lower() in barred:
            continue
        req = reqs.get(spell or -1)
        line = req.skill_line if req and req.skill_line else skill
        prof = PROFESSIONS.get(line)
        if not prof:
            continue   # not a profession recipe (class abilities, card decks, quest combines)
        makes = makes or 1
        mats, cost = [], 0
        for reagent_id, count, mat_name in json.loads(reagents):
            price, from_vendor = unit_price(reagent_id)
            mats.append([mat_name, count, price, int(from_vendor)])
            cost = None if cost is None or price is None else cost + count * price
        cost = round(cost / makes) if cost is not None else None
        variant = base.get(item_id)
        m = market.get(variant)
        cheapest = m[0] if m else None
        typical = m[2] if m and m[3] and m[3] >= tun.min_history_snaps else None
        sell = min([x for x in (cheapest, typical) if x], default=None)
        info = items.get(item_id, (name, None, None, None))
        vendor = info[3] or None
        learn = None
        if req and req.learned_from:
            book = listed(req.learned_from)
            learn = {"i": req.learned_from, "n": (items.get(req.learned_from) or (None,))[0],
                     "p": book[0] if book else None}
        if req and req.spec:
            specs.setdefault(prof, set()).add(req.spec)
        rec = record.get(variant)
        if rec:
            agg = by_prof_record.setdefault(prof, [0, 0])
            agg[0] += rec[0]
            agg[1] += rec[1]
        rows.append({
            "s": spell, "v": variant, "i": item_id, "n": info[0] or name, "q": info[1], "c": info[2], "p": prof,
            "need": req.need if req else None, "ex": int(bool(req and req.exact)),
            "y": req.yellow if req else None, "g": req.grey if req else None, "sp": req.spec if req else None,
            "learn": learn, "cd": COOLDOWN_DAYS.get(item_id), "new": int(bool(spell and spell >= NEW_RECIPE_FROM)),
            "mk": makes, "mats": mats, "cost": cost,
            "sell": sell, "cheap": cheapest, "typ": typical, "vend": vendor,
            "net": round(sell * (1 - cut) - cost) if sell and cost is not None else None,
            "vnet": vendor - cost if vendor and cost is not None else None,
            "spd": round(m[4], 1) if m and m[4] else 0, "lst": m[5] if m else 0, "qty": m[6] if m else 0,
            "rec": list(rec) if rec else None,
        })

    # What each gathering profession brings in: the items it gathers, by gold traded a day.
    gather: dict[str, list] = {}
    for item_id, f in facts.items():
        codes = set(json.loads(f[1])) if f[1] else set()
        m = listed(item_id)
        if not m or not m[4]:
            continue
        price = m[2] or m[1]
        for code, prof in GATHERING.items():
            if code in codes and price:
                gather.setdefault(prof, []).append(
                    [base[item_id], (items.get(item_id) or (f[0],))[0] or f[0], price, round(m[4], 1), m[6], m[2]])
    for prof, lst in gather.items():
        lst.sort(key=lambda x: -x[2] * x[3])
        del lst[25:]

    return {"recipes": rows, "gather": gather, "records": by_prof_record,
            "specs": {k: sorted(v) for k, v in specs.items()}, "gap": ESTIMATE_GAP, "cut": cut,
            "record_days": RECORD_DAYS}
