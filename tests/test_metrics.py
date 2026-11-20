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

