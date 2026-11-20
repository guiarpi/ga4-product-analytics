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
    ARRAY_AGG(medium IGNORE NULLS ORDER BY event_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS session_medium,
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
    ROUND(APPROX_QUANTILES(IF(revenue > 0, revenue, NULL),100)[OFFSET(50)],2) AS median_order_value
  FROM sessions WHERE s5
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
  APPROX_QUANTILES(engaged_s, 2)[OFFSET(1)] AS median_engaged_s,
  APPROX_QUANTILES(n_events, 2)[OFFSET(1)] AS median_events
FROM f
GROUP BY GROUPING SETS ((attributed), (size_bucket, attributed), (dev, attributed), (visitor))
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
)
SELECT t.*, cart.*
FROM (
  SELECT
    ROUND(APPROX_QUANTILES((ts_v-ts_s)/60000000.0,100)[OFFSET(50)],1) AS p50_s_to_v,
    ROUND(APPROX_QUANTILES((ts_c-ts_v)/60000000.0,100)[OFFSET(50)],1) AS p50_v_to_c,
    ROUND(APPROX_QUANTILES((ts_k-ts_c)/60000000.0,100)[OFFSET(50)],1) AS p50_c_to_k,
    ROUND(APPROX_QUANTILES((ts_p-ts_k)/60000000.0,100)[OFFSET(50)],1) AS p50_k_to_p,
    ROUND(APPROX_QUANTILES((ts_p-ts_s)/60000000.0,100)[OFFSET(50)],1) AS p50_total,
    COUNT(*) AS converting_sessions,
    COUNTIF(ts_c = ts_v) AS converting_same_ts
  FROM sessions WHERE s5
) t CROSS JOIN cart
"""


# ── Named funnels ─────────────────────────────────────────────────────────────

def session_funnel_sql(dataset: str, start: str, end: str) -> str:
    """The headline funnel: session_start → view_item → add_to_cart →
    begin_checkout → purchase, over all sessions."""
    return funnel_sql(dataset, start, end)


def device_funnel_sql(dataset: str, start: str, end: str) -> str:
    """The same funnel by device.category."""
    return funnel_sql(dataset, start, end, event_dims={"device_category": "device.category"},
                      group_by={"device_category": "device_category"}, order_by="sessions DESC")


def channel_funnel_sql(dataset: str, start: str, end: str) -> str:
    """The same funnel with revenue, by BOTH the session's own medium and the
    user's first-touch medium (traffic_source.medium), so the grain trap can be
    quantified. A session with no medium on any event is '(not set)'."""
    return funnel_sql(dataset, start, end, revenue=True, attributes=True,
                      event_dims={"first_touch_medium": "traffic_source.medium"},
                      group_by={"session_medium": "COALESCE(session_medium, '(not set)')",
                                "first_touch_medium": "COALESCE(first_touch_medium, '(not set)')"})


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
ORDER BY event_count DESC
"""


def unique_users_sql(dataset: str, start: str, end: str) -> str:
    return f"""SELECT COUNT(DISTINCT user_pseudo_id) AS unique_users
FROM {events_from(dataset, start, end)}
  AND event_name = 'session_start'
"""


def event_quality_sql(dataset: str, start: str, end: str) -> str:
    """What view_item and add_to_cart actually record. A list-sized items array
    means a listing impression / quick-add, not a product-page view; on
    add_to_cart only the item with quantity > 0 was actually carted."""
    return f"""SELECT event_name, COUNT(*) AS events,
  ROUND(AVG(ARRAY_LENGTH(items)), 1) AS avg_items,
  COUNTIF(ARRAY_LENGTH(items) > 1) AS multi_item_events,
  COUNTIF((SELECT COUNTIF(IFNULL(it.quantity, 0) > 0) FROM UNNEST(items) AS it) = 1) AS one_carted_item_events
FROM {events_from(dataset, start, end)}
  AND event_name IN ('view_item', 'add_to_cart')
GROUP BY event_name
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
    """Seen → carted by product category.

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
-- (a confounder for seen → carted). Exact PERCENTILE_CONT, not APPROX_QUANTILES,
-- so repeated runs and the notebook and dashboard always agree.
price AS (
  SELECT DISTINCT item_category,
    PERCENTILE_CONT(price, 0.5) OVER (PARTITION BY item_category) AS median_price
  FROM normalised
  WHERE event_name = 'view_item' AND price IS NOT NULL
)
SELECT agg.*, labels.n_source_labels, labels.source_labels, price.median_price
FROM agg JOIN labels USING (item_category) LEFT JOIN price USING (item_category)
ORDER BY viewing_sessions DESC
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
