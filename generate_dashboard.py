#!/usr/bin/env python3
"""
generate_dashboard.py
---------------------
Runs all BigQuery analysis queries and produces a standalone dashboard.html
that anyone can open in a browser — no Python, no Jupyter required.

Usage:
    python generate_dashboard.py

Project, dataset and date window come from config.py and can be overridden
with GA4_PROJECT_ID, GA4_DATASET, GA4_DATE_START and GA4_DATE_END — see the
README. Defaults run against Google's public GA4 sample.

Output: dashboard.html
"""

import warnings
warnings.filterwarnings("ignore")

import html as _html
import re
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs

# ── Config ─────────────────────────────────────────────────────────────────────
# All run settings live in config.py (environment-variable overrides); nothing
# about the project, dataset or window is hardcoded in this script.
from config import (DATASET, DATE_START, DATE_END, IS_PUBLIC_SAMPLE,
                    make_client, describe)
from stats_utils import wilson_ci, chi_square, format_p
from metrics import site_average, flag_categories
from md_render import md_to_html

def fmt_p(p: float) -> str:
    """format_p, HTML-escaped (\"p < 0.001\" must not reach the page as a raw '<')."""
    return _html.escape(format_p(p))

HERE = Path(__file__).resolve().parent     # paths work from any working directory
OUTPUT = HERE / "dashboard.html"

client = make_client()
# Console only (never saved), so the project is shown here and nowhere else.
print(f"Connected  |  {describe(client, include_project=True)}")
if not IS_PUBLIC_SAMPLE:
    print("  NOTE: the 'About This Dataset' card and some recommendation text describe "
          "the public sample — review them before sharing this dashboard.")


def q(sql: str) -> pd.DataFrame:
    return client.query(sql).to_dataframe()

def chart_div(fig, div_id: str) -> str:
    return fig.to_html(full_html=False, include_plotlyjs=False, div_id=div_id)


# ══════════════════════════════════════════════════════════════════════════════
# QUERIES
# ══════════════════════════════════════════════════════════════════════════════

# ── 1. Session funnel ─────────────────────────────────────────────────────────
print("Querying session funnel...")
funnel_raw = q(f"""
WITH base AS (
  SELECT user_pseudo_id,
    (SELECT ep.value.int_value FROM UNNEST(event_params) AS ep
     WHERE ep.key = 'ga_session_id' LIMIT 1) AS session_id,
    event_name, event_timestamp
  FROM `{DATASET}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{DATE_START}' AND '{DATE_END}'
    AND event_name IN ('session_start','view_item','add_to_cart','begin_checkout','purchase')
),
se AS (
  SELECT user_pseudo_id, session_id, event_name, MIN(event_timestamp) AS first_ts
  FROM base WHERE session_id IS NOT NULL
  GROUP BY user_pseudo_id, session_id, event_name
),
pv AS (
  SELECT user_pseudo_id, session_id,
    MAX(CASE WHEN event_name='session_start'  THEN first_ts END) AS ts_s,
    MAX(CASE WHEN event_name='view_item'      THEN first_ts END) AS ts_v,
    MAX(CASE WHEN event_name='add_to_cart'    THEN first_ts END) AS ts_c,
    MAX(CASE WHEN event_name='begin_checkout' THEN first_ts END) AS ts_k,
    MAX(CASE WHEN event_name='purchase'       THEN first_ts END) AS ts_p
  FROM se GROUP BY user_pseudo_id, session_id
)
SELECT
  COUNT(*) AS step1_sessions,
  COUNTIF(ts_v IS NOT NULL AND ts_v>=ts_s) AS step2_view_item,
  COUNTIF(ts_c IS NOT NULL AND ts_c>=ts_v AND ts_v>=ts_s) AS step3_add_to_cart,
  COUNTIF(ts_k IS NOT NULL AND ts_k>=ts_c AND ts_c>=ts_v AND ts_v>=ts_s) AS step4_begin_checkout,
  COUNTIF(ts_p IS NOT NULL AND ts_p>=ts_k AND ts_k>=ts_c AND ts_c>=ts_v AND ts_v>=ts_s) AS step5_purchase
FROM pv WHERE ts_s IS NOT NULL
""")

# "View Products", not "View Item": in this export view_item fires on listing and
# home pages with ~12 items per event (see the data-quality checks), so the step
# means "saw products", not "opened a product page".
step_labels = ["Session Start","View Products","Add to Cart","Begin Checkout","Purchase"]
step_cols   = ["step1_sessions","step2_view_item","step3_add_to_cart",
               "step4_begin_checkout","step5_purchase"]
funnel_df = pd.DataFrame({
    "step":     step_labels,
    "sessions": [int(funnel_raw[c].iloc[0]) for c in step_cols],
})
funnel_df["step_cvr"]    = (funnel_df["sessions"] / funnel_df["sessions"].shift(1) * 100).round(1)
funnel_df["overall_cvr"] = (funnel_df["sessions"] / funnel_df["sessions"].iloc[0] * 100).round(1)
funnel_df["drop_off"]    = (100 - funnel_df["step_cvr"]).round(1)
funnel_df["lost"]        = funnel_df["sessions"] - funnel_df["sessions"].shift(-1).fillna(funnel_df["sessions"])
funnel_df["lost"]        = funnel_df["lost"].clip(lower=0).astype(int)

# ── 1b. Unique users ──────────────────────────────────────────────────────────
print("Querying unique users...")
unique_users_df = q(f"""
SELECT COUNT(DISTINCT user_pseudo_id) AS unique_users
FROM `{DATASET}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{DATE_START}' AND '{DATE_END}'
  AND event_name = 'session_start'
""")
unique_users = int(unique_users_df["unique_users"].iloc[0])

# ── 2. Revenue ────────────────────────────────────────────────────────────────
print("Querying revenue...")
revenue_df = q(f"""
WITH base AS (
  SELECT user_pseudo_id,
    (SELECT ep.value.int_value FROM UNNEST(event_params) AS ep
     WHERE ep.key = 'ga_session_id' LIMIT 1) AS session_id,
    event_name, event_timestamp,
    ecommerce.purchase_revenue AS purchase_revenue
  FROM `{DATASET}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{DATE_START}' AND '{DATE_END}'
    AND event_name IN ('session_start','view_item','add_to_cart','begin_checkout','purchase')
),
se AS (
  SELECT user_pseudo_id, session_id, event_name,
    MIN(event_timestamp) AS first_ts, SUM(purchase_revenue) AS rev
  FROM base WHERE session_id IS NOT NULL
  GROUP BY user_pseudo_id, session_id, event_name
),
pv AS (
  SELECT user_pseudo_id, session_id,
    MAX(CASE WHEN event_name='session_start'  THEN first_ts END) AS ts_s,
    MAX(CASE WHEN event_name='view_item'      THEN first_ts END) AS ts_v,
    MAX(CASE WHEN event_name='add_to_cart'    THEN first_ts END) AS ts_c,
    MAX(CASE WHEN event_name='begin_checkout' THEN first_ts END) AS ts_k,
    MAX(CASE WHEN event_name='purchase'       THEN first_ts END) AS ts_p,
    MAX(CASE WHEN event_name='purchase'       THEN rev END) AS revenue
  FROM se GROUP BY user_pseudo_id, session_id
),
conv AS (SELECT * FROM pv
  WHERE ts_s IS NOT NULL AND ts_v>=ts_s AND ts_c>=ts_v AND ts_k>=ts_c AND ts_p>=ts_k),
-- Every session with a purchase, sequential or not: the funnel's revenue is a
-- subset of this, and the gap is disclosed rather than hidden.
allp AS (
  SELECT COUNT(*) AS all_purchasing_sessions,
    ROUND(SUM(revenue),2) AS all_revenue,
    COUNTIF(ts_c IS NULL) AS purchases_without_cart
  FROM pv WHERE ts_p IS NOT NULL
)
SELECT c.*, allp.*
FROM (
  SELECT
    COUNT(*) AS purchasing_sessions,
    -- Some purchase events carry no purchase_revenue. AOV is averaged over the
    -- orders that do, so both counts are returned to keep the figures reconcilable.
    COUNTIF(revenue > 0) AS orders_with_revenue,
    ROUND(SUM(revenue),2) AS total_revenue,
    ROUND(AVG(IF(revenue > 0, revenue, NULL)),2) AS avg_order_value,
    ROUND(APPROX_QUANTILES(IF(revenue > 0, revenue, NULL),100)[OFFSET(50)],2) AS median_order_value
  FROM conv
) c CROSS JOIN allp
""")

# ── 2b. Data quality: sessions with no session-level medium ───────────────────
# About a quarter of sessions carry no 'medium' on any event. This query tests
# whether they are a distinct population (bots, a broken channel) or ordinary
# short sessions that simply lack the tag: the medium param is attached to only
# some events, so the fewer events a session has, the likelier it has none.
# Grouping sets give the overall split, a split by session length, and a split
# by device in one scan.
# The same per-session scan also carries ga_session_number, so the new vs
# returning split (E3) is computed on exactly the same session basis.
print("Querying session-level splits (data quality, new vs returning)...")
dq_raw = q(f"""
WITH ev AS (
  SELECT user_pseudo_id, event_name, event_timestamp, device.category AS dev,
    ecommerce.purchase_revenue AS purchase_revenue,
    (SELECT ep.value.int_value FROM UNNEST(event_params) AS ep
     WHERE ep.key = 'ga_session_id' LIMIT 1) AS session_id,
    (SELECT ep.value.string_value FROM UNNEST(event_params) AS ep
     WHERE ep.key = 'medium' LIMIT 1) AS medium,
    (SELECT ep.value.int_value FROM UNNEST(event_params) AS ep
     WHERE ep.key = 'engagement_time_msec' LIMIT 1) AS eng_msec,
    (SELECT ep.value.int_value FROM UNNEST(event_params) AS ep
     WHERE ep.key = 'ga_session_number' LIMIT 1) AS session_number
  FROM `{DATASET}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{DATE_START}' AND '{DATE_END}'
),
s AS (
  SELECT user_pseudo_id, session_id, ANY_VALUE(dev) AS dev,
    COUNT(*) AS n_events, COUNTIF(medium IS NOT NULL) AS n_medium,
    COUNTIF(event_name = 'page_view') AS n_page_view,
    SUM(IFNULL(eng_msec, 0)) / 1000 AS engaged_s,
    -- GA4 counts sessions per user from the first visit ever, not from the
    -- start of this window, so 1 = genuinely new (for that cookie).
    MAX(session_number) AS session_number,
    MIN(IF(event_name='session_start',  event_timestamp, NULL)) AS ts_s,
    MIN(IF(event_name='view_item',      event_timestamp, NULL)) AS ts_v,
    MIN(IF(event_name='add_to_cart',    event_timestamp, NULL)) AS ts_c,
    MIN(IF(event_name='begin_checkout', event_timestamp, NULL)) AS ts_k,
    MIN(IF(event_name='purchase',       event_timestamp, NULL)) AS ts_p,
    SUM(IF(event_name='purchase', purchase_revenue, NULL))      AS revenue
  FROM ev WHERE session_id IS NOT NULL
  GROUP BY user_pseudo_id, session_id
),
f AS (
  SELECT *,
    n_medium > 0 AS attributed,
    CASE WHEN session_number = 1 THEN 'new' WHEN session_number > 1 THEN 'returning'
         ELSE 'unknown' END AS visitor,
    CASE WHEN n_events <= 3 THEN '1–3' WHEN n_events <= 5 THEN '4–5'
         WHEN n_events <= 8 THEN '6–8' WHEN n_events <= 15 THEN '9–15'
         ELSE '16+' END AS size_bucket,
    IFNULL(ts_v >= ts_s, FALSE) AS s2,
    IFNULL(ts_c >= ts_v AND ts_v >= ts_s, FALSE) AS s3,
    IFNULL(ts_k >= ts_c AND ts_c >= ts_v AND ts_v >= ts_s, FALSE) AS s4,
    IFNULL(ts_p >= ts_k AND ts_k >= ts_c AND ts_c >= ts_v AND ts_v >= ts_s, FALSE) AS s5
  FROM s WHERE ts_s IS NOT NULL
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
""")

# ── 2c. Event-quality checks: what view_item and add_to_cart actually record ──
# view_item and add_to_cart carry an items array. If it holds a whole product
# list rather than one product, the event is a listing impression / quick-add,
# not a product-page view — which changes how "product page" claims can be made.
# Only items with quantity > 0 on add_to_cart are the item actually carted.
print("Querying event-quality checks...")
evq_raw = q(f"""
SELECT event_name, COUNT(*) AS events,
  ROUND(AVG(ARRAY_LENGTH(items)), 1) AS avg_items,
  COUNTIF(ARRAY_LENGTH(items) > 1) AS multi_item_events,
  COUNTIF((SELECT COUNTIF(IFNULL(it.quantity, 0) > 0) FROM UNNEST(items) AS it) = 1) AS one_carted_item_events
FROM `{DATASET}.events_*`
WHERE _TABLE_SUFFIX BETWEEN '{DATE_START}' AND '{DATE_END}'
  AND event_name IN ('view_item', 'add_to_cart')
GROUP BY event_name
""").set_index("event_name")

# ── 3. Device breakdown ───────────────────────────────────────────────────────
# Rendered as a summary table rather than a grouped bar chart: with all three
# devices tracking within ~0.1pp of each other, a chart encodes three nearly
# identical lines and invites the reader to hunt for a difference that isn't
# there. The table states the finding directly — device is not the lever.
print("Querying device breakdown...")
device_raw = q(f"""
WITH base AS (
  SELECT user_pseudo_id, device.category AS dev,
    (SELECT ep.value.int_value FROM UNNEST(event_params) AS ep
     WHERE ep.key = 'ga_session_id' LIMIT 1) AS session_id,
    event_name, event_timestamp
  FROM `{DATASET}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{DATE_START}' AND '{DATE_END}'
    AND event_name IN ('session_start','view_item','add_to_cart','begin_checkout','purchase')
),
se AS (
  SELECT user_pseudo_id, session_id, ANY_VALUE(dev) AS dev,
    event_name, MIN(event_timestamp) AS first_ts
  FROM base WHERE session_id IS NOT NULL
  GROUP BY user_pseudo_id, session_id, event_name
),
pv AS (
  SELECT user_pseudo_id, session_id, ANY_VALUE(dev) AS dev,
    MAX(CASE WHEN event_name='session_start'  THEN first_ts END) AS ts_s,
    MAX(CASE WHEN event_name='view_item'      THEN first_ts END) AS ts_v,
    MAX(CASE WHEN event_name='add_to_cart'    THEN first_ts END) AS ts_c,
    MAX(CASE WHEN event_name='begin_checkout' THEN first_ts END) AS ts_k,
    MAX(CASE WHEN event_name='purchase'       THEN first_ts END) AS ts_p
  FROM se GROUP BY user_pseudo_id, session_id
)
SELECT dev AS device_category,
  COUNT(*) AS sessions,
  COUNTIF(ts_v IS NOT NULL AND ts_v>=ts_s) AS view_item,
  COUNTIF(ts_c IS NOT NULL AND ts_c>=ts_v AND ts_v>=ts_s) AS add_to_cart,
  COUNTIF(ts_p IS NOT NULL AND ts_p>=ts_k AND ts_k>=ts_c AND ts_c>=ts_v AND ts_v>=ts_s) AS purchase
FROM pv WHERE ts_s IS NOT NULL
GROUP BY dev ORDER BY sessions DESC
""")

device_tbl = device_raw.copy()
device_tbl["share"]      = device_tbl["sessions"] / device_tbl["sessions"].sum() * 100
device_tbl["view_pct"]   = device_tbl["view_item"]   / device_tbl["sessions"] * 100
device_tbl["cart_pct"]   = device_tbl["add_to_cart"] / device_tbl["sessions"] * 100
device_tbl["purch_pct"]  = device_tbl["purchase"]    / device_tbl["sessions"] * 100

