#!/opt/miniconda3/bin/python
"""
generate_dashboard.py
---------------------
Runs all BigQuery analysis queries and produces a standalone dashboard.html
that anyone can open in a browser — no Python, no Jupyter required.

Usage:
    python generate_dashboard.py          # conda (base) — recommended
    /opt/miniconda3/bin/python generate_dashboard.py

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
from google.cloud import bigquery

# ── Config ─────────────────────────────────────────────────────────────────────
PROJECT_ID = "project-92e3923f-0e27-4c05-b11"
DATASET    = "bigquery-public-data.ga4_obfuscated_sample_ecommerce"
DATE_START = "20201101"
DATE_END   = "20210131"
OUTPUT     = Path("dashboard.html")

client = bigquery.Client(project=PROJECT_ID)
print(f"Connected  |  {DATE_START} → {DATE_END}")


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

step_labels = ["Session Start","View Item","Add to Cart","Begin Checkout","Purchase"]
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
  WHERE ts_s IS NOT NULL AND ts_v>=ts_s AND ts_c>=ts_v AND ts_k>=ts_c AND ts_p>=ts_k)
SELECT
  COUNT(*) AS purchasing_sessions,
  ROUND(SUM(revenue),2) AS total_revenue,
  ROUND(AVG(revenue),2) AS avg_order_value,
  ROUND(APPROX_QUANTILES(revenue,100)[OFFSET(50)],2) AS median_order_value
FROM conv WHERE revenue IS NOT NULL AND revenue > 0
""")

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
top_device = device_tbl.loc[device_tbl["purch_pct"].idxmax(), "device_category"]

device_rows_html = "\n".join(
    f'<tr><td class="dev-name">{r.device_category}</td>'
    f'<td>{int(r.sessions):,}</td>'
    f'<td>{r.share:.1f}%</td>'
    f'<td>{r.view_pct:.1f}%</td>'
    f'<td>{r.cart_pct:.1f}%</td>'
    f'<td class="dev-key">{r.purch_pct:.2f}%</td></tr>'
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
    COALESCE(NULLIF(TRIM(it.item_category), ''), '(not set)') AS raw_category
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
    MAX(IF(event_name='add_to_cart', event_timestamp, NULL)) AS ts_last_cart
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
)
SELECT agg.*, labels.n_source_labels, labels.source_labels
FROM agg JOIN labels USING (item_category)
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

# Benchmark = pooled rate across ranked categories (volume-weighted), so one
# tiny category with an extreme rate cannot move it.
category_benchmark = (category_tbl["carting_sessions"].sum()
                      / category_tbl["viewing_sessions"].sum() * 100) if len(category_tbl) else 0.0

# Carts at stake: extra carting sessions if a below-benchmark category merely
# converted at the benchmark. This is the prioritisation key — a mildly weak
# category with huge traffic outranks a terrible one nobody visits.
category_tbl["carts_at_stake"] = (
    (category_benchmark - category_tbl["atc_pct"]).clip(lower=0) / 100
    * category_tbl["viewing_sessions"]
).round().astype(int)
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

category_rows_html = "\n".join(
    f'<tr><td class="dev-name">{_html.escape(r.item_category)}{_merged_note(r)}</td>'
    f'<td>{int(r.viewing_sessions):,}</td>'
    f'<td>{int(r.carting_sessions):,}</td>'
    f'<td class="dev-key">{r.atc_pct:.1f}%</td>'
    f'<td>{int(r.carts_at_stake):,}</td></tr>'
    for r in category_tbl.sort_values("carts_at_stake", ascending=False).itertuples()
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
  WHERE ts_s IS NOT NULL AND ts_v>=ts_s AND ts_c>=ts_v AND ts_k>=ts_c AND ts_p>=ts_k)
SELECT
  ROUND(APPROX_QUANTILES((ts_v-ts_s)/3600000000.0,100)[OFFSET(50)],2) AS p50_s_to_v,
  ROUND(APPROX_QUANTILES((ts_c-ts_v)/3600000000.0,100)[OFFSET(50)],2) AS p50_v_to_c,
  ROUND(APPROX_QUANTILES((ts_k-ts_c)/3600000000.0,100)[OFFSET(50)],2) AS p50_c_to_k,
  ROUND(APPROX_QUANTILES((ts_p-ts_k)/3600000000.0,100)[OFFSET(50)],2) AS p50_k_to_p,
  ROUND(APPROX_QUANTILES((ts_p-ts_s)/3600000000.0,100)[OFFSET(50)],2) AS p50_total,
  COUNT(*) AS converting_sessions
