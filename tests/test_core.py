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


def test_cheapest_commodity_gone_is_sold():
    prev = {1: L(price=100), 2: L(price=150)}
    d = diff_snapshot(prev, {2: L(price=150)}, 1.0)
    assert d.flows[1].sold_qty == 10 and d.flows[1].sold_value == 1000


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
    assert s.sell_price == 1000                      # capped at typical, not 1999
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