device_gap = round(float(device_tbl["purch_pct"].max()) - float(device_tbl["purch_pct"].min()), 2)

# E6 — is the device gap more than sampling noise? Wilson 95% intervals per
# device and a chi-square test of purchase rate × device.
device_tbl["ci"] = [wilson_ci(p, n) for p, n in zip(device_tbl["purchase"], device_tbl["sessions"])]
device_test = chi_square(device_tbl["purchase"], device_tbl["sessions"])
device_significant = device_test["p"] < 0.05
top_device = device_tbl.loc[device_tbl["purch_pct"].idxmax(), "device_category"]

device_rows_html = "\n".join(
    f'<tr><td class="dev-name">{r.device_category}</td>'
    f'<td>{int(r.sessions):,}</td>'
    f'<td>{r.share:.1f}%</td>'
    f'<td>{r.view_pct:.1f}%</td>'
    f'<td>{r.cart_pct:.1f}%</td>'
    f'<td class="dev-key">{r.purch_pct:.2f}%</td>'
    f'<td class="ci">{r.ci[0]:.2f}–{r.ci[1]:.2f}%</td></tr>'
    for r in device_tbl.itertuples()
)

# ── 3b. Acquisition channel breakdown (E1) ────────────────────────────────────
# Question: is the ~79% "never views a product" loss a site-navigation problem
# (uniform across channels) or a traffic-quality problem (concentrated in some)?
#
# Grain matters here. traffic_source.medium on the GA4 export is the USER's
# first-touch medium — it is the same on every session that user ever has, so
# grouping a session funnel by it misattributes returning sessions. The
# session-level medium lives in event_params ('medium'), so it is taken from the
# earliest event in the session that carries one. Both are returned; the
# session-level value is used unless its coverage is too thin (see fallback).
print("Querying acquisition channel breakdown...")
channel_raw = q(f"""
WITH base AS (
  SELECT user_pseudo_id,
    (SELECT ep.value.int_value FROM UNNEST(event_params) AS ep
     WHERE ep.key = 'ga_session_id' LIMIT 1) AS session_id,
    traffic_source.medium AS ft_medium,
    event_name, event_timestamp,
    ecommerce.purchase_revenue AS purchase_revenue
  FROM `{DATASET}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{DATE_START}' AND '{DATE_END}'
    AND event_name IN ('session_start','view_item','add_to_cart','begin_checkout','purchase')
),
sm AS (
  -- Session medium: value on the earliest event in the session that has one.
  -- Scans all event types — medium is not guaranteed on the funnel events.
  SELECT user_pseudo_id, session_id,
    ARRAY_AGG(medium ORDER BY event_timestamp LIMIT 1)[OFFSET(0)] AS session_medium
  FROM (
    SELECT user_pseudo_id, event_timestamp,
      (SELECT ep.value.int_value FROM UNNEST(event_params) AS ep
       WHERE ep.key = 'ga_session_id' LIMIT 1) AS session_id,
      (SELECT ep.value.string_value FROM UNNEST(event_params) AS ep
       WHERE ep.key = 'medium' LIMIT 1) AS medium
    FROM `{DATASET}.events_*`
    WHERE _TABLE_SUFFIX BETWEEN '{DATE_START}' AND '{DATE_END}'
  )
  WHERE session_id IS NOT NULL AND medium IS NOT NULL
  GROUP BY user_pseudo_id, session_id
),
se AS (
  SELECT user_pseudo_id, session_id, ANY_VALUE(ft_medium) AS ft_medium,
    event_name, MIN(event_timestamp) AS first_ts, SUM(purchase_revenue) AS rev
  FROM base WHERE session_id IS NOT NULL
  GROUP BY user_pseudo_id, session_id, event_name
),
pv AS (
  SELECT user_pseudo_id, session_id, ANY_VALUE(ft_medium) AS ft_medium,
    MAX(CASE WHEN event_name='session_start'  THEN first_ts END) AS ts_s,
    MAX(CASE WHEN event_name='view_item'      THEN first_ts END) AS ts_v,
    MAX(CASE WHEN event_name='add_to_cart'    THEN first_ts END) AS ts_c,
    MAX(CASE WHEN event_name='begin_checkout' THEN first_ts END) AS ts_k,
    MAX(CASE WHEN event_name='purchase'       THEN first_ts END) AS ts_p,
    MAX(CASE WHEN event_name='purchase'       THEN rev END)      AS revenue
  FROM se GROUP BY user_pseudo_id, session_id
)
SELECT
  COALESCE(sm.session_medium, '(not set)') AS session_medium,
  COALESCE(pv.ft_medium,      '(not set)') AS first_touch_medium,
  COUNT(*) AS sessions,
  COUNTIF(ts_v IS NOT NULL AND ts_v>=ts_s) AS view_item,
  COUNTIF(ts_c IS NOT NULL AND ts_c>=ts_v AND ts_v>=ts_s) AS add_to_cart,
  COUNTIF(ts_p IS NOT NULL AND ts_p>=ts_k AND ts_k>=ts_c AND ts_c>=ts_v AND ts_v>=ts_s) AS purchase,
  ROUND(SUM(IF(ts_p IS NOT NULL AND ts_p>=ts_k AND ts_k>=ts_c AND ts_c>=ts_v AND ts_v>=ts_s,
               IFNULL(revenue, 0), 0)), 2) AS revenue
FROM pv LEFT JOIN sm USING (user_pseudo_id, session_id)
WHERE ts_s IS NOT NULL
GROUP BY session_medium, first_touch_medium
""")

# Fallback: if most sessions carry no session-level medium, the session view is
# mostly "(not set)" and says nothing — use first-touch medium and label it.
_ch_metrics = ["sessions", "view_item", "add_to_cart", "purchase", "revenue"]
session_medium_coverage = 1 - (
    channel_raw.loc[channel_raw["session_medium"] == "(not set)", "sessions"].sum()
    / channel_raw["sessions"].sum()
)
if session_medium_coverage >= 0.5:
    channel_dim, channel_basis = "session_medium", "session-level medium (event_params)"
else:
    channel_dim, channel_basis = "first_touch_medium", "user first-touch medium (traffic_source.medium)"
    print("  NOTE: channel fell back to first-touch medium — the dashboard's (not set) "
          "explanation describes the session-level tag and should be reviewed.")

channel_tbl = (channel_raw.groupby(channel_dim, as_index=False)[_ch_metrics].sum()
               .rename(columns={channel_dim: "channel"}))

# Reconciliation: every session must land in exactly one channel. A mismatch
# means the LEFT JOIN duplicated or dropped sessions.
_ch_total = int(channel_tbl["sessions"].sum())
if _ch_total != int(funnel_raw["step1_sessions"].iloc[0]):
    print(f"  WARNING: channel sessions ({_ch_total:,}) != funnel sessions "
          f"({int(funnel_raw['step1_sessions'].iloc[0]):,}) — check the sm join")

# Collapse channels under 1% of sessions into one row so the chart is not
# dominated by noise from tiny denominators.
_small = channel_tbl["sessions"] / _ch_total < 0.01
if _small.sum() > 1:
    _other = channel_tbl[_small][_ch_metrics].sum().to_frame().T
    _other.insert(0, "channel", f"other ({int(_small.sum())} small channels)")
    channel_tbl = pd.concat([channel_tbl[~_small], _other], ignore_index=True)

channel_tbl[_ch_metrics]         = channel_tbl[_ch_metrics].astype(float)
channel_tbl["share"]             = channel_tbl["sessions"] / _ch_total * 100
channel_tbl["view_pct"]          = channel_tbl["view_item"] / channel_tbl["sessions"] * 100
channel_tbl["cart_from_view"]    = (channel_tbl["add_to_cart"] /
                                    channel_tbl["view_item"].where(channel_tbl["view_item"] > 0) * 100)
channel_tbl["purch_pct"]         = channel_tbl["purchase"] / channel_tbl["sessions"] * 100
channel_tbl["rev_per_session"]   = channel_tbl["revenue"] / channel_tbl["sessions"]
# Fewer than 30 purchases → the purchase rate is too noisy to rank on.
channel_tbl["low_n"]             = channel_tbl["purchase"] < 30
channel_tbl = channel_tbl.sort_values("sessions", ascending=False).reset_index(drop=True)

# Spread in view-item rate across real channels (excludes the "other" bucket
# and "(not set)", which are not channels anyone can act on).
_ch_rank = channel_tbl[~channel_tbl["channel"].str.startswith(("other (", "(not set)"))]
if _ch_rank.empty:
    _ch_rank = channel_tbl
_best_ch  = _ch_rank.loc[_ch_rank["view_pct"].idxmax()]
_worst_ch = _ch_rank.loc[_ch_rank["view_pct"].idxmin()]
channel_view_spread = round(float(_best_ch["view_pct"] - _worst_ch["view_pct"]), 1)
best_view_channel,  best_view_pct  = _html.escape(_best_ch["channel"]),  float(_best_ch["view_pct"])
worst_view_channel, worst_view_pct = _html.escape(_worst_ch["channel"]), float(_worst_ch["view_pct"])
_ch_rps = _ch_rank[~_ch_rank["low_n"]]
top_rev_channel = (_html.escape(_ch_rps.loc[_ch_rps["rev_per_session"].idxmax(), "channel"])
                   if len(_ch_rps) else None)
top_rev_rps     = (float(_ch_rps["rev_per_session"].max()) if len(_ch_rps) else None)

# 10pp is the decision threshold: below it, every channel loses roughly the
# same share of visitors before a product page — the leak is on the site.
channel_is_driver = channel_view_spread >= 10
# Within 3pp of the threshold the verdict is reported as borderline rather than
# settled — a few points of obfuscation noise could move it either way.
channel_borderline = 7 <= channel_view_spread < 10

# E6 — the spread is tested too, but with this many sessions even a trivial gap
# is "significant"; the site-vs-traffic verdict rests on practical size (10pp).
channel_test = chi_square(_ch_rank["view_item"], _ch_rank["sessions"])

# Sessions with no session-level medium are excluded from the spread, but they
# are a large block and they move the site average, so they are disclosed.
_ns = channel_tbl[channel_tbl["channel"] == "(not set)"]
notset_share    = float(_ns["share"].iloc[0])    if len(_ns) else 0.0
notset_view_pct = float(_ns["view_pct"].iloc[0]) if len(_ns) else None

def _channel_row(r) -> str:
    # Built outside an f-string expression: backslashes inside f-string
    # expressions are a SyntaxError before Python 3.12.
    cart = "—" if pd.isna(r.cart_from_view) else f"{r.cart_from_view:.1f}%"
    flag = ' <span title="fewer than 30 purchases">*</span>' if r.low_n else ""
    return (f'<tr><td class="dev-name">{_html.escape(r.channel)}</td>'
            f'<td>{int(r.sessions):,}</td>'
            f'<td>{r.share:.1f}%</td>'
            f'<td class="dev-key">{r.view_pct:.1f}%</td>'
            f'<td>{cart}</td>'
            f'<td>{r.purch_pct:.2f}%{flag}</td>'
            f'<td>${r.rev_per_session:,.2f}</td></tr>')

channel_rows_html = "\n".join(_channel_row(r) for r in channel_tbl.itertuples())

# ── 4. Shoppers funnel (entry at view_item) ───────────────────────────────────
print("Querying shoppers funnel...")
shoppers_raw = q(f"""
WITH base AS (
  SELECT user_pseudo_id,
    (SELECT ep.value.int_value FROM UNNEST(event_params) AS ep
     WHERE ep.key = 'ga_session_id' LIMIT 1) AS session_id,
    event_name, event_timestamp
  FROM `{DATASET}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{DATE_START}' AND '{DATE_END}'
    AND event_name IN ('view_item','add_to_cart','begin_checkout','purchase')
),
se AS (
  SELECT user_pseudo_id, session_id, event_name, MIN(event_timestamp) AS first_ts
  FROM base WHERE session_id IS NOT NULL
  GROUP BY user_pseudo_id, session_id, event_name
),
pv AS (
  SELECT user_pseudo_id, session_id,
    MAX(CASE WHEN event_name='view_item'      THEN first_ts END) AS ts_v,
    MAX(CASE WHEN event_name='add_to_cart'    THEN first_ts END) AS ts_c,
    MAX(CASE WHEN event_name='begin_checkout' THEN first_ts END) AS ts_k,
    MAX(CASE WHEN event_name='purchase'       THEN first_ts END) AS ts_p
  FROM se GROUP BY user_pseudo_id, session_id
)
SELECT
  COUNT(*) AS step1_view_item,
  COUNTIF(ts_c IS NOT NULL AND ts_c>=ts_v) AS step2_add_to_cart,
  COUNTIF(ts_k IS NOT NULL AND ts_k>=ts_c AND ts_c>=ts_v) AS step3_begin_checkout,
  COUNTIF(ts_p IS NOT NULL AND ts_p>=ts_k AND ts_k>=ts_c AND ts_c>=ts_v) AS step4_purchase
FROM pv WHERE ts_v IS NOT NULL
""")

# ── 4b. Product category view → cart (E2) ─────────────────────────────────────
# Question: the second-largest leak is view_item → add_to_cart. Which product
# categories is it concentrated in? This turns "audit the product pages" into a
# ranked list of where to start.
#
# Grain: one row per (session, item_category). A session counts as "carting" a
# category only if it added an item OF THAT CATEGORY at or after first viewing
# that category in the same session. Adding a different category's item does
# not count — so this rate is stricter than the session-level shoppers ATC
# rate, and category rows are not additive (one session can view several).
#
# This is deliberately a separate query, not an extension of the session
# funnel CTE: the items array only exists on view_item / add_to_cart / purchase,
# so session_start and begin_checkout have no category to group by.
MIN_CATEGORY_VIEWS = 500   # categories below this are too noisy to rank
print("Querying product category view → cart...")
category_raw = q(f"""
WITH item_events AS (
  SELECT user_pseudo_id,
    (SELECT ep.value.int_value FROM UNNEST(event_params) AS ep
     WHERE ep.key = 'ga_session_id' LIMIT 1) AS session_id,
    event_name, event_timestamp,
    COALESCE(NULLIF(TRIM(it.item_category), ''), '(not set)') AS raw_category,
    -- add_to_cart events carry the whole product list (~11 items); only the
    -- item with quantity > 0 was actually carted. view_item rows have no quantity.
    IFNULL(it.quantity, 0) AS quantity,
    it.price AS price
  FROM `{DATASET}.events_*`, UNNEST(items) AS it
  WHERE _TABLE_SUFFIX BETWEEN '{DATE_START}' AND '{DATE_END}'
    AND event_name IN ('view_item','add_to_cart')
),
normalised AS (
  -- item_category mixes flat labels ("Bags") with paths ("Lifestyle/Bags/").
  -- Keep the last path segment so both land in one bucket. Normalising here,
  -- before the per-session grouping, means a session that viewed both labels
  -- is counted once — merging rows afterwards in pandas would double-count it.
  -- ' / ' (slash with spaces) is part of a name, e.g. "Men's / Unisex", not a separator.
  SELECT *,
    IF(raw_category = '(not set)', raw_category,
       COALESCE(NULLIF(TRIM(REPLACE(ARRAY_REVERSE(SPLIT(TRIM(REPLACE(raw_category, ' / ', ' | '), '/'), '/'))[SAFE_OFFSET(0)], ' | ', ' / ')), ''),
                '(not set)')) AS item_category
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
-- Median listed price of the category's products as seen in product lists:
-- price is a confounder for seen → carted (cheap items are an easier yes).
price AS (
  -- Exact median (PERCENTILE_CONT), not APPROX_QUANTILES: the approximate one
  -- varies slightly between runs, and the notebook and dashboard must agree.
  SELECT DISTINCT item_category,
    PERCENTILE_CONT(price, 0.5) OVER (PARTITION BY item_category) AS median_price
  FROM normalised
  WHERE event_name = 'view_item' AND price IS NOT NULL
)
SELECT agg.*, labels.n_source_labels, labels.source_labels, price.median_price
FROM agg JOIN labels USING (item_category) LEFT JOIN price USING (item_category)
ORDER BY viewing_sessions DESC
""")

