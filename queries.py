"""
queries.py
----------
Every BigQuery query used by ga4_funnel_analysis.ipynb and generate_dashboard.py,
in one place, so the two can never compute the same metric two different ways.

Each function returns a SQL string and takes the dataset and date window as
arguments (callers pass the values from config.py). Nothing here touches
BigQuery, so the SQL can be inspected, printed in the notebook and unit-tested.

The core is sessions_cte(): one row per session with the first timestamp of
each funnel event and sequential step flags. funnel_sql() counts sessions per
step on top of it, for any grouping, so device, channel and segment funnels all
share one definition of "reached step N".

Grain: one session = (user_pseudo_id, ga_session_id from event_params).
Sequential: a session reaches step N only if it reached step N−1 first, in
order, within the same session — like a closed funnel in the GA4 UI.
"""

# Funnel events in order, and the timestamp column each one gets.
FUNNEL_EVENTS = ("session_start", "view_item", "add_to_cart", "begin_checkout", "purchase")
TS = {"session_start": "ts_s", "view_item": "ts_v", "add_to_cart": "ts_c",
      "begin_checkout": "ts_k", "purchase": "ts_p"}
# Output column per step in funnel_sql(): the entry step is the session count.
STEP_COLUMNS = {"session_start": "sessions", "view_item": "view_item", "add_to_cart": "add_to_cart",
                "begin_checkout": "begin_checkout", "purchase": "purchase"}


def param(key: str, kind: str = "int") -> str:
    """SQL for one event parameter, e.g. param('ga_session_id')."""
    return (f"(SELECT ep.value.{kind}_value FROM UNNEST(event_params) AS ep\n"
            f"     WHERE ep.key = '{key}' LIMIT 1)")


def events_from(dataset: str, start: str, end: str) -> str:
    """FROM + partition filter: only the daily tables in the window are scanned."""
    return (f"`{dataset}.events_*`\n"
            f"  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'")


def _steps(entry: str) -> list[str]:
    return list(FUNNEL_EVENTS[FUNNEL_EVENTS.index(entry):])


# ── Core: one row per session ─────────────────────────────────────────────────

def session_attrs_cte(dataset: str, start: str, end: str) -> str:
    """Session attributes that need EVERY event of the session, not just the
    funnel events:

    - session_medium: the medium on the earliest event that carries one. This
      is the session's own channel; traffic_source.medium is the user's
      first-touch medium, repeated on every session they ever have.
    - n_events, n_medium, n_page_view, engaged_s: used by the data-quality
      checks (a session with no medium on any event is "unattributed").
    - session_number: GA4's ga_session_number, counted from the user's first
      visit ever (not from the start of the window), so 1 = new.
    """
    return f"""session_attrs AS (
  SELECT user_pseudo_id, session_id,
    ARRAY_AGG(medium IGNORE NULLS ORDER BY event_timestamp, medium LIMIT 1)[SAFE_OFFSET(0)] AS session_medium,
    COUNT(*) AS n_events,
    COUNTIF(medium IS NOT NULL) AS n_medium,
    COUNTIF(event_name = 'page_view') AS n_page_view,
    SUM(IFNULL(eng_msec, 0)) / 1000 AS engaged_s,
    MAX(session_number) AS session_number
  FROM (
    SELECT user_pseudo_id, event_name, event_timestamp,
      {param('ga_session_id')} AS session_id,
      {param('medium', 'string')} AS medium,
      {param('engagement_time_msec')} AS eng_msec,
      {param('ga_session_number')} AS session_number
    FROM {events_from(dataset, start, end)}
  )
  WHERE session_id IS NOT NULL
  GROUP BY user_pseudo_id, session_id
)"""


def sessions_cte(dataset: str, start: str, end: str, *, entry: str = "session_start",
                 event_dims: dict[str, str] | None = None, revenue: bool = False,
                 attributes: bool = False) -> str:
    """WITH-clause ending in a `sessions` CTE: one row per session that has any
    funnel event from `entry` onwards.

    Columns: user_pseudo_id, session_id, ts_* (first timestamp of each funnel
    event), any `event_dims` (name → SQL on the raw event row, e.g.
    {"device_category": "device.category"}), `revenue` (sum of purchase_revenue
    on the session's purchase events) and, with `attributes`, the session
    attributes from session_attrs_cte().

    Step flags s1… are sequential: s1 = the session has the entry event; s2 =
    s1 and the next event came at or after it; and so on. They are plain
    booleans (never NULL), so COUNTIF(sN) is the funnel count for step N.
    Sessions are NOT filtered on s1 here — callers that need every session with
    a purchase (sequential or not) read them from the same CTE.
    """
    steps = _steps(entry)
    event_dims = event_dims or {}
    events = ", ".join(f"'{e}'" for e in steps)
    base_dims = "".join(f"\n    {expr} AS {name}," for name, expr in event_dims.items())
    any_dims = "".join(f"\n    ANY_VALUE({name}) AS {name}," for name in event_dims)
    rev_base = ",\n    ecommerce.purchase_revenue AS purchase_revenue" if revenue else ""
    rev_se = ",\n    SUM(purchase_revenue) AS rev" if revenue else ""
    ts = ",\n    ".join(f"MAX(CASE WHEN event_name='{e}' THEN first_ts END) AS {TS[e]}" for e in steps)
    rev_pv = ",\n    MAX(CASE WHEN event_name='purchase' THEN rev END) AS revenue" if revenue else ""
    flags, cond = [], f"{TS[steps[0]]} IS NOT NULL"
    flags.append(f"{cond} AS s1")
    for n, (prev, cur) in enumerate(zip(steps, steps[1:]), start=2):
        cond = f"{cond} AND {TS[cur]} >= {TS[prev]}"
        flags.append(f"IFNULL({cond}, FALSE) AS s{n}")
    flag_sql = ",\n    ".join(flags)
    attrs_cte = f"{session_attrs_cte(dataset, start, end)},\n" if attributes else ""
    attrs_cols = ("\n    a.session_medium, a.n_events, a.n_medium, a.n_page_view,"
                  "\n    a.engaged_s, a.session_number,") if attributes else ""
    attrs_join = "\n  LEFT JOIN session_attrs AS a USING (user_pseudo_id, session_id)" if attributes else ""
    return f"""WITH {attrs_cte}base AS (
  SELECT user_pseudo_id,
    {param('ga_session_id')} AS session_id,{base_dims}
    event_name, event_timestamp{rev_base}
  FROM {events_from(dataset, start, end)}
    AND event_name IN ({events})
),
se AS (
  SELECT user_pseudo_id, session_id,{any_dims}
    event_name, MIN(event_timestamp) AS first_ts{rev_se}
  FROM base WHERE session_id IS NOT NULL
  GROUP BY user_pseudo_id, session_id, event_name
),
pv AS (
  SELECT user_pseudo_id, session_id,{any_dims}
    {ts}{rev_pv}
  FROM se GROUP BY user_pseudo_id, session_id
),
sessions AS (
  SELECT pv.*,{attrs_cols}
    {flag_sql}
  FROM pv{attrs_join}
)"""


