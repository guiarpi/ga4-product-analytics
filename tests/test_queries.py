"""queries.py builds SQL strings only, so their structure can be checked offline."""
import inspect
import re

import pytest

import queries

W = ("proj.dataset_x", "20240101", "20240131")


def all_query_sql():
    """Every public *_sql builder in queries.py, called with a test window."""
    out = {}
    for name, fn in inspect.getmembers(queries, inspect.isfunction):
        if not name.endswith("_sql") or name.startswith("_"):
            continue
        n_args = len(inspect.signature(fn).parameters)
        out[name] = fn(*W[:1]) if n_args == 1 else fn(*W)
    return out


def test_every_query_scans_only_the_window():
    for name, sql in all_query_sql().items():
        assert "`proj.dataset_x.events_*`" in sql, name
        if name != "date_range_sql":           # reads table names only, by design
            assert sql.count("_TABLE_SUFFIX BETWEEN '20240101' AND '20240131'") >= sql.count("events_*`"), name


def test_no_unfilled_placeholders():
    for name, sql in all_query_sql().items():
        assert "{" not in sql and "}" not in sql, name


def test_step_flags_are_sequential_and_never_null():
    sql = queries.sessions_cte(*W)
    assert "ts_s IS NOT NULL AS s1" in sql
    assert "IFNULL(ts_s IS NOT NULL AND ts_v >= ts_s, FALSE) AS s2" in sql
    assert ("IFNULL(ts_s IS NOT NULL AND ts_v >= ts_s AND ts_c >= ts_v AND ts_k >= ts_c "
            "AND ts_p >= ts_k, FALSE) AS s5") in sql


def test_funnel_columns_follow_step_order():
    sql = queries.session_funnel_sql(*W)
    cols = re.findall(r"AS (sessions|view_item|add_to_cart|begin_checkout|purchase)\b", sql.split("SELECT\n  ")[-1])
    assert cols == ["sessions", "view_item", "add_to_cart", "begin_checkout", "purchase"]
    assert sql.rstrip().endswith("FROM sessions WHERE s1")


def test_shoppers_funnel_starts_at_view_item():
    sql = queries.shoppers_funnel_sql(*W)
    assert "'session_start'" not in sql
    assert "ts_v IS NOT NULL AS s1" in sql
    assert "COUNT(*) AS view_item" in sql and "COUNTIF(s4) AS purchase" in sql


def test_grouped_funnel_groups_by_position():
    sql = queries.device_funnel_sql(*W)
    assert "device.category AS device_category" in sql
    assert "GROUP BY 1" in sql and "ORDER BY sessions DESC" in sql


def test_channel_funnel_uses_session_medium_not_first_touch_for_the_session():
    sql = queries.channel_funnel_sql(*W)
    assert "session_attrs AS (" in sql                       # medium from every event of the session
    assert "COALESCE(session_medium, '(not set)') AS session_medium" in sql
    assert "traffic_source.medium AS first_touch_medium" in sql
    assert "GROUP BY 1, 2" in sql
    assert "SUM(IF(s5, IFNULL(revenue, 0), 0))" in sql      # revenue of full-sequence sessions only


def test_category_query_counts_only_the_carted_item():
    sql = queries.category_sql(*W)
    assert "event_name='add_to_cart' AND quantity > 0" in sql
    assert queries.CATEGORY_LABEL_SQL in sql


@pytest.mark.parametrize("entry, first", [("session_start", "sessions"), ("view_item", "view_item")])
def test_funnel_sql_entry_names_the_first_count(entry, first):
    assert f"COUNT(*) AS {first}" in queries.funnel_sql(*W, entry=entry)


def test_medians_are_exact():
    # APPROX_QUANTILES varies between uncached runs, so a regenerated page could
    # show a different median with no change to the data or code.
    for name, sql in all_query_sql().items():
        assert "APPROX_QUANTILES" not in sql, name