_cat_all_views   = int(category_raw["viewing_sessions"].sum())
category_raw_labels    = int(category_raw["n_source_labels"].sum())
category_merged_groups = int((category_raw["n_source_labels"] > 1).sum())
category_notset_share = (
    category_raw.loc[category_raw["item_category"] == "(not set)", "viewing_sessions"].sum()
    / _cat_all_views * 100 if _cat_all_views else 0.0
)
category_tbl = category_raw[
    (category_raw["item_category"] != "(not set)")
    & (category_raw["viewing_sessions"] >= MIN_CATEGORY_VIEWS)
].copy()
category_excluded_n = int(
    (category_raw["item_category"] != "(not set)").sum() - len(category_tbl)
)
category_tbl["atc_pct"] = category_tbl["carting_sessions"] / category_tbl["viewing_sessions"] * 100

# Site average = pooled rate across ranked categories (metrics.site_average).
# E6 — flags and carts at stake come from metrics.flag_categories: a category is
# "below" only when its whole Wilson 95% interval lies under the site average,
# and carts at stake count flagged categories only. (23-odd intervals at 95%
# each: expect about one false flag by chance, so flags are a shortlist.)
category_benchmark = site_average(category_tbl["carting_sessions"], category_tbl["viewing_sessions"])
category_tbl = flag_categories(category_tbl, category_benchmark)
category_n_below   = int((category_tbl["status"] == "below").sum())
category_n_above   = int((category_tbl["status"] == "above").sum())
category_n_neutral = int((category_tbl["status"] == "neutral").sum())

# Price as a confounder: rank correlation between a category's median listed
# price and its seen → carted rate. Reported either way — a weak result means
# price cannot explain the ranking on its own, not that price is irrelevant.
from scipy.stats import spearmanr
_pr = category_tbl.dropna(subset=["median_price"])
if len(_pr) >= 5:
    _rho, _rho_p = spearmanr(_pr["median_price"], _pr["atc_pct"])
    category_price_rho, category_price_p = float(_rho), float(_rho_p)
else:
    category_price_rho = category_price_p = None

if category_price_rho is None:
    category_price_note = ""
else:
    _price_reading = (
        "That is too weak to explain the ranking on its own, but cheap categories may "
        "still be an easier yes, so a low rate is not proof of a presentation problem."
        if category_price_p >= 0.05 else
        "Cheaper categories convert better, so part of each gap may be price, not presentation.")
    category_price_note = (
        '<p class="caveat-note"><strong>Price is not controlled.</strong> Across ranked '
        "categories, median listed price and seen → carted rate have a rank correlation of "
        f"{category_price_rho:+.2f} ({_html.escape(format_p(category_price_p))}). {_price_reading}</p>")

category_tbl = category_tbl.sort_values("atc_pct").reset_index(drop=True)

_cat_priority = category_tbl.sort_values("carts_at_stake", ascending=False)
_cat_priority = _cat_priority[_cat_priority["carts_at_stake"] > 0].head(3)
category_top_targets = [_html.escape(c) for c in _cat_priority["item_category"]]
category_total_at_stake = int(category_tbl["carts_at_stake"].sum())
category_top3_at_stake  = int(_cat_priority["carts_at_stake"].sum())

def _merged_note(r) -> str:
    if r.n_source_labels <= 1:
        return ""
    return ('<div style="font-size:0.7rem;font-weight:400;color:#9ca3af;text-transform:none">'
            f'merged: {_html.escape(r.source_labels)}</div>')

def _status_label(st: str) -> str:
    return {"below": '<span class="flag-below">below</span>',
            "above": '<span class="flag-above">above</span>'}.get(st, '<span class="flag-neutral">≈ average</span>')

category_rows_html = "\n".join(
    f'<tr><td class="dev-name">{_html.escape(r.item_category)}{_merged_note(r)}</td>'
    f'<td>{int(r.viewing_sessions):,}</td>'
    f'<td>{int(r.carting_sessions):,}</td>'
    f'<td class="dev-key">{r.atc_pct:.1f}%</td>'
    f'<td class="ci">{r.ci_lo:.1f}–{r.ci_hi:.1f}%</td>'
    f'<td>{_status_label(r.status)}</td>'
    f'<td>{int(r.carts_at_stake):,}</td></tr>'
    for r in category_tbl.sort_values(["carts_at_stake", "atc_pct"], ascending=[False, True]).itertuples()
)

# ── 5. Time-to-convert ────────────────────────────────────────────────────────
print("Querying time-to-convert...")
ttc_df = q(f"""
WITH base AS (
  SELECT user_pseudo_id,
    (SELECT ep.value.int_value FROM UNNEST(event_params) AS ep
     WHERE ep.key = 'ga_session_id' LIMIT 1) AS session_id,
    event_name, event_timestamp
  FROM `{DATASET}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{DATE_START}' AND '{DATE_END}'
    AND event_name IN ('session_start','view_item','add_to_cart','begin_checkout','purchase')
),
se AS (
  SELECT user_pseudo_id, session_id, event_name, MIN(event_timestamp) AS first_ts
  FROM base WHERE session_id IS NOT NULL GROUP BY user_pseudo_id, session_id, event_name
),
pv AS (
  SELECT user_pseudo_id, session_id,
    MAX(CASE WHEN event_name='session_start'  THEN first_ts END) AS ts_s,
    MAX(CASE WHEN event_name='view_item'      THEN first_ts END) AS ts_v,
    MAX(CASE WHEN event_name='add_to_cart'    THEN first_ts END) AS ts_c,
    MAX(CASE WHEN event_name='begin_checkout' THEN first_ts END) AS ts_k,
    MAX(CASE WHEN event_name='purchase'       THEN first_ts END) AS ts_p
  FROM se GROUP BY user_pseudo_id, session_id
),
ff AS (SELECT * FROM pv
  WHERE ts_s IS NOT NULL AND ts_v>=ts_s AND ts_c>=ts_v AND ts_k>=ts_c AND ts_p>=ts_k),
-- Tracking check over every session with an add_to_cart (not only converters):
-- an add-to-cart logged at the exact microsecond of the first view_item, or
-- before / without any view_item, is an instrumentation signal, not behaviour.
cart AS (
  SELECT COUNT(*) AS carting_sessions,
    COUNTIF(ts_c = ts_v) AS cart_same_ts_as_view,
    COUNTIF(ts_v IS NULL OR ts_c < ts_v) AS cart_before_or_without_view,
    COUNTIF(ts_c > ts_v AND ts_c - ts_v < 1000000) AS cart_within_1s_after_view
  FROM pv WHERE ts_c IS NOT NULL
)
SELECT t.*, cart.*
FROM (
  -- Minutes: the steps are minutes apart, so hours hide them behind decimals.
  SELECT
    ROUND(APPROX_QUANTILES((ts_v-ts_s)/60000000.0,100)[OFFSET(50)],1) AS p50_s_to_v,
    ROUND(APPROX_QUANTILES((ts_c-ts_v)/60000000.0,100)[OFFSET(50)],1) AS p50_v_to_c,
    ROUND(APPROX_QUANTILES((ts_k-ts_c)/60000000.0,100)[OFFSET(50)],1) AS p50_c_to_k,
    ROUND(APPROX_QUANTILES((ts_p-ts_k)/60000000.0,100)[OFFSET(50)],1) AS p50_k_to_p,
    ROUND(APPROX_QUANTILES((ts_p-ts_s)/60000000.0,100)[OFFSET(50)],1) AS p50_total,
    COUNT(*) AS converting_sessions,
    COUNTIF(ts_c = ts_v) AS converting_same_ts
  FROM ff
) t CROSS JOIN cart
""")

# ── 6. Cohort retention ───────────────────────────────────────────────────────
print("Querying cohort retention...")
cohort_df = q(f"""
WITH fs AS (
  SELECT user_pseudo_id,
    DATE_TRUNC(PARSE_DATE('%Y%m%d', MIN(event_date)), WEEK(MONDAY)) AS cohort_week
  FROM `{DATASET}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{DATE_START}' AND '{DATE_END}'
    AND event_name = 'session_start'
  GROUP BY user_pseudo_id
),
act AS (
  SELECT DISTINCT user_pseudo_id,
    DATE_TRUNC(PARSE_DATE('%Y%m%d', event_date), WEEK(MONDAY)) AS activity_week
  FROM `{DATASET}.events_*`
  WHERE _TABLE_SUFFIX BETWEEN '{DATE_START}' AND '{DATE_END}'
    AND event_name = 'session_start'
)
SELECT f.cohort_week,
  DATE_DIFF(a.activity_week, f.cohort_week, WEEK) AS weeks_since_cohort,
  COUNT(DISTINCT a.user_pseudo_id) AS active_users
FROM fs f JOIN act a USING (user_pseudo_id)
WHERE a.activity_week >= f.cohort_week
GROUP BY f.cohort_week, weeks_since_cohort
ORDER BY f.cohort_week, weeks_since_cohort
""")
cohort_pivot = cohort_df.pivot(index="cohort_week", columns="weeks_since_cohort",
                               values="active_users")

# ── Censoring guard ───────────────────────────────────────────────────────────
# A recent cohort has not yet had the chance to return in later weeks. Filling
# those cells with 0 would report "hasn't happened yet" as "0% retention" — a
# fabricated finding. Only weeks fully covered by the observation window are
# kept; everything beyond it stays NaN and renders blank.
import datetime as _dt
_start = _dt.date(int(DATE_START[:4]), int(DATE_START[4:6]), int(DATE_START[6:8]))
_end   = _dt.date(int(DATE_END[:4]),   int(DATE_END[4:6]),   int(DATE_END[6:8]))
# Window label derived from the config so prose never hardcodes the months.
date_label = f"{_start:%b %Y} – {_end:%b %Y}"
_cohort_dates = [pd.Timestamp(d).date() for d in cohort_pivot.index]

_observed = pd.DataFrame(
    [[(cd + _dt.timedelta(days=7 * int(k) + 6)) <= _end for k in cohort_pivot.columns]
     for cd in _cohort_dates],
    index=cohort_pivot.index, columns=cohort_pivot.columns,
)
cohort_pivot = cohort_pivot.where(_observed)                          # censored → NaN
cohort_pivot = cohort_pivot.mask(_observed & cohort_pivot.isna(), 0)  # observed & absent → real 0

cohort_pct = cohort_pivot.div(cohort_pivot[0], axis=0).multiply(100).round(1)

# ── Partial first cohort guard ────────────────────────────────────────────────
# Cohort weeks are Monday-anchored, so when DATE_START is not a Monday the first
# bucket only contains the tail of that week. It has a much smaller base and
# therefore much higher variance — it is an artefact of the window boundary,
# not a behavioural signal, and must be excluded from the averages.
partial_first        = _cohort_dates[0] < _start
partial_label        = str(_cohort_dates[0]) if partial_first else None
partial_days         = (_dt.timedelta(days=6) - (_start - _cohort_dates[0])).days + 1 if partial_first else None
partial_n            = int(cohort_pivot[0].iloc[0]) if partial_first else None
first_full_label     = str(_cohort_dates[1]) if partial_first and len(_cohort_dates) > 1 else None
first_full_n         = int(cohort_pivot[0].iloc[1]) if partial_first and len(cohort_pivot) > 1 else None

cohort_pct.index   = cohort_pct.index.astype(str)
cohort_pct.columns = [f"Week {c}" for c in cohort_pct.columns]

# Averages computed over full cohorts only; NaN (censored) cells are skipped.
cohort_pct_full = cohort_pct.iloc[1:] if partial_first else cohort_pct


# ══════════════════════════════════════════════════════════════════════════════
# DERIVED METRICS & DYNAMIC INSIGHTS
# ══════════════════════════════════════════════════════════════════════════════
print("Computing insights...")

total_sessions  = int(funnel_df["sessions"].iloc[0])
view_item_n     = int(funnel_df["sessions"].iloc[1])
purchase_n      = int(funnel_df["sessions"].iloc[4])
# Unrounded: funnel_df["overall_cvr"] is rounded to 1 dp for display, which turns
# 0.767% into "0.80%" once printed with two decimals.
overall_cvr     = purchase_n / total_sessions * 100
view_rate       = float(funnel_df["step_cvr"].iloc[1])   # session → view_item
atc_from_view    = float(funnel_df["step_cvr"].iloc[2])     # add_to_cart as % of PRODUCT VIEWERS
atc_all_sessions = float(funnel_df["overall_cvr"].iloc[2])  # add_to_cart as % of ALL SESSIONS
aov             = float(revenue_df["avg_order_value"].iloc[0])
median_order    = float(revenue_df["median_order_value"].iloc[0])
total_revenue   = float(revenue_df["total_revenue"].iloc[0])
total_ttc_min   = float(ttc_df["p50_total"].iloc[0])

# ── Revenue reconciliation ────────────────────────────────────────────────────
# AOV is averaged over orders that carry revenue; some purchase events have none.
# Sizing uses revenue per funnel purchase (total ÷ all purchases) so that
# "extra purchases × $ per purchase" reproduces the observed total.
orders_with_rev   = int(revenue_df["orders_with_revenue"].iloc[0])
orders_no_rev     = purchase_n - orders_with_rev
rev_per_purchase  = total_revenue / purchase_n if purchase_n else 0.0
all_purch_sess    = int(revenue_df["all_purchasing_sessions"].iloc[0])
all_revenue       = float(revenue_df["all_revenue"].iloc[0])
purch_no_cart     = int(revenue_df["purchases_without_cart"].iloc[0])
if int(revenue_df["purchasing_sessions"].iloc[0]) != purchase_n:
    print(f"  WARNING: revenue query purchases ({int(revenue_df['purchasing_sessions'].iloc[0]):,}) "
          f"!= funnel purchases ({purchase_n:,})")

# ── Step losses after the shopper shows intent ────────────────────────────────
carted_n          = int(funnel_df["sessions"].iloc[2])
checkout_n        = int(funnel_df["sessions"].iloc[3])
cart_to_checkout  = float(funnel_df["step_cvr"].iloc[3])
cart_abandon_pct  = 100 - cart_to_checkout
checkout_to_purch = float(funnel_df["step_cvr"].iloc[4])

# ── Unattributed sessions (no session-level medium) ──────────────────────────
_dq_all  = dq_raw[dq_raw["cut"] == "all"].set_index("attributed")
_dq_size = dq_raw[dq_raw["cut"] == "size"].copy()
_dq_dev  = dq_raw[dq_raw["cut"] == "device"].copy()
ua, at   = _dq_all.loc[False], _dq_all.loc[True]
ua_sessions, ua_share = int(ua["sessions"]), int(ua["sessions"]) / total_sessions * 100
ua_view_pct   = ua["view_item"] / ua["sessions"] * 100
at_view_pct   = at["view_item"] / at["sessions"] * 100
ua_pv_pct     = ua["with_page_view"] / ua["sessions"] * 100
at_pv_pct     = at["with_page_view"] / at["sessions"] * 100
ua_med_eng, at_med_eng = float(ua["median_engaged_s"]), float(at["median_engaged_s"])
ua_med_ev,  at_med_ev  = int(ua["median_events"]),    int(at["median_events"])
# Share of the never-saw-a-product loss that sits in this block.
total_lost_discovery = total_sessions - view_item_n
ua_lost_share = (int(ua["sessions"]) - int(ua["view_item"])) / total_lost_discovery * 100
# Session-length test: unattributed share and view rate per size bucket.
_bucket_order = ["1–3", "4–5", "6–8", "9–15", "16+"]
_sz = _dq_size.pivot(index="size_bucket", columns="attributed",
                     values=["sessions", "view_item"]).reindex(_bucket_order)
