from forever_ah.config import Tuning
from forever_ah.lifecycle import Listing, classify_gone, diff_snapshot
from forever_ah.pricing import format_money, market_price, median, price_levels
from forever_ah.signals import History, HouseFees, build_history, dip_signal, flip_signal, vendor_signal

FEES = HouseFees(cut=0.05, deposit_rate=0.30, min_deposit=100, listing_hours=24)
TUN = Tuning()


def L(variant=1, commodity=1, price=100, qty=10, left="LONG"):
    return Listing(variant, commodity, price, qty, left)


# -- pricing ------------------------------------------------------------------
def test_price_levels_merges_and_sorts():
    assert price_levels([(200, 1), (100, 2), (200, 3)]) == [(100, 2), (200, 4)]


def test_market_price_ignores_tiny_lowball():
    levels = [(1, 1), (100, 1000), (200, 3000)]
    assert 95 <= market_price(levels, 0.15) <= 100


def test_market_price_empty():
    assert market_price([], 0.15) is None


def test_median_and_money():
    assert median([3, 1, 2]) == 2
    assert median([1, 2, 3, 4]) == 2.5
    assert format_money(123456) == "12g 34s 56c"
    assert format_money(10000) == "1g"
    assert format_money(5) == "5c"
    assert format_money(None) == "-"


# -- lifecycle ----------------------------------------------------------------
def test_partial_fill_is_a_certain_sale():
    d = diff_snapshot({1: L(qty=10)}, {1: L(qty=4)}, 1.0)
    assert d.flows[1].sold_qty == 6 and d.flows[1].sold_value == 600
    assert d.changed == [1] and not d.gone


def test_commodity_gone_while_cheaper_survives_is_cancelled_not_sold():
    prev = {1: L(price=100), 2: L(price=150)}
    d = diff_snapshot(prev, {1: L(price=100)}, 1.0)
    assert d.flows[1].sold_qty == 0 and d.flows[1].cancelled_qty == 10


def test_cheapest_commodity_gone_is_probably_sold():
    # Nothing proves it: it was at the front and could not have expired, so it
    # counts as sold less the small chance it was quietly withdrawn.
    prev = {1: L(price=100), 2: L(price=150)}
    f = diff_snapshot(prev, {2: L(price=150)}, 1.0).flows[1]
    assert 8.5 < f.sold_qty < 9 and abs(f.sold_qty + f.cancelled_qty - 10) < 1e-9
    assert abs(f.sold_value - f.sold_qty * 100) < 1e-9


def test_stack_ahead_of_a_part_bought_one_was_certainly_sold():
    prev = {1: L(price=100, left="SHORT"), 2: L(price=150, qty=10)}
    f = diff_snapshot(prev, {2: L(price=150, qty=4)}, 1.0).flows[1]
    assert f.sold_qty == 16 and f.sold_value == 10 * 100 + 6 * 150


def test_newest_listing_at_a_price_sells_first():
    # Same price: the higher auction id is newer and at the front of the queue.
    older_gone = diff_snapshot({1: L(), 2: L()}, {2: L()}, 1.0).flows[1]
    assert older_gone.sold_qty == 0 and older_gone.cancelled_qty == 10    # the newer one was untouched
    newer_gone = diff_snapshot({1: L(), 2: L()}, {1: L()}, 1.0).flows[1]
    assert newer_gone.sold_qty > 8
    behind_part_bought = diff_snapshot({1: L(), 2: L()}, {2: L(qty=3)}, 1.0).flows[1]
    assert behind_part_bought.sold_qty == 7                               # only the part-fill counts


def test_identical_repost_is_not_a_sale():
    d = diff_snapshot({1: L(commodity=0, price=5000, qty=1)}, {2: L(commodity=0, price=5000, qty=1)}, 1.0)
    assert d.flows[1].sold_qty == 0 and d.flows[1].cancelled_qty == 1
    # one fresh listing explains one disappearance, not two
    two = diff_snapshot({1: L(commodity=0, price=5000, qty=1), 2: L(commodity=0, price=5000, qty=1)},
                        {3: L(commodity=0, price=5000, qty=1)}, 1.0).flows[1]
    assert 0 < two.sold_qty < 1 and two.cancelled_qty >= 1


