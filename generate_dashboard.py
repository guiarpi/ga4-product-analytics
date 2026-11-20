#!/usr/bin/env python3
"""
generate_dashboard.py
---------------------
Runs the analysis queries (queries.py), computes the metrics, builds the charts
(charts.py) and renders templates/dashboard.html.j2 into a standalone
dashboard.html that anyone can open in a browser — no Python, no Jupyter.

The page's HTML, CSS and narrative text live in templates/; this script only
supplies values. Numbers in prose are computed here, never written by hand.

Usage:
    python generate_dashboard.py              # run the queries, write the page
    python generate_dashboard.py --dry-run    # estimate bytes scanned, run nothing

Project, dataset and date window come from config.py and can be overridden
with GA4_PROJECT_ID, GA4_DATASET, GA4_DATE_START and GA4_DATE_END — see the
README. Defaults run against Google's public GA4 sample. Every query runs under
a bytes-billed cap (GA4_MAX_BYTES_BILLED, default 5GB per query).

Output: dashboard.html
"""

import warnings
warnings.filterwarnings("ignore")

import argparse
import re
import sys
from pathlib import Path

import datetime as _dt

import jinja2
import jinja2.meta
import pandas as pd
import plotly
from plotly.offline import get_plotlyjs

# ── Config ─────────────────────────────────────────────────────────────────────
# All run settings live in config.py (environment-variable overrides); nothing
# about the project, dataset or window is hardcoded in this script.
from config import (DATASET, DATE_START, DATE_END, IS_PUBLIC_SAMPLE, MAX_BYTES_BILLED,
                    make_client, describe, run_query, estimate_bytes, format_bytes)
from stats_utils import wilson_ci, chi_square, format_p
from metrics import (site_average, flag_categories, listing_levels,
                     weak_landing_pages, mix_decomposition)
from md_render import md_to_html
import charts
import queries


WINDOW = (DATASET, DATE_START, DATE_END)   # every query takes these three
HERE = Path(__file__).resolve().parent     # paths work from any working directory
OUTPUT = HERE / "dashboard.html"

# Every query the page runs, in run order. q() refuses anything not listed, so
# the --dry-run estimate always covers exactly what a real run executes.
DASHBOARD_QUERIES = (
    queries.session_funnel_sql, queries.unique_users_sql, queries.revenue_sql,
    queries.session_quality_sql, queries.event_quality_sql, queries.device_funnel_sql,
    queries.channel_funnel_sql, queries.shoppers_funnel_sql, queries.category_sql,
    queries.category_overlap_sql, queries.price_band_sql, queries.landing_pages_sql,
    queries.time_to_convert_sql, queries.cohort_sql, queries.cohort_channel_sql,
)

args = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip(),
                               formatter_class=argparse.RawDescriptionHelpFormatter)
args.add_argument("--dry-run", action="store_true",
                  help="estimate the bytes each query would scan (free), then exit")
args = args.parse_args()

client = make_client()
# Console only (never saved), so the project is shown here and nowhere else.
print(f"Connected  |  {describe(client, include_project=True)}")
print(f"Cost cap   |  {format_bytes(MAX_BYTES_BILLED)} billed per query (GA4_MAX_BYTES_BILLED)")
if not IS_PUBLIC_SAMPLE:
    print("  NOTE: the 'About This Dataset' card and some recommendation text describe "
          "the public sample — review them before sharing this dashboard.")

if args.dry_run:
    total, over = 0, []
    print("\nDry run — nothing is executed or billed:")
    for build in DASHBOARD_QUERIES:
        n = estimate_bytes(client, build(*WINDOW))
        total += n
        flag = "  ← over the cap" if n > MAX_BYTES_BILLED else ""
        if flag:
            over.append(build.__name__)
        print(f"  {build.__name__:<22} {format_bytes(n):>10}{flag}")
    print(f"  {'total':<22} {format_bytes(total):>10}")
    if over:
        print(f"\n{len(over)} quer{'y' if len(over) == 1 else 'ies'} would be refused — "
              "raise GA4_MAX_BYTES_BILLED or narrow the window.")
    sys.exit(1 if over else 0)


def q(build) -> pd.DataFrame:
    """Run one of DASHBOARD_QUERIES for the configured window, under the cost cap."""
    if build not in DASHBOARD_QUERIES:
        raise ValueError(f"{build.__name__} is not in DASHBOARD_QUERIES — add it there "
                         "so --dry-run estimates it too")
    return run_query(client, build(*WINDOW))