def funnel_sql(dataset: str, start: str, end: str, *, entry: str = "session_start",
               group_by: dict[str, str] | None = None, event_dims: dict[str, str] | None = None,
               revenue: bool = False, attributes: bool = False, order_by: str | None = None) -> str:
    """Sequential funnel counts, one row per group (one row in total without
    `group_by`). Columns: the group columns, then one count per step named by
    STEP_COLUMNS (the entry step is `sessions`, or `view_item` when the funnel
    starts there), then `revenue` — purchase revenue of sessions that completed
    the full sequence — when requested.

    group_by maps output column → SQL over the `sessions` CTE, e.g.
    {"session_medium": "COALESCE(session_medium, '(not set)')"}.
    """
    steps = _steps(entry)
    group_by = group_by or {}
    cols = [f"{expr} AS {name}" for name, expr in group_by.items()]
    cols.append(f"COUNT(*) AS {STEP_COLUMNS[steps[0]]}")
    cols += [f"COUNTIF(s{n}) AS {STEP_COLUMNS[e]}" for n, e in enumerate(steps[1:], start=2)]
    if revenue:
        cols.append(f"ROUND(SUM(IF(s{len(steps)}, IFNULL(revenue, 0), 0)), 2) AS revenue")
    group = (f"\nGROUP BY {', '.join(str(i) for i in range(1, len(group_by) + 1))}" if group_by else "")
    order = f"\nORDER BY {order_by}" if order_by else ""
    select = ",\n  ".join(cols)
    return (sessions_cte(dataset, start, end, entry=entry, event_dims=event_dims,
                         revenue=revenue, attributes=attributes)
            + f"\nSELECT\n  {select}\nFROM sessions WHERE s1{group}{order}\n")


# ── Queries built on the session table ────────────────────────────────────────

def revenue_sql(dataset: str, start: str, end: str) -> str:
    """Revenue of funnel purchases, plus every purchasing session for context.

    Some purchase events carry no purchase_revenue, so AOV is averaged over the
    orders that do and both counts are returned to keep the figures
    reconcilable. all_* covers every session with a purchase, sequential or not:
    the funnel's revenue is a subset, and the gap is disclosed rather than
    hidden (most of it is purchases whose cart was filled in an earlier visit).
    """
    return sessions_cte(dataset, start, end, revenue=True) + """,
allp AS (
  SELECT COUNT(*) AS all_purchasing_sessions,
    ROUND(SUM(revenue),2) AS all_revenue,
    COUNTIF(ts_c IS NULL) AS purchases_without_cart
  FROM sessions WHERE ts_p IS NOT NULL
)
SELECT c.*, allp.*
FROM (
  SELECT
    COUNT(*) AS purchasing_sessions,
    COUNTIF(revenue > 0) AS orders_with_revenue,
    ROUND(SUM(revenue),2) AS total_revenue,
    ROUND(AVG(IF(revenue > 0, revenue, NULL)),2) AS avg_order_value,
    ROUND(ANY_VALUE(med_order),2) AS median_order_value,
    ROUND(MIN(IF(revenue > 0, revenue, NULL)),2) AS min_order_value,
    ROUND(MAX(IF(revenue > 0, revenue, NULL)),2) AS max_order_value
  FROM (
    -- Exact median (PERCENTILE_CONT ignores the NULLs, i.e. zero-revenue orders)
    SELECT *, PERCENTILE_CONT(IF(revenue > 0, revenue, NULL), 0.5) OVER () AS med_order
    FROM sessions WHERE s5
  )
) c CROSS JOIN allp
"""


