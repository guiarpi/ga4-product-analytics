"""
charts.py
---------
Plotly figures for dashboard.html. Each builder takes the computed table it
draws and returns a go.Figure; generate_dashboard.py turns them into HTML.

Values go in as plain Python lists, not numpy arrays: Plotly binary-encodes
arrays, which previously needed a JS patch to show near-zero values correctly.
Text labels are pre-formatted in Python (texttemplate format strings fail in
standalone HTML).
"""

import pandas as pd
import plotly.graph_objects as go


# One meaning per colour across the page: orange = below the comparison,
# blue = above it or the plain measure, grey = neutral or not a real segment.
BELOW, ABOVE, NEUTRAL, MEASURE = "#ea580c", "#1d4ed8", "#cbd5e1", "#4C78A8"


def funnel(funnel_df: pd.DataFrame) -> go.Figure:
    """Native go.Funnel. Each step's name carries its step-to-step rate
    ("21.2% continue"), the bar its full count, the hover the share of all
    sessions with two decimals (0.77% never reads 1%). Short bar labels keep
    the chart readable at phone width."""
    n = funnel_df["sessions"].tolist()
    steps = funnel_df["step"].tolist()
    names = [steps[0]] + [f"{s}<br><span style='font-size:11px;color:#6b7280'>{v / prev * 100:.1f}% continue</span>"
                          for s, prev, v in zip(steps[1:], n, n[1:])]
    fig = go.Figure(go.Funnel(
        y=names, x=n,
        text=[f"{v:,}" for v in n], textinfo="text",
        textposition=["inside"] + ["outside"] * (len(n) - 1),
        insidetextfont=dict(color="white"),
        customdata=[f"{v / n[0] * 100:.2f}% of all sessions" for v in n],
        marker=dict(color=MEASURE, line=dict(color="white", width=1)),
        connector=dict(line=dict(color="#e2e8f0", width=1), fillcolor="#f1f5f9"),
        hovertemplate="%{text} sessions<br>%{customdata}<extra></extra>",
    ))
    fig.update_layout(
        height=400, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="white", plot_bgcolor="white", showlegend=False,
        font=dict(size=12),
    )
    return fig


def cohort_heatmap(cohort_pct: pd.DataFrame, cohort_sizes: list, partial_label: str | None = None) -> go.Figure:
    """Weekly cohort retention from Week 1 on (Week 0 is 100% by definition and
    would only flatten the scale; the cohort size is in the row label instead).
    Rows are plain-text labels, so no axis reformats the dates. The partial first
    cohort is drawn in grey: it is excluded from the averages. The colour scale
    tops out at the highest full-cohort value. Censored cells are NaN → blank."""
    d = cohort_pct.drop(columns=[c for c in cohort_pct.columns if c == "Week 0"])
    labels = [f"{pd.Timestamp(c):%d %b %y} · {int(n):,}" + (" · partial" if c == partial_label else "")
              for c, n in zip(d.index, cohort_sizes)]
    is_partial = [c == partial_label for c in d.index]
    full = [[None if p or pd.isna(v) else v for v in row] for row, p in zip(d.values.tolist(), is_partial)]
    part = [[v if p and not pd.isna(v) else None for v in row] for row, p in zip(d.values.tolist(), is_partial)]
    txt = lambda z: [["" if v is None else f"{v:.0f}%" for v in row] for row in z]
    zmax = max([v for row in full for v in row if v is not None] or [1])
    weeks = [c.replace("Week ", "") for c in d.columns]   # axis title says "weeks since first session"
    common = dict(x=weeks, y=labels, texttemplate="%{text}", textfont=dict(size=10),
                  hoverongaps=False, xgap=1, ygap=1)
    fig = go.Figure([
        go.Heatmap(z=full, text=txt(full), colorscale="Blues", zmin=0, zmax=zmax,
                   colorbar=dict(title="%", thickness=10, len=0.6),
                   hovertemplate="Cohort <b>%{y}</b> users<br>Week %{x}: %{z:.1f}% returned<extra></extra>", **common),
        go.Heatmap(z=part, text=txt(part), colorscale=[[0, "#e5e7eb"], [1, "#e5e7eb"]], showscale=False,
                   hovertemplate="Partial first week — excluded from averages<br>Week %{x}: %{z:.1f}%<extra></extra>", **common),
    ])
    fig.update_layout(
        xaxis_title="Weeks since first session", yaxis_title="Cohort (week starting · users)",
        height=40 * len(labels) + 120, yaxis=dict(autorange="reversed", type="category"),
        xaxis=dict(type="category", tickangle=0),
        paper_bgcolor="white", plot_bgcolor="white", margin=dict(t=10, l=10, r=10, b=10),
    )
    return fig