# ══════════════════════════════════════════════════════════════════════════════
# QUERIES
# ══════════════════════════════════════════════════════════════════════════════

# ── 1. Session funnel ─────────────────────────────────────────────────────────
print("Querying session funnel...")
funnel_raw = q(queries.session_funnel_sql)

# "View Products", not "View Item": in this export view_item fires on listing and
# home pages with ~12 items per event (see the data-quality checks), so the step
# means "saw products", not "opened a product page".
step_labels = ["Session Start","View Products","Add to Cart","Begin Checkout","Purchase"]
step_cols   = ["sessions","view_item","add_to_cart","begin_checkout","purchase"]   # queries.STEP_COLUMNS
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
unique_users_df = q(queries.unique_users_sql)
unique_users = int(unique_users_df["unique_users"].iloc[0])

# ── 2. Revenue ────────────────────────────────────────────────────────────────
print("Querying revenue...")
revenue_df = q(queries.revenue_sql)

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
dq_raw = q(queries.session_quality_sql)

# ── 2c. Event-quality checks: what view_item and add_to_cart actually record ──
# view_item and add_to_cart carry an items array. If it holds a whole product
# list rather than one product, the event is a listing impression / quick-add,
# not a product-page view — which changes how "product page" claims can be made.
# Only items with quantity > 0 on add_to_cart are the item actually carted.
print("Querying event-quality checks...")
evq_raw = q(queries.event_quality_sql).set_index("event_name")

# ── 3. Device breakdown ───────────────────────────────────────────────────────
# Rendered as a summary table rather than a grouped bar chart: with all three
# devices tracking within ~0.1pp of each other, a chart encodes three nearly
# identical lines and invites the reader to hunt for a difference that isn't
# there. The table states the finding directly — device is not the lever.
print("Querying device breakdown...")
device_raw = q(queries.device_funnel_sql)

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