def session_quality_sql(dataset: str, start: str, end: str) -> str:
    """Per-session splits for the data-quality checks and the new vs returning
    funnel (E3), from one scan.

    About a quarter of sessions carry no 'medium' on any event. This tests
    whether they are a distinct population (bots, a broken channel) or ordinary
    short sessions that lack the tag — the param is on only some events, so the
    fewer events a session has, the likelier it has none. Grouping sets give
    the overall attributed/unattributed split, the split by session length, by
    device, and by new vs returning (ga_session_number = 1).
    """
    return sessions_cte(dataset, start, end, event_dims={"dev": "device.category"},
                        revenue=True, attributes=True) + """,
f AS (
  SELECT *,
    n_medium > 0 AS attributed,
    CASE WHEN session_number = 1 THEN 'new' WHEN session_number > 1 THEN 'returning'
         ELSE 'unknown' END AS visitor,
    CASE WHEN n_events <= 3 THEN '1–3' WHEN n_events <= 5 THEN '4–5'
         WHEN n_events <= 8 THEN '6–8' WHEN n_events <= 15 THEN '9–15'
         ELSE '16+' END AS size_bucket
  FROM sessions WHERE s1
),
-- Exact medians, one window per grouping set (PERCENTILE_CONT is analytic-only,
-- so it cannot sit in the GROUP BY below); the SELECT picks the matching one.
g AS (
  SELECT *,
    PERCENTILE_CONT(engaged_s, 0.5) OVER (PARTITION BY attributed)              AS eng_a,
    PERCENTILE_CONT(engaged_s, 0.5) OVER (PARTITION BY size_bucket, attributed) AS eng_sa,
    PERCENTILE_CONT(engaged_s, 0.5) OVER (PARTITION BY dev, attributed)         AS eng_da,
    PERCENTILE_CONT(engaged_s, 0.5) OVER (PARTITION BY visitor)                 AS eng_v,
    PERCENTILE_DISC(n_events, 0.5)  OVER (PARTITION BY attributed)              AS ev_a,
    PERCENTILE_DISC(n_events, 0.5)  OVER (PARTITION BY size_bucket, attributed) AS ev_sa,
    PERCENTILE_DISC(n_events, 0.5)  OVER (PARTITION BY dev, attributed)         AS ev_da,
    PERCENTILE_DISC(n_events, 0.5)  OVER (PARTITION BY visitor)                 AS ev_v
  FROM f
)
SELECT
  CASE WHEN GROUPING(size_bucket) = 0 THEN 'size'
       WHEN GROUPING(dev) = 0 THEN 'device'
       WHEN GROUPING(visitor) = 0 THEN 'visitor' ELSE 'all' END AS cut,
  size_bucket, dev, attributed, visitor,
  COUNT(*) AS sessions,
  COUNTIF(s2) AS view_item, COUNTIF(s3) AS add_to_cart,
  COUNTIF(s4) AS begin_checkout, COUNTIF(s5) AS purchase,
  ROUND(SUM(IF(s5, IFNULL(revenue, 0), 0)), 2) AS revenue,
  COUNTIF(n_page_view > 0) AS with_page_view,
  CASE WHEN GROUPING(size_bucket) = 0 THEN ANY_VALUE(eng_sa)
       WHEN GROUPING(dev) = 0 THEN ANY_VALUE(eng_da)
       WHEN GROUPING(visitor) = 0 THEN ANY_VALUE(eng_v)
       ELSE ANY_VALUE(eng_a) END AS median_engaged_s,
  CASE WHEN GROUPING(size_bucket) = 0 THEN ANY_VALUE(ev_sa)
       WHEN GROUPING(dev) = 0 THEN ANY_VALUE(ev_da)
       WHEN GROUPING(visitor) = 0 THEN ANY_VALUE(ev_v)
       ELSE ANY_VALUE(ev_a) END AS median_events
FROM g
GROUP BY GROUPING SETS ((attributed), (size_bucket, attributed), (dev, attributed), (visitor))
ORDER BY cut, size_bucket, dev, attributed, visitor
"""


def time_to_convert_sql(dataset: str, start: str, end: str) -> str:
    """Median minutes between funnel steps for converting sessions, plus a
    tracking check over every session with an add_to_cart: an add-to-cart at the
    exact microsecond of the first view_item, or before / without one, is an
    instrumentation signal (listing quick-add), not shopper behaviour."""
    return sessions_cte(dataset, start, end) + """,
cart AS (
  SELECT COUNT(*) AS carting_sessions,
    COUNTIF(ts_c = ts_v) AS cart_same_ts_as_view,
    COUNTIF(ts_v IS NULL OR ts_c < ts_v) AS cart_before_or_without_view,
    COUNTIF(ts_c > ts_v AND ts_c - ts_v < 1000000) AS cart_within_1s_after_view
  FROM sessions WHERE ts_c IS NOT NULL
),
-- Exact medians (PERCENTILE_CONT), so every run gives the same figures
conv AS (
  SELECT ts_c, ts_v,
    PERCENTILE_CONT((ts_v-ts_s)/60000000.0, 0.5) OVER () AS m_s_to_v,
    PERCENTILE_CONT((ts_c-ts_v)/60000000.0, 0.5) OVER () AS m_v_to_c,
    PERCENTILE_CONT((ts_k-ts_c)/60000000.0, 0.5) OVER () AS m_c_to_k,
    PERCENTILE_CONT((ts_p-ts_k)/60000000.0, 0.5) OVER () AS m_k_to_p,
    PERCENTILE_CONT((ts_p-ts_s)/60000000.0, 0.5) OVER () AS m_total
  FROM sessions WHERE s5
)
SELECT t.*, cart.*
FROM (
  SELECT
    ROUND(ANY_VALUE(m_s_to_v),1) AS p50_s_to_v,
    ROUND(ANY_VALUE(m_v_to_c),1) AS p50_v_to_c,
    ROUND(ANY_VALUE(m_c_to_k),1) AS p50_c_to_k,
    ROUND(ANY_VALUE(m_k_to_p),1) AS p50_k_to_p,
    ROUND(ANY_VALUE(m_total),1) AS p50_total,
    COUNT(*) AS converting_sessions,
    COUNTIF(ts_c = ts_v) AS converting_same_ts
  FROM conv
) t CROSS JOIN cart
"""


def _user_sessions_cte(dataset: str, start: str, end: str) -> str:
    """The funnel's session table, ordered within each user: rn (1 = first
    session in the window), t0 (the session's first funnel timestamp), the last
    earlier cart, and the window's last timestamp (for censoring)."""
    return sessions_cte(dataset, start, end, revenue=True) + """,
ordered AS (
  SELECT *,
    COALESCE(ts_s, ts_v, ts_c, ts_k, ts_p) AS t0,
    ROW_NUMBER() OVER (PARTITION BY user_pseudo_id
                       ORDER BY COALESCE(ts_s, ts_v, ts_c, ts_k, ts_p), session_id) AS rn
  FROM sessions
),
user_sessions AS (
  SELECT *,
    MAX(ts_c) OVER w_before AS prev_cart_ts,
    MAX(IF(ts_c IS NOT NULL, rn, NULL)) OVER w_before AS prev_cart_rn,
    MIN(ts_p) OVER w_after AS next_purchase_ts,
    MAX(t0) OVER () AS window_end_ts
  FROM ordered
  WINDOW w_before AS (PARTITION BY user_pseudo_id ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
         w_after  AS (PARTITION BY user_pseudo_id ORDER BY rn ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING)
)"""