def channel(channel_tbl: pd.DataFrame, view_rate: float) -> go.Figure:
    """Share of sessions that saw a product, per channel, against the site
    average. One metric only: it is the metric the question is about."""
    d = channel_tbl.sort_values("view_pct")
    fig = go.Figure(go.Bar(
        y=d["channel"].tolist(),
        x=d["view_pct"].round(1).tolist(),
        orientation="h",
        text=[f"{v:.1f}%" for v in d["view_pct"]],
        texttemplate="%{text}", textposition="outside",
        # (not set) is a tagging gap, not a channel: grey, not "bad".
        marker_color=[NEUTRAL if ch == "(not set)" else MEASURE for ch in d["channel"]],
        customdata=[[f"{int(s):,}", f"{sh:.1f}%"] for s, sh in zip(d["sessions"], d["share"])],
        hovertemplate="<b>%{y}</b><br>Sessions that saw a product: %{x:.1f}%"
                      "<br>Sessions: %{customdata[0]} (%{customdata[1]} of total)<extra></extra>",
    ))
    fig.add_vline(x=view_rate, line_dash="dash", line_color="#6b7280",
                  annotation_text=f"site avg {view_rate:.1f}%",
                  annotation_position="top")
    fig.update_layout(
        height=max(260, 46 * len(d) + 80), showlegend=False,
        paper_bgcolor="white", plot_bgcolor="white",
        margin=dict(l=10, r=40, t=30, b=10),
        xaxis=dict(title="% of sessions that saw a product",
                   range=[0, max(float(d["view_pct"].max()), view_rate) * 1.2]),
    )
    return fig


# Colour follows the significance flag, not the raw gap.
CATEGORY_COLOURS = {"below": BELOW, "above": ABOVE, "neutral": NEUTRAL}


def category(category_tbl: pd.DataFrame, site_average: float) -> go.Figure:
    """Seen → carted per product-type listing page, worst at the top, with Wilson
    95% whiskers. Orange = interval entirely below the average, blue = entirely above,
    grey = cannot be told apart from average."""
    # Same order as the text and the table: carts at stake, then the weakest rate.
    t = category_tbl.sort_values(["carts_at_stake", "atc_pct"], ascending=[False, True])
    fig = go.Figure(go.Bar(
        y=t["item_category"].tolist(),
        x=t["atc_pct"].round(1).tolist(),
        orientation="h",
        text=[f"{v:.1f}%" for v in t["atc_pct"]],
        texttemplate="%{text}", textposition="outside",
        marker_color=[CATEGORY_COLOURS[st] for st in t["status"]],
        error_x=dict(type="data", symmetric=False,
                     array=(t["ci_hi"] - t["atc_pct"]).round(2).tolist(),
                     arrayminus=(t["atc_pct"] - t["ci_lo"]).round(2).tolist(),
                     color="#6b7280", thickness=1, width=3),
        customdata=[[f"{int(v):,}", f"{int(k):,}", f"{lo:.1f}–{hi:.1f}%"] for v, k, lo, hi in
                    zip(t["viewing_sessions"], t["carts_at_stake"], t["ci_lo"], t["ci_hi"])],
        hovertemplate="<b>%{y}</b><br>Seen → carted: %{x:.1f}% (95% CI %{customdata[2]})"
                      "<br>Sessions that saw it: %{customdata[0]}"
                      "<br>Carts at stake: %{customdata[1]}<extra></extra>",
    ))
    fig.add_vline(x=site_average, line_dash="dash", line_color="#6b7280",
                  annotation_text=f"product-type average {site_average:.1f}%",
                  annotation_position="top")
    fig.update_layout(
        height=max(300, 30 * len(t) + 80), showlegend=False,
        paper_bgcolor="white", plot_bgcolor="white",
        margin=dict(l=10, r=40, t=30, b=10),
        yaxis=dict(autorange="reversed"),   # most carts at stake at the top
        xaxis=dict(title="% of sessions that saw the page's products and carted one",
                   range=[0, (float(t["ci_hi"].max()) if len(t) else 10) * 1.15]),
    )
    return fig


def price_bands(bands: pd.DataFrame) -> go.Figure:
    """Seen → carted by the item's own price band, cheapest first, with Wilson
    95% whiskers. One colour: the bands are an ordered scale, not a ranking."""
    lo = [c[0] for c in bands["ci"]]
    hi = [c[1] for c in bands["ci"]]
    rate = bands["atc_pct"].tolist()
    fig = go.Figure(go.Bar(
        x=bands["price_band"].tolist(), y=[round(v, 1) for v in rate],
        text=[f"{v:.1f}%" for v in rate], texttemplate="%{text}", textposition="outside",
        marker_color=MEASURE,
        error_y=dict(type="data", symmetric=False,
                     array=[round(h - r, 2) for h, r in zip(hi, rate)],
                     arrayminus=[round(r - l, 2) for l, r in zip(lo, rate)],
                     color="#6b7280", thickness=1, width=4),
        customdata=[[f"{int(v):,}", f"{l:.1f}–{h:.1f}%"] for v, l, h in zip(bands["viewing_sessions"], lo, hi)],
        hovertemplate="<b>%{x}</b><br>Seen → carted: %{y:.1f}% (95% CI %{customdata[1]})"
                      "<br>Sessions that saw an item in the band: %{customdata[0]}<extra></extra>",
    ))
    fig.update_layout(
        height=300, showlegend=False, paper_bgcolor="white", plot_bgcolor="white",
        margin=dict(l=10, r=10, t=20, b=10),
        yaxis=dict(title="Seen → carted (%)",
                   range=[0, max(hi) * 1.25 if hi else 1]),
    )
    return fig