device_rows = device_tbl.to_dict("records")

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
channel_raw = q(queries.channel_funnel_sql)

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
if _ch_total != int(funnel_raw["sessions"].iloc[0]):
    print(f"  WARNING: channel sessions ({_ch_total:,}) != funnel sessions "
          f"({int(funnel_raw['sessions'].iloc[0]):,}) — check the session_attrs join")

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
best_view_channel,  best_view_pct  = _best_ch["channel"],  float(_best_ch["view_pct"])
worst_view_channel, worst_view_pct = _worst_ch["channel"], float(_worst_ch["view_pct"])
_ch_rps = _ch_rank[~_ch_rank["low_n"]]
top_rev_channel = (_ch_rps.loc[_ch_rps["rev_per_session"].idxmax(), "channel"]
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

channel_rows = [dict(r, cart_from_view=None if pd.isna(r["cart_from_view"]) else r["cart_from_view"])
                for r in channel_tbl.to_dict("records")]

# ── 4. Shoppers funnel (entry at view_item) ───────────────────────────────────
print("Querying shoppers funnel...")
shoppers_raw = q(queries.shoppers_funnel_sql)

# ── 4b. Listing pages: seen → carted (E2) ────────────────────────────────────
# Question: the second-largest leak is view_item → add_to_cart. Where in the
# catalogue is it concentrated? This turns "audit the product pages" into a
# ranked list of where to start.
#
# item_category is the LISTING a product was shown in, not a product attribute:
# almost every product appears under several (a tee under Apparel, Google, Sale).
# Rows are therefore listing pages, split by level from their raw paths. Only
# product-type pages are ranked against each other; brand, collection and parent
# pages are shown separately, because they mix product types.
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
category_raw = q(queries.category_sql)

_cat_all_views   = int(category_raw["viewing_sessions"].sum())
category_raw_labels    = int(category_raw["n_source_labels"].sum())
category_merged_groups = int((category_raw["n_source_labels"] > 1).sum())
category_notset_share = (
    category_raw.loc[category_raw["item_category"] == "(not set)", "viewing_sessions"].sum()
    / _cat_all_views * 100 if _cat_all_views else 0.0
)
category_raw["level"] = listing_levels(category_raw["item_category"], category_raw["parent_node"])
_listed = category_raw[
    (category_raw["item_category"] != "(not set)")
    & (category_raw["viewing_sessions"] >= MIN_CATEGORY_VIEWS)
].copy()
category_excluded_n = int(
    (category_raw["item_category"] != "(not set)").sum() - len(_listed)
)
_listed["atc_pct"] = _listed["carting_sessions"] / _listed["viewing_sessions"] * 100
category_tbl = _listed[_listed["level"] == "product type"].copy()

# Brand, collection and parent pages: rate and interval only, not ranked.
listing_other = _listed[_listed["level"] != "product type"].copy()
listing_other["ci"] = [wilson_ci(k, n) for k, n in zip(listing_other["carting_sessions"], listing_other["viewing_sessions"])]
_level_order = {"brand": 0, "collection": 1, "parent": 2}
listing_other = listing_other.sort_values(
    ["level", "viewing_sessions"], key=lambda c: c.map(_level_order) if c.name == "level" else -c)
listing_other_rows = listing_other.to_dict("records")
listing_level_counts = {lv: int((_listed["level"] == lv).sum()) for lv in ("product type", "brand", "collection", "parent")}

# Average = pooled rate across ranked product-type pages (metrics.site_average).
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


category_tbl = category_tbl.sort_values("atc_pct").reset_index(drop=True)

_cat_priority = category_tbl.sort_values("carts_at_stake", ascending=False)
_cat_priority = _cat_priority[_cat_priority["carts_at_stake"] > 0].head(3)
category_top_targets = list(_cat_priority["item_category"])
category_total_at_stake = int(category_tbl["carts_at_stake"].sum())
category_top3_at_stake  = int(_cat_priority["carts_at_stake"].sum())

# Table order: carts at stake first, then the weakest rate.
category_rows = category_tbl.sort_values(["carts_at_stake", "atc_pct"], ascending=[False, True]).to_dict("records")
n_categories = len(category_tbl)
cat_rate_min = float(category_tbl["atc_pct"].min())
cat_rate_max = float(category_tbl["atc_pct"].max())
# Price of the flagged pages' products against the other product types — the
# confounder named in Recommendation 3.
_flagged = category_tbl[category_tbl["status"] == "below"]
flagged_median_price = float(_flagged["median_price"].median()) if len(_flagged) else None
other_median_price   = float(category_tbl.loc[category_tbl["status"] != "below", "median_price"].median()) \
                       if (category_tbl["status"] != "below").any() else None

# The evidence that rows are listing contexts: products seen under more than one.
category_overlap = q(queries.category_overlap_sql)
overlap_items   = len(category_overlap)
overlap_multi   = int((category_overlap["n_categories"] > 1).sum())
_ex = category_overlap[category_overlap["n_categories"] > 1].head(1)
overlap_example = ({"item": _ex["item_name"].iloc[0], "n": int(_ex["n_categories"].iloc[0]),
                    "categories": _ex["categories"].iloc[0].split(" · ")} if len(_ex) else None)

# ── 4c. Price bands: seen → carted by the item's own price ────────────────────
# Price IS a product attribute, so this split has none of the listing caveat.
print("Querying price bands...")
price_band_raw = q(queries.price_band_sql)
_band_order = [label for _, label in queries.PRICE_BANDS]
price_bands = (price_band_raw.set_index("price_band").reindex(_band_order).dropna(subset=["viewing_sessions"])
               .reset_index())
price_bands["atc_pct"] = price_bands["carting_sessions"] / price_bands["viewing_sessions"] * 100
price_bands["ci"] = [wilson_ci(int(k), int(n)) for k, n in zip(price_bands["carting_sessions"], price_bands["viewing_sessions"])]
price_band_test = chi_square(price_bands["carting_sessions"], price_bands["viewing_sessions"])
_pb_best, _pb_worst = price_bands.loc[price_bands["atc_pct"].idxmax()], price_bands.loc[price_bands["atc_pct"].idxmin()]
price_best_band,  price_best_pct  = _pb_best["price_band"],  float(_pb_best["atc_pct"])
price_worst_band, price_worst_pct = _pb_worst["price_band"], float(_pb_worst["atc_pct"])
price_band_rows = price_bands.to_dict("records")

# ── 4d. Landing pages ─────────────────────────────────────────────────────────
# Where do the sessions that never see a product start? Pages with at least 1%
# of sessions as landings and a saw-a-product rate 5pp or more under the site's
# are the concrete targets for Recommendation 1.
print("Querying landing pages...")
landing_raw = q(queries.landing_pages_sql)

# ── 5. Time-to-convert ────────────────────────────────────────────────────────
print("Querying time-to-convert...")
ttc_df = q(queries.time_to_convert_sql)

# ── 6. Cohort retention ───────────────────────────────────────────────────────
print("Querying cohort retention...")
cohort_df = q(queries.cohort_sql)
cohort_pivot = cohort_df.pivot(index="cohort_week", columns="weeks_since_cohort",
                               values="active_users")

# ── Censoring guard ───────────────────────────────────────────────────────────
# A recent cohort has not yet had the chance to return in later weeks. Filling
# those cells with 0 would report "hasn't happened yet" as "0% retention" — a
# fabricated finding. Only weeks fully covered by the observation window are
# kept; everything beyond it stays NaN and renders blank.
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
# Median minutes per step (session→view, view→cart, cart→checkout, checkout→purchase).
ttc_minutes     = [float(ttc_df[c].iloc[0]) for c in ("p50_s_to_v", "p50_v_to_c", "p50_c_to_k", "p50_k_to_p")]

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
dq_buckets = dq_buckets.to_dict("records")   # rows for the template
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
vi_no_item_pct = vi_q["no_item_events"] / vi_q["events"] * 100
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
    int(shoppers_raw["add_to_cart"].iloc[0]) /
    int(shoppers_raw["view_item"].iloc[0]) * 100, 1
)