def test_listing_that_may_have_expired_counts_only_partly_as_sold():
    from forever_ah.lifecycle import expiry_chance, sale_weight

    assert expiry_chance("MEDIUM", 1.0) == 1 / 3 and expiry_chance("LONG", 1.0) == 0
    assert expiry_chance("SHORT", 1.0) == 1
    gear_long = sale_weight(L(commodity=0, qty=1, left="LONG"), 1.0)
    gear_medium = sale_weight(L(commodity=0, qty=1, left="MEDIUM"), 1.0)
    stack_medium = sale_weight(L(left="MEDIUM"), 1.0)
    assert 0.6 < gear_long < 0.7          # gear rarely sells in a given hour; some of these are withdrawals
    assert gear_medium < 0.15             # with under 2h left, expiry is far likelier than a sale
    assert gear_medium < stack_medium < sale_weight(L(left="LONG"), 1.0)
    assert sale_weight(L(left="SHORT"), 1.0) == 0


def test_short_time_left_is_expired():
    assert classify_gone(L(left="SHORT"), None, 1.0, 3.0, 1.2) == "EXPIRED"


def test_uncompetitive_medium_listing_could_have_expired():
    assert classify_gone(L(price=500, left="MEDIUM"), 100, 1.0, 3.0, 1.2) == "EXPIRED"
    assert classify_gone(L(price=500, left="VERY_LONG"), 100, 1.0, 3.0, 1.2) == "CANCELLED"


def test_long_gap_is_unknown():
    d = diff_snapshot({1: L()}, {}, 5.0)
    assert d.gone == [1] and d.flows[1].sold_qty == 0 and d.flows[1].cancelled_qty == 0


def test_gear_tolerance():
    assert classify_gone(L(commodity=0, price=115, qty=1), 100, 1.0, 3.0, 1.2) == "SOLD"
    assert classify_gone(L(commodity=0, price=130, qty=1, left="VERY_LONG"), 100, 1.0, 3.0, 1.2) == "CANCELLED"


def test_cancel_and_relist_lower_is_not_a_sale():
    # Sole listing vanishes and a cheaper one appears: the seller reposted.
    d = diff_snapshot({1: L(price=200, left="VERY_LONG")}, {2: L(price=150)}, 1.0)
    assert d.flows[1].sold_qty == 0 and d.flows[1].cancelled_qty == 10 and d.flows[1].new_qty == 10


def test_first_snapshot_counts_nothing_as_new():
    d = diff_snapshot({}, {1: L()}, None, first=True)
    assert d.new == [1] and not d.flows


# -- history / signals --------------------------------------------------------
def test_build_history_basics():
    points = [(float(h), 100, 2) for h in range(48)]
    h = build_history(points, span_hours=47)
    assert h.ref_price == 100 and h.n_snaps == 48 and h.total_sold == 96
    assert 48 < h.sold_per_day < 50
    assert abs(h.trend_per_day) < 1e-9 and h.cv == 0


def test_build_history_detects_falling_price():
    points = [(float(h), 200 - 2 * h, 1) for h in range(48)]
    assert build_history(points, 47).trend_per_day < -0.15


def test_build_history_too_short_for_sales_rate():
    assert build_history([(0.0, 100, 5), (1.0, 100, 5)], span_hours=1).sold_per_day is None


def test_vendor_signal():
    s = vendor_signal([(50, 4), (90, 1), (120, 10)], vendor=100, verified=True, tun=TUN)
    assert s.buy_qty == 5 and s.buy_cost == 290 and s.profit == 210 and s.risk_level == "NONE"
    assert vendor_signal([(120, 10)], 100, True, TUN) is None


def test_flip_signal_buys_under_the_gap_and_caps_at_typical_price():
    hist = History(n_snaps=48, ref_price=1000, trend_per_day=0.0, cv=0.05, sold_per_day=50, total_sold=100)
    s = flip_signal([(500, 5), (2000, 100)], hist, vendor=10, is_commodity=True, fees=FEES, tun=TUN)
    assert s.buy_qty == 5 and s.buy_cost == 2500
    assert s.sell_price == 1000                      # capped at typical, not 2000
    cheap_gap = flip_signal([(500, 5), (900, 100)], hist, vendor=10, is_commodity=True, fees=FEES, tun=TUN)
    assert cheap_gap.sell_price == 900               # match the next price: the newest listing sells first
    assert s.profit == int(5 * 1000 * 0.95) - 2500
    assert s.risk_level == "LOW"


