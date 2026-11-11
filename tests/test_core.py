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
    hist = History(n_snaps=48, ref_price=1000, trend_per_day=-0.2, cv=0.3, sold_per_day=20, total_sold=100)
    s = dip_signal([(600, 10), (650, 100), (1100, 50)], hist, 10, True, FEES, TUN)
    assert s is not None and s.strategy == "DIP"
    assert s.buy_qty == 20 and s.sell_price == 900
    assert s.days_to_sell > 1                         # 90 other cheap units are ahead in the queue
    assert s.risk_level == "HIGH"
    assert any("falling" in r for r in s.reasons)


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
    # 15% cut, less the 3s deposit times the 16% chance-weighted expiries of a half-day sale
    assert sig.profit == 10 * 850 - 5000 - 47
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
    assert _deposit(1136, 5, False, horde_24h) == 3408
    assert _deposit(4, 4, True, horde_24h) == 100          # the minimum covers the whole posting


def test_expected_expiries():
    from forever_ah.signals import expected_expiries

    assert expected_expiries(0, 24) == 0
    assert expected_expiries(0.1, 24) < 0.001              # sells in a couple of hours: expiry is negligible
    assert 0.55 < expected_expiries(1.0, 24) < 0.60        # a "one day" sale often outlasts a 24h listing
    assert 2.4 < expected_expiries(3.0, 24) < 2.6