def purchase_paths_sql(dataset: str, start: str, end: str) -> str:
    """One row per purchasing session, with the path that led to it (E10):
    sequential in one session (the funnel's purchases); carted in the session
    but out of order; carted in an EARLIER session of the same user (a
    cross-session purchase — the funnel cannot count it); or no cart seen in
    the window (carted before the window, on another device or cookie, or
    never). For cross-session purchases, the hours and sessions since the last
    earlier cart. "Cart" = an add_to_cart event, as in the funnel."""
    return _user_sessions_cte(dataset, start, end) + """
SELECT
  CASE WHEN s5 THEN 'sequential, in one session'
       WHEN ts_c IS NOT NULL THEN 'carted in the session, out of order'
       WHEN prev_cart_ts IS NOT NULL THEN 'carted in an earlier session'
       ELSE 'no cart seen in the window' END AS path,
  IFNULL(revenue, 0) AS revenue,
  rn AS session_number_in_window,
  IF(ts_c IS NULL AND prev_cart_ts IS NOT NULL, (ts_p - prev_cart_ts) / 3.6e9, NULL) AS hours_since_cart,
  IF(ts_c IS NULL AND prev_cart_ts IS NOT NULL, rn - prev_cart_rn, NULL) AS sessions_since_cart
FROM user_sessions
WHERE ts_p IS NOT NULL
ORDER BY path, hours_since_cart, session_number_in_window, revenue
"""


def cart_recovery_sql(dataset: str, start: str, end: str) -> str:
    """One row per carting session WITHOUT a purchase in it (E10): did the same
    user buy in a later session, and how soon? hours_left is the time from the
    cart to the end of the window — a cart can only count as "not recovered
    within N days" if N days were observable after it. reached_checkout splits
    carts abandoned before checkout from those abandoned inside it."""
    return _user_sessions_cte(dataset, start, end) + """
SELECT
  ts_k IS NOT NULL AS reached_checkout,
  (next_purchase_ts - ts_c) / 3.6e9 AS hours_to_next_purchase,
  (window_end_ts - ts_c) / 3.6e9 AS hours_left
FROM user_sessions
WHERE ts_c IS NOT NULL AND ts_p IS NULL
ORDER BY reached_checkout, hours_to_next_purchase, hours_left
"""


# ── Named funnels ─────────────────────────────────────────────────────────────

def session_funnel_sql(dataset: str, start: str, end: str) -> str:
    """The headline funnel: session_start → view_item → add_to_cart →
    begin_checkout → purchase, over all sessions."""
    return funnel_sql(dataset, start, end)


def device_funnel_sql(dataset: str, start: str, end: str) -> str:
    """The same funnel by device.category."""
    return funnel_sql(dataset, start, end, event_dims={"device_category": "device.category"},
                      group_by={"device_category": "device_category"}, order_by="sessions DESC, device_category")


def country_funnel_sql(dataset: str, start: str, end: str) -> str:
    """The same funnel, with revenue, by geo.country (E11). geo is per event; a
    session takes the country of its events (ANY_VALUE — sessions rarely span
    two). Empty or '(not set)' country is grouped as '(not set)'."""
    return funnel_sql(dataset, start, end, revenue=True,
                      event_dims={"country": "NULLIF(NULLIF(geo.country, ''), '(not set)')"},
                      group_by={"country": "COALESCE(country, '(not set)')"},
                      order_by="sessions DESC, country")


def channel_funnel_sql(dataset: str, start: str, end: str) -> str:
    """The same funnel with revenue, by BOTH the session's own medium and the
    user's first-touch medium (traffic_source.medium), so the grain trap can be
    quantified. A session with no medium on any event is '(not set)'."""
    return funnel_sql(dataset, start, end, revenue=True, attributes=True,
                      event_dims={"first_touch_medium": "traffic_source.medium"},
                      group_by={"session_medium": "COALESCE(session_medium, '(not set)')",
                                "first_touch_medium": "COALESCE(first_touch_medium, '(not set)')"},
                      order_by="session_medium, first_touch_medium")


def shoppers_funnel_sql(dataset: str, start: str, end: str) -> str:
    """Funnel that starts at view_item: only sessions that saw a product."""
    return funnel_sql(dataset, start, end, entry="view_item")


# ── Other queries ─────────────────────────────────────────────────────────────

def date_range_sql(dataset: str) -> str:
    """Earliest and latest daily table in the export (scans table names only)."""
    return f"""SELECT
  MIN(_TABLE_SUFFIX) AS earliest_date,
  MAX(_TABLE_SUFFIX) AS latest_date,
  COUNT(DISTINCT _TABLE_SUFFIX) AS num_daily_tables
FROM `{dataset}.events_*`
"""


def event_inventory_sql(dataset: str, start: str, end: str) -> str:
    """Every event_name in the window with volume and reach."""
    return f"""SELECT
  event_name,
  COUNT(*)                       AS event_count,
  COUNT(DISTINCT user_pseudo_id) AS unique_users
FROM {events_from(dataset, start, end)}
GROUP BY event_name
ORDER BY event_count DESC, event_name
"""


def unique_users_sql(dataset: str, start: str, end: str) -> str:
    return f"""SELECT COUNT(DISTINCT user_pseudo_id) AS unique_users
FROM {events_from(dataset, start, end)}
  AND event_name = 'session_start'
"""