def test_flip_signal_never_buys_more_than_a_day_of_sales():
    hist = History(n_snaps=48, ref_price=1000, sold_per_day=3, total_sold=20)
    assert flip_signal([(500, 50), (2000, 100)], hist, 10, True, FEES, TUN) is None


def test_flip_needs_history():
    hist = History(n_snaps=2, ref_price=1000, sold_per_day=50, total_sold=100)
    assert flip_signal([(500, 5), (2000, 100)], hist, 10, True, FEES, TUN) is None


def test_no_flip_when_gap_does_not_cover_the_cut():
    hist = History(n_snaps=48, ref_price=1000, sold_per_day=50, total_sold=100)
    assert flip_signal([(980, 5), (1000, 100)], hist, 10, True, FEES, TUN) is None


def test_dip_signal_is_riskier_and_accounts_for_queue():
    hist = History(n_snaps=48, ref_price=1000, trend_per_day=-0.08, cv=0.3, sold_per_day=20, total_sold=100)
    s = dip_signal([(600, 10), (650, 100), (1100, 50)], hist, 10, True, FEES, TUN)
    assert s is not None and s.strategy == "DIP"
    assert s.buy_qty == 20 and s.sell_price == 900
    assert s.days_to_sell > 1                         # 90 other cheap units are ahead in the queue
    assert s.risk_level == "HIGH"
    assert any("drifting down" in r for r in s.reasons)
    # a price still falling fast is not a dip
    sliding = History(n_snaps=48, ref_price=1000, trend_per_day=-0.2, cv=0.3, sold_per_day=20, total_sold=100)
    assert dip_signal([(600, 10), (650, 100), (1100, 50)], sliding, 10, True, FEES, TUN) is None


def test_thin_history_keeps_recent_and_preserves_sales_total():
    from forever_ah.export import thin_history

    points = [(h * 3600, 100 + h, 90 + h, 1, 50) for h in range(100)]
    thin = thin_history(points)
    assert sum(p[3] for p in thin) == 100
    assert thin[-49:] == points[-49:]
    assert len(thin) < len(points)


def test_parse_wowhead_item():
    from forever_ah.blizzard import parse_wowhead_item

    xml = ('<?xml version="1.0"?><wowhead><item id="277072"><name><![CDATA[Al\'Aketh Pants]]></name>'
           '<level>11</level><quality id="2">Uncommon</quality><class id="4"><![CDATA[Armor]]></class>'
           '<subclass id="2"><![CDATA[Leather Armor]]></subclass>'
           '<json><![CDATA["id":277072,"level":11,"reqlevel":6]]></json>'
           '<jsonEquip><![CDATA["buyprice":782,"reqlevel":6,"sellprice":156]]></jsonEquip></item></wowhead>')
    item = parse_wowhead_item(xml)
    assert item["name"] == "Al'Aketh Pants"
    assert item["quality"]["name"] == "Uncommon"
    assert item["item_class"]["name"] == "Armor"
    assert (item["level"], item["required_level"], item["sell_price"]) == (11, 6, 156)
    assert parse_wowhead_item("<wowhead><error>Item not found!</error></wowhead>") is None


# -- export via Blackwater ------------------------------------------------------
BW = HouseFees(cut=0.15, deposit_rate=3.0, min_deposit=100, listing_hours=24)
BW_HIST = History(n_snaps=30, ref_price=1000, sold_per_day=20.0, total_sold=40)


def export(levels=((500, 10),), dest_levels=((1200, 5),), hist=BW_HIST, sold_price=1000.0,
           buyer_price=1100, buyer="alliance", vendor=10, commodity=True):
    from forever_ah.signals import export_signal
    return export_signal(list(levels), list(dest_levels), hist, sold_price, buyer_price, buyer,
                         vendor, commodity, BW, TUN)


