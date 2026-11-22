"""Site average, category flags and carts at stake (metrics.py)."""
import pandas as pd
import pytest

from metrics import flag_categories, site_average


def categories():
    return pd.DataFrame({
        "item_category":    ["Weak big", "Weak small", "Average", "Strong"],
        "viewing_sessions": [10_000,       50,           5_000,     4_000],
        "carting_sessions": [1_000,        5,            1_000,     1_200],
    }).assign(atc_pct=lambda d: d["carting_sessions"] / d["viewing_sessions"] * 100)


def test_site_average_is_pooled_not_a_mean_of_rates():
    df = categories()
    # (1000 + 5 + 1000 + 1200) / (10000 + 50 + 5000 + 4000) = 3205 / 19050
    assert site_average(df["carting_sessions"], df["viewing_sessions"]) == pytest.approx(3205 / 19050 * 100)
    assert site_average(df["carting_sessions"], df["viewing_sessions"]) != pytest.approx(df["atc_pct"].mean())


def test_site_average_with_no_volume_is_zero():
    assert site_average(pd.Series([], dtype=float), pd.Series([], dtype=float)) == 0.0


def test_flags_follow_the_confidence_interval_not_the_point_estimate():
    df = categories()
    out = flag_categories(df, site_average(df["carting_sessions"], df["viewing_sessions"])).set_index("item_category")
    # "Weak small" is at 10% like "Weak big", but 50 sessions are too few to be sure:
    # its 95% interval (about 4%–21%) overlaps the average.
    assert out.loc["Weak big", "status"] == "below"
    assert out.loc["Weak small", "status"] == "neutral"
    assert out.loc["Average", "status"] == "above"      # 20% vs a 16.8% site average, n = 5,000
    assert out.loc["Strong", "status"] == "above"


def test_carts_at_stake_only_for_flagged_categories():
    df = categories()
    avg = site_average(df["carting_sessions"], df["viewing_sessions"])
    out = flag_categories(df, avg).set_index("item_category")
    # (average − 10%) × 10,000 sessions, rounded.
    assert out.loc["Weak big", "carts_at_stake"] == round((avg - 10) / 100 * 10_000)
    # Same gap, but not significant → no carts counted.
    assert out.loc["Weak small", "carts_at_stake"] == 0
    assert out.loc["Strong", "carts_at_stake"] == 0


def test_flag_categories_does_not_modify_its_input():
    df = categories()
    before = df.copy()
    flag_categories(df, 15.0)
    pd.testing.assert_frame_equal(df, before)


# ── listing_levels ────────────────────────────────────────────────────────────
from metrics import listing_levels, weak_landing_pages, mix_decomposition


def test_listing_levels_follow_the_parent_path():
    cats    = pd.Series(["Hats", "Apparel", "YouTube", "Shop by Brand", "Sale", "Bags", "Lifestyle", "Backpacks"])
    parents = pd.Series(["Apparel", "Home", "Shop by Brand", "Home", "Home", "Lifestyle", None, None])
    assert list(listing_levels(cats, parents)) == [
        "product type", "parent", "brand", "parent", "collection", "product type", "parent", "product type"]


# ── weak_landing_pages ────────────────────────────────────────────────────────
def test_weak_landing_pages_needs_volume_and_a_real_gap():
    pages = pd.DataFrame({
        "landing_page": ["/", "/weak", "/tiny-weak", "/close", "(no page_view)"],
        "sessions":     [5000, 2000,    50,          2000,     1000],
        "view_item":    [1100, 100,     0,           380,      0],
    })
    w = weak_landing_pages(pages, site_view_pct=20.0, total_sessions=10_050)
    assert list(w["landing_page"]) == ["/weak"]          # tiny page and 1pp gap dropped
    assert w.loc[0, "view_pct"] == 5.0 and w.loc[0, "gap_pp"] == 15.0
    assert w.loc[0, "extra_views"] == 300                 # 15% of 2,000