def session_start_check_sql(dataset: str, start: str, end: str) -> str:
    """Why session_start events outnumber funnel sessions: events without IDs,
    and sessions that log session_start more than once (how often, how far
    apart). The funnel counts each (user_pseudo_id, ga_session_id) once."""
    return f"""WITH ss AS (
  SELECT user_pseudo_id, {param('ga_session_id')} AS session_id, event_timestamp
  FROM {events_from(dataset, start, end)}
    AND event_name = 'session_start'
),
per AS (
  SELECT user_pseudo_id, session_id, COUNT(*) AS n,
    COUNT(DISTINCT event_timestamp) AS n_ts,
    (MAX(event_timestamp) - MIN(event_timestamp)) / 1e6 AS span_s
  FROM ss WHERE session_id IS NOT NULL AND user_pseudo_id IS NOT NULL
  GROUP BY 1, 2
)
SELECT
  (SELECT COUNT(*) FROM ss) AS session_start_events,
  (SELECT COUNTIF(session_id IS NULL OR user_pseudo_id IS NULL) FROM ss) AS events_without_ids,
  (SELECT COUNT(*) FROM per) AS distinct_sessions,
  (SELECT COUNTIF(n > 1) FROM per) AS sessions_with_repeat_start,
  (SELECT IFNULL(SUM(n - 1), 0) FROM per) AS repeat_start_events,
  (SELECT IFNULL(MAX(n), 0) FROM per) AS max_starts_in_one_session,
  (SELECT COUNTIF(n > 1 AND n_ts < n) FROM per) AS repeats_with_identical_timestamp,
  (SELECT MAX(IF(n > 1, span_s, NULL)) FROM per) AS max_seconds_between_repeats
"""

def event_quality_sql(dataset: str, start: str, end: str) -> str:
    """What view_item and add_to_cart actually record. A list-sized items array
    means a listing impression / quick-add, not a product-page view; on
    add_to_cart only the item with quantity > 0 was actually carted. Events
    with no items cannot be attributed to a listing page or a price band."""
    return f"""SELECT event_name, COUNT(*) AS events,
  ROUND(AVG(ARRAY_LENGTH(items)), 1) AS avg_items,
  COUNTIF(ARRAY_LENGTH(items) > 1) AS multi_item_events,
  COUNTIF(IFNULL(ARRAY_LENGTH(items), 0) = 0) AS no_item_events,
  COUNTIF((SELECT COUNTIF(IFNULL(it.quantity, 0) > 0) FROM UNNEST(items) AS it) = 1) AS one_carted_item_events
FROM {events_from(dataset, start, end)}
  AND event_name IN ('view_item', 'add_to_cart')
GROUP BY event_name
ORDER BY event_name DESC   -- view_item, then add_to_cart (funnel order)
"""


# item_category mixes flat labels ("Bags") with paths ("Lifestyle/Bags/"). Keep
# the last path segment so both land in one bucket. ' / ' (slash with spaces) is
# part of a name, e.g. "Men's / Unisex", not a separator. Mirrored in Python in
# tests/test_category_labels.py.
CATEGORY_LABEL_SQL = (
    "IF(raw_category = '(not set)', raw_category,\n"
    "       COALESCE(NULLIF(TRIM(REPLACE(ARRAY_REVERSE(SPLIT(TRIM(REPLACE(raw_category, ' / ', ' | '), '/'), '/'))[SAFE_OFFSET(0)], ' | ', ' / ')), ''),\n"
    "                '(not set)'))"
)


def category_sql(dataset: str, start: str, end: str) -> str:
    """Seen → carted by item_category.

    item_category is the listing context a product was shown in, not a fixed
    product attribute: in the public sample almost every product appears under
    several categories (a tee under Apparel, Google, Men's T-Shirts and Sale).
    parent_node, from the raw paths, lets the caller split listing pages into
    product types, brands, collections and parent nodes.

    Grain: one row per (session, item_category). A session "saw" a category
    when a product from it appeared in a viewed list, and "carted" it only if
    it added an item OF THAT CATEGORY — the one with quantity > 0, since
    add_to_cart carries the whole list — at or after the first sighting.
    Labels are normalised BEFORE the per-session grouping, so a session that
    saw both "Bags" and "Lifestyle/Bags/" counts once; merging rows afterwards
    would double-count it. A separate query, not a funnel split: only
    view_item / add_to_cart / purchase carry items, so session_start and
    begin_checkout have no category.
    """
    return f"""WITH item_events AS (
  SELECT user_pseudo_id,
    {param('ga_session_id')} AS session_id,
    event_name, event_timestamp,
    COALESCE(NULLIF(TRIM(it.item_category), ''), '(not set)') AS raw_category,
    IFNULL(it.quantity, 0) AS quantity,
    it.price AS price
  FROM `{dataset}.events_*`, UNNEST(items) AS it
  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
    AND event_name IN ('view_item','add_to_cart')
),
normalised AS (
  SELECT *,
    {CATEGORY_LABEL_SQL} AS item_category
  FROM item_events
),
labels AS (
  SELECT item_category,
    COUNT(DISTINCT raw_category) AS n_source_labels,
    STRING_AGG(DISTINCT raw_category, ' · ' ORDER BY raw_category) AS source_labels
  FROM normalised
  GROUP BY item_category
),
-- Parent node: the path segment above the label in the raw paths
-- ("Home/Apparel/Hats/" → Apparel, "Home/Sale/" → Home, "Lifestyle/Bags/" →
-- Lifestyle). Flat labels have none. The most frequent parent wins, ties by name.
parents AS (
  SELECT item_category,
    ARRAY_AGG(parent ORDER BY n DESC, parent LIMIT 1)[OFFSET(0)] AS parent_node
  FROM (
    SELECT item_category, parent, COUNT(*) AS n
    FROM (
      SELECT item_category,
        TRIM(REPLACE(segs[SAFE_OFFSET(ARRAY_LENGTH(segs) - 2)], ' | ', ' / ')) AS parent
      FROM (SELECT item_category,
              SPLIT(TRIM(REPLACE(raw_category, ' / ', ' | '), '/'), '/') AS segs
            FROM normalised WHERE raw_category != '(not set)')
    )
    WHERE parent IS NOT NULL AND parent != ''
    GROUP BY 1, 2
  )
  GROUP BY item_category
),
sc AS (
  SELECT user_pseudo_id, session_id, item_category,
    MIN(IF(event_name='view_item',   event_timestamp, NULL)) AS ts_first_view,
    MAX(IF(event_name='add_to_cart' AND quantity > 0, event_timestamp, NULL)) AS ts_last_cart
  FROM normalised WHERE session_id IS NOT NULL
  GROUP BY user_pseudo_id, session_id, item_category
),
agg AS (
  SELECT item_category,
    COUNTIF(ts_first_view IS NOT NULL) AS viewing_sessions,
    -- MAX(cart) >= MIN(view) ⇔ at least one add-to-cart at/after the first view
    COUNTIF(ts_first_view IS NOT NULL AND ts_last_cart >= ts_first_view) AS carting_sessions
  FROM sc
  GROUP BY item_category
),
-- Median listed price of the category's products as seen in product lists
-- (a confounder for seen → carted). Exact PERCENTILE_CONT, like every median here.
price AS (
  SELECT DISTINCT item_category,
    PERCENTILE_CONT(price, 0.5) OVER (PARTITION BY item_category) AS median_price
  FROM normalised
  WHERE event_name = 'view_item' AND price IS NOT NULL
)
SELECT agg.*, labels.n_source_labels, labels.source_labels, price.median_price,
  parents.parent_node
FROM agg JOIN labels USING (item_category) LEFT JOIN price USING (item_category)
  LEFT JOIN parents USING (item_category)
ORDER BY viewing_sessions DESC, item_category
"""