def test_export_prices_at_the_lowest_ceiling_and_nets_the_cut():
    sig = export()
    assert sig.strategy == "EXPORT"
    assert sig.sell_price == 1000                       # what it sold for there, below both asks
    assert (sig.buy_qty, sig.buy_max_price, sig.buy_cost) == (10, 500, 5000)
    # 15% cut, less the 3s deposit times the expected expiries (0.58) of a sale that is
    # predicted to take half a day and stretched to a full one, as exports run late
    assert sig.profit == 10 * 850 - 5000 - 175
    assert sig.reasons[0].startswith("List on Blackwater")
    assert "Alliance pays 11s at home" in sig.reasons


def test_export_never_asks_more_than_buyers_pay_at_home():
    assert export(buyer_price=900).sell_price == 899
    assert export(buyer_price=550) is None              # no margin left after the cut


def test_export_matches_the_cheapest_blackwater_listing():
    assert export(dest_levels=((800, 5),)).sell_price == 800   # newest listing at a price sells first


def test_export_needs_real_blackwater_sales():
    assert export(hist=History(n_snaps=30, ref_price=1000, sold_per_day=None, total_sold=0)) is None
    assert export(hist=History(n_snaps=30, ref_price=1000, sold_per_day=2.0, total_sold=2)) is None
    assert export(sold_price=None, buyer_price=None) is None   # only asking prices to go on


def test_export_buys_no_more_than_a_day_of_blackwater_sales():
    sig = export(levels=((500, 100),))
    assert sig.buy_qty == 20


def test_export_skips_items_where_one_lost_deposit_eats_the_profit():
    # vendor 400 -> a 24h Blackwater deposit of 12s per unit against 3s 50c profit per unit
    assert export(vendor=400, commodity=False, levels=((500, 1),)) is None


def test_export_item_missing_from_buyers_house():
    sig = export(buyer_price=None)
    assert sig.sell_price == 1000
    assert "Not listed on the Alliance house" in sig.reasons


def test_deposit_matches_what_the_game_charged():
    from forever_ah.signals import _deposit

    horde_24h = HouseFees(cut=0.05, deposit_rate=0.60, min_deposit=100, listing_hours=24)
    # Bronze Shortsword, vendor value 11s 36c: 6s 81c for one, 34s 8c for five
    assert _deposit(1136, 1, False, horde_24h) in (681, 682)
    assert _deposit(1136, 5, False, horde_24h) in (3405, 3410)
    assert _deposit(4, 4, True, horde_24h) == 100          # a stack is one auction: one minimum
    horde_2h = HouseFees(cut=0.05, deposit_rate=0.05, min_deposit=100, listing_hours=2)
    assert _deposit(1136, 5, False, horde_2h) == 500       # gear: the minimum is charged per item


def test_expected_expiries():
    from forever_ah.signals import expected_expiries

    assert expected_expiries(0, 24) == 0
    assert expected_expiries(0.1, 24) < 0.001              # sells in a couple of hours: expiry is negligible
    assert 0.55 < expected_expiries(1.0, 24) < 0.60        # a "one day" sale often outlasts a 24h listing
    assert 2.4 < expected_expiries(3.0, 24) < 2.6


# -- price-specific demand, undercut pressure, listing duration -----------------
def levelled(sold=None, new=None, **kw):
    base = dict(n_snaps=48, ref_price=1000, trend_per_day=0.0, cv=0.05, sold_per_day=100.0, total_sold=700,
                level_days=1.0)
    base.update(kw)
    return History(sold_levels=tuple(sorted((sold or {}).items())), new_levels=tuple(sorted((new or {}).items())), **base)


def test_demand_counts_only_sales_at_or_above_the_price():
    from forever_ah.signals import demand_at

    hist = levelled(sold={500: 80, 990: 15, 1200: 5})
    rate, share, pressure = demand_at(hist, 1000)
    assert share == 0.2 and rate == 20 and pressure == 0      # 990 is within 3% of 1000; 500 is not
    assert demand_at(hist, 400)[0] == 100
    assert demand_at(levelled(sold={500: 100}), 1000)[0] == 0   # nothing has ever sold that high
    assert demand_at(History(sold_per_day=50.0), 1000) == (50.0, None, 0.0)   # no price data yet: all sales count