# ── mix_decomposition ─────────────────────────────────────────────────────────
def test_mix_decomposition_pure_mix_change():
    # Same rates in each segment, only the mix moves → the whole change is mix.
    a = pd.DataFrame({"users": [500, 500], "successes": [50, 10]}, index=["hi", "lo"])
    b = pd.DataFrame({"users": [200, 800], "successes": [20, 16]}, index=["hi", "lo"])
    d = mix_decomposition(a, b)
    assert d["rate_a"] == pytest.approx(6.0) and d["rate_b"] == pytest.approx(3.6)
    assert d["within_pp"] == pytest.approx(0.0) and d["mix_pp"] == pytest.approx(-2.4)


def test_mix_decomposition_pure_rate_change():
    a = pd.DataFrame({"users": [500, 500], "successes": [50, 10]}, index=["hi", "lo"])
    b = pd.DataFrame({"users": [500, 500], "successes": [25, 5]}, index=["hi", "lo"])
    d = mix_decomposition(a, b)
    assert d["mix_pp"] == pytest.approx(0.0) and d["within_pp"] == pytest.approx(-3.0)


def test_weak_landing_share_uses_all_sessions_not_the_listed_pages():
    # The landing query drops rare pages; shares must still be of every session.
    pages = pd.DataFrame({"landing_page": ["/weak"], "sessions": [50], "view_item": [0]})
    assert weak_landing_pages(pages, 20.0, total_sessions=10_000).empty    # 0.5% of sessions
    assert len(weak_landing_pages(pages, 20.0, total_sessions=50)) == 1     # its own sum: 100%


def test_weak_landing_pages_skip_pages_not_meant_to_show_products():
    pages = pd.DataFrame({"landing_page": ["/signin.html", "/basket.html", "/store-policies/faq/", "/Apparel"],
                          "sessions": [1000] * 4, "view_item": [0] * 4})
    assert list(weak_landing_pages(pages, 20.0, total_sessions=4000)["landing_page"]) == ["/Apparel"]


# ── diagnose_landing_page ─────────────────────────────────────────────────────
from metrics import diagnose_landing_page


def _rows(weekly):
    """weekly = [(week, entry, sessions, view_item, scrolled, untagged)]"""
    return pd.DataFrame([{"week": w, "entry": e, "sessions": n, "view_item_sessions": v,
                          "scrolled_sessions": sc, "untagged_sessions": u, "eng_s_total": n * 5.0}
                         for w, e, n, v, sc, u in weekly])


def test_silent_weeks_with_normal_behaviour_are_a_tracking_gap():
    weeks = [(k, "landed", 500, 150 if k in (1, 2, 6, 7) else 2, 50, 10) for k in range(1, 8)]
    d = diagnose_landing_page(_rows(weeks), site_view_pct=20.0)
    assert d["verdict"] == "tracking"
    assert (d["gap"]["start"], d["gap"]["end"], d["gap"]["weeks"]) == (3, 5, 3)


def test_silent_weeks_with_collapsing_behaviour_are_the_page():
    weeks = [(k, "landed", 500, 150 if k in (1, 2, 6, 7) else 2, 50 if k in (1, 2, 6, 7) else 5, 10) for k in range(1, 8)]
    assert diagnose_landing_page(_rows(weeks), 20.0)["verdict"] == "page"


def test_page_that_works_from_inside_points_at_entry_traffic():
    rows = _rows([(k, "landed", 500, 25, 40, 300) for k in range(1, 6)] +
                 [(k, "inside", 200, 60, 80, 5) for k in range(1, 6)])
    d = diagnose_landing_page(rows, 20.0)
    assert d["verdict"] == "entry traffic"
    assert d["landed_rate"] == 5.0 and d["inside_rate"] == 30.0 and d["landed_untagged_pct"] == 60.0


def test_page_that_fails_from_inside_too_is_the_page():
    rows = _rows([(k, "landed", 500, 25, 40, 10) for k in range(1, 6)] +
                 [(k, "inside", 200, 10, 30, 5) for k in range(1, 6)])
    assert diagnose_landing_page(rows, 20.0)["verdict"] == "page"