# Biggest absolute drop step
funnel_df["next_sessions"] = funnel_df["sessions"].shift(-1)
funnel_df["abs_drop_n"]    = (funnel_df["sessions"] - funnel_df["next_sessions"]).fillna(0).astype(int)
biggest_drop_idx  = funnel_df["abs_drop_n"].idxmax()
biggest_drop_step = funnel_df.loc[biggest_drop_idx, "step"]
biggest_drop_n    = int(funnel_df.loc[biggest_drop_idx, "abs_drop_n"])
# Every step's loss, largest first: the funnel section states the order rather
# than asserting which leak is "second".
step_losses = sorted(
    ({"from": funnel_df.loc[i, "step"], "to": funnel_df.loc[i + 1, "step"],
      "lost": int(funnel_df.loc[i, "abs_drop_n"]), "drop_pct": float(funnel_df.loc[i + 1, "drop_off"])}
     for i in range(len(funnel_df) - 1)),
    key=lambda r: -r["lost"])

window_start = _start   # the partial-cohort note formats it

# Cohort retention — full cohorts only, censored cells excluded automatically by .mean()
avg_wk1 = round(float(cohort_pct_full["Week 1"].mean()), 1) if "Week 1" in cohort_pct_full.columns else None
avg_wk2 = round(float(cohort_pct_full["Week 2"].mean()), 1) if "Week 2" in cohort_pct_full.columns else None

# ── Retention trend ───────────────────────────────────────────────────────────
# The average hides a trend: Week-1 return by calendar month of the cohort week
# (full cohorts with an observed Week 1 only). Is a fall a change in channel mix
# (more low-intent traffic) or a fall within every channel? mix_decomposition
# answers it from the users' first-session channel.
_wk1_series = cohort_pct_full["Week 1"].dropna() if "Week 1" in cohort_pct_full.columns else pd.Series(dtype=float)
_wk1_month = pd.to_datetime(_wk1_series.index).to_period("M")
wk1_by_month = [
    {"month": m.strftime("%b"), "lo": float(g.min()), "hi": float(g.max()), "n": len(g)}
    for m, g in _wk1_series.groupby(_wk1_month)
]
has_retention_trend = len(wk1_by_month) >= 2
if has_retention_trend:
    _mean = _wk1_series.groupby(_wk1_month).mean()
    _peak_m, _trough_m = _mean.idxmax(), _mean.idxmin()
    trend_peak   = next(r for r in wk1_by_month if r["month"] == _peak_m.strftime("%b"))
    trend_trough = next(r for r in wk1_by_month if r["month"] == _trough_m.strftime("%b"))
    # Same cohorts as the averages: full, with Week 1 inside the window.
    cohort_ch = q(queries.cohort_channel_sql)
    cohort_ch["cohort_week"] = cohort_ch["cohort_week"].astype(str)
    cohort_ch = cohort_ch[cohort_ch["cohort_week"].isin(_wk1_series.index)]
    cohort_ch["month"] = pd.to_datetime(cohort_ch["cohort_week"]).dt.to_period("M")
    _by = lambda m: (cohort_ch[cohort_ch["month"] == m].groupby("medium")[["users", "wk1_users"]].sum())
    _a, _b = _by(_peak_m), _by(_trough_m)
    retention_mix = mix_decomposition(_a, _b, n="users", k="wk1_users")
    # Channels with at least 1% of the peak month's users: did each one fall?
    _big = _a[_a["users"] / _a["users"].sum() >= 0.01].index
    _ra = _a.loc[_big, "wk1_users"] / _a.loc[_big, "users"] * 100
    _rb = (_b.reindex(_big)["wk1_users"] / _b.reindex(_big)["users"] * 100)
    retention_channels_n    = len(_big)
    retention_channels_fell = int((_rb < _ra).sum())
    retention_mix_share     = (retention_mix["mix_pp"] / retention_mix["change_pp"] * 100
                               if retention_mix["change_pp"] else 0.0)
    _top = _a["users"].sort_values(ascending=False).index[:3]
    retention_channel_examples = [
        {"channel": ch, "a": float(_ra[ch]), "b": float(_rb[ch])} for ch in _top if ch in _ra.index and pd.notna(_rb[ch])]