FROM ff
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
overall_cvr     = float(funnel_df["overall_cvr"].iloc[4])
view_rate       = float(funnel_df["step_cvr"].iloc[1])   # session → view_item
atc_from_view    = float(funnel_df["step_cvr"].iloc[2])     # add_to_cart as % of PRODUCT VIEWERS
atc_all_sessions = float(funnel_df["overall_cvr"].iloc[2])  # add_to_cart as % of ALL SESSIONS
aov             = float(revenue_df["avg_order_value"].iloc[0])
median_order    = float(revenue_df["median_order_value"].iloc[0])
total_revenue   = float(revenue_df["total_revenue"].iloc[0])
total_ttc_hrs   = float(ttc_df["p50_total"].iloc[0])

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

# Opportunity sizing: what if we lift session → view_item by 5pp?
# Extra sessions that would now view a product if the rate goes from current to current+5pp
lift_5pp_views = int(total_sessions * 0.05)
# Of those extra view_item sessions, apply the observed view_item → purchase rate
# purchase rate = (purchases from view_item sessions) / (sessions that viewed item)
view_item_n    = int(funnel_raw["step2_view_item"].iloc[0])
purchase_n_raw = int(funnel_raw["step5_purchase"].iloc[0])
view_to_purchase_rate = purchase_n_raw / view_item_n  # e.g. 2721/75261 ≈ 3.6%
lift_5pp_purchases = round(lift_5pp_views * view_to_purchase_rate)
lift_5pp_revenue   = round(lift_5pp_purchases * aov, 0)
# Sensitivity: incremental traffic almost always converts below the existing
# average, so the headline figure is an optimistic upper bound. Model 50% / 75%.
lift_5pp_rev_50 = round(lift_5pp_revenue * 0.50, 0)
lift_5pp_rev_75 = round(lift_5pp_revenue * 0.75, 0)

# ── Executive summary (computed from real numbers) ────────────────────────────
bottleneck = "product discovery" if view_rate < 30 else "product-to-cart conversion"
discovery_severity = "critical" if view_rate < 20 else "significant"

exec_summary = (
    f"Of {total_sessions:,} sessions analysed across {date_label}, only "
    f"<strong>{overall_cvr:.1f}%</strong> completed a purchase. "
    f"The {discovery_severity} bottleneck is <strong>{bottleneck}</strong>: "
    f"just <strong>{view_rate:.0f}%</strong> of sessions ever reached a product page — "
    f"meaning the majority of traffic was lost before the buying experience even began. "
    f"Once users did view a product, checkout conversion was comparatively stronger, "
    f"suggesting the site's core problem is getting users to products, "
    f"not the purchase flow itself."
)

# ── Priority label ─────────────────────────────────────────────────────────────
if view_rate < 30:
    priority_label = "Top-of-Funnel: Product Discovery"
    priority_color = "#dc2626"
elif atc_from_view < 40:
    priority_label = "Mid-Funnel: Product Page Conversion"
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
ttc_steps = pd.DataFrame({
    "transition": ["Session → View Item","View Item → Add to Cart","Add to Cart → Checkout","Checkout → Purchase"],
    "median_hours": [float(ttc_df["p50_s_to_v"].iloc[0]),
                     float(ttc_df["p50_v_to_c"].iloc[0]),
                     float(ttc_df["p50_c_to_k"].iloc[0]),
                     float(ttc_df["p50_k_to_p"].iloc[0])],
})
fig_ttc = px.bar(ttc_steps, x="transition", y="median_hours",
                 color="median_hours", color_continuous_scale="Blues",
                 text=[f"{v:.2f}h" for v in ttc_steps["median_hours"]],
                 labels={"median_hours":"Median hours","transition":""})
fig_ttc.update_traces(
    texttemplate="%{text}",
    textposition="outside",
    hovertemplate="<b>%{x}</b><br>Median time: %{y:.2f} hours<extra></extra>",
    marker_color="#4C78A8",  # solid single colour — no redundant colour encoding
)
fig_ttc.update_layout(height=360, showlegend=False, coloraxis_showscale=False,
                      paper_bgcolor="white", plot_bgcolor="white",
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
    hovertemplate="<b>%{y}</b><br>Session → View Item: %{x:.1f}%"
                  "<br>Sessions: %{customdata[0]} (%{customdata[1]} of total)<extra></extra>",
))
fig_channel.add_vline(x=view_rate, line_dash="dash", line_color="#6b7280",
                      annotation_text=f"site avg {view_rate:.1f}%",
                      annotation_position="top")
