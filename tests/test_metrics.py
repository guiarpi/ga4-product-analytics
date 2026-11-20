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
