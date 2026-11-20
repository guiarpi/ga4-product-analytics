"""charts.py builds Plotly figures from computed tables; the menus' data is
checked here without a browser."""
import pandas as pd

import charts

STEPS = ["Session Start", "View Products", "Add to Cart", "Begin Checkout", "Purchase"]


def test_funnel_labels_carry_counts_and_step_rates():
    v = charts._funnel_view(STEPS, [1000, 200, 40, 14, 7])
    assert v["text"] == ["1,000", "200", "40", "14", "7"]
    assert "20.0% continue" in v["y"][1] and "50.0% continue" in v["y"][4]
    assert v["customdata"][4].startswith("0.70%")          # two decimals: never "1%"


def test_funnel_menu_has_one_button_per_segment_and_starts_on_the_first():
    segs = [("All sessions", [1000, 200, 40, 14, 7]), ("New sessions", [600, 100, 15, 5, 2])]
    fig = charts.funnel(STEPS, segs)
    buttons = fig.layout.updatemenus[0].buttons
    assert [b.label for b in buttons] == ["All sessions · 1,000", "New sessions · 600"]
    assert list(fig.data[0].x) == [1000, 200, 40, 14, 7]
    assert buttons[1].args[0]["x"] == [[600, 100, 15, 5, 2]]


def test_category_menu_sorts_keep_every_row_and_its_colour():
    t = pd.DataFrame({"item_category": ["A", "B", "C"], "atc_pct": [10.0, 30.0, 20.0],
                      "viewing_sessions": [500, 900, 700], "carts_at_stake": [50, 0, 0],
                      "ci_lo": [8.0, 28.0, 18.0], "ci_hi": [12.0, 32.0, 22.0],
                      "status": ["below", "above", "neutral"]})
    fig = charts.category(t, 20.0)
    orders = [b.args[0]["y"][0] for b in fig.layout.updatemenus[0].buttons]
    assert orders == [["A", "C", "B"], ["A", "C", "B"], ["B", "C", "A"]]
    for b in fig.layout.updatemenus[0].buttons:
        colour = dict(zip(b.args[0]["y"][0], b.args[0]["marker.color"][0]))
        assert colour == {"A": charts.BELOW, "B": charts.ABOVE, "C": charts.NEUTRAL}