fig_channel.update_layout(
    height=max(260, 46 * len(_ch_plot) + 80), showlegend=False,
    paper_bgcolor="white", plot_bgcolor="white",
    margin=dict(l=10, r=40, t=30, b=10),
    xaxis=dict(title="% of sessions reaching a product page",
               range=[0, max(float(_ch_plot["view_pct"].max()), view_rate) * 1.2]),
)

# Category chart (E2) — view → cart rate per category, worst at the top,
# benchmark as a reference line. Below-benchmark bars are highlighted.
fig_category = go.Figure(go.Bar(
    y=category_tbl["item_category"].tolist(),
    x=category_tbl["atc_pct"].round(1).tolist(),
    orientation="h",
    text=[f"{v:.1f}%" for v in category_tbl["atc_pct"]],
    texttemplate="%{text}", textposition="outside",
    marker_color=["#ea580c" if v < category_benchmark else "#4C78A8"
                  for v in category_tbl["atc_pct"]],
    customdata=[[f"{int(v):,}", f"{int(k):,}"] for v, k in
                zip(category_tbl["viewing_sessions"], category_tbl["carts_at_stake"])],
    hovertemplate="<b>%{y}</b><br>View → Cart: %{x:.1f}%"
                  "<br>Viewing sessions: %{customdata[0]}"
                  "<br>Carts at stake: %{customdata[1]}<extra></extra>",
))
fig_category.add_vline(x=category_benchmark, line_dash="dash", line_color="#6b7280",
                       annotation_text=f"benchmark {category_benchmark:.1f}%",
                       annotation_position="top")
fig_category.update_layout(
    height=max(300, 30 * len(category_tbl) + 80), showlegend=False,
    paper_bgcolor="white", plot_bgcolor="white",
    margin=dict(l=10, r=40, t=30, b=10),
    yaxis=dict(autorange="reversed"),   # lowest rate at the top
    xaxis=dict(title="% of viewing sessions that add that category to cart",
               range=[0, (float(category_tbl["atc_pct"].max()) if len(category_tbl) else 10) * 1.2]),
)


# ══════════════════════════════════════════════════════════════════════════════
# AI BRIEF
# ══════════════════════════════════════════════════════════════════════════════
brief_html = ""
brief_model = "AI"
brief_stale_note = ""
# Date the channel (E1) and category (E2) analyses were added. A brief generated
# before this cannot know their results and may recommend work they already did.
BRIEF_MIN_DATE = "2026-11-12"
brief_path = Path("product_brief_ga4_funnel.md")
if brief_path.exists():
    brief_md   = brief_path.read_text(encoding="utf-8")
    _gen  = re.search(r"\*Generated (\d{4}-\d{2}-\d{2})", brief_md)
    _mdl  = re.search(r"Model: ([^·*]+)", brief_md)
    brief_model = _html.escape(_mdl.group(1).strip()) if _mdl else "Claude"
    if _gen and _gen.group(1) < BRIEF_MIN_DATE:
        brief_stale_note = (
            f'<p class="caveat-note" style="margin-bottom:16px">This brief was generated on '
            f'{_gen.group(1)}, before the acquisition-channel and product-category analyses '
            f'were added, so it does not reflect them and may recommend analysis that is '
            f'already done above. Regenerate it with Section 10 of the notebook.</p>'
        )
    brief_body = "\n".join(brief_md.splitlines()[5:])
    def md_to_html(text):
        lines, out = text.splitlines(), []
        for line in lines:
            line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            line = re.sub(r"\*(.+?)\*",     r"<em>\1</em>", line)
            if line.startswith("## "):   out.append(f"<h3>{line[3:]}</h3>")
            elif line.startswith("# "): out.append(f"<h2>{line[2:]}</h2>")
            elif re.match(r"^\d+\.", line): out.append(f"<li>{line[line.index('.')+1:].strip()}</li>")
            elif line.startswith(("- ","* ")): out.append(f"<li>{line[2:].strip()}</li>")
            elif line.strip() == "---": out.append("<hr>")
            elif line.strip():          out.append(f"<p>{line}</p>")
        return "\n".join(out)
    brief_html = md_to_html(brief_body)
    print("AI product brief included.")
else:
    print("No product_brief_ga4_funnel.md — run Section 10 of the notebook first.")