dq_buckets = pd.DataFrame({
    "bucket":      _bucket_order,
    "sessions":    (_sz[("sessions", True)] + _sz[("sessions", False)]).astype(int).values,
    "ua_pct":      (_sz[("sessions", False)] / (_sz[("sessions", True)] + _sz[("sessions", False)]) * 100).values,
    "view_ua":     (_sz[("view_item", False)] / _sz[("sessions", False)] * 100).values,
    "view_at":     (_sz[("view_item", True)]  / _sz[("sessions", True)]  * 100).values,
})
# Device mix: share of each device's sessions that are unattributed.
_dv = _dq_dev.pivot(index="dev", columns="attributed", values="sessions")
_dv_ua_pct = (_dv[False] / (_dv[False] + _dv[True]) * 100)
ua_dev_min, ua_dev_max = float(_dv_ua_pct.min()), float(_dv_ua_pct.max())

# Sensitivity funnel: attributed sessions only. NOT the primary view — removing
# the block removes short sessions selectively, so every rate is biased upward.
def _funnel_stats(r):
    return {"sessions": int(r["sessions"]),
            "view_pct": r["view_item"] / r["sessions"] * 100,
            "view_to_cart": r["add_to_cart"] / r["view_item"] * 100 if r["view_item"] else float("nan"),
            "cart_to_checkout": r["begin_checkout"] / r["add_to_cart"] * 100 if r["add_to_cart"] else float("nan"),
            "checkout_to_purchase": r["purchase"] / r["begin_checkout"] * 100 if r["begin_checkout"] else float("nan"),
            "cvr": r["purchase"] / r["sessions"] * 100,
            "rev_per_session": r["revenue"] / r["sessions"]}
sens_all = _funnel_stats(_dq_all.sum(numeric_only=True))

# ── E3 — new vs returning sessions (ga_session_number = 1 → new) ─────────────
# Same per-session scan and the same all-sessions basis as the primary funnel.
_vis = dq_raw[dq_raw["cut"] == "visitor"].set_index("visitor")
visitor_stats = {k: _funnel_stats(_vis.loc[k]) for k in ("new", "returning") if k in _vis.index}
for k in visitor_stats:
    visitor_stats[k]["purchases"] = int(_vis.loc[k, "purchase"])
    visitor_stats[k]["ci"] = wilson_ci(int(_vis.loc[k, "purchase"]), int(_vis.loc[k, "sessions"]))
visitor_unknown_n = int(_vis.loc["unknown", "sessions"]) if "unknown" in _vis.index else 0
has_visitor_split = {"new", "returning"} <= set(visitor_stats)
if has_visitor_split:
    vs_new, vs_ret = visitor_stats["new"], visitor_stats["returning"]
    visitor_cvr_ratio = vs_ret["cvr"] / vs_new["cvr"] if vs_new["cvr"] else float("nan")
    visitor_test = chi_square([vs_new["purchases"], vs_ret["purchases"]],
                              [vs_new["sessions"], vs_ret["sessions"]])
sens_ex  = _funnel_stats(at)
if sens_all["sessions"] != total_sessions:
    print(f"  WARNING: data-quality sessions ({sens_all['sessions']:,}) != funnel sessions ({total_sessions:,})")

# ── Event quality: view_item / add_to_cart are list-level events here ─────────
vi_q, atc_q = evq_raw.loc["view_item"], evq_raw.loc["add_to_cart"]
vi_avg_items   = float(vi_q["avg_items"])
vi_multi_pct   = vi_q["multi_item_events"] / vi_q["events"] * 100
atc_avg_items  = float(atc_q["avg_items"])
atc_one_pct    = atc_q["one_carted_item_events"] / atc_q["events"] * 100
carting_all    = int(ttc_df["carting_sessions"].iloc[0])
cart_same_ts   = int(ttc_df["cart_same_ts_as_view"].iloc[0])
cart_same_ts_pct = cart_same_ts / carting_all * 100 if carting_all else 0.0
cart_within_1s = int(ttc_df["cart_within_1s_after_view"].iloc[0])
cart_no_view   = int(ttc_df["cart_before_or_without_view"].iloc[0])
conv_same_ts_pct = (int(ttc_df["converting_same_ts"].iloc[0])
                    / int(ttc_df["converting_sessions"].iloc[0]) * 100)

# Shoppers ATC rate
shoppers_atc = round(
    int(shoppers_raw["step2_add_to_cart"].iloc[0]) /
    int(shoppers_raw["step1_view_item"].iloc[0]) * 100, 1
)

# Biggest absolute drop step
funnel_df["next_sessions"] = funnel_df["sessions"].shift(-1)
funnel_df["abs_drop_n"]    = (funnel_df["sessions"] - funnel_df["next_sessions"]).fillna(0).astype(int)
biggest_drop_idx  = funnel_df["abs_drop_n"].idxmax()
biggest_drop_step = funnel_df.loc[biggest_drop_idx, "step"]
biggest_drop_n    = int(funnel_df.loc[biggest_drop_idx, "abs_drop_n"])

# Partial-first-cohort caveat — rendered only when the window opens mid-week
if partial_first and first_full_n:
    partial_cohort_note = (
        f'<p><strong>1 · The first cohort is a partial week.</strong> Cohorts are '
        f'Monday-anchored, but the data window opens on '
        f'{_start.strftime("%d %b %Y")} — a {_start.strftime("%A")}. The '
        f'{partial_label} bucket therefore captures only <strong>{partial_days} day(s)</strong> '
        f'of first sessions: <strong>{partial_n:,} users</strong> against '
        f'<strong>{first_full_n:,}</strong> in the first full cohort '
        f'({first_full_label}). Its elevated Week-1 rate is small-sample variance '
        f'from the window boundary, not a behavioural signal — so it is '
        f'<strong>excluded from the averages above</strong> rather than explained.</p>'
    )
else:
    partial_cohort_note = (
        '<p><strong>1 · Cohort alignment.</strong> The observation window opens on '
        'a Monday, so every cohort bucket represents a full week and no '
        'partial-week distortion is present.</p>'
    )

# Cohort retention — full cohorts only, censored cells excluded automatically by .mean()
avg_wk1 = round(float(cohort_pct_full["Week 1"].mean()), 1) if "Week 1" in cohort_pct_full.columns else None
avg_wk2 = round(float(cohort_pct_full["Week 2"].mean()), 1) if "Week 2" in cohort_pct_full.columns else None

# Re-engagement sizing. WK1_TARGET is a planning target, not an industry
# benchmark. Only full cohorts whose Week 1 is inside the window count, so the
# partial first cohort and right-censored cohorts cannot inflate the result.
WK1_TARGET = 8.0
_wk1_obs = cohort_pivot.iloc[1:] if partial_first else cohort_pivot
_wk1_obs = _wk1_obs[_wk1_obs[1].notna()] if 1 in _wk1_obs.columns else _wk1_obs.iloc[0:0]
wk1_base_users  = int(_wk1_obs[0].sum())
wk1_n_cohorts   = len(_wk1_obs)
wk1_extra_users = max(0, round(wk1_base_users * WK1_TARGET / 100 - float(_wk1_obs[1].sum()))) if wk1_n_cohorts else 0

# Recommendation 2 in revenue (E3). Each extra returning user is counted as ONE
# extra session (a floor — returners often have more). The value of that session
# is bracketed: returning sessions earn more per session, but partly because the
# users who come back unprompted were keener to begin with, so re-engaged users
# are priced between the new-session and returning-session rates.
if has_visitor_split:
    rec2_rev_low  = round(wk1_extra_users * vs_new["rev_per_session"], 0)
    rec2_rev_high = round(wk1_extra_users * vs_ret["rev_per_session"], 0)

# Opportunity sizing: what if we lift session → view_item by 5pp?
# Extra sessions that would now view a product if the rate goes from current to current+5pp
lift_5pp_views = int(total_sessions * 0.05)
# Of those extra view_item sessions, apply the observed view_item → purchase rate
# purchase rate = (purchases from view_item sessions) / (sessions that viewed item)
view_item_n    = int(funnel_raw["step2_view_item"].iloc[0])
purchase_n_raw = int(funnel_raw["step5_purchase"].iloc[0])
view_to_purchase_rate = purchase_n_raw / view_item_n  # e.g. 2721/75261 ≈ 3.6%
lift_5pp_purchases = round(lift_5pp_views * view_to_purchase_rate)
# Revenue per funnel purchase, not AOV: AOV excludes the purchases that record
# no revenue, so multiplying it by a purchase count would overstate the total.
lift_5pp_revenue   = round(lift_5pp_purchases * rev_per_purchase, 0)
# Sensitivity: incremental traffic almost always converts below the existing
# average, so the headline figure is an optimistic upper bound. Model 50% / 75%.
lift_5pp_rev_50 = round(lift_5pp_revenue * 0.50, 0)
lift_5pp_rev_75 = round(lift_5pp_revenue * 0.75, 0)

# ── Executive summary (computed from real numbers) ────────────────────────────
# One story, stated with numbers: the largest absolute loss is discovery; after
# that, product views convert to carts at a category-dependent rate, and most
# carts never reach checkout. Each clause is computed, so the sentence stays
# true if the window or dataset changes.
_disc_is_biggest = biggest_drop_idx == 0
exec_summary = (
    f"Of {total_sessions:,} sessions in {date_label}, "
    f"<strong>{overall_cvr:.2f}%</strong> ended in a purchase. "
    + (f"The largest loss is <strong>discovery</strong>: only "
       f"<strong>{view_rate:.1f}%</strong> of sessions saw any product, so "
       f"{total_lost_discovery:,} sessions ({100-view_rate:.1f}%) left without seeing one. "
       if _disc_is_biggest else
       f"The largest absolute loss is between <strong>{biggest_drop_step}</strong> and the "
       f"next step ({biggest_drop_n:,} sessions). ")
    + f"Of sessions that did see products, <strong>{atc_from_view:.1f}%</strong> added one "
      f"to cart — a rate that varies by category, a separate merchandising problem. "
      f"Once a cart exists, <strong>{cart_abandon_pct:.0f}%</strong> of carting sessions "
      f"({carted_n - checkout_n:,} of {carted_n:,}) never begin checkout."
)

# ── Priority label ─────────────────────────────────────────────────────────────
if view_rate < 30:
    priority_label = "Top-of-Funnel: Product Discovery"
    priority_color = "#dc2626"
elif atc_from_view < 40:
    priority_label = "Mid-Funnel: Product → Cart Conversion"
    priority_color = "#ea580c"
else:
    priority_label = "Retention & Lifecycle"
    priority_color = "#7c3aed"


# ══════════════════════════════════════════════════════════════════════════════
# CHARTS
# ══════════════════════════════════════════════════════════════════════════════

# Funnel chart — native go.Funnel (tapering funnel shape, cleaner than horizontal bar)
fig_funnel = go.Figure(go.Funnel(
    y=funnel_df["step"].tolist(),
    x=funnel_df["sessions"].tolist(),
    textposition="auto",
    textinfo="value+percent initial",
    marker=dict(
        color=["#2c5282", "#1e40af", "#2563eb", "#3b82f6", "#60a5fa"],
        line=dict(color="white", width=1),
    ),
    connector=dict(line=dict(color="#e2e8f0", width=1)),
    hovertemplate="<b>%{y}</b><br>Sessions: %{x:,}<br>% of start: %{percentInitial:.1%}<extra></extra>",
    opacity=0.9,
))
fig_funnel.update_layout(
    height=420, margin=dict(l=20, r=20, t=10, b=10),
    paper_bgcolor="white", plot_bgcolor="white", showlegend=False,
)

# Time-to-convert chart
# Minutes, from plain Python lists (numpy arrays are binary-encoded by Plotly,
# which previously needed a JS patch to display near-zero values correctly).
_ttc_x = ["Session → View Products", "View Products → Add to Cart",
          "Add to Cart → Checkout", "Checkout → Purchase"]
_ttc_y = [float(ttc_df[c].iloc[0]) for c in ("p50_s_to_v", "p50_v_to_c", "p50_c_to_k", "p50_k_to_p")]
_ttc_txt = [f"{v:.1f} min" for v in _ttc_y]
_ttc_txt[1] += " †"           # explained in the note: identical timestamps
fig_ttc = go.Figure(go.Bar(
    x=_ttc_x, y=_ttc_y, text=_ttc_txt, texttemplate="%{text}", textposition="outside",
    marker_color="#4C78A8",
    hovertemplate="<b>%{x}</b><br>Median: %{y:.1f} min<extra></extra>",
))
fig_ttc.update_layout(height=360, showlegend=False,
                      paper_bgcolor="white", plot_bgcolor="white",
                      yaxis=dict(title="Median minutes (converting sessions)",
                                 range=[0, max(_ttc_y) * 1.25 if max(_ttc_y) else 1]),
                      margin=dict(t=20, b=10), xaxis_tickangle=-15)

# Cohort heatmap — zmax=12 so 0-12% retention range uses full colour spectrum
# Week 0 cells (100%) will be max-dark; all subsequent cells show meaningful gradient
# Blues runs light → dark as the value rises, so darker = better retention.
# (reversescale was previously on, which rendered 0% as the darkest cell.)
# zmax=12 keeps the 0–12% band using the full colour range; Week 0 is always 100%.
_z_cohort = cohort_pct.values.tolist()
fig_cohort = go.Figure(go.Heatmap(
    z=_z_cohort,
    x=cohort_pct.columns.tolist(),
    y=cohort_pct.index.tolist(),
    text=[["" if pd.isna(v) else f"{v:.0f}%" for v in row] for row in _z_cohort],
    texttemplate="%{text}",
    colorscale="Blues", zmin=0, zmax=12,
    hoverongaps=False,  # censored (NaN) cells show no tooltip
    hovertemplate="Cohort: <b>%{y}</b><br>%{x}<br>Retention: %{z:.1f}%<extra></extra>",
))
fig_cohort.update_layout(
    xaxis_title="Weeks Since First Session", yaxis_title="Cohort Week",
    height=440, yaxis_autorange="reversed",
    paper_bgcolor="white", margin=dict(t=20),
)

# Channel chart (E1) — session → view_item rate per channel against the site
# average. One metric only: it is the metric the question is about. Labels are
# pre-formatted in Python (texttemplate format strings fail in standalone HTML).
_ch_plot = channel_tbl.sort_values("view_pct")
fig_channel = go.Figure(go.Bar(
    y=_ch_plot["channel"].tolist(),
    x=_ch_plot["view_pct"].round(1).tolist(),
    orientation="h",
    text=[f"{v:.1f}%" for v in _ch_plot["view_pct"]],
    texttemplate="%{text}", textposition="outside",
    marker_color=["#dc2626" if v < view_rate - 5 else "#4C78A8" for v in _ch_plot["view_pct"]],
    customdata=[[f"{int(s):,}", f"{sh:.1f}%"] for s, sh in zip(_ch_plot["sessions"], _ch_plot["share"])],
    hovertemplate="<b>%{y}</b><br>Sessions that saw a product: %{x:.1f}%"
                  "<br>Sessions: %{customdata[0]} (%{customdata[1]} of total)<extra></extra>",
))
fig_channel.add_vline(x=view_rate, line_dash="dash", line_color="#6b7280",
                      annotation_text=f"site avg {view_rate:.1f}%",
                      annotation_position="top")
fig_channel.update_layout(
    height=max(260, 46 * len(_ch_plot) + 80), showlegend=False,
    paper_bgcolor="white", plot_bgcolor="white",
    margin=dict(l=10, r=40, t=30, b=10),
    xaxis=dict(title="% of sessions that saw a product",
               range=[0, max(float(_ch_plot["view_pct"].max()), view_rate) * 1.2]),
)