def test_new_listings_at_the_same_price_jump_the_queue():
    from forever_ah.signals import demand_at

    hist = levelled(sold={1000: 100}, new={1000: 30, 990: 30, 800: 500})
    rate, _, pressure = demand_at(hist, 1000)
    assert rate == 40 and pressure == 0.6                     # 800 is far below: it sells at its own price
    buried = demand_at(levelled(sold={1000: 100}, new={1000: 400}), 1000)
    assert buried[0] == 10 and buried[2] == 4                 # a tenth of demand is kept as a floor


def test_flip_slows_and_gets_riskier_when_few_sales_reach_the_relist_price():
    easy = flip_signal([(500, 5), (900, 100)], levelled(sold={900: 100}), 10, True, FEES, TUN)
    hard = flip_signal([(500, 5), (900, 100)], levelled(sold={520: 90, 900: 10}), 10, True, FEES, TUN)
    assert 9 < hard.days_to_sell / easy.days_to_sell < 11
    assert any("10% of sales reach this price" in r for r in hard.reasons)
    crowded = flip_signal([(500, 5), (900, 100)], levelled(sold={900: 100}, new={900: 200}), 10, True, FEES, TUN)
    assert crowded.risk_score >= easy.risk_score + 25
    assert any("arrive faster than it sells" in r for r in crowded.reasons)


def test_listing_duration():
    from forever_ah.signals import choose_listing_hours

    fees = HouseFees(cut=0.05, deposit_rate=0.60, min_deposit=100, listing_hours=24)
    steady, falling = levelled(), levelled(trend_per_day=-0.2)
    assert choose_listing_hours(5000, 1, False, fees, steady, 0.0) == 24    # longest loses least on average
    assert choose_listing_hours(5000, 1, False, fees, falling, 0.0) == 8    # may need repricing: cap the forfeit
    assert choose_listing_hours(5000, 1, False, fees, steady, 0.6) == 8     # crowded price: same
    assert choose_listing_hours(100, 1, False, fees, falling, 0.0) == 24    # 1s minimum either way: longer is free
    sig = flip_signal([(500, 5), (2000, 100)], levelled(trend_per_day=-0.2, sold={1000: 100}), 5000, True, FEES, TUN)
    assert sig.list_hours == 8 and any("List for 8h" in r for r in sig.reasons)


def test_squash_levels():
    import json

    from forever_ah.pricing import squash_levels

    assert squash_levels({}) is None
    assert json.loads(squash_levels({100: 2, 200: 1.5})) == [[100, 2], [200, 1.5]]
    five = json.loads(squash_levels({100: 10, 101: 10, 500: 1, 900: 1, 2000: 1}))
    assert len(five) == 4 and five[0] == [100, 20] and sum(q for _, q in five) == 23


# -- base rates ----------------------------------------------------------------
def test_rates_are_remeasured_only_from_a_big_enough_sample():
    from forever_ah.lifecycle import RateCounts, Rates, rates_from_counts

    default = Rates()
    assert rates_from_counts(None, None) == default
    thin = RateCounts(behind_n=10, behind_gone=9, front_n=10, front_gone=9, lapsed_n=10, lapsed_reposted=0)
    assert rates_from_counts(thin, thin) == default
    big = RateCounts(behind_n=10000, behind_gone=300, front_n=10000, front_gone=2300, lapsed_n=1000, lapsed_reposted=500)
    r = rates_from_counts(big, None)
    assert (r.stack_cancel, round(r.stack_sale, 4), r.stack_repost) == (0.03, 0.2, 0.5)
    assert r.gear_sale == default.gear_sale


def test_diff_tallies_base_rates_for_hourly_gaps_only():
    prev = {1: L(price=100, left="LONG"), 2: L(price=150, left="LONG"), 3: L(price=150, left="LONG")}
    hourly = diff_snapshot(prev, {1: L(price=100, left="LONG"), 2: L(price=150, left="LONG")}, 1.0)
    c = hourly.counts["stack"]
    assert (c.front_n, c.front_gone, c.behind_n, c.behind_gone) == (1, 0, 2, 1)
    assert diff_snapshot(prev, {1: L(price=100, left="LONG")}, 2.5).counts == {}