def category_overlap_sql(dataset: str, start: str, end: str) -> str:
    """How many products appear under more than one item_category (normalised
    as in category_sql) in view_item lists — the evidence that item_category is
    a listing context, not a product attribute. Returns one row per product,
    most-viewed first, with its categories."""
    return f"""WITH items AS (
  SELECT it.item_name,
    COALESCE(NULLIF(TRIM(it.item_category), ''), '(not set)') AS raw_category
  FROM `{dataset}.events_*`, UNNEST(items) AS it
  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
    AND event_name = 'view_item' AND it.item_name IS NOT NULL
),
normalised AS (
  SELECT item_name, {CATEGORY_LABEL_SQL} AS item_category FROM items
)
SELECT item_name,
  COUNT(*) AS sightings,
  COUNT(DISTINCT item_category) AS n_categories,
  STRING_AGG(DISTINCT item_category, ' · ' ORDER BY item_category) AS categories
FROM normalised
WHERE item_category != '(not set)'
GROUP BY item_name
ORDER BY sightings DESC, item_name
"""

PRICE_BANDS = ((10, "under $10"), (20, "$10–20"), (30, "$20–30"), (50, "$30–50"), (None, "$50+"))
"""Upper bound (exclusive) and label of each band, cheapest first."""


def _price_band_sql(col: str) -> str:
    parts = [f"WHEN {col} < {hi} THEN '{label}'" for hi, label in PRICE_BANDS if hi]
    return f"CASE WHEN {col} IS NULL OR {col} <= 0 THEN NULL {' '.join(parts)} ELSE '{PRICE_BANDS[-1][1]}' END"


def price_band_sql(dataset: str, start: str, end: str) -> str:
    """Seen → carted by the item's own price band — the same rule as
    category_sql (seen in a viewed list; carted only if the carted item, the one
    with quantity > 0, is in the band and was carted at or after the first
    sighting). Price is a product attribute, unlike item_category."""
    return f"""WITH item_events AS (
  SELECT user_pseudo_id,
    {param('ga_session_id')} AS session_id,
    event_name, event_timestamp,
    IFNULL(it.quantity, 0) AS quantity,
    {_price_band_sql('it.price')} AS price_band
  FROM `{dataset}.events_*`, UNNEST(items) AS it
  WHERE _TABLE_SUFFIX BETWEEN '{start}' AND '{end}'
    AND event_name IN ('view_item','add_to_cart')
),
sb AS (
  SELECT user_pseudo_id, session_id, price_band,
    MIN(IF(event_name='view_item', event_timestamp, NULL)) AS ts_first_view,
    MAX(IF(event_name='add_to_cart' AND quantity > 0, event_timestamp, NULL)) AS ts_last_cart
  FROM item_events WHERE session_id IS NOT NULL AND price_band IS NOT NULL
  GROUP BY user_pseudo_id, session_id, price_band
)
SELECT price_band,
  COUNTIF(ts_first_view IS NOT NULL) AS viewing_sessions,
  COUNTIF(ts_first_view IS NOT NULL AND ts_last_cart >= ts_first_view) AS carting_sessions
FROM sb
GROUP BY price_band
ORDER BY price_band
"""


def _page_path_sql() -> str:
    """The page_location path without host, query string or fragment."""
    return (f"REGEXP_REPLACE(REGEXP_REPLACE({param('page_location', 'string')}, "
            f"r'^https?://[^/]+', ''), r'[?#].*$', '')")


def landing_pages_sql(dataset: str, start: str, end: str, min_sessions: int = 100) -> str:
    """Funnel by landing page: the path (no host, no query string) of the
    session's first page_view. Uses the funnel's own session table, so the
    saw-a-product rate is the funnel's step 2. Pages with fewer than
    min_sessions landings are left out; '(no page_view)' collects sessions
    without one."""
    path = _page_path_sql()
    return sessions_cte(dataset, start, end, attributes=True) + f""",
landing AS (
  SELECT user_pseudo_id, session_id,
    ARRAY_AGG(path IGNORE NULLS ORDER BY event_timestamp, path LIMIT 1)[SAFE_OFFSET(0)] AS landing_page
  FROM (
    SELECT user_pseudo_id, {param('ga_session_id')} AS session_id, event_timestamp, {path} AS path
    FROM {events_from(dataset, start, end)}
      AND event_name = 'page_view'
  )
  WHERE session_id IS NOT NULL
  GROUP BY user_pseudo_id, session_id
)
SELECT COALESCE(l.landing_page, '(no page_view)') AS landing_page,
  COUNT(*) AS sessions,
  COUNTIF(s.s2) AS view_item,
  COUNTIF(s.s5) AS purchase,
  COUNTIF(s.session_number = 1) AS new_sessions,
  COUNTIF(s.n_medium = 0) AS unattributed_sessions
FROM sessions AS s LEFT JOIN landing AS l USING (user_pseudo_id, session_id)
WHERE s.s1
GROUP BY 1
HAVING COUNT(*) >= {int(min_sessions)}
ORDER BY sessions DESC, landing_page
"""