# Category chart (E2 + E6) — seen → carted rate per category, worst at the top,
# with Wilson 95% whiskers. Colour follows the significance flag, not the raw
# gap: orange = interval entirely below the site average, blue = entirely above,
# grey = cannot be told apart from average.
_CAT_COLOURS = {"below": "#ea580c", "above": "#4C78A8", "neutral": "#cbd5e1"}
fig_category = go.Figure(go.Bar(
    y=category_tbl["item_category"].tolist(),
    x=category_tbl["atc_pct"].round(1).tolist(),
    orientation="h",
    text=[f"{v:.1f}%" for v in category_tbl["atc_pct"]],
    texttemplate="%{text}", textposition="outside",
    marker_color=[_CAT_COLOURS[st] for st in category_tbl["status"]],
    error_x=dict(type="data", symmetric=False,
                 array=(category_tbl["ci_hi"] - category_tbl["atc_pct"]).round(2).tolist(),
                 arrayminus=(category_tbl["atc_pct"] - category_tbl["ci_lo"]).round(2).tolist(),
                 color="#6b7280", thickness=1, width=3),
    customdata=[[f"{int(v):,}", f"{int(k):,}", f"{lo:.1f}–{hi:.1f}%"] for v, k, lo, hi in
                zip(category_tbl["viewing_sessions"], category_tbl["carts_at_stake"],
                    category_tbl["ci_lo"], category_tbl["ci_hi"])],
    hovertemplate="<b>%{y}</b><br>Seen → carted: %{x:.1f}% (95% CI %{customdata[2]})"
                  "<br>Sessions that saw it: %{customdata[0]}"
                  "<br>Carts at stake: %{customdata[1]}<extra></extra>",
))
fig_category.add_vline(x=category_benchmark, line_dash="dash", line_color="#6b7280",
                       annotation_text=f"site average {category_benchmark:.1f}%",
                       annotation_position="top")
fig_category.update_layout(
    height=max(300, 30 * len(category_tbl) + 80), showlegend=False,
    paper_bgcolor="white", plot_bgcolor="white",
    margin=dict(l=10, r=40, t=30, b=10),
    yaxis=dict(autorange="reversed"),   # lowest rate at the top
    xaxis=dict(title="% of sessions that saw the category and carted an item from it",
               range=[0, (float(category_tbl["ci_hi"].max()) if len(category_tbl) else 10) * 1.15]),
)


# ══════════════════════════════════════════════════════════════════════════════
# AI BRIEF
# ══════════════════════════════════════════════════════════════════════════════
brief_html = ""
brief_model = "AI"
brief_stale_note = ""
# The notebook stamps the brief with the version of the LLM context it was
# written from (§10f header: "Context vN"). A brief from an older context cannot
# know the newer verdicts and may contradict the page, so it is flagged.
#   v3 = computed verdicts, confounders, data-quality, new vs returning, significance
BRIEF_CONTEXT_VERSION = 3
brief_path = HERE / "product_brief_ga4_funnel.md"


# Markdown → HTML for the brief lives in md_render.py (unit-tested).
if brief_path.exists():
    brief_md   = brief_path.read_text(encoding="utf-8")
    _gen  = re.search(r"\*Generated (\d{4}-\d{2}-\d{2})", brief_md)
    _mdl  = re.search(r"Model: ([^·*]+)", brief_md)
    _ver  = re.search(r"Context v(\d+)", brief_md)
    brief_model = _html.escape(_mdl.group(1).strip()) if _mdl else "Claude"
    brief_ctx_version = int(_ver.group(1)) if _ver else 0
    if brief_ctx_version < BRIEF_CONTEXT_VERSION:
        brief_stale_note = (
            '<p class="caveat-note" style="margin-bottom:16px">This brief'
            + (f" (generated {_gen.group(1)})" if _gen else "")
            + " was written from an earlier version of the analysis, before the computed "
              "verdicts, data-quality checks, significance tests and new-vs-returning split "
              "were passed to the model. Where it disagrees with the sections above, the "
              "sections above are correct. Regenerate it with Section 10 of the notebook.</p>"
        )
        print(f"  NOTE: the AI brief is from context v{brief_ctx_version} (current: "
              f"v{BRIEF_CONTEXT_VERSION}) — it is flagged as out of date on the page.")
    # Body = everything after the header's horizontal rule.
    _lines = brief_md.splitlines()
    _hr = next((k for k, l in enumerate(_lines) if l.strip() == "---"), 4)
    brief_html = md_to_html("\n".join(_lines[_hr + 1:]))
    print("AI product brief included.")
else:
    print("No product_brief_ga4_funnel.md — run Section 10 of the notebook first.")


# ══════════════════════════════════════════════════════════════════════════════
# ASSEMBLE HTML
# ══════════════════════════════════════════════════════════════════════════════
print("Assembling dashboard...")

wk1_display   = f"{avg_wk1}%" if avg_wk1 else "—"
# Compared against the planning target, not an external benchmark: no sourced
# industry figure exists for calendar-week return on this kind of store.
wk1_warn      = avg_wk1 is not None and avg_wk1 < WK1_TARGET
retention_flag = "⚠️" if wk1_warn else ""
WK1_DEFINITION = ("the share of a weekly cohort with any session in the next calendar "
                  "week (Monday-anchored) — so a user's return window is 1 to 13 days, not exactly 7")

# ── Channel narrative (E1) — the conclusion flips on the 10pp threshold ───────
if channel_is_driver:
    channel_verdict = (
        f"<p><strong>Traffic mix is a real driver.</strong> The share of sessions "
        f"that saw a product ranges from <strong>{worst_view_pct:.1f}%</strong> "
        f"({worst_view_channel}) to <strong>{best_view_pct:.1f}%</strong> "
        f"({best_view_channel}) — a <strong>{channel_view_spread:.1f}pp</strong> spread. "
        f"Part of the discovery loss is who is arriving, not only what they find.</p>"
        f"<p>Before redesigning navigation, check what the {worst_view_channel} "
        f"landing experience promises versus what the site shows on arrival.</p>"
    )
    channel_rec_problem = (
        f"Product reach varies by <strong>{channel_view_spread:.1f}pp</strong> across "
        f"channels — from {worst_view_pct:.1f}% ({worst_view_channel}) to "
        f"{best_view_pct:.1f}% ({best_view_channel}). Acquisition quality explains part of "
        f"the discovery gap, so budget and landing pages are levers alongside navigation."
    )
elif channel_borderline:
    channel_verdict = (
        f"<p><strong>Leaning site, not traffic — but borderline.</strong> The spread "
        f"between {worst_view_channel} ({worst_view_pct:.1f}%) and {best_view_channel} "
        f"({best_view_pct:.1f}%) is <strong>{channel_view_spread:.1f}pp</strong>: under "
        f"the 10pp threshold, but close to it. Every channel still loses at least "
        f"{100-best_view_pct:.0f}% of its sessions before they see a product, so navigation / "
        f"discovery remains the leading explanation rather than a settled one.</p>"
        f"<p>Cheap check before committing to a navigation redesign: review the "
        f"{worst_view_channel} landing pages against what its ads promise.</p>"
    )
else:
    channel_verdict = (
        f"<p><strong>The leak is on the site, not in the traffic.</strong> No "
        f"channel is an outlier on product reach: the spread between "
        f"{best_view_channel} ({best_view_pct:.1f}%) and {worst_view_channel} "
        f"({worst_view_pct:.1f}%) is <strong>{channel_view_spread:.1f}pp</strong>, "
        f"well under the 10pp threshold — and every channel loses at least "
        f"{100-best_view_pct:.0f}% of its sessions before they see a product.</p>"
        f"<p>This supports the navigation / discovery hypothesis: reallocating "
        f"acquisition budget would not fix a loss that happens equally to every "
        f"visitor once they land.</p>"
    )
if notset_view_pct is not None and notset_share >= 1:
    # Only claim "the average sits under every channel" when that is literally true.
    _below_all = (notset_view_pct < view_rate < float(_ch_rank["view_pct"].min()))
    channel_verdict += (
        f"<p><strong>(not set)</strong> = sessions with no session-level medium "
        f"({notset_share:.1f}% of sessions). They see a product in "
        f"<strong>{notset_view_pct:.1f}%</strong> of cases and are excluded from the spread. "
        f"This is a tagging gap that hits short sessions hardest, not a channel"
        + (f"; it is why the site average ({view_rate:.1f}%) sits below every named channel"
           if _below_all else "")
        + " (see Data quality above). Because short sessions drop into (not set), "
          "every named channel's rate is somewhat inflated.</p>"
    )
if not channel_is_driver:
    channel_rec_problem = (
        f"Product reach is within <strong>{channel_view_spread:.1f}pp</strong> across "
        f"channels{' (just under the 10pp threshold)' if channel_borderline else ''}, "
        f"so discovery is mainly not a traffic-quality problem. Channels still differ "
        f"in <em>value</em>, though — revenue per session is the remaining reason to segment."
    )
if top_rev_channel:
    channel_rec_problem += (
        f" Highest revenue per session: <strong>{top_rev_channel}</strong> "
        f"(${top_rev_rps:,.2f})."
    )
channel_verdict += (
    f"<p><strong>Significant, but that is not the question.</strong> A chi-square test across "
    f"the named channels gives {fmt_p(channel_test['p'])} (χ² = {channel_test['chi2']:,.0f}, "
    f"{channel_test['dof']} df) — "
    + ("with this many sessions almost any gap is statistically significant. "
       if channel_test["p"] < 0.05 else "the channel gaps are within sampling noise. ")
    + f"Whether the leak is the site or the traffic is a question of <em>practical</em> size "
      f"(the 10pp threshold), not of significance; the effect size is small "
      f"(Cramér's V = {channel_test['cramers_v']:.3f}).</p>"
    if channel_test["cramers_v"] < 0.1 else
    f"<p><strong>Significance.</strong> Chi-square across named channels: "
    f"{fmt_p(channel_test['p'])} (Cramér's V = {channel_test['cramers_v']:.3f}). The verdict "
    f"still rests on the practical 10pp threshold, not on significance.</p>"
)

channel_basis_note = (
    "Channel = the session's own medium, taken from <code>event_params</code>; "
    "<code>(none)</code> is GA4's label for direct traffic. The 10pp decision threshold "
    f"is an analyst-set assumption ({10/view_rate*100:.0f}% of the site's {view_rate:.1f}% "
    "product-view rate), not an industry standard."
    if channel_dim == "session_medium" else
    f"Session-level medium covered only {session_medium_coverage*100:.0f}% of sessions, "
    "so channel falls back to the user's <strong>first-touch</strong> medium "
    "(<code>traffic_source.medium</code>) — returning sessions inherit their first channel."
)

# ── Priority-3 impact — must agree with the channel verdict ───────────────────
# Budget reallocation is only argued when channel IS the driver; otherwise the
# impact is sized from the category gap, the lever the analysis points to.
_cart_to_purchase = purchase_n / int(funnel_raw["step3_add_to_cart"].iloc[0])
_cat_orders  = round(category_top3_at_stake * _cart_to_purchase)
_cat_revenue = round(_cat_orders * rev_per_purchase, 0)
if channel_is_driver:
    rec3_impact = (
        "Reallocating even 20% of acquisition budget from low- to high-converting "
        "channels could improve overall CVR without increasing total spend."
    )
elif category_top_targets:
    rec3_impact = (
        f"Lifting the top {len(category_top_targets)} categories to the "
        f"{category_benchmark:.1f}% site average adds ~<strong>{category_top3_at_stake:,}</strong> "
        f"carting sessions. At today's {_cart_to_purchase*100:.1f}% cart → purchase rate and "
        f"${rev_per_purchase:,.2f} revenue per purchase that is roughly <strong>~{_cat_orders:,} orders / "
        f"${_cat_revenue:,.0f}</strong> — an upper bound, since category carts and "
        f"session purchases are not one-to-one."
    )
else:
    rec3_impact = ("No category sits meaningfully below the site average, so the remaining "
                   "lever is weighting spend towards revenue per session.")

# ── Category narrative (E2) ───────────────────────────────────────────────────
if category_top_targets:
    _targets = ", ".join(f"<strong>{c}</strong>" for c in category_top_targets)
    category_verdict = (
        f"<p>A category is flagged as <strong>underperforming</strong> only when its whole "
        f"95% confidence interval lies below the {category_benchmark:.1f}% site average "
        f"(all ranked categories pooled): <strong>{category_n_below}</strong> of "
        f"{len(category_tbl)} are, {category_n_above} sit clearly above, and "
        f"{category_n_neutral} cannot be told apart from average (grey). With "
        f"{len(category_tbl)} intervals at 95%, about {len(category_tbl) * 0.05:.0f} flag(s) could be "
        f"chance, so treat the list as a shortlist.</p>"
        f"<p>Ranked by <strong>carts at stake</strong> — the extra carting sessions a "
        f"flagged category would gain at the site average — the audit should start with "
        f"{_targets}. Together they account for "
        f"<strong>{category_top3_at_stake:,}</strong> of the "
        f"<strong>{category_total_at_stake:,}</strong> carts at stake across flagged "
        f"categories.</p>"
        f"<p>Ranking by volume × gap, not by rate alone, stops a niche category "
        f"with a dramatic rate from jumping the queue ahead of a high-traffic "
        f"category with a moderate gap.</p>"
    )
else:
    category_verdict = (
        "<p>No ranked category's 95% interval lies entirely below the site average — the "
        "seen → carted drop is spread evenly across the catalogue rather than "
        "concentrated in specific categories.</p>"
    )

# ── New vs returning (E3) ─────────────────────────────────────────────────────
if has_visitor_split:
    def _vrow(label, key, fmt):
        return (f'<tr><td class="dev-name">{label}</td><td>{fmt.format(vs_new[key])}</td>'
                f'<td>{fmt.format(vs_ret[key])}</td></tr>')
    visitor_rows = "\n".join([
        _vrow("Sessions", "sessions", "{:,}"),
        _vrow("Saw a product", "view_pct", "{:.1f}%"),
        _vrow("Saw → carted", "view_to_cart", "{:.1f}%"),
        _vrow("Cart → checkout", "cart_to_checkout", "{:.1f}%"),
        _vrow("Checkout → purchase", "checkout_to_purchase", "{:.1f}%"),
        _vrow("Session → purchase", "cvr", "{:.2f}%"),
        _vrow("Revenue / session", "rev_per_session", "${:,.2f}"),
    ]) + (f'<tr><td class="dev-name">95% CI (purchase)</td>'
          f'<td class="ci">{vs_new["ci"][0]:.2f}–{vs_new["ci"][1]:.2f}%</td>'
          f'<td class="ci">{vs_ret["ci"][0]:.2f}–{vs_ret["ci"][1]:.2f}%</td></tr>')
    visitor_html = f"""
  <p class="section-label">New vs Returning Sessions</p>
  <div class="card">
    <div class="two-col" style="gap:28px">
      <div>
        <table class="dev-table">
          <thead><tr><th>Funnel</th><th>New (session 1)</th><th>Returning</th></tr></thead>
          <tbody>
{visitor_rows}
          </tbody>
        </table>
      </div>
      <div class="insight-panel">
        <h4>Conclusion</h4>
        <p>A session is <strong>new</strong> when GA4's <code>ga_session_number</code> is 1 (the
        user's first session ever on that device/browser), otherwise <strong>returning</strong>.
        Returning sessions are {vs_ret["sessions"] / (vs_new["sessions"] + vs_ret["sessions"]) * 100:.0f}% of
        traffic but buy at <strong>{vs_ret["cvr"]:.2f}%</strong> against
        {vs_new["cvr"]:.2f}% — <strong>{visitor_cvr_ratio:.1f}×</strong> — and earn
        ${vs_ret["rev_per_session"]:,.2f} per session against ${vs_new["rev_per_session"]:,.2f}
        (χ² {fmt_p(visitor_test["p"])}). They do better at every step, the largest gap being
        checkout → purchase ({vs_ret["checkout_to_purchase"]:.0f}% vs
        {vs_new["checkout_to_purchase"]:.0f}%).</p>
        <p class="caveat-note">This is not the effect of coming back. Users who return
        unprompted were already keener, so re-engaged users are priced between the two
        rates in Recommendation 2, not at the returning rate.</p>
      </div>
    </div>
  </div>
"""
else:
    visitor_html = ""