else:
    cohort_ch = None
    trend_peak = trend_trough = retention_mix = None
    retention_channels_n = retention_channels_fell = 0
    retention_mix_share = 0.0
    retention_channel_examples = []

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
view_item_n    = int(funnel_raw["view_item"].iloc[0])
purchase_n_raw = int(funnel_raw["purchase"].iloc[0])
view_to_purchase_rate = purchase_n_raw / view_item_n  # e.g. 2721/75261 ≈ 3.6%
lift_5pp_purchases = round(lift_5pp_views * view_to_purchase_rate)
# Revenue per funnel purchase, not AOV: AOV excludes the purchases that record
# no revenue, so multiplying it by a purchase count would overstate the total.
lift_5pp_revenue   = round(lift_5pp_purchases * rev_per_purchase, 0)
# Sensitivity: incremental traffic almost always converts below the existing
# average, so the headline figure is an optimistic upper bound. Model 50% / 75%.
lift_5pp_rev_50 = round(lift_5pp_revenue * 0.50, 0)
lift_5pp_rev_75 = round(lift_5pp_revenue * 0.75, 0)

# ── Recommendation 1: the landing pages that fail to show products ───────────
# The exact rate, not view_rate (rounded to 1 dp for display): sizing multiplies it
# by tens of thousands of landings, so the rounding would move the result.
weak_pages = weak_landing_pages(landing_raw, view_item_n / total_sessions * 100, total_sessions)
weak_pages["new_pct"] = weak_pages["new_sessions"] / weak_pages["sessions"] * 100
weak_pages["unattr_pct"] = weak_pages["unattributed_sessions"] / weak_pages["sessions"] * 100
weak_page_rows   = weak_pages.to_dict("records")
weak_pages_n     = len(weak_pages)
weak_landings    = int(weak_pages["sessions"].sum())
weak_share       = weak_landings / total_sessions * 100
weak_extra_views = int(weak_pages["extra_views"].sum())
weak_lost_share  = ((weak_pages["sessions"] - weak_pages["view_item"]).sum() / total_lost_discovery * 100
                    if total_lost_discovery else 0.0)
weak_unattr_pct  = (float(weak_pages["unattributed_sessions"].sum()) / weak_landings * 100) if weak_landings else 0.0
weak_new_pct     = (float(weak_pages["new_sessions"].sum()) / weak_landings * 100) if weak_landings else 0.0
# Same conversion logic and haircut as the +5pp scenario: a page fixed to the site
# rate adds product-viewing sessions that convert at today's rate at best.
rec1_orders      = round(weak_extra_views * view_to_purchase_rate)
rec1_rev_high    = round(rec1_orders * rev_per_purchase, 0)
rec1_rev_low     = round(rec1_rev_high * 0.50, 0)
_home = landing_raw[landing_raw["landing_page"] == "/"]
home_share    = float(_home["sessions"].iloc[0]) / total_sessions * 100 if len(_home) else None
home_view_pct = float(_home["view_item"].iloc[0]) / float(_home["sessions"].iloc[0]) * 100 if len(_home) else None

