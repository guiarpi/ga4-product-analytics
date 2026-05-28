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

import re
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
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

# ── 3. Shoppers funnel (entry at view_item) ───────────────────────────────────
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
cohort_pivot = (cohort_df.pivot(index="cohort_week", columns="weeks_since_cohort",
                                values="active_users").fillna(0).astype(int))
cohort_pct = cohort_pivot.div(cohort_pivot[0], axis=0).multiply(100).round(1)
cohort_pct.index   = cohort_pct.index.astype(str)
cohort_pct.columns = [f"Week {c}" for c in cohort_pct.columns]


# ══════════════════════════════════════════════════════════════════════════════
# DERIVED METRICS & DYNAMIC INSIGHTS
# ══════════════════════════════════════════════════════════════════════════════
print("Computing insights...")

total_sessions  = int(funnel_df["sessions"].iloc[0])
view_item_n     = int(funnel_df["sessions"].iloc[1])
purchase_n      = int(funnel_df["sessions"].iloc[4])
overall_cvr     = float(funnel_df["overall_cvr"].iloc[4])
view_rate       = float(funnel_df["step_cvr"].iloc[1])   # session → view_item
atc_from_view   = float(funnel_df["step_cvr"].iloc[2])   # view_item → add_to_cart
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

# Cohort retention
avg_wk1 = round(float(cohort_pct["Week 1"].mean()), 1) if "Week 1" in cohort_pct.columns else None
avg_wk2 = round(float(cohort_pct["Week 2"].mean()), 1) if "Week 2" in cohort_pct.columns else None

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

# ── Executive summary (computed from real numbers) ────────────────────────────
bottleneck = "product discovery" if view_rate < 30 else "product-to-cart conversion"
discovery_severity = "critical" if view_rate < 20 else "significant"