def test_diff_records_what_sold_and_what_was_listed_at_each_price():
    d = diff_snapshot({1: L(price=100, qty=10)}, {1: L(price=100, qty=4), 2: L(price=90, qty=7)}, 1.0)
    assert d.flows[1].sold_levels == {100: 6} and d.flows[1].new_levels == {90: 7}


# -- scorecard -----------------------------------------------------------------
def test_scorecard_judges_a_recommendation_against_later_sales():
    from forever_ah.scorecard import judge, window_hours

    assert window_hours(0.05) == (2, 6) and window_hours(1.0) == (24, 48) and window_hours(5) == (120, 120)
    t = "2026-11-11 %02d:25:00"
    sales = [(t % 15, 4, "[[1000,4]]"), (t % 16, 20, "[[600,14],[980,6]]"), (t % 17, 5, "[[1000,5]]")]
    assert judge(t % 14, 10, 1000, 0.1, sales)[0] == "ON_TIME"          # 10 units at ~1000 within 2h (predicted 3h)
    assert judge(t % 14, 15, 1000, 0.05, sales) == ("LATE", 1.0, 1, 3.0)  # needed the third hour; predicted 2h
    outcome, share, any_ok, hours = judge(t % 14, 40, 1000, 0.1, sales)
    assert (outcome, share, any_ok, hours) == ("MISSED", 0.375, 0, None)
    assert judge(t % 14, 20, 1000, 0.1, sales)[2] == 1                   # 29 units sold at some price


def test_scorecard_logs_once_a_day_and_scores_when_the_window_closes(tmp_path):
    from forever_ah import db, scorecard
    from forever_ah.config import Settings

    conn = db.connect(tmp_path / "t.db")
    settings = Settings(root=tmp_path, houses={"alliance": -1, "horde": 114, "blackwater": 117})
    conn.execute("INSERT INTO houses (house_id, name, faction, slot) VALUES (114, 'Horde Auction House', 'horde', 1)")
    t = "2026-11-11 %02d:25:00"

    def snapshot(hour, sold_levels=None, sold=0):
        sid = conn.execute("INSERT INTO snapshots (house_id, published_at, fetched_at, n_auctions, n_commodities) "
                           "VALUES (114, ?, ?, 0, 0)", (t % hour, t % hour)).lastrowid
        conn.execute("INSERT INTO item_stats (house_id, variant_id, snapshot_id, sold_qty, sold_levels) "
                     "VALUES (114, 7, ?, ?, ?)", (sid, sold, sold_levels))

    snapshot(10)
    conn.execute("INSERT INTO signals VALUES (114, 7, 'FLIP', 5, 500, 2500, 1000, 2000, 0.8, 0.05, 20, 'LOW', '[]', 24)")
    conn.execute("INSERT INTO signals VALUES (114, 8, 'VENDOR', 5, 50, 250, 100, 250, 1.0, 0, 0, 'NONE', '[]', NULL)")
    assert scorecard.log_signals(conn, settings) == 1            # vendor sales carry no market risk to score
    assert scorecard.log_signals(conn, settings) == 0            # already being watched
    snapshot(11, "[[1000,3]]", 3)
    snapshot(12, "[[1000,2]]", 2)
    assert scorecard.score(conn) == 0                            # the 6h window is still open
    for hour in (13, 14, 15, 16):
        snapshot(hour)
    assert scorecard.score(conn) == 1
    s = scorecard.summary(conn)
    assert s["pending"] == 0
    assert s["rows"] == [{"strategy": "FLIP", "risk_level": "LOW", "checked": 1, "on_time": 1, "late": 0,
                          "missed": 0, "enough_buyers": 1}]
    conn.close()