# ── Appendix: AI brief (collapsed) ────────────────────────────────────────────
# Collapsed by default: the computed sections are the source of truth; the brief
# is a written interpretation of them, kept for the reader who wants prose.
appendix_brief_html = "" if not brief_html else f"""
  <p class="section-label">Appendix</p>
  <details class="card appendix">
    <summary><span class="ai-badge">{brief_model}</span> AI-generated product brief
      <span class="appendix-sub">written by an LLM from the computed results above;
      where they differ, the computed sections are correct</span></summary>
    <div class="appendix-body">
      {brief_stale_note}
      <div class="brief-body">{brief_html}</div>
    </div>
  </details>
"""

# ── Data-quality callout ──────────────────────────────────────────────────────
# Stated near the top because three of these facts change how the headline
# numbers may be read. Every figure is computed in this run.
dq_bucket_rows = "\n".join(
    f'<tr><td class="dev-name">{r.bucket} events</td><td>{int(r.sessions):,}</td>'
    f'<td>{r.ua_pct:.1f}%</td><td>{r.view_ua:.1f}%</td><td>{r.view_at:.1f}%</td></tr>'
    for r in dq_buckets.itertuples()
)
def _sens_row(label, key, fmt):
    return (f'<tr><td class="dev-name">{label}</td>'
            f'<td class="dev-key">{fmt.format(sens_all[key])}</td>'
            f'<td>{fmt.format(sens_ex[key])}</td></tr>')
dq_sens_rows = "\n".join([
    _sens_row("Sessions", "sessions", "{:,}"),
    _sens_row("Saw a product", "view_pct", "{:.1f}%"),
    _sens_row("Saw → carted", "view_to_cart", "{:.1f}%"),
    _sens_row("Cart → checkout", "cart_to_checkout", "{:.1f}%"),
    _sens_row("Checkout → purchase", "checkout_to_purchase", "{:.1f}%"),
    _sens_row("Session → purchase", "cvr", "{:.2f}%"),
    _sens_row("Revenue / session", "rev_per_session", "${:,.2f}"),
])

data_quality_html = f"""
  <p class="section-label">Data quality — read before the numbers</p>
  <div class="card dq-card">
    <div class="two-col" style="gap:28px">
      <div>
        <h4 class="dq-h">1 · A quarter of sessions have no channel tag — a tagging gap, not bots</h4>
        <p><strong>{ua_sessions:,} sessions ({ua_share:.1f}%)</strong> carry no session-level
        <code>medium</code> on any event. In channel tables they appear as
        <code>(not set)</code>. They see a product in only <strong>{ua_view_pct:.1f}%</strong>
        of cases (attributed: {at_view_pct:.1f}%) and account for
        <strong>{ua_lost_share:.0f}%</strong> of the {total_lost_discovery:,} sessions that
        never see a product.</p>
        <p>They don't look like bots: {ua_pv_pct:.0f}% have a <code>page_view</code>
        (attributed: {at_pv_pct:.0f}%), and {f"every device has the same unattributed share ({ua_dev_min:.1f}%)" if f"{ua_dev_min:.1f}" == f"{ua_dev_max:.1f}" else f"the unattributed share barely varies by device ({ua_dev_min:.1f}–{ua_dev_max:.1f}%)"}.
        They are <em>short</em>: median
        {ua_med_ev} events and {ua_med_eng:.0f}s engaged, against {at_med_ev} events and
        {at_med_eng:.0f}s. The tag sits on only some events, so the fewer events a
        session has, the likelier it has none. <strong>At the same session length, view
        rates are close</strong> (table): the low rate is a session-length effect, not a
        different kind of traffic.</p>
        <p><strong>So all sessions stay the primary view.</strong> Dropping the block would
        remove short sessions selectively and flatter every rate. The right-hand column is a
        sensitivity check only.</p>
      </div>
      <div>
        <table class="dev-table">
          <thead><tr><th>Session length</th><th>Sessions</th><th>Unattributed</th>
            <th>Saw product · unattr.</th><th>· attributed</th></tr></thead>
          <tbody>
{dq_bucket_rows}
          </tbody>
        </table>
        <table class="dev-table" style="margin-top:22px">
          <thead><tr><th>Funnel</th><th>All sessions · primary</th>
            <th>Excl. (not set) · biased up</th></tr></thead>
          <tbody>
{dq_sens_rows}
          </tbody>
        </table>
      </div>
    </div>
    <div class="two-col dq-foot" style="gap:28px">
      <div>
        <h4 class="dq-h">2 · "View item" means "saw products", usually on a listing</h4>
        <p>{vi_multi_pct:.0f}% of <code>view_item</code> events carry several products
        (avg {vi_avg_items:.1f}), which makes them list impressions, not product-page views.
        So this page counts sessions that <em>saw</em> products, not ones that opened a
        product page.
        <code>add_to_cart</code> also fires from listings, with the whole list attached (avg
        {atc_avg_items:.0f} items); only the item with a quantity was carted
        ({atc_one_pct:.0f}% of events have exactly one), and category figures use that item
        only.</p>
      </div>
      <div>
        <h4 class="dq-h">3 · Revenue covers funnel purchases only</h4>
        <p>The funnel counts a purchase only if the session went through every step in order
        (<em>sequential</em>). That gives {purchase_n:,} purchases and
        ${total_revenue:,.0f}. {orders_no_rev:,} of them record no revenue, so the
        ${aov:,.2f} AOV averages the {orders_with_rev:,} that do, and revenue per purchase is
        ${rev_per_purchase:,.2f}. Across all sessions, {all_purch_sess:,} include a purchase
        (${all_revenue:,.0f}). Of the {all_purch_sess - purchase_n:,} left out,
        {purch_no_cart:,} have no add-to-cart in that session, which usually means the cart
        was filled in an earlier visit.</p>
      </div>
    </div>
  </div>
"""

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GA4 Funnel Analysis — Product Dashboard</title>
<!-- plotly.js is embedded (not loaded from a CDN) so the file works offline and
     the JS version always matches the Python plotly package that built the figures. -->