# ══════════════════════════════════════════════════════════════════════════════
# ASSEMBLE HTML
# ══════════════════════════════════════════════════════════════════════════════
print("Assembling dashboard...")

wk1_display   = f"{avg_wk1}%" if avg_wk1 else "—"
wk1_warn      = avg_wk1 is not None and avg_wk1 < 10
retention_flag = "⚠️" if wk1_warn else ""

# ── Channel narrative (E1) — the conclusion flips on the 10pp threshold ───────
if channel_is_driver:
    channel_verdict = (
        f"<p><strong>Traffic mix is a real driver.</strong> The share of sessions "
        f"reaching a product page ranges from <strong>{worst_view_pct:.1f}%</strong> "
        f"({worst_view_channel}) to <strong>{best_view_pct:.1f}%</strong> "
        f"({best_view_channel}) — a <strong>{channel_view_spread:.1f}pp</strong> spread. "
        f"Part of the discovery loss is who is arriving, not only what they find.</p>"
        f"<p>Before redesigning navigation, check what the {worst_view_channel} "
        f"landing experience promises versus what the site shows on arrival.</p>"
    )
    channel_rec_problem = (
        f"Product-page reach varies by <strong>{channel_view_spread:.1f}pp</strong> across "
        f"channels — from {worst_view_pct:.1f}% ({worst_view_channel}) to "
        f"{best_view_pct:.1f}% ({best_view_channel}). Acquisition quality explains part of "
        f"the discovery gap, so budget and landing pages are levers alongside navigation."
    )
elif channel_borderline:
    channel_verdict = (
        f"<p><strong>Leaning site, not traffic — but borderline.</strong> The spread "
        f"between {worst_view_channel} ({worst_view_pct:.1f}%) and {best_view_channel} "
        f"({best_view_pct:.1f}%) is <strong>{channel_view_spread:.1f}pp</strong>: under "
        f"the 10pp threshold, but close to it. Every channel still loses the large "
        f"majority of its visitors before a product page, so navigation / discovery "
        f"remains the leading explanation rather than a settled one.</p>"
        f"<p>Cheap check before committing to a navigation redesign: review the "
        f"{worst_view_channel} landing pages against what its ads promise.</p>"
    )
else:
    channel_verdict = (
        f"<p><strong>The leak is on the site, not in the traffic.</strong> No "
        f"channel is an outlier on product-page reach: the spread between "
        f"{best_view_channel} ({best_view_pct:.1f}%) and {worst_view_channel} "
        f"({worst_view_pct:.1f}%) is <strong>{channel_view_spread:.1f}pp</strong>, "
        f"well under the 10pp threshold — and every channel loses the large majority "
        f"of its visitors before a product page.</p>"
        f"<p>This supports the navigation / discovery hypothesis: reallocating "
        f"acquisition budget would not fix a loss that happens equally to every "
        f"visitor once they land.</p>"
    )
if notset_view_pct is not None and notset_share >= 1:
    # Only claim "the average sits under every channel" when that is literally true.
    _below_all = (notset_view_pct < view_rate < float(_ch_rank["view_pct"].min()))
    channel_verdict += (
        f"<p><strong>Unattributed sessions.</strong> {notset_share:.1f}% of sessions carry "
        f"no session-level medium; they reach a product page at "
        f"<strong>{notset_view_pct:.1f}%</strong> and are excluded from the spread."
        + (f" That drags the site average ({view_rate:.1f}%) "
           f"below every named channel — this block is worth "
           f"investigating on its own." if _below_all else "")
        + "</p>"
    )
if not channel_is_driver:
    channel_rec_problem = (
        f"Product-page reach is within <strong>{channel_view_spread:.1f}pp</strong> across "
        f"channels{' (just under the 10pp threshold)' if channel_borderline else ''}, "
        f"so discovery is mainly not a traffic-quality problem. Channels still differ "
        f"in <em>value</em>, though — revenue per session is the remaining reason to segment."
    )
if top_rev_channel:
    channel_rec_problem += (
        f" Highest revenue per session: <strong>{top_rev_channel}</strong> "
        f"(${top_rev_rps:,.2f})."
    )