exec_summary = (
    f"Of {total_sessions:,} sessions analysed across Nov 2020–Jan 2021, only "
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
fig_cohort = go.Figure(go.Heatmap(
    z=cohort_pct.values.tolist(),
    x=cohort_pct.columns.tolist(),
    y=cohort_pct.index.tolist(),
    text=[[f"{v:.0f}%" for v in row] for row in cohort_pct.values.tolist()],
    texttemplate="%{text}",
    colorscale="Blues", reversescale=True, zmin=0, zmax=12,
    hovertemplate="Cohort: <b>%{y}</b><br>Week: %{x}<br>Retention: %{z:.1f}%<extra></extra>",
))
fig_cohort.update_layout(
    xaxis_title="Weeks Since First Session", yaxis_title="Cohort Week",
    height=440, yaxis_autorange="reversed",
    paper_bgcolor="white", margin=dict(t=20),
)


# ══════════════════════════════════════════════════════════════════════════════
# AI BRIEF
# ══════════════════════════════════════════════════════════════════════════════
brief_html = ""
brief_path = Path("product_brief_ga4_funnel.md")
if brief_path.exists():
    brief_md   = brief_path.read_text(encoding="utf-8")
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

date_label    = f"Nov {DATE_START[:4]} – Jan {DATE_END[:4]}"
wk1_display   = f"{avg_wk1}%" if avg_wk1 else "—"
wk1_warn      = avg_wk1 is not None and avg_wk1 < 10
retention_flag = "⚠️" if wk1_warn else ""

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GA4 Funnel Analysis — Product Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
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
        add to cart — compared to just <strong>{atc_from_view:.1f}%</strong>
        of all sessions. The gap between these two numbers is the discovery
        problem. The product detail page itself is not the bottleneck.
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
        Segmenting high-value users by acquisition channel could reveal
        which traffic sources drive disproportionate revenue.
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
          (at current AOV of ${aov:,.0f} · assumes same {view_to_purchase_rate*100:.1f}% view→purchase rate)</p>
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
        <p><strong>Likely causes:</strong> no triggered email or push
        re-engagement, traffic from low-intent channels, or a first
        session that didn't reach a product.</p>
        <p><strong>In SaaS terms:</strong> this is equivalent to D7 retention —
        the % of activated users who return within 7 days. The intervention
        is lifecycle emails or in-app nudges surfaced at Day 2–3.</p>
        <h4 style="margin-top:16px">Why the Oct 26 cohort stands out</h4>
        <p>The earliest cohort (week of Oct 26) shows <strong>~11% Week-1 retention</strong> —
        roughly double the average. This cohort arrived <em>before</em> the main
        holiday acquisition push, which means it likely skewed toward organic, high-intent
        visitors rather than broad paid traffic. As campaigns ramped up in November and
        December, the audience widened and average intent dropped — pulling retention
        down. This tells us the site <strong>can</strong> retain engaged users;
        the challenge is audience quality, not product experience alone.</p>
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
        healthy e-commerce cohorts. No post-visit re-engagement mechanism is in place.</p>
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
        <p>The Oct 26 cohort benchmarks at <strong>~11% retention</strong> with a high-intent
        organic audience — confirming the site can retain engaged users.
        Even a modest lift from 4.4% to 8% Week-1 retention compounds across all new cohorts
        and multiplies total session volume without increasing acquisition spend.</p>
        <div class="metric">Week-1 cohort retention rate (target: &gt;8%)</div>
      </div>
    </div>

    <div class="rec">
      <span class="rec-priority p3">Priority 3 · Investigate</span>
      <h4>Segment by traffic source</h4>

      <div class="rec-section">
        <div class="rec-section-label">Problem</div>
        <p><strong>${total_revenue:,.0f} total revenue</strong> came from just
        {purchase_n:,} sessions — but the mean/median AOV gap (${aov:,.0f} vs ${median_order:,.0f})
        suggests a small segment of high-value buyers is driving disproportionate revenue.
        Currently there is no visibility into which acquisition channels produce buyers
        versus browsers.</p>
      </div>

      <div class="rec-section">
        <div class="rec-section-label">Recommended Actions</div>
        <p>Break down session → purchase conversion rate and average order value by
        <code>traffic_source.medium</code>. Identify which channels — paid search,
        organic, email, referral — drive purchasers. Then cross-check which channels
        produce high-AOV buyers vs high-volume low-intent traffic.</p>
      </div>

      <div class="rec-section rec-impact">
        <div class="rec-section-label">Business Impact</div>
        <p>Reallocating even 20% of acquisition budget from low-converting to
        high-converting channels could meaningfully improve overall CVR without
        increasing total spend. If one channel converts at 2× the average,
        doubling investment there has immediate revenue impact.</p>
        <div class="metric">revenue per session by traffic_source.medium</div>
      </div>
    </div>

  </div>

  {f'''
  <!-- AI Product Brief -->
  <p class="section-label">AI-Generated Product Brief
    <span class="ai-badge">Claude</span></p>
  <div class="card">
    <div class="brief-body">{brief_html}</div>
  </div>
  ''' if brief_html else ""}

  <!-- ── Report Automation ──────────────────────────────────────────────── -->
  <p class="section-label">AI-Augmented Report Automation</p>
  <div class="card">
    <p style="font-size:0.87rem;color:#4b5563;margin-bottom:20px;line-height:1.7">
      This pipeline goes beyond a standard scheduled script. It combines a
      <strong>parameterised Python data pipeline</strong> with a
      <strong>large language model (Claude, via Anthropic API)</strong> to produce
      two distinct outputs on every run: structured interactive charts from BigQuery data,
      and a freshly written AI product brief that reads the new numbers and generates
      updated analysis, hypotheses, and recommendations in natural language —
      automatically, without a human analyst in the loop.
    </p>
    <div class="two-col" style="gap:24px">
      <div>
        <div class="insight-panel">
          <h4>Where the LLM fits in</h4>
          <p>After all BigQuery queries complete, the pipeline packages the results —
          funnel rates, revenue metrics, cohort retention figures — into a structured
          JSON context and sends them to the Claude API with a prompt instructing it to
          act as a senior product analyst.</p>
          <p>The LLM does not read raw data or SQL. It receives curated, computed metrics
          and produces a <strong>free-text product brief</strong>: executive summary,
          key findings ranked by confidence, hypotheses with reasoning, and recommended
          actions. This output is different every run because the underlying numbers change.</p>
          <p>This is distinct from a rule-based template ("if CVR &lt; 1% then show warning"):
          the model reasons over the data holistically, surfaces non-obvious patterns,
          and frames findings in business language without being explicitly programmed to do so.</p>
        </div>
      </div>
      <div>
        <div class="insight-panel">
          <h4>End-to-end architecture</h4>
          <p style="font-family:monospace;font-size:0.81rem;background:#f3f4f6;padding:14px 16px;border-radius:8px;line-height:2.1;color:#1f2937">
            GA4 BigQuery Export<br>
            &nbsp;&nbsp;&nbsp;↓ SQL · partition-pruned queries<br>
            Computed metrics (Python)<br>
            &nbsp;&nbsp;&nbsp;↓ structured JSON context<br>
            <strong>Claude API (LLM)</strong><br>
            &nbsp;&nbsp;&nbsp;↓ natural-language product brief<br>
            generate_dashboard.py<br>
            &nbsp;&nbsp;&nbsp;↓ Plotly charts + AI brief → HTML<br>
            dashboard.html → Stakeholders
          </p>
          <p style="margin-top:14px;font-size:0.83rem;color:#374151">
            Change <code>DATE_START</code>, <code>DATE_END</code>, and <code>DATASET</code>
            at the top of the script — then run
            <code style="background:#f3f4f6;padding:2px 6px;border-radius:4px">python generate_dashboard.py</code>.
            Every chart, metric, and AI insight refreshes in under 60 seconds.
            Schedule via cron, Cloud Scheduler, or Airflow for weekly delivery.
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

    // 1. Device chart — linear scale; no JS patch needed for this chart

    // 2. Cohort heatmap — zmax=12 so the 0–12% retention range uses full colour spectrum
    var cohortDiv = document.getElementById('chart-cohort');
    if (cohortDiv && cohortDiv.data && cohortDiv.data.length) {{
      Plotly.restyle('chart-cohort', {{
        zmin: [0], zmax: [12],
        hovertemplate: ['Cohort: <b>%{{y}}</b><br>Week: %{{x}}<br>Retention: %{{z:.1f}}%<extra></extra>']
      }}, [0]);
    }}

    // 3. TTC chart — fix any corrupted bdata y-values with authoritative values from query
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