# -- crafting, disenchanting, sources ---------------------------------------------
def test_parse_wowhead_facts():
    from forever_ah.blizzard import parse_wowhead_facts

    xml = ('<wowhead><item id="6533"><name><![CDATA[Aquadynamic Fish Attractor]]></name>'
           '<json><![CDATA["id":6533,"source":[1,2,5],"sourcemore":[{"c":11,"n":"x","s":202,"t":6,"ti":9271},{"n":"Rack","t":2}]]]></json>'
           '<jsonEquip><![CDATA["buyprice":250,"sellprice":62]]></jsonEquip>'
           '<createdBy><spell id="9271" name="Aquadynamic Fish Attractor" icon="i" minCount="3" maxCount="3">'
           '<reagent id="2841" name="Bronze Bar" quality="1" icon="i" count="2" />'
           '<reagent id="6530" name="Nightcrawlers" quality="1" icon="i" count="1" /></spell></createdBy></item></wowhead>')
    f = parse_wowhead_facts(xml)
    assert f["sources"] == [1, 2, 5] and f["buy_price"] == 250 and f["skill"] == 202 and f["makes"] == 3
    assert f["reagents"] == [[2841, 2, "Bronze Bar"], [6530, 1, "Nightcrawlers"]]
    plain = parse_wowhead_facts('<wowhead><item id="2770"><name><![CDATA[Copper Ore]]></name>'
                                '<json><![CDATA["id":2770,"source":[2,19]]]></json></item></wowhead>')
    assert plain["sources"] == [2, 19] and plain["reagents"] is None and plain["buy_price"] is None
    assert parse_wowhead_facts("<wowhead><error>Item not found!</error></wowhead>") is None


def test_sources_and_disenchant_table():
    from forever_ah.professions import G_ASTRAL, L_ASTRAL, S_GLIMMERING, S_GLOWING, STRANGE, disenchant_yield, how_obtained, is_farmable

    assert how_obtained([2, 19]) == "Mining, Drops" and is_farmable([2, 19])
    assert how_obtained([1]) == "Crafted" and not is_farmable([1]) and not is_farmable([5])
    assert how_obtained(None) is None and how_obtained([]) is None
    armour = dict(disenchant_yield("Uncommon", "Armor", 24))
    weapon = dict(disenchant_yield("Uncommon", "Weapon", 24))
    assert armour == {STRANGE: 0.75 * 5.0, L_ASTRAL: 0.20 * 1.5, S_GLIMMERING: 0.05}
    assert weapon[L_ASTRAL] > armour[L_ASTRAL] and weapon[STRANGE] < armour[STRANGE]   # weapons favour essence
    assert disenchant_yield("Rare", "Armor", 33) == [(S_GLOWING, 1.0)]
    assert G_ASTRAL in dict(disenchant_yield("Uncommon", "Armor", 28))
    assert disenchant_yield("Common", "Armor", 24) == [] and disenchant_yield("Uncommon", "Trade Goods", 24) == []
    assert disenchant_yield("Uncommon", "Armor", None) == [] and disenchant_yield("Uncommon", "Armor", 3) == []


def test_craft_signal():
    from forever_ah.signals import craft_signal

    hist = levelled(sold={2000: 40}, sold_per_day=40.0, total_sold=280, ref_price=2000)
    mats = [("Bronze Bar", 5, 100, False), ("Weak Flux", 4, 25, True)]        # 6s per craft
    sig = craft_signal([(2000, 3)], hist, mats, 1.0, "Blacksmithing", 500, False, FEES, TUN)
    assert sig.strategy == "CRAFT" and sig.sell_price == 2000                 # match the cheapest listing
    assert sig.buy_qty == 20 and sig.buy_max_price == 600 and sig.buy_cost == 12000   # capped at 20 crafts
    assert sig.reasons[0] == "Blacksmithing: 5x Bronze Bar (1s), 4x Weak Flux (25c at a vendor)"
    assert sig.profit > 20 * (2000 * 0.95 - 600) - 2000
    three = craft_signal([(2000, 3)], hist, mats, 3.0, None, 500, False, FEES, TUN)
    assert three.buy_max_price == 200 and "makes 3" in three.reasons[0]
    assert craft_signal([(500, 3)], hist, mats, 1.0, None, 500, False, FEES, TUN) is None       # sells below cost
    assert craft_signal([], hist, mats, 1.0, None, 500, False, FEES, TUN).sell_price == 2000    # none listed: typical price
    quiet = levelled(sold_per_day=0.5, total_sold=1)
    assert craft_signal([(2000, 3)], quiet, mats, 1.0, None, 500, False, FEES, TUN) is None     # no proof it sells