# ── Recommendation 2: cart recovery, sized with a stated assumption ──────────
# CART_RECOVERY is a planning assumption (share of abandoned carts brought back
# to checkout), not a benchmark. Recovered checkouts complete at today's rate.
CART_RECOVERY = 0.10
abandoned_n      = carted_n - checkout_n
rec2_cart_orders = round(abandoned_n * CART_RECOVERY * checkout_to_purch / 100)
rec2_cart_rev    = round(rec2_cart_orders * rev_per_purchase, 0)
rec2_checkout_target = (checkout_n + abandoned_n * CART_RECOVERY) / carted_n * 100 if carted_n else 0.0

# ── Executive summary (computed from real numbers) ────────────────────────────
# One story, stated with numbers: the largest absolute loss is discovery; after
# that, product views convert to carts at a category-dependent rate, and most
# carts never reach checkout. Each clause is computed, so the sentence stays
# true if the window or dataset changes.
disc_is_biggest = bool(biggest_drop_idx == 0)

# ── Priority label ─────────────────────────────────────────────────────────────
if view_rate < 30:
    priority_label = "Top-of-Funnel: Product Discovery"
    priority_color = "#ea580c"
elif atc_from_view < 40:
    priority_label = "Mid-Funnel: Product → Cart Conversion"
    priority_color = "#ea580c"
else:
    priority_label = "Retention & Lifecycle"
    priority_color = "#ea580c"


# ══════════════════════════════════════════════════════════════════════════════
# CHARTS (charts.py)
# ══════════════════════════════════════════════════════════════════════════════
def chart_div(fig, div_id: str) -> str:
    return fig.to_html(full_html=False, include_plotlyjs=False, div_id=div_id)

# Funnel segments for the chart's menu: pre-computed here, switched in the
# browser with no server. Every segment uses the same sequential session funnel.
_STEPS = list(queries.STEP_COLUMNS.values())   # sessions, view_item, … purchase
_counts = lambda r: [int(r[c]) for c in _STEPS]
funnel_segments = [("All sessions", _counts(funnel_raw.iloc[0])),
                   ("Excluding (not set) · biased up", _counts(at))]
if has_visitor_split:
    funnel_segments += [("New sessions", _counts(_vis.loc["new"])),
                        ("Returning sessions", _counts(_vis.loc["returning"]))]
for _, r in device_raw.sort_values("sessions", ascending=False).iterrows():
    funnel_segments.append((f"Device · {r['device_category'].capitalize()}", _counts(r)))
_ch_seg = channel_raw.groupby(channel_dim)[_STEPS].sum()
_ch_seg = _ch_seg[_ch_seg["sessions"] / _ch_seg["sessions"].sum() >= 0.01].sort_values("sessions", ascending=False)
for _ch, r in _ch_seg.iterrows():
    funnel_segments.append((f"Channel · {_ch}", _counts(r)))

# Returning users per cohort and week, labelled like cohort_pct (censored → NaN).
cohort_users = cohort_pivot.copy()
cohort_users.index = cohort_users.index.astype(str)
cohort_users.columns = [f"Week {c}" for c in cohort_users.columns]

chart_html = {
    "funnel":   chart_div(charts.funnel(funnel_df["step"].tolist(), funnel_segments), "chart-funnel"),
    "cohort":   chart_div(charts.cohort_heatmap(cohort_pct, cohort_users, partial_label), "chart-cohort"),
    "channel":  chart_div(charts.channel(channel_tbl, view_rate), "chart-channel"),
    "category": chart_div(charts.category(category_tbl, category_benchmark), "chart-category"),
    "price":    chart_div(charts.price_bands(price_bands), "chart-price"),
}