channel_basis_note = (
    "Channel = the session's own medium, taken from <code>event_params</code>."
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
_cat_revenue = round(_cat_orders * aov, 0)
if channel_is_driver:
    rec3_impact = (
        "Reallocating even 20% of acquisition budget from low- to high-converting "
        "channels could improve overall CVR without increasing total spend."
    )
elif category_top_targets:
    rec3_impact = (
        f"Lifting the top {len(category_top_targets)} categories to the "
        f"{category_benchmark:.1f}% benchmark adds ~<strong>{category_top3_at_stake:,}</strong> "
        f"carting sessions. At today's {_cart_to_purchase*100:.1f}% cart → purchase rate and "
        f"${aov:,.0f} AOV that is roughly <strong>~{_cat_orders:,} orders / "
        f"${_cat_revenue:,.0f}</strong> — an upper bound, since category carts and "
        f"session purchases are not one-to-one."
    )
else:
    rec3_impact = ("No category sits meaningfully below the benchmark, so the remaining "
                   "lever is weighting spend towards revenue per session.")

# ── Category narrative (E2) ───────────────────────────────────────────────────
if category_top_targets:
    _targets = ", ".join(f"<strong>{c}</strong>" for c in category_top_targets)
    category_verdict = (
        f"<p>Ranked by <strong>carts at stake</strong> — extra carting sessions if a "
        f"category merely converted at the {category_benchmark:.1f}% benchmark — the "
        f"audit should start with {_targets}. Together they account for "
        f"<strong>{category_top3_at_stake:,}</strong> of the "
        f"<strong>{category_total_at_stake:,}</strong> carts at stake across all "
        f"ranked categories.</p>"
        f"<p>Ranking by volume × gap, not by rate alone, stops a niche category "
        f"with a dramatic rate from jumping the queue ahead of a high-traffic "
        f"category with a moderate gap.</p>"
    )
else:
    category_verdict = (
        "<p>No ranked category sits meaningfully below the benchmark — the "
        "view → cart drop is spread evenly across the catalogue rather than "
        "concentrated in specific product pages.</p>"
    )

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
  .brief-body li {{ font-size: 0.87rem; color: #374151; margin: 5px 0 5px 20px;
                   line-height: 1.65; }}
  .brief-body hr {{ border: none; border-top: 1px solid #e5e7eb; margin: 16px 0; }}

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
        Google's public obfuscated GA4 export on BigQuery<br>
        (<code>bigquery-public-data.ga4_obfuscated_sample_ecommerce</code>)<br>
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
      <div class="kpi-value">${aov:,.0f}</div>
      <div class="kpi-sub">median ${median_order:,.0f} · {purchase_n:,} orders</div>
    </div>
    <div class="kpi {'warn' if wk1_warn else ''}">
      <div class="kpi-label">Week-1 Retention {retention_flag}</div>
      <div class="kpi-value">{wk1_display}</div>
      <div class="kpi-sub">{"⚠ below 10% benchmark" if wk1_warn else "users returning after first session"}</div>
    </div>
  </div>

  <!-- ── Key Findings ───────────────────────────────────────────────────── -->
  <p class="section-label">Key Findings</p>
  <div class="findings-grid">

    <div class="finding">
      <div class="finding-num">Finding 01 · Biggest drop</div>
      <div class="finding-stat red">{view_rate:.0f}%</div>
      <div class="finding-title">of sessions ever view a product</div>
      <div class="finding-body">
        <strong>{100-view_rate:.0f}%</strong> of sessions — that's
        <strong>{total_sessions - view_item_n:,} visits</strong> — leave
        without ever reaching a product page. This is the single largest loss
        in the funnel and the highest-leverage opportunity. In a SaaS context,
        this maps to users who log in but never navigate to a core feature.
      </div>
    </div>

    <div class="finding">
      <div class="finding-num">Finding 02 · Once engaged, buyers are decisive</div>
      <div class="finding-stat blue">{shoppers_atc:.0f}%</div>
      <div class="finding-title">of product viewers add to cart</div>
      <div class="finding-body">
        When users do reach a product page, <strong>{shoppers_atc:.0f}%</strong>
        add to cart — but measured across <em>all</em> sessions the add-to-cart
        rate is only <strong>{atc_all_sessions:.1f}%</strong>. The same behaviour
        looks {shoppers_atc/atc_all_sessions:.0f}× weaker once non-viewers are
        included, and that entire difference is the discovery gap — not a product
        page problem.
      </div>
    </div>

    <div class="finding">
      <div class="finding-num">Finding 03 · Conversion is fast</div>
      <div class="finding-stat green">{total_ttc_hrs:.1f}h</div>
      <div class="finding-title">median session-to-purchase time</div>
      <div class="finding-body">
        Purchasing sessions complete the full journey in
        <strong>{total_ttc_hrs:.1f} hours</strong> at the median.
        This indicates low deliberation friction — users who intend to buy,
        buy quickly. No urgent need to invest in cart-abandonment recovery flows.
      </div>
    </div>

    <div class="finding">
      <div class="finding-num">Finding 04 · Retention is the second problem</div>
      <div class="finding-stat {'amber' if wk1_warn else 'blue'}">{wk1_display}</div>
      <div class="finding-title">Week-1 return rate</div>
      <div class="finding-body">
        Only <strong>{wk1_display}</strong> of users return in the week after
        their first session — well below the ~20% benchmark for healthy
        e-commerce cohorts. Acquisition campaigns may be driving
        low-intent traffic, or there is no post-visit re-engagement in place.
      </div>
    </div>


    <div class="finding">
      <div class="finding-num">Finding 05 · Revenue concentration</div>
      <div class="finding-stat green">${aov:,.0f}</div>
      <div class="finding-title">avg order value (${median_order:,.0f} median)</div>
      <div class="finding-body">
        The gap between mean (${aov:,.0f}) and median (${median_order:,.0f})
        suggests some high-value orders are pulling the average up. Total
        revenue in the window: <strong>${total_revenue:,.0f}</strong> from
        <strong>{purchase_n:,} purchasing sessions</strong>.
        Revenue per session by channel is broken out in the Acquisition
        Channel section below.
      </div>
    </div>

    <div class="finding">
      <div class="finding-num">Finding 06 · Device is not the lever</div>
      <div class="finding-stat blue">{device_gap:.2f}pp</div>
      <div class="finding-title">spread in purchase rate across all devices</div>
      <div class="finding-body">
        Purchase conversion differs by only <strong>{device_gap:.2f} percentage
        points</strong> between desktop, mobile and tablet. A mobile-first
        redesign — the intuitive response to a {overall_cvr:.1f}% conversion rate — would not
        have moved the number. Effort belongs in discovery and lifecycle, not
        device-specific checkout work.
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
          <p>Each session is counted at a step only if it completed all prior steps
          <em>in order within the same session</em> — matching how GA4 UI
          calculates funnel conversion.</p>
          <p>The <strong>largest absolute loss</strong> is at
          <strong>{biggest_drop_step}</strong>:
          {biggest_drop_n:,} sessions dropped here.</p>
          <p>The step-over-step rates after View Item are comparatively
          healthy — the problem is upstream, not in the checkout flow.</p>
        </div>
        <div class="opportunity">
          <h4>Revenue opportunity</h4>
          <p>If session → view_item improves by just <strong>5 percentage points</strong> ({view_rate:.0f}% → {view_rate+5:.0f}%):</p>
          <span class="big-number">~{lift_5pp_purchases:,} extra orders</span>
          <p>~<strong>${lift_5pp_revenue:,.0f}</strong> additional revenue
          at current AOV of ${aov:,.0f}.</p>
          <p style="font-size:0.78rem;opacity:0.85;margin-top:8px">
          <strong>Sensitivity.</strong> This assumes incremental sessions convert at
          the same {view_to_purchase_rate*100:.1f}% view→purchase rate as today's
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
        <p>Converting sessions complete the journey in
        <strong>{total_ttc_hrs:.1f} hours</strong> (median).
        The checkout → purchase step is near-instant, indicating
        no meaningful payment friction for users who reach this stage.</p>
        <p>Cart abandonment flows and checkout UX are lower priority
        than driving more users to product pages in the first place.</p>
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
              <th>View Item</th><th>Add to Cart</th><th>Purchase</th>
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
        mobile and tablet. The funnel leaks at the same rate regardless of how
        users reach the site.</p>
        <p><strong>This is reported precisely because it is a null result.</strong>
        The intuitive next step after seeing a {overall_cvr:.1f}% conversion rate is a
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
          <th>Channel</th><th>Sessions</th><th>Share</th><th>→ View Item</th>
          <th>View → Cart</th><th>Purchase</th><th>Rev / Session</th>
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
        <h4>Where to start the product-page audit</h4>
        {category_verdict}
        <p class="caveat-note">A session counts for a category only if it added an
        item <em>of that category</em> after viewing it, so rates are stricter than
        the {shoppers_atc:.0f}% shoppers figure and rows are not additive.
        Categories with fewer than {MIN_CATEGORY_VIEWS:,} viewing sessions
        ({category_excluded_n} excluded) and <code>(not set)</code>
        ({category_notset_share:.1f}% of category views) are not ranked.
        Labels are normalised to their last path segment
        (<code>Lifestyle/Bags/</code> → <code>Bags</code>): {category_raw_labels} raw
        labels collapse into {len(category_raw)} categories, {category_merged_groups} of
        them merged from more than one label.</p>
      </div>
    </div>
    <table class="dev-table" style="margin-top:20px">
      <thead>
        <tr>
          <th>Category</th><th>Viewing sessions</th><th>Carting sessions</th>
          <th>View → Cart</th><th>Carts at stake</th>
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
        in that week. The cells show what % of them returned in each
        subsequent week.</p>
        <p>Avg Week-1 return: <strong>{wk1_display}</strong><br>
        Avg Week-2 return: <strong>{f"{avg_wk2}%" if avg_wk2 else "—"}</strong></p>
        <h4 style="margin-top:16px">What this means</h4>
        <p>Week-1 retention of <strong>{wk1_display}</strong> is
        {"significantly below the ~20% benchmark" if wk1_warn else "approaching benchmark levels"}.
        Most users who visit once do not come back.</p>
        <p><strong>Possible causes</strong> (this dataset cannot tell them
        apart): no triggered email or push re-engagement, traffic from
        low-intent channels, or a first session that didn't reach a product.</p>
        <p><strong>In SaaS terms:</strong> this is equivalent to D7 retention —
        the % of activated users who return within 7 days. The intervention
        is lifecycle emails or in-app nudges surfaced at Day 2–3.</p>
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

  <!-- ── Recommendations ────────────────────────────────────────────────── -->
  <p class="section-label">Recommendations — Prioritised by Impact</p>
  <div class="rec-grid">

    <div class="rec">
      <span class="rec-priority p1">Priority 1 · Immediate</span>
      <h4>Fix product discovery</h4>

      <div class="rec-section">
        <div class="rec-section-label">Problem</div>
        <p><strong>{100-view_rate:.0f}% of sessions never reach a product page</strong> —
        that's <strong>{total_sessions - view_item_n:,} visits</strong> lost before the
        buying experience even begins. This is the single largest drop in the funnel and
        the highest-leverage opportunity. In a SaaS context, this maps to users who log in
        but never navigate to a core feature.</p>
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
        <p>A <strong>5pp improvement</strong> in session → view_item rate would drive
        approximately <strong>~{lift_5pp_purchases:,} additional orders</strong> and
        ~<strong>${lift_5pp_revenue:,.0f}</strong> in additional revenue at current AOV.</p>
        <div class="metric">session → view_item rate (target: &gt;30%)</div>
      </div>
    </div>

    <div class="rec">
      <span class="rec-priority p2">Priority 2 · Short-term</span>
      <h4>Build a re-engagement lifecycle</h4>

      <div class="rec-section">
        <div class="rec-section-label">Problem</div>
        <p>Week-1 retention of <strong>{wk1_display}</strong> means the vast majority of
        acquired users visit once and never return — well below the ~20% benchmark for
        healthy e-commerce cohorts. This dataset cannot show whether any re-engagement
        (email, push, remarketing) was running; if none was, it is the first thing to test.</p>
      </div>

      <div class="rec-section">
        <div class="rec-section-label">Recommended Actions</div>
        <p>Launch a triggered email or push sequence:
        <strong>Day 1</strong> (product browsed but not purchased),
        <strong>Day 3</strong> (cart abandonment reminder),
        <strong>Day 7</strong> (win-back with incentive).
        Measure Week-1 and Week-2 return rates per cohort separately to isolate the effect.</p>
      </div>

      <div class="rec-section rec-impact">
        <div class="rec-section-label">Business Impact</div>
        <p>Lifting Week-1 retention from today's <strong>{wk1_display}</strong> average to
        <strong>{WK1_TARGET:.0f}%</strong> would have brought back
        ~<strong>{wk1_extra_users:,}</strong> more users in their second week, across the
        {wk1_n_cohorts} full cohorts ({wk1_base_users:,} first-time users) observed in this
        window — repeat sessions that need no extra acquisition spend.</p>
        <div class="metric">Week-1 cohort retention rate (target: &gt;{WK1_TARGET:.0f}%)</div>
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
        <p>Start the product-page audit with
        {", ".join(category_top_targets) if category_top_targets else "the lowest view → cart categories"}
        (see Product Category above): review content, pricing and trust signals on
        their highest-traffic pages. On the channel side, weight acquisition spend
        towards revenue per session, not session volume.</p>
      </div>

      <div class="rec-section rec-impact">
        <div class="rec-section-label">Business Impact</div>
        <p>{rec3_impact}</p>
        <div class="metric">revenue per session by channel · view → cart in target categories</div>
      </div>
    </div>

  </div>

  {f'''
  <!-- AI Product Brief -->
  <p class="section-label">AI-Generated Product Brief
    <span class="ai-badge">{brief_model}</span></p>
  <div class="card">
    {brief_stale_note}
    <div class="brief-body">{brief_html}</div>
  </div>
  ''' if brief_html else ""}

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
            Change <code>DATE_START</code>, <code>DATE_END</code> and <code>DATASET</code>
            at the top of the script and run
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
          <td class="rate">{shoppers_atc:.1f}% of product viewers</td>
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
          <td>Week-1 retention</td>
          <td>D7 retention — returning within 7 days of first login</td>
          <td>Early retention</td>
          <td class="rate">{wk1_display} avg across cohorts</td>
        </tr>
      </tbody>
    </table>
  </div>

  <!-- Footer -->
  <div class="footer">
    <strong>Methodology</strong><br>
    Session-scoped funnel: each row is a unique (user_pseudo_id, ga_session_id) pair extracted
    from event_params. Sequential constraint: a session is counted at step N only if it reached
    step N−1 first within the same session. This matches GA4 UI funnel behaviour and avoids
    inflating conversion by attributing past-purchase behaviour to new sessions.<br><br>
    <strong>Data note</strong><br>
    Source: Google's public obfuscated GA4 e-commerce sample dataset.
    Absolute numbers are illustrative. Conversion rates, funnel patterns, and retention
    methodology are valid. The same SQL applies directly to any GA4 BigQuery export.<br><br>
    Python {sys.version.split()[0]} · Plotly {__import__("plotly").__version__}
  </div>

</div>

<script>
(function() {{
  function patchCharts() {{

    // Cohort heatmap needs no patch — colourscale, zmin/zmax, gap handling and
    // hover template are all set in Python at render time.

    // TTC chart — Plotly's float64 bdata encoding corrupts near-zero values,
    // so restyle with the authoritative medians straight from the query.
    var ttcDiv = document.getElementById('chart-ttc');
    if (ttcDiv && ttcDiv.data && ttcDiv.data.length) {{
      var p50sv = {float(ttc_df["p50_s_to_v"].iloc[0])};
      var p50vc = {float(ttc_df["p50_v_to_c"].iloc[0])};
      var p50ck = {float(ttc_df["p50_c_to_k"].iloc[0])};
      var p50kp = {float(ttc_df["p50_k_to_p"].iloc[0])};
      // Clamp near-zero values so bars remain visible
      var yVals = [p50sv, Math.max(p50vc, 0.001), p50ck, p50kp];
      var labels = [p50sv+'h', p50vc < 0.001 ? '< 1 sec' : p50vc+'h', p50ck+'h', p50kp+'h'];
      Plotly.restyle('chart-ttc', {{
        x: [['Session → View Item','View Item → Add to Cart','Add to Cart → Checkout','Checkout → Purchase']],
        y: [yVals],
        text: [labels],
        texttemplate: ['%{{text}}'],
        'marker.color': ['#4C78A8'],
        hovertemplate: ['<b>%{{x}}</b><br>Median time: %{{customdata}}<extra></extra>'],
        customdata: [[
          '~' + Math.round(p50sv*60) + ' min',
          p50vc < 0.001 ? '< 1 sec (decisive shoppers add to cart immediately)' : '~' + Math.round(p50vc*60) + ' min',
          '~' + Math.round(p50ck*60) + ' min',
          '~' + Math.round(p50kp*60) + ' min'
        ]]
      }}, [0]);
      Plotly.relayout('chart-ttc', {{
        'xaxis.tickangle': -15,
        'yaxis.title.text': 'Median hours',
        coloraxis: null
      }});
    }}
  }}

  if (document.readyState === 'complete') {{ patchCharts(); }}
  else {{ window.addEventListener('load', patchCharts); }}
}})();
</script>

</body>
</html>"""

OUTPUT.write_text(html, encoding="utf-8")
size_kb = OUTPUT.stat().st_size / 1024
print(f"\nDone → {OUTPUT}  ({size_kb:.0f} KB)")
print("Open dashboard.html in any browser — no Python required.")