def test_disenchant_signal():
    from forever_ah.signals import disenchant_signal

    mats = [("Strange Dust", 3.75, 200), ("Lesser Astral Essence", 0.3, 1000), ("Small Glimmering Shard", 0.05, None)]
    # worth 10s 50c in materials; 9s 97c after the cut; buy at up to 8s 31c (20% margin)
    sig = disenchant_signal([(300, 2), (800, 3), (900, 5)], mats, FEES, TUN)
    assert sig.strategy == "DISENCHANT" and sig.sell_price == 1050
    assert (sig.buy_qty, sig.buy_max_price, sig.buy_cost) == (5, 800, 3000)
    assert sig.profit == int(5 * 1050 * 0.95) - 3000 and sig.risk_level == "LOW"
    assert disenchant_signal([(900, 5)], mats, FEES, TUN) is None
    no_dust_price = [("Strange Dust", 3.75, None), ("Lesser Astral Essence", 0.3, 1000)]
    assert disenchant_signal([(10, 5)], no_dust_price, FEES, TUN) is None    # cannot value it without its main material


def test_junk_listings_are_cut_at_a_multiple_of_the_going_price():
    from forever_ah.pricing import junk_ceiling
    cloth = [(100, 40), (110, 60), (150, 20), (10_000_000, 1)]
    assert junk_ceiling(cloth, None, 5.0) == 500
    # a market that has fallen keeps its old, higher prices: the typical price lifts the ceiling
    assert junk_ceiling(cloth, 400, 5.0) == 2000
    assert junk_ceiling([], None, 5.0) is None


def test_sales_far_above_typical_are_not_counted():
    points = [(float(t), 100, 2) for t in range(12)]
    h = build_history(points, span_hours=12, sold_levels={100: 20, 100_000: 4}, level_days=0.5,
                      junk_multiple=5.0)
    assert h.sold_levels == ((100, 20),)
    assert h.total_sold == 20
    assert h.sold_per_day == 40


def test_cooldown_recipe_is_judged_as_a_single_craft():
    from forever_ah.signals import craft_signal

    hist = levelled(sold={200000: 40}, sold_per_day=40.0, total_sold=280, ref_price=200000)
    mats = [("Thorium Bar", 1, 2500, False), ("Arcane Crystal", 1, 100000, False)]
    free = craft_signal([(200000, 3)], hist, mats, 1.0, "Alchemy", 500, False, FEES, TUN)
    capped = craft_signal([(200000, 3)], hist, mats, 1.0, "Alchemy", 500, False, FEES, TUN, cooldown_days=2)
    assert free.buy_qty == 20
    assert capped.buy_qty == 1 and capped.buy_cost == 102500
    assert "one craft" in capped.reasons[1]


def test_a_variant_far_above_its_siblings_is_junk():
    # Feral Cloak [variant 12718]: its only listing sat at 10g for 13 hours, then one appeared at 15s.
    points = [(0.0, 300, 0), (2.0, 600, 0)] + [(float(t), 100300, 0) for t in range(3, 16)] \
        + [(16.0, 1500, 0), (17.0, 1500, 0)]
    assert build_history(points, span_hours=17).ref_price == 100300
    h = build_history(points, span_hours=17, price_cap=12500)
    assert h.ref_price == 1050 and h.n_snaps == 4
    silly = build_history([(float(t), 100300, 0) for t in range(8)], span_hours=8, price_cap=12500)
    assert silly.ref_price is None


def test_family_cap_trims_the_ladder_but_never_empties_it():
    from forever_ah.analyse import without_junk
    ladders = {1: (0, [(1500, 1), (100300, 1)], [1, 1]), 2: (0, [(90000, 1)], [1])}
    out = without_junk(ladders, {}, TUN, {1: 12500, 2: 12500})
    assert out[1][1] == [(1500, 1)] and out[1][2] == [1]
    assert out[2][1] == [(90000, 1)]


def test_gear_flips_and_exports_are_rated_riskier():
    from forever_ah.signals import assess_risk
    hist = levelled()
    stack, _, _ = assess_risk("FLIP", 0.1, hist, is_commodity=True)
    gear, _, why = assess_risk("FLIP", 0.1, hist, is_commodity=False)
    export, _, _ = assess_risk("EXPORT", 0.1, hist)
    assert gear == stack + 25 and any("Gear" in r for r in why)
    assert export == stack + 35