# ══════════════════════════════════════════════════════════════════════════════
# AI BRIEF
# ══════════════════════════════════════════════════════════════════════════════
# The notebook stamps the brief with the version of the LLM context it was
# written from (§10f header: "Context vN"). A brief from an older context cannot
# know the newer verdicts and may contradict the page, so it is flagged.
#   v3 = computed verdicts, confounders, data-quality, new vs returning, significance
#   v4 = listing-page levels, price bands, landing pages, retention trend, page recommendations
BRIEF_CONTEXT_VERSION = 4
brief_path = HERE / "product_brief_ga4_funnel.md"
brief_html, brief_model, brief_stale, brief_generated = "", "AI", False, None
if brief_path.exists():
    brief_md = brief_path.read_text(encoding="utf-8")
    _gen = re.search(r"\*Generated (\d{4}-\d{2}-\d{2})", brief_md)
    _mdl = re.search(r"Model: ([^·*]+)", brief_md)
    _ver = re.search(r"Context v(\d+)", brief_md)
    brief_model = _mdl.group(1).strip() if _mdl else "Claude"
    brief_generated = _gen.group(1) if _gen else None
    brief_ctx_version = int(_ver.group(1)) if _ver else 0
    brief_stale = brief_ctx_version < BRIEF_CONTEXT_VERSION
    if brief_stale:
        print(f"  NOTE: the AI brief is from context v{brief_ctx_version} (current: "
              f"v{BRIEF_CONTEXT_VERSION}) — it is flagged as out of date on the page.")
    # Body = everything after the header's horizontal rule; md_render escapes it.
    _lines = brief_md.splitlines()
    _hr = next((k for k, l in enumerate(_lines) if l.strip() == "---"), 4)
    brief_html = md_to_html("\n".join(_lines[_hr + 1:]))
    print("AI product brief included.")
else:
    print("No product_brief_ga4_funnel.md — run Section 10 of the notebook first.")


# ══════════════════════════════════════════════════════════════════════════════
# VALUES THE NARRATIVE NEEDS
# ══════════════════════════════════════════════════════════════════════════════
wk1_display    = f"{avg_wk1}%" if avg_wk1 else "—"
# Compared against the planning target, not an external benchmark: no sourced
# industry figure exists for calendar-week return on this kind of store.
wk1_warn       = avg_wk1 is not None and avg_wk1 < WK1_TARGET
retention_flag = "⚠️" if wk1_warn else ""
WK1_DEFINITION = ("the share of a weekly cohort with any session in the next calendar "
                  "week (Monday-anchored) — so a user's return window is 1 to 13 days, not exactly 7")

# Only claim "the site average sits under every named channel" when it is true.
notset_below_all = (notset_view_pct is not None
                    and notset_view_pct < view_rate < float(_ch_rank["view_pct"].min()))

# Recommendation 3 sizing — category route (used unless channel is the driver).
cart_to_purchase = purchase_n / int(funnel_raw["add_to_cart"].iloc[0])
cat_orders  = round(category_top3_at_stake * cart_to_purchase)
cat_revenue = round(cat_orders * rev_per_purchase, 0)
cat_revenue_low = round(cat_revenue * 0.50, 0)

if not has_visitor_split:   # the template references these inside an if-block
    vs_new = vs_ret = visitor_test = visitor_cvr_ratio = rec2_rev_low = rec2_rev_high = None

generated_date = pd.Timestamp.now().strftime("%Y-%m-%d")
python_version = sys.version.split()[0]
plotly_version = plotly.__version__
plotly_js      = get_plotlyjs()   # embedded, not a CDN: works offline and always
                                  # matches the plotly that built the figures


# ══════════════════════════════════════════════════════════════════════════════
# RENDER
# ══════════════════════════════════════════════════════════════════════════════
print("Assembling dashboard...")
env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(HERE / "templates"),
    autoescape=True,                   # values are HTML-escaped unless marked |safe
    undefined=jinja2.StrictUndefined,  # a missing value is an error, not a blank
    keep_trailing_newline=True,
)
env.filters["fmt"] = lambda value, spec: format(value, spec)   # {{ x|fmt(',.1f') }}
env.filters["pval"] = format_p                                  # {{ p|pval }} → "p < 0.001"

# The template declares what it needs: pass exactly those names (including the
# partials'), so a misspelt or forgotten value fails loudly here.
_names = set()
for _t in env.list_templates(filter_func=lambda n: n.endswith((".j2", ".css"))):
    _names |= jinja2.meta.find_undeclared_variables(env.parse(env.loader.get_source(env, _t)[0]))
_names.discard("charts"); _ctx_extra = {"charts": chart_html}
_missing = sorted(n for n in _names if n not in globals())
if _missing:
    raise NameError(f"template needs values this script does not define: {_missing}")
page = {n: globals()[n] for n in _names} | _ctx_extra

html = env.get_template("dashboard.html.j2").render(page)
OUTPUT.write_text(html, encoding="utf-8")
size_kb = OUTPUT.stat().st_size / 1024
print(f"\nDone → {OUTPUT.name}  ({size_kb:.0f} KB)")
print("Open dashboard.html in any browser — no Python required.")