def test_a_long_gap_does_not_hide_itself():
    # 7 silent weeks out of 13: a median baseline would be dragged down to the gap.
    rates = [41, 7, 19, 23, 1, 0.3, 0.7, 2, 0.8, 0.3, 0.7, 48, 89]
    weeks = [(k, "landed", 1000, int(r * 10), 70, 20) for k, r in enumerate(rates, start=1)]
    d = diagnose_landing_page(_rows(weeks), 21.0)
    assert d["verdict"] == "tracking" and (d["gap"]["start"], d["gap"]["end"]) == (5, 11)


def test_a_steady_decline_is_not_a_gap():
    rates = [28, 22, 22, 24, 17, 15, 11, 11, 8, 7, 8, 9, 9, 10]
    rows = _rows([(k, "landed", 1000, int(r * 10), 150, 600) for k, r in enumerate(rates, start=1)] +
                 [(k, "inside", 300, 90, 120, 5) for k in range(1, 15)])
    d = diagnose_landing_page(rows, 21.0)
    assert d["gap"] is None and d["verdict"] == "entry traffic"


# ── E10: purchase paths and cart recovery ─────────────────────────────────────
from metrics import purchase_path_summary, recovery_curve, PATH_ORDER


def test_purchase_path_summary_shares_revenue_and_exact_medians():
    paths = pd.DataFrame({
        "path": ["sequential, in one session"] * 2 + ["carted in an earlier session"] * 3 + ["no cart seen in the window"],
        "revenue": [10.0, 20.0, 5.0, 5.0, 0.0, 7.0],
        "hours_since_cart": [None, None, 2.0, 30.0, 100.0, None],
        "sessions_since_cart": [None, None, 1, 1, 4, None],
    })
    s = purchase_path_summary(paths)
    assert list(s.index) == list(PATH_ORDER)
    assert s.loc["carted in an earlier session", "sessions"] == 3
    assert s.loc["carted in an earlier session", "share_pct"] == 50.0
    assert s.loc["carted in an earlier session", "median_hours_since_cart"] == 30.0   # exact, not approximate
    assert s.loc["carted in the session, out of order", "sessions"] == 0               # missing path → 0 row
    assert s["revenue"].sum() == 47.0


def test_recovery_curve_excludes_carts_without_enough_time_left():
    carts = pd.DataFrame({
        "reached_checkout":       [False, False, True, False],
        "hours_to_next_purchase": [5.0,   None,  100.0, None],
        "hours_left":             [400.0, 400.0, 400.0, 30.0],   # the last cart has < 7 days left
    })
    c = recovery_curve(carts, days=(1, 7))
    one, seven = c.iloc[0], c.iloc[1]
    assert (one["eligible_carts"], one["recovered"]) == (4, 1)          # 30h left still covers 1 day
    assert (seven["eligible_carts"], seven["recovered"]) == (3, 2)      # the censored cart is left out
    assert recovery_curve(carts, days=(7,), checkout=True).iloc[0]["recovered"] == 1
    assert seven["ci_lo"] < seven["recovered_pct"] < seven["ci_hi"]


# ── E11: segment_spread ───────────────────────────────────────────────────────
from metrics import segment_spread


def test_segment_spread_compares_only_real_segments_with_volume():
    df = pd.DataFrame({"country": ["US", "India", "UK", "(not set)", "Tiny"],
                       "sessions": [5000, 2000, 1000, 3000, 50],
                       "view_item": [1250, 300, 220, 30, 40]})
    s = segment_spread(df, "country")
    assert list(s["rows"]["country"]) == ["US", "UK", "India"]     # (not set) and <1% left out, best first
    assert s["best"] == ("US", 25.0) and s["worst"] == ("India", 15.0)
    assert s["spread_pp"] == 10.0 and s["verdict"] == "driver"
    assert 0 <= s["test"]["cramers_v"] <= 1


def test_segment_spread_verdict_follows_practical_size_not_significance():
    df = pd.DataFrame({"country": ["A", "B"], "sessions": [500000, 500000], "view_item": [105000, 100000]})
    s = segment_spread(df, "country")
    assert s["test"]["p"] < 0.001 and s["verdict"] == "uniform"    # significant, but a 1pp gap