def landing_diagnosis_sql(dataset: str, start: str, end: str, pages: tuple = ("/",)) -> str:
    """Why a landing page rarely shows a product: page, stock, tracking or the
    traffic that lands there. For each page and week, the sessions that viewed it
    split by entry — 'landed' (its first page_view) or 'inside' (reached from
    another page) — with how many recorded view_item, scrolled, or carried no
    medium tag, and their engagement time on the page. A page that works for
    visitors who reach it from inside, but not for those who land on it, points
    at the traffic; a dated run of weeks with no view_item while behaviour is
    unchanged points at the tag. `pages` are paths without host or query."""
    path = _page_path_sql()
    page_list = ", ".join("'" + p.replace("\\", "").replace("'", "") + "'" for p in pages)
    # One pass over the events: each session's landing page and tag, plus its
    # events on the diagnosed pages as an array (a CTE referenced several times
    # would be recomputed each time, with all the parameter unnesting).
    return f"""WITH ev AS (
  SELECT user_pseudo_id, {param('ga_session_id')} AS session_id, event_name, event_timestamp, event_date,
    {path} AS page_path,
    {param('medium', 'string')} AS medium,
    IFNULL({param('engagement_time_msec')}, 0) AS eng_msec
  FROM {events_from(dataset, start, end)}
),
sess AS (
  SELECT user_pseudo_id, session_id,
    ARRAY_AGG(IF(event_name = 'page_view', page_path, NULL) IGNORE NULLS
              ORDER BY event_timestamp, page_path LIMIT 1)[SAFE_OFFSET(0)] AS landing_page,
    LOGICAL_OR(medium IS NOT NULL) AS has_medium,
    ARRAY_AGG(IF(page_path IN ({page_list}), STRUCT(page_path, event_name, event_date, eng_msec), NULL)
              IGNORE NULLS) AS hits
  FROM ev WHERE session_id IS NOT NULL
  GROUP BY 1, 2
  HAVING COUNTIF(page_path IN ({page_list})) > 0
),
on_page AS (   -- each session's activity on each diagnosed page
  SELECT s.user_pseudo_id, s.session_id, h.page_path, ANY_VALUE(s.landing_page) AS landing_page,
    ANY_VALUE(s.has_medium) AS has_medium,
    DATE_TRUNC(PARSE_DATE('%Y%m%d', MIN(h.event_date)), WEEK(MONDAY)) AS week,
    LOGICAL_OR(h.event_name = 'view_item') AS viewed_item,
    LOGICAL_OR(h.event_name = 'scroll') AS scrolled,
    SUM(h.eng_msec) / 1000 AS eng_s
  FROM sess AS s, UNNEST(s.hits) AS h
  GROUP BY 1, 2, 3
  HAVING LOGICAL_OR(h.event_name = 'page_view')
)
SELECT page_path, week,
  IF(landing_page = page_path, 'landed', 'inside') AS entry,
  COUNT(*) AS sessions,
  COUNTIF(viewed_item) AS view_item_sessions,
  COUNTIF(scrolled) AS scrolled_sessions,
  COUNTIF(NOT has_medium) AS untagged_sessions,
  ROUND(SUM(eng_s), 1) AS eng_s_total
FROM on_page
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
"""

def cohort_channel_sql(dataset: str, start: str, end: str) -> str:
    """Week-1 return by weekly cohort AND the channel of the user's first
    session in the window (the session's own medium, as in the channel
    funnel). Lets a change in Week-1 return be split into a change in channel
    mix and a change within channels. Cohorts as in cohort_sql."""
    return f"""WITH ev AS (
  SELECT user_pseudo_id, event_name, event_timestamp,
    {param('ga_session_id')} AS session_id,
    {param('medium', 'string')} AS medium,
    DATE_TRUNC(PARSE_DATE('%Y%m%d', event_date), WEEK(MONDAY)) AS wk
  FROM {events_from(dataset, start, end)}
),
first_session AS (
  SELECT user_pseudo_id,
    ARRAY_AGG(STRUCT(session_id, wk) ORDER BY event_timestamp, session_id LIMIT 1)[OFFSET(0)] AS f
  FROM ev WHERE event_name = 'session_start'
  GROUP BY user_pseudo_id
),
first_medium AS (
  SELECT fs.user_pseudo_id, fs.f.wk AS cohort_week,
    ARRAY_AGG(ev.medium IGNORE NULLS ORDER BY ev.event_timestamp, ev.medium LIMIT 1)[SAFE_OFFSET(0)] AS medium
  FROM first_session AS fs
  LEFT JOIN ev ON ev.user_pseudo_id = fs.user_pseudo_id AND ev.session_id = fs.f.session_id
  GROUP BY 1, 2
),
active AS (
  SELECT DISTINCT user_pseudo_id, wk FROM ev WHERE event_name = 'session_start'
)
SELECT m.cohort_week, COALESCE(m.medium, '(not set)') AS medium,
  COUNT(*) AS users,
  COUNTIF(a.user_pseudo_id IS NOT NULL) AS wk1_users
FROM first_medium AS m
LEFT JOIN active AS a
  ON a.user_pseudo_id = m.user_pseudo_id AND a.wk = DATE_ADD(m.cohort_week, INTERVAL 1 WEEK)
GROUP BY 1, 2
ORDER BY 1, 2
"""


def cohort_sql(dataset: str, start: str, end: str) -> str:
    """Weekly cohorts (Monday-anchored) by first session_start in the window,
    and the users active in each later calendar week."""
    return f"""WITH fs AS (
  SELECT user_pseudo_id,
    DATE_TRUNC(PARSE_DATE('%Y%m%d', MIN(event_date)), WEEK(MONDAY)) AS cohort_week
  FROM {events_from(dataset, start, end)}
    AND event_name = 'session_start'
  GROUP BY user_pseudo_id
),
act AS (
  SELECT DISTINCT user_pseudo_id,
    DATE_TRUNC(PARSE_DATE('%Y%m%d', event_date), WEEK(MONDAY)) AS activity_week
  FROM {events_from(dataset, start, end)}
    AND event_name = 'session_start'
)
SELECT f.cohort_week,
  DATE_DIFF(a.activity_week, f.cohort_week, WEEK) AS weeks_since_cohort,
  COUNT(DISTINCT a.user_pseudo_id) AS active_users
FROM fs f JOIN act a USING (user_pseudo_id)
WHERE a.activity_week >= f.cohort_week
GROUP BY f.cohort_week, weeks_since_cohort
ORDER BY f.cohort_week, weeks_since_cohort
"""