<script type="text/javascript">{get_plotlyjs()}</script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #f4f5f7; color: #111827; line-height: 1.6; font-size: 15px; }}

  /* Header */
  .page-header {{ background: linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%);
                  color: white; padding: 40px 48px 32px; }}
  .page-header h1 {{ font-size: 1.6rem; font-weight: 700; letter-spacing: -0.4px; }}
  .page-header .subtitle {{ margin-top: 6px; font-size: 0.85rem; opacity: 0.65;
                             display: flex; gap: 20px; flex-wrap: wrap; }}
  .page-header .subtitle span::before {{ content: "· "; }}
  .page-header .subtitle span:first-child::before {{ content: ""; }}

  /* Layout */
  .container {{ max-width: 1180px; margin: 0 auto; padding: 36px 24px 60px; }}
  .section-label {{ font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
                    letter-spacing: 1.2px; color: #6b7280; margin: 40px 0 14px; }}
  .section-label:first-of-type {{ margin-top: 0; }}

  /* Cards */
  .card {{ background: white; border-radius: 12px; padding: 28px 32px;
           box-shadow: 0 1px 3px rgba(0,0,0,.08), 0 1px 8px rgba(0,0,0,.04);
           margin-bottom: 20px; }}

  /* Executive summary */
  .exec-card {{ background: white; border-radius: 12px; padding: 32px;
               box-shadow: 0 1px 3px rgba(0,0,0,.08); margin-bottom: 20px;
               border-left: 5px solid {priority_color}; }}
  .exec-card .priority-tag {{
    display: inline-block; background: {priority_color}18;
    color: {priority_color}; font-size: 0.7rem; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.8px;
    padding: 4px 12px; border-radius: 20px; margin-bottom: 14px;
  }}
  .exec-card p {{ font-size: 1.05rem; color: #1f2937; line-height: 1.75; }}

  /* KPI grid */
  .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
               gap: 16px; margin-bottom: 20px; }}
  .kpi {{ background: white; border-radius: 12px; padding: 22px 24px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); border-top: 3px solid #4C78A8; }}
  .kpi.warn {{ border-top-color: #ea580c; }}
  .kpi-label {{ font-size: 0.72rem; color: #6b7280; text-transform: uppercase;
                letter-spacing: 0.7px; font-weight: 600; margin-bottom: 6px; }}
  .kpi-value {{ font-size: 2.1rem; font-weight: 800; color: #111827; line-height: 1.1; }}
  .kpi-sub   {{ font-size: 0.73rem; color: #9ca3af; margin-top: 5px; }}

  /* Key findings */
  .findings-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
                    gap: 16px; margin-bottom: 20px; }}
  .finding {{ background: white; border-radius: 12px; padding: 24px;
              box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .finding-num {{ font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
                  letter-spacing: 0.8px; color: #9ca3af; margin-bottom: 8px; }}
  .finding-stat {{ font-size: 2rem; font-weight: 800; line-height: 1;
                   margin-bottom: 10px; }}
  .finding-stat.red {{ color: #dc2626; }}
  .finding-stat.amber {{ color: #ea580c; }}
  .finding-stat.blue {{ color: #2563eb; }}
  .finding-stat.green {{ color: #16a34a; }}
  .finding-title {{ font-size: 0.9rem; font-weight: 600; color: #111827; margin-bottom: 6px; }}
  .finding-body  {{ font-size: 0.83rem; color: #4b5563; line-height: 1.6; }}

  /* Chart + insight side-by-side */
  .chart-insight {{ display: grid; grid-template-columns: 2fr 1fr; gap: 24px;
                    align-items: start; }}
  .chart-insight.flip {{ grid-template-columns: 1fr 2fr; }}
  @media (max-width: 860px) {{
    .chart-insight, .chart-insight.flip {{ grid-template-columns: 1fr; }}
  }}
  .insight-panel {{ background: #f8fafc; border-radius: 10px; padding: 22px 24px; }}
  .insight-panel h4 {{ font-size: 0.8rem; font-weight: 700; text-transform: uppercase;
                       letter-spacing: 0.8px; color: #6b7280; margin-bottom: 12px; }}
  .insight-panel p {{ font-size: 0.85rem; color: #374151; margin-bottom: 10px; line-height: 1.65; }}
  .insight-panel p:last-child {{ margin-bottom: 0; }}
  .insight-panel strong {{ color: #111827; }}
  .caveat-note {{ border-left: 3px solid #cbd5e1; padding-left: 12px;
                  font-style: italic; color: #6b7280 !important; }}

  /* Device summary table */
  .dev-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  .dev-table th {{ text-align: right; font-size: 0.68rem; font-weight: 700;
                   text-transform: uppercase; letter-spacing: 0.7px;
                   color: #9ca3af; padding: 0 10px 10px; border-bottom: 1px solid #e5e7eb; }}
  .dev-table th:first-child {{ text-align: left; }}
  .dev-table td {{ text-align: right; padding: 12px 10px; color: #374151;
                   border-bottom: 1px solid #f3f4f6; font-variant-numeric: tabular-nums; }}
  .dev-table tr:last-child td {{ border-bottom: none; }}
  .dev-table .dev-name {{ text-align: left; font-weight: 600; color: #111827;
                          text-transform: capitalize; }}
  .dev-table .dev-key {{ font-weight: 700; color: #1d4ed8; }}
  .dev-table .ci {{ color: #6b7280; font-size: 0.78rem; white-space: nowrap; }}
  .flag-below {{ color: #c2410c; font-weight: 700; }}
  .flag-above {{ color: #1d4ed8; font-weight: 700; }}
  .flag-neutral {{ color: #9ca3af; }}

  /* Opportunity box */
  .opportunity {{ background: #eff6ff; border: 1px solid #bfdbfe;
                  border-radius: 10px; padding: 20px 24px; margin-top: 16px; }}
  .opportunity h4 {{ font-size: 0.78rem; font-weight: 700; text-transform: uppercase;
                     letter-spacing: 0.8px; color: #1d4ed8; margin-bottom: 10px; }}
  .opportunity p {{ font-size: 0.84rem; color: #1e40af; line-height: 1.6; }}
  .opportunity .big-number {{ font-size: 1.5rem; font-weight: 800;
                               color: #1d4ed8; display: block; margin: 4px 0; }}

  /* Recommendations */
  .rec-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
               gap: 16px; }}
  .rec {{ background: white; border-radius: 12px; padding: 24px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .rec-priority {{ font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
                   letter-spacing: 0.8px; padding: 3px 10px; border-radius: 12px;
                   display: inline-block; margin-bottom: 14px; }}
  .rec-priority.p1 {{ background: #fef2f2; color: #dc2626; }}
  .rec-priority.p2 {{ background: #fff7ed; color: #ea580c; }}
  .rec-priority.p3 {{ background: #f0fdf4; color: #16a34a; }}
  .rec h4 {{ font-size: 0.95rem; font-weight: 700; color: #111827; margin-bottom: 8px; }}
  .rec p  {{ font-size: 0.83rem; color: #4b5563; line-height: 1.65; margin-bottom: 8px; }}
  .rec .metric {{ font-size: 0.78rem; font-weight: 600; color: #6b7280;
                  border-top: 1px solid #f3f4f6; padding-top: 10px; margin-top: 4px; }}
  .rec .metric::before {{ content: "📊 Watch: "; }}
  .rec-section {{ margin-top: 14px; padding-top: 12px; border-top: 1px solid #f3f4f6; }}
  .rec-section:first-of-type {{ margin-top: 10px; padding-top: 10px; }}
  .rec-section-label {{ font-size: 0.67rem; font-weight: 700; text-transform: uppercase;
                        letter-spacing: 0.9px; color: #9ca3af; margin-bottom: 6px; }}
  .rec-impact {{ background: #f8fafc; border-radius: 8px; padding: 10px 14px; margin-top: 12px; }}
  .rec-impact .rec-section-label {{ color: #1d4ed8; }}

  /* SaaS translation table */
  .saas-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  .saas-table th {{ background: #f8fafc; padding: 10px 16px; text-align: left;
                    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.8px;
                    color: #6b7280; font-weight: 600; border-bottom: 2px solid #e5e7eb; }}
  .saas-table td {{ padding: 12px 16px; border-bottom: 1px solid #f3f4f6;
                    color: #374151; vertical-align: top; }}
  .saas-table tr:last-child td {{ border-bottom: none; }}
  .saas-table td:first-child {{ font-weight: 600; color: #111827; width: 22%; }}
  .saas-table .rate {{ font-size: 0.78rem; color: #6b7280; }}

  /* AI brief */
  .ai-badge {{ background: linear-gradient(135deg, #7c3aed, #4f46e5); color: white;
               font-size: 0.65rem; font-weight: 700; padding: 3px 10px;
               border-radius: 20px; letter-spacing: 0.5px; margin-right: 8px; }}
  .brief-body h3 {{ font-size: 1rem; font-weight: 700; color: #111827;
                   margin: 20px 0 8px; padding-top: 16px;
                   border-top: 1px solid #f3f4f6; }}
  .brief-body h3:first-child {{ border-top: none; padding-top: 0; margin-top: 0; }}
  .brief-body p  {{ font-size: 0.87rem; color: #374151; margin-bottom: 10px;
                   line-height: 1.7; }}
  .brief-body li {{ font-size: 0.87rem; color: #374151; margin: 5px 0;
                   line-height: 1.65; }}
  .brief-body hr {{ border: none; border-top: 1px solid #e5e7eb; margin: 16px 0; }}
  .brief-body ul, .brief-body ol {{ margin: 4px 0 10px; padding-left: 22px; }}
  .brief-body li ul, .brief-body li ol {{ margin: 4px 0 2px; }}
  .brief-body h4 {{ font-size: 0.92rem; font-weight: 700; color: #111827; margin: 14px 0 6px; }}
  .brief-body code {{ font-size: 0.8rem; background: #f3f4f6; padding: 1px 5px; border-radius: 4px; }}
  .appendix summary {{ cursor: pointer; font-weight: 700; color: #111827; list-style: revert; }}
  .appendix .appendix-sub {{ display: block; font-weight: 400; font-size: 0.78rem;
                            color: #6b7280; margin: 4px 0 0 18px; }}
  .appendix .appendix-body {{ margin-top: 18px; }}

  /* Data-quality callout */
  .dq-card {{ border-left: 5px solid #d97706; }}
  .dq-card p {{ font-size: 0.85rem; color: #374151; line-height: 1.65; margin-bottom: 10px; }}
  .dq-h {{ font-size: 0.9rem; font-weight: 700; color: #111827; margin-bottom: 8px; }}
  .dq-foot {{ margin-top: 22px; padding-top: 18px; border-top: 1px solid #f3f4f6; }}
  .dq-card .dev-table th {{ white-space: normal; }}

  /* Two-col utility */
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  @media (max-width: 720px) {{ .two-col {{ grid-template-columns: 1fr; }} }}

  /* Footer */
  .footer {{ border-top: 1px solid #e5e7eb; margin-top: 48px; padding-top: 24px;
             font-size: 0.76rem; color: #9ca3af; line-height: 1.9; }}
  .footer strong {{ color: #6b7280; }}
</style>
</head>
<body>

<div class="page-header">
  <h1>GA4 E-Commerce — Product Funnel Analysis</h1>
  <div class="subtitle">
    <span>Session-scoped · Sequential conversion</span>
    <span>{date_label}</span>
    <span>GA4 public e-commerce sample · BigQuery</span>
    <span>Generated {pd.Timestamp.now().strftime("%Y-%m-%d")}</span>
  </div>
</div>

<div class="container">

  <!-- ── About This Dataset ────────────────────────────────────────────── -->
  <p class="section-label">About This Dataset</p>
  <div class="card">
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:24px;font-size:0.87rem;color:#374151;line-height:1.7">
      <div>
        <strong style="display:block;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.8px;color:#6b7280;margin-bottom:8px">Business</strong>
        <strong>Google Merchandise Store</strong> (<a href="https://shop.googlemerchandisestore.com" target="_blank" style="color:#2563eb">store.google.com</a>)<br>
        B2C e-commerce selling Google-branded merchandise: apparel, accessories, and electronics.
      </div>
      <div>
        <strong style="display:block;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.8px;color:#6b7280;margin-bottom:8px">Dataset</strong>
        {"Google's public obfuscated GA4 export on BigQuery<br>" if IS_PUBLIC_SAMPLE else "GA4 BigQuery export<br>"}
        (<code>{_html.escape(DATASET)}</code>)<br>
        {date_label} · {total_sessions:,} sessions · {unique_users:,} unique users
      </div>
      <div>
        <strong style="display:block;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.8px;color:#6b7280;margin-bottom:8px">Events analysed</strong>
        <code>session_start</code> · <code>view_item</code> · <code>add_to_cart</code><br>
        <code>begin_checkout</code> · <code>purchase</code><br>
        Raw GA4 event stream — nested <code>event_params</code>, session IDs extracted via <code>UNNEST</code>
      </div>
    </div>
  </div>

  <!-- ── Executive Summary ───────────────────────────────────────────────── -->
  <p class="section-label">Executive Summary</p>
  <div class="exec-card">
    <div class="priority-tag">Primary bottleneck: {priority_label}</div>
    <p>{exec_summary}</p>
  </div>

{data_quality_html}

  <!-- ── KPIs ───────────────────────────────────────────────────────────── -->
  <p class="section-label">Key Metrics · {date_label}</p>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-label">Total Sessions</div>
      <div class="kpi-value">{total_sessions:,}</div>
      <div class="kpi-sub">unique user × session pairs</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Unique Users</div>
      <div class="kpi-value">{unique_users:,}</div>
      <div class="kpi-sub">distinct user_pseudo_ids · {total_sessions/unique_users:.1f} sessions/user avg</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Session → Purchase</div>
      <div class="kpi-value">{overall_cvr:.2f}%</div>
      <div class="kpi-sub">end-to-end conversion rate</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Avg Order Value</div>
      <div class="kpi-value">${aov:,.2f}</div>
      <div class="kpi-sub">${total_revenue:,.0f} ÷ {orders_with_rev:,} orders with revenue
      ({orders_no_rev:,} of {purchase_n:,} purchases record none) · median ${median_order:,.0f}</div>
    </div>
    <div class="kpi {'warn' if wk1_warn else ''}">
      <div class="kpi-label">Week-1 Return {retention_flag}</div>
      <div class="kpi-value">{wk1_display}</div>
      <div class="kpi-sub">return in the next calendar week ·
      {f"below the {WK1_TARGET:.0f}% planning target (an assumption)" if wk1_warn else f"planning target {WK1_TARGET:.0f}% (an assumption)"}</div>
    </div>
  </div>

  <!-- ── Key Findings ───────────────────────────────────────────────────── -->
  <p class="section-label">Key Findings</p>
  <div class="findings-grid">

    <div class="finding">
      <div class="finding-num">Finding 01 · Biggest drop</div>
      <div class="finding-stat red">{view_rate:.0f}%</div>
      <div class="finding-title">of sessions ever see a product</div>
      <div class="finding-body">
        <strong>{100-view_rate:.0f}%</strong> of sessions —
        <strong>{total_lost_discovery:,} visits</strong> — end without seeing a
        single product. This is the largest absolute loss in the funnel.
        {ua_lost_share:.0f}% of it sits in very short sessions with no channel tag (see Data
        quality). In a SaaS context, this maps to users who log in but never
        navigate to a core feature.
      </div>
    </div>

    <div class="finding">
      <div class="finding-num">Finding 02 · Seeing products rarely leads to a cart</div>
      <div class="finding-stat amber">{atc_from_view:.0f}%</div>
      <div class="finding-title">of sessions that saw products added one to cart</div>
      <div class="finding-body">
        {100-atc_from_view:.0f}% of sessions that saw products
        ({view_item_n - carted_n:,}) added nothing to cart. This is a separate problem from
        discovery, and it depends on the category. Per-category rates run from
        {float(category_tbl["atc_pct"].min()):.0f}% to {float(category_tbl["atc_pct"].max()):.0f}%
        (Product Category, below).
      </div>
    </div>

    <div class="finding">
      <div class="finding-num">Finding 03 · Most carts are abandoned</div>
      <div class="finding-stat red">{cart_abandon_pct:.0f}%</div>
      <div class="finding-title">of carting sessions never begin checkout</div>
      <div class="finding-body">
        Only <strong>{cart_to_checkout:.0f}%</strong> of sessions with a cart
        ({checkout_n:,} of {carted_n:,}) start checkout, and
        <strong>{checkout_to_purch:.0f}%</strong> of those finish the purchase.
        {"This is the largest loss once a shopper has shown purchase intent. " if cart_abandon_pct > 100 - checkout_to_purch else ""}How fast
        <em>converters</em> buy (median {total_ttc_min:.0f} min) says nothing about the
        sessions that abandon.
      </div>
    </div>

    <div class="finding">
      <div class="finding-num">Finding 04 · Few visitors come back</div>
      <div class="finding-stat {'amber' if wk1_warn else 'blue'}">{wk1_display}</div>
      <div class="finding-title">Week-1 return rate</div>
      <div class="finding-body">
        On average <strong>{wk1_display}</strong> of a weekly cohort has a session in
        the next calendar week, against an {WK1_TARGET:.0f}% planning target (an
        assumption, not an industry benchmark). This dataset cannot say whether
        campaigns brought low-intent traffic or there was no post-visit
        re-engagement.
      </div>
    </div>


    <div class="finding">
      <div class="finding-num">Finding 05 · Revenue</div>
      <div class="finding-stat green">${total_revenue:,.0f}</div>
      <div class="finding-title">from {purchase_n:,} funnel purchases</div>
      <div class="finding-body">
        ${rev_per_purchase:,.2f} per purchase. {orders_no_rev:,} purchases record no
        revenue, so AOV over the {orders_with_rev:,} that do is
        <strong>${aov:,.2f}</strong> (median ${median_order:,.0f}). Revenue per session
        by channel is broken out in the Acquisition Channel section below.
      </div>
    </div>

    <div class="finding">
      <div class="finding-num">Finding 06 · Device is not the lever</div>
      <div class="finding-stat blue">{device_gap:.2f}pp</div>
      <div class="finding-title">spread in purchase rate across all devices</div>
      <div class="finding-body">
        Purchase conversion differs by only <strong>{device_gap:.2f} percentage
        points</strong> between desktop, mobile and tablet
        ({"significant but negligible" if device_significant else "not statistically significant"},
        χ² {fmt_p(device_test["p"])}). A mobile-first
        redesign — the intuitive response to a {overall_cvr:.2f}% conversion rate — would not
        have moved the number. Effort belongs in discovery, carts and lifecycle,
        not device-specific work.
      </div>
    </div>

  </div>

  <!-- ── Funnel + insight ────────────────────────────────────────────────── -->
  <p class="section-label">Conversion Funnel — Session-Scoped · Sequential</p>
  <div class="card">
    <div class="chart-insight">
      <div>{chart_div(fig_funnel, "chart-funnel")}</div>
      <div>
        <div class="insight-panel">
          <h4>What this shows</h4>
          <p><strong>Sequential</strong>: a session counts at a step only if it
          completed every earlier step <em>in order, within the same session</em>, as
          a closed funnel does in the GA4 UI.</p>
          <p>The <strong>largest absolute loss</strong> is between
          <strong>{biggest_drop_step}</strong> and
          <strong>{funnel_df.loc[biggest_drop_idx + 1, "step"]}</strong>:
          {biggest_drop_n:,} sessions ({funnel_df.loc[biggest_drop_idx + 1, "drop_off"]:.1f}%).</p>
          <p>After that, step pass rates are
          <strong>{atc_from_view:.1f}%</strong> (saw products → cart),
          <strong>{cart_to_checkout:.1f}%</strong> (cart → checkout) and
          <strong>{checkout_to_purch:.1f}%</strong> (checkout → purchase). Fewer
          sessions are lost at these steps, but the drop-off rates are just as steep:
          {cart_abandon_pct:.0f}% of carts never reach checkout.</p>
        </div>
        <div class="opportunity">
          <h4>Revenue opportunity</h4>
          <p>If the share of sessions that see a product rises by <strong>5 percentage points</strong> ({view_rate:.1f}% → {view_rate+5:.1f}%):</p>
          <span class="big-number">~{lift_5pp_purchases:,} extra orders</span>
          <p>~<strong>${lift_5pp_revenue:,.0f}</strong> additional revenue
          at ${rev_per_purchase:,.2f} per funnel purchase.</p>
          <p style="font-size:0.78rem;opacity:0.85;margin-top:8px">
          <strong>Sensitivity.</strong> This assumes incremental sessions convert at
          the same {view_to_purchase_rate*100:.1f}% saw-products → purchase rate as today's
          traffic — an optimistic upper bound, since marginal traffic typically
          converts below average. At 75% of that rate:
          <strong>${lift_5pp_rev_75:,.0f}</strong>. At 50%:
          <strong>${lift_5pp_rev_50:,.0f}</strong>. Treat
          <strong>${lift_5pp_rev_50:,.0f}–${lift_5pp_revenue:,.0f}</strong> as the
          planning range.</p>
        </div>
      </div>
    </div>
  </div>

  <!-- ── Time-to-convert ────────────────────────────────────────────────── -->
  <p class="section-label">Time-to-Convert</p>
  <div class="card">
    <div class="two-col">
      <div>{chart_div(fig_ttc, "chart-ttc")}</div>
      <div class="insight-panel">
        <h4>Conclusion</h4>
        <p>Sessions that convert take a median <strong>{total_ttc_min:.0f} minutes</strong>
        from session start to purchase, with checkout → purchase taking
        {_ttc_y[3]:.1f} min. That describes buyers only. It doesn't show why
        {cart_abandon_pct:.0f}% of carts never reach checkout.</p>
        <p><strong>† Partly a tracking signal, not shopper behaviour.</strong> The median
        saw-products → cart gap is only {_ttc_y[1]:.1f} min, and
        <strong>{cart_same_ts_pct:.0f}%</strong> of carting sessions
        ({cart_same_ts:,} of {carting_all:,}; {conv_same_ts_pct:.0f}% of converting ones)
        log their first add-to-cart at the <em>exact</em> microsecond of their first
        product impression. {f"{cart_within_1s:,}" if cart_within_1s else "None"} fall
        strictly between zero and one second, and
        {cart_no_view:,} carts come before, or without, any product impression. Together
        with the list-sized item arrays (Data quality, point 2), this points to
        quick-add buttons on listing pages firing both events at once.</p>
      </div>
    </div>
  </div>

  <!-- ── Device breakdown (table — a negative result) ───────────────────── -->
  <p class="section-label">Device Breakdown — A Negative Result</p>
  <div class="card">
    <div class="two-col">
      <div>
        <table class="dev-table">
          <thead>
            <tr>
              <th>Device</th><th>Sessions</th><th>Share</th>
              <th>Saw Product</th><th>Add to Cart</th><th>Purchase</th><th>95% CI</th>
            </tr>
          </thead>
          <tbody>
{device_rows_html}
          </tbody>
        </table>
      </div>
      <div class="insight-panel">
        <h4>Conclusion</h4>
        <p>Session-to-purchase rates sit within
        <strong>{device_gap:.2f} percentage points</strong> across desktop,
        mobile and tablet{"" if device_significant else ", and their 95% intervals overlap"}.
        A chi-square test of purchase × device gives
        <strong>{fmt_p(device_test["p"])}</strong> (χ² = {device_test["chi2"]:.1f},
        {device_test["dof"]} df): {"statistically significant, but the effect is negligible "
        f"(Cramér's V = {device_test['cramers_v']:.3f})" if device_significant else
        "no evidence that device changes the purchase rate"}.</p>
        <p><strong>This is reported precisely because it is a null result.</strong>
        The intuitive next step after seeing a {overall_cvr:.2f}% conversion rate is a
        mobile-first redesign. The data says that work would not have moved the
        number — the leak is upstream of device, in discovery.</p>
        <p class="caveat-note">Shown as a table, not a chart: three series
        differing by {device_gap:.2f}pp render as three identical bars and invite
        the reader to hunt for a difference that isn't there.</p>
      </div>
    </div>
  </div>

  <!-- ── Acquisition channel (E1) ───────────────────────────────────────── -->
  <p class="section-label">Acquisition Channel — Is Discovery a Traffic Problem?</p>
  <div class="card">
    <div class="chart-insight">
      <div>{chart_div(fig_channel, "chart-channel")}</div>
      <div class="insight-panel">
        <h4>Conclusion</h4>
        {channel_verdict}
        <p class="caveat-note">{channel_basis_note} The public sample obfuscates
        some values (<code>&lt;Other&gt;</code>, <code>(data deleted)</code>);
        they are kept as reported rather than guessed.</p>
      </div>
    </div>
    <table class="dev-table" style="margin-top:20px">
      <thead>
        <tr>
          <th>Channel</th><th>Sessions</th><th>Share</th><th>Saw Product</th>
          <th>Saw → Cart</th><th>Purchase</th><th>Rev / Session</th>
        </tr>
      </thead>
      <tbody>
{channel_rows_html}
      </tbody>
    </table>
    <p style="font-size:0.75rem;color:#9ca3af;margin-top:10px">
      * fewer than 30 purchases — the <em>purchase</em> and revenue columns are too
      noisy to compare. View-item rates rest on session counts and are unaffected.
      Channels under 1% of sessions are grouped.</p>
  </div>

  <!-- ── Product category (E2) ──────────────────────────────────────────── -->
  <p class="section-label">Product Category — Where Interest Fails to Become Intent</p>
  <div class="card">
    <div class="chart-insight">
      <div>{chart_div(fig_category, "chart-category")}</div>
      <div class="insight-panel">
        <h4>Where to start the merchandising audit</h4>
        {category_verdict}
        <p class="caveat-note">A session "saw" a category when a product from it appeared
        in a viewed product list. It counts as carted only if the item actually carted
        (the one with a quantity, not the rest of the list) is from that category and
        was carted after the first sighting. So rates are stricter than the
        {shoppers_atc:.0f}% shoppers figure, and rows are not additive.
        Categories with fewer than {MIN_CATEGORY_VIEWS:,} viewing sessions
        ({category_excluded_n} excluded) and <code>(not set)</code>
        ({category_notset_share:.1f}% of category views) are not ranked.
        Labels are normalised to their last path segment
        (<code>Lifestyle/Bags/</code> → <code>Bags</code>): {category_raw_labels} raw
        labels collapse into {len(category_raw)} categories, {category_merged_groups} of
        them merged from more than one label.</p>
        {category_price_note}
      </div>
    </div>
    <table class="dev-table" style="margin-top:20px">
      <thead>
        <tr>
          <th>Category</th><th>Sessions that saw it</th><th>Carting sessions</th>
          <th>Seen → Carted</th><th>95% CI</th><th>vs site average</th><th>Carts at stake</th>
        </tr>
      </thead>
      <tbody>
{category_rows_html}
      </tbody>
    </table>
  </div>

  <!-- ── Cohort retention ────────────────────────────────────────────────── -->
  <p class="section-label">Weekly Cohort Retention</p>
  <div class="card">
    <div class="chart-insight flip">
      <div class="insight-panel">
        <h4>What this shows</h4>
        <p>Each row is a group of users who had their <em>first ever session</em>
        in that week. The cells show what % of them had a session in each
        later calendar week.</p>
        <p><strong>Week-1 return</strong> is {WK1_DEFINITION}.</p>
        <p>Avg Week-1 return: <strong>{wk1_display}</strong><br>
        Avg Week-2 return: <strong>{f"{avg_wk2}%" if avg_wk2 else "—"}</strong></p>
        <h4 style="margin-top:16px">What this means</h4>
        <p>Week-1 return of <strong>{wk1_display}</strong> is
        {f"{WK1_TARGET - avg_wk1:.1f}pp below" if wk1_warn else "at or above"} the
        {WK1_TARGET:.0f}% planning target (an assumption, not an external benchmark).
        {100 - avg_wk1:.0f}% of a typical cohort shows no session the following week.</p>
        <p><strong>Possible causes</strong> (this dataset cannot tell them
        apart): no triggered email or push re-engagement, traffic from
        low-intent channels, or a first session that didn't reach a product.</p>
        <p><strong>In SaaS terms:</strong> the closest analogue is D7 retention,
        but this is a calendar-week measure, so it is not directly comparable. The
        usual intervention is lifecycle emails or in-app nudges on day 2–3.</p>
        <h4 style="margin-top:16px">Data quality — two boundary effects</h4>
        {partial_cohort_note}
        <p><strong>2 · Recent cohorts are right-censored.</strong> A user who
        joined in the final week has not had the opportunity to return. Those
        cells are left <strong>blank rather than 0%</strong>, because filling them
        with zero would report "hasn't happened yet" as "did not retain" and
        drag the trend line down artificially.</p>
        <p class="caveat-note">Both are the kind of artefact that produces a
        confident, entirely false narrative if left unhandled — the drop-off
        across later cohorts would look far steeper than it is.</p>
      </div>
      <div>{chart_div(fig_cohort, "chart-cohort")}</div>
    </div>
  </div>

{visitor_html}
  <!-- ── Recommendations ────────────────────────────────────────────────── -->
  <p class="section-label">Recommendations — Prioritised by Impact</p>
  <div class="rec-grid">

    <div class="rec">
      <span class="rec-priority p1">Priority 1 · Immediate</span>
      <h4>Fix product discovery</h4>

      <div class="rec-section">
        <div class="rec-section-label">Problem</div>
        <p><strong>{100-view_rate:.0f}% of sessions never see a product</strong> —
        <strong>{total_lost_discovery:,} visits</strong> lost before shopping begins.
        This is the largest absolute loss in the funnel. In a SaaS context, it maps to
        users who log in but never navigate to a core feature.</p>
      </div>

      <div class="rec-section">
        <div class="rec-section-label">Recommended Actions</div>
        <p>Audit the homepage and category navigation — are products surfaced above the fold?
        Test a personalised "Recommended for you" module on the landing page.
        Instrument clicks on navigation elements to identify which paths lead to product views
        and which lead to exits.</p>
      </div>

      <div class="rec-section rec-impact">
        <div class="rec-section-label">Business Impact</div>
        <p>A <strong>5pp improvement</strong> in the share of sessions that see a product
        would drive approximately <strong>~{lift_5pp_purchases:,} additional orders</strong>
        and ~<strong>${lift_5pp_revenue:,.0f}</strong> (upper bound; see sensitivity above).</p>
        <div class="metric">share of sessions that see a product (target: {view_rate+5:.1f}% — the +5pp planning scenario, an assumption)</div>
      </div>
    </div>

    <div class="rec">
      <span class="rec-priority p2">Priority 2 · Short-term</span>
      <h4>Recover carts and build re-engagement</h4>

      <div class="rec-section">
        <div class="rec-section-label">Problem</div>
        <p><strong>{cart_abandon_pct:.0f}% of carting sessions</strong>
        ({carted_n - checkout_n:,} of {carted_n:,}) never begin checkout, and Week-1 return
        is <strong>{wk1_display}</strong> ({100 - avg_wk1:.0f}% of a cohort has no session the
        next calendar week). This dataset cannot show whether any re-engagement
        (email, push, remarketing) was running; if none was, it is the first thing to test.</p>
      </div>

      <div class="rec-section">
        <div class="rec-section-label">Recommended Actions</div>
        <p>Launch a triggered email or push sequence for identifiable users:
        <strong>within 24h</strong> a cart reminder for sessions that carted but did not
        check out, <strong>day 3</strong> a reminder of products they browsed, and
        <strong>day 7</strong> a win-back offer. Test it against a holdout group and measure cart →
        checkout and Week-1 / Week-2 return per cohort.</p>
      </div>

      <div class="rec-section rec-impact">
        <div class="rec-section-label">Business Impact</div>
        <p>Lifting Week-1 return from today's <strong>{wk1_display}</strong> average to
        <strong>{WK1_TARGET:.0f}%</strong> would have brought back
        ~<strong>{wk1_extra_users:,}</strong> more users in their second week, across the
        {wk1_n_cohorts} full cohorts ({wk1_base_users:,} first-time users) observed in this
        window — repeat sessions that need no extra acquisition spend.
        {f"Counting one session each, valued between a new session (${vs_new['rev_per_session']:,.2f}) "
         f"and a returning one (${vs_ret['rev_per_session']:,.2f}), that is "
         f"<strong>${rec2_rev_low:,.0f}–${rec2_rev_high:,.0f}</strong> over the window, before any "
         f"cart-recovery effect." if has_visitor_split else ""}</p>
        <div class="metric">cart → checkout rate · Week-1 return (target: {WK1_TARGET:.0f}%, a planning assumption)</div>
      </div>
    </div>

    <div class="rec">
      <span class="rec-priority p3">Priority 3 · Investigate</span>
      <h4>Act on the channel and category splits</h4>

      <div class="rec-section">
        <div class="rec-section-label">Problem</div>
        <p>{channel_rec_problem}</p>
      </div>

      <div class="rec-section">
        <div class="rec-section-label">Recommended Actions</div>
        <p>Start the merchandising audit with
        {", ".join(category_top_targets) if category_top_targets else "the lowest view → cart categories"}
        (see Product Category above): review how their products are presented in
        listings, including images, price and stock. On the channel side, weight acquisition spend
        towards revenue per session, not session volume.</p>
      </div>

      <div class="rec-section rec-impact">
        <div class="rec-section-label">Business Impact</div>
        <p>{rec3_impact}</p>
        <div class="metric">revenue per session by channel · seen → carted in target categories</div>
      </div>
    </div>

  </div>


  <!-- ── How the AI brief is produced ───────────────────────────────────── -->
  <!-- Describes what the code actually does: the brief is written in notebook
       Section 10 and reviewed; this script only embeds it. -->
  <p class="section-label">AI-Augmented Reporting — How the Brief Is Produced</p>
  <div class="card">
    <p style="font-size:0.87rem;color:#4b5563;margin-bottom:20px;line-height:1.7">
      This page is built in two separate steps. A <strong>parameterised Python
      script</strong> (<code>generate_dashboard.py</code>) recomputes every query, chart
      and number shown here. The written <strong>product brief</strong> is produced
      in Section 10 of the analysis notebook by a large language model, reviewed by
      the analyst, and then embedded in this page.
    </p>
    <div class="two-col" style="gap:24px">
      <div>
        <div class="insight-panel">
          <h4>Where the LLM fits in</h4>
          <p>Notebook Section 10 packages the computed results — funnel rates,
          revenue, device, channel and category splits, cohort retention — as
          structured JSON and sends them to an LLM: Claude (Anthropic API) when an
          Anthropic key is set, otherwise Gemini as a free alternative. The model
          used is named on the brief.</p>
          <p>The LLM never sees raw events or SQL. It receives curated, computed
          metrics and writes the executive summary, findings, hypotheses and
          recommended actions. Keeping it downstream of verified numbers means it
          interprets results rather than calculating them.</p>
          <p>Unlike a rule-based template ("if CVR &lt; 1% then show warning"), the
          model frames the findings in business language. Its output is reviewed
          before publishing, and this page marks a brief as out of date if it was
          generated before the latest analyses were added.</p>
        </div>
      </div>
      <div>
        <div class="insight-panel">
          <h4>End-to-end architecture</h4>
          <p style="font-family:monospace;font-size:0.81rem;background:#f3f4f6;padding:14px 16px;border-radius:8px;line-height:2.1;color:#1f2937">
            GA4 BigQuery Export<br>
            &nbsp;&nbsp;&nbsp;↓ SQL · partition-pruned queries<br>
            Computed metrics (pandas)<br>
            &nbsp;&nbsp;&nbsp;├→ notebook §10 → <strong>LLM (Claude / Gemini)</strong><br>
            &nbsp;&nbsp;&nbsp;│&nbsp;&nbsp;&nbsp;&nbsp;→ product brief → analyst review<br>
            &nbsp;&nbsp;&nbsp;└→ generate_dashboard.py → Plotly charts<br>
            &nbsp;&nbsp;&nbsp;↓ charts + reviewed brief → HTML<br>
            dashboard.html → Stakeholders
          </p>
          <p style="margin-top:14px;font-size:0.83rem;color:#374151">
            Set <code>GA4_DATASET</code>, <code>GA4_DATE_START</code> and
            <code>GA4_DATE_END</code> (read by <code>config.py</code>) and run
            <code style="background:#f3f4f6;padding:2px 6px;border-radius:4px">python generate_dashboard.py</code>
            to recompute every query, chart and number on this page. Re-run notebook
            Section 10 to refresh the brief. The script step can be scheduled; the
            brief step deliberately keeps a human reviewer in the loop.
          </p>
        </div>
        <div class="opportunity" style="margin-top:12px">
          <h4>Next enhancement</h4>
          <p>Pass the current period metrics <em>and</em> the prior period metrics to the LLM
          context so the AI brief can reason about trends and changes — not just the
          current snapshot. This moves the pipeline from a static summary tool to a
          genuine period-over-period analytical narrative, automated end to end.</p>
        </div>
      </div>
    </div>
  </div>

  <!-- ── B2B SaaS translation ────────────────────────────────────────────── -->
  <p class="section-label">Methodology Transfer — How This Applies to B2B SaaS</p>
  <div class="card">
    <p style="font-size:0.87rem;color:#4b5563;margin-bottom:20px;line-height:1.7">
      This analysis uses GA4 e-commerce events as the data source, but the SQL patterns,
      funnel logic, and retention methodology are directly transferable to any
      event-based product analytics dataset — including B2B SaaS products.
    </p>
    <table class="saas-table">
      <thead>
        <tr>
          <th>E-Commerce Event</th>
          <th>B2B SaaS Equivalent</th>
          <th>Metric</th>
          <th>This Analysis</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>session_start</td>
          <td>User login / app open</td>
          <td>DAU / session volume</td>
          <td class="rate">{total_sessions:,} sessions</td>
        </tr>
        <tr>
          <td>view_item</td>
          <td>Feature discovery — navigating to a core feature</td>
          <td>Feature reach rate</td>
          <td class="rate">{view_rate:.1f}% of sessions</td>
        </tr>
        <tr>
          <td>add_to_cart</td>
          <td>Feature first use — attempting the core action</td>
          <td>Activation attempt rate</td>
          <td class="rate">{shoppers_atc:.1f}% of sessions that saw products</td>
        </tr>
        <tr>
          <td>begin_checkout</td>
          <td>Core workflow initiated — e.g. creating first record</td>
          <td>Workflow start rate</td>
          <td class="rate">{funnel_df.loc[3,'step_cvr']:.1f}% step-over-step</td>
        </tr>
        <tr>
          <td>purchase</td>
          <td>Activation — completing the key value moment</td>
          <td>Activation rate</td>
          <td class="rate">{overall_cvr:.2f}% of all sessions</td>
        </tr>
        <tr>
          <td>Week-1 return</td>
          <td>Closest to D7 retention — but this is calendar-week return (1–13 days), not exactly 7 days</td>
          <td>Early retention</td>
          <td class="rate">{wk1_display} avg across cohorts</td>
        </tr>
      </tbody>
    </table>
  </div>

{appendix_brief_html}

  <!-- Footer -->
  <div class="footer">
    <strong>Methodology</strong><br>
    Session-scoped funnel: each row is a unique (user_pseudo_id, ga_session_id) pair extracted
    from event_params. Sequential constraint: a session is counted at step N only if it reached
    step N−1 first within the same session. This matches a closed funnel in the GA4 UI and avoids
    inflating conversion by attributing past-purchase behaviour to new sessions. The cost is
    that purchases whose cart was filled in an earlier session are not counted (see Data quality).<br><br>
    <strong>Data note</strong><br>
    {"Source: Google's public obfuscated GA4 e-commerce sample dataset. Absolute numbers are illustrative. Conversion rates, funnel patterns, and retention methodology are valid." if IS_PUBLIC_SAMPLE else f"Source: <code>{_html.escape(DATASET)}</code>, {DATE_START}–{DATE_END}."}
    The same SQL applies directly to any GA4 BigQuery export.<br><br>
    Python {sys.version.split()[0]} · Plotly {__import__("plotly").__version__}
  </div>

</div>

</body>
</html>"""

OUTPUT.write_text(html, encoding="utf-8")
size_kb = OUTPUT.stat().st_size / 1024
print(f"\nDone → {OUTPUT.name}  ({size_kb:.0f} KB)")
print("Open dashboard.html in any browser — no Python required.")