# ── Power BI extracts (E7) ─────────────────────────────────────────────────────
# Row-level tables for the Power BI model. Each reuses the dashboard's own query
# text up to its final SELECT (sliced at a marker, so the shared logic cannot
# drift) or the shared session table. powerbi_export.py runs them under the cost
# cap, or writes them out for the BigQuery connector.

def _upto(sql: str, marker: str) -> str:
    """The dashboard query's text up to (not including) `marker`; fails loudly
    if the marker moves, rather than silently extracting the wrong part."""
    i = sql.find(marker)
    if i < 0:
        raise ValueError(f"marker not found: {marker!r}")
    return sql[:i]


def pbi_sessions_sql(dataset: str, start: str, end: str) -> str:
    """fct_sessions: one row per funnel session (the funnel's 354,857 on the
    public sample) with its attributes and sequential step flags. Every rate in
    the Power BI model is a measure over this table, so slicers cut every rate
    the same way the dashboard's segment menu does."""
    return sessions_cte(dataset, start, end, revenue=True, attributes=True,
                        event_dims={"device_category": "device.category",
                                    "first_touch_medium": "traffic_source.medium",
                                    "country": "NULLIF(NULLIF(geo.country, ''), '(not set)')"}) + f""",
meta AS (   -- session date and landing page, from all of the session's events
  SELECT user_pseudo_id, {param('ga_session_id')} AS session_id,
    PARSE_DATE('%Y%m%d', MIN(event_date)) AS session_date,
    ARRAY_AGG(IF(event_name = 'page_view', {_page_path_sql()}, NULL) IGNORE NULLS
              ORDER BY event_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS landing_page
  FROM {events_from(dataset, start, end)}
  GROUP BY 1, 2
),
funnel AS (
  SELECT s.*, m.session_date, m.landing_page,
    COALESCE(s.session_medium, '(not set)') AS channel
  FROM sessions AS s LEFT JOIN meta AS m USING (user_pseudo_id, session_id)
  WHERE s.s1
),
small AS (   -- channels under 1% of sessions are pooled when there are 2+, as on the dashboard
  SELECT channel, COUNT(*) / SUM(COUNT(*)) OVER () < 0.01 AS is_small FROM funnel GROUP BY channel
),
n_small AS (SELECT COUNTIF(is_small) AS n FROM small)
SELECT
  CONCAT(f.user_pseudo_id, '-', CAST(f.session_id AS STRING)) AS session_key,
  f.user_pseudo_id, f.session_date,
  f.device_category, f.channel,
  IF(sm.is_small AND n_small.n > 1, CONCAT('other (', CAST(n_small.n AS STRING), ' small channels)'), f.channel)
    AS channel_group,
  COALESCE(f.first_touch_medium, '(not set)') AS first_touch_medium,
  COALESCE(f.country, '(not set)') AS country,
  CASE WHEN f.session_number = 1 THEN 'new' WHEN f.session_number > 1 THEN 'returning' ELSE 'unknown' END AS visitor_type,
  COALESCE(f.landing_page, '(no page_view)') AS landing_page,
  f.n_medium = 0 AS unattributed,
  f.n_events,
  f.s2 AS saw_product, f.s3 AS added_to_cart, f.s4 AS began_checkout, f.s5 AS purchased,
  f.ts_p IS NOT NULL AS purchased_any_order,
  IF(f.s5, IFNULL(f.revenue, 0), 0) AS revenue,
  IF(f.s5, (f.ts_p - f.ts_s) / 60000000.0, NULL) AS minutes_to_purchase
FROM funnel AS f JOIN small AS sm USING (channel) CROSS JOIN n_small
ORDER BY session_key
"""


def pbi_session_listing_sql(dataset: str, start: str, end: str) -> str:
    """fct_session_listing: one row per (session, listing page) the session saw
    — category_sql's own per-session step, so rates match the dashboard."""
    return _upto(category_sql(dataset, start, end), ",\nagg AS (") + """
SELECT CONCAT(user_pseudo_id, '-', CAST(session_id AS STRING)) AS session_key,
  item_category,
  IFNULL(ts_last_cart >= ts_first_view, FALSE) AS carted_after_view
FROM sc
WHERE ts_first_view IS NOT NULL
ORDER BY session_key, item_category
"""


def pbi_session_price_band_sql(dataset: str, start: str, end: str) -> str:
    """fct_session_price_band: one row per (session, price band) the session saw
    — price_band_sql's own per-session step."""
    return _upto(price_band_sql(dataset, start, end), "\nSELECT price_band,") + """
SELECT CONCAT(user_pseudo_id, '-', CAST(session_id AS STRING)) AS session_key,
  price_band,
  IFNULL(ts_last_cart >= ts_first_view, FALSE) AS carted_after_view
FROM sb
WHERE ts_first_view IS NOT NULL
ORDER BY session_key, price_band
"""


def pbi_users_sql(dataset: str, start: str, end: str) -> str:
    """dim_users: each user's Monday-anchored cohort week — cohort_sql's own
    first-session step."""
    return _upto(cohort_sql(dataset, start, end), "\nSELECT f.cohort_week,") + """
SELECT user_pseudo_id, cohort_week FROM fs ORDER BY user_pseudo_id
"""


def pbi_user_activity_sql(dataset: str, start: str, end: str) -> str:
    """fct_user_activity: one row per (user, week with a session_start) —
    cohort_sql's own activity step."""
    return _upto(cohort_sql(dataset, start, end), "\nSELECT f.cohort_week,") + """
SELECT user_pseudo_id, activity_week FROM act ORDER BY user_pseudo_id, activity_week
"""

