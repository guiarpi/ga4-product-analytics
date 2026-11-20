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


def funnel(funnel_df: pd.DataFrame) -> go.Figure:
    """Native go.Funnel — tapering shape, cleaner than a horizontal bar."""
    fig = go.Figure(go.Funnel(
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
    fig.update_layout(
        height=420, margin=dict(l=20, r=20, t=10, b=10),
        paper_bgcolor="white", plot_bgcolor="white", showlegend=False,
    )
    return fig


def time_to_convert(ttc_df: pd.DataFrame) -> go.Figure:
    """Median minutes between steps for converting sessions. The saw → cart bar
    carries a dagger: its median is shaped by identical timestamps (see note)."""
    x = ["Session → View Products", "View Products → Add to Cart",
         "Add to Cart → Checkout", "Checkout → Purchase"]
    y = [float(ttc_df[c].iloc[0]) for c in ("p50_s_to_v", "p50_v_to_c", "p50_c_to_k", "p50_k_to_p")]
    text = [f"{v:.1f} min" for v in y]
    text[1] += " †"
    fig = go.Figure(go.Bar(
        x=x, y=y, text=text, texttemplate="%{text}", textposition="outside",
        marker_color="#4C78A8",
        hovertemplate="<b>%{x}</b><br>Median: %{y:.1f} min<extra></extra>",
    ))
    fig.update_layout(height=360, showlegend=False,
                      paper_bgcolor="white", plot_bgcolor="white",
                      yaxis=dict(title="Median minutes (converting sessions)",
                                 range=[0, max(y) * 1.25 if max(y) else 1]),
                      margin=dict(t=20, b=10), xaxis_tickangle=-15)
    return fig


def cohort_heatmap(cohort_pct: pd.DataFrame) -> go.Figure:
    """Weekly cohort retention. Blues runs light → dark as the value rises, so
    darker = better retention; zmax=12 keeps the 0–12% band using the full
    colour range (Week 0 is always 100%). Censored cells are NaN → blank."""
    z = cohort_pct.values.tolist()
    fig = go.Figure(go.Heatmap(
        z=z,
        x=cohort_pct.columns.tolist(),
        y=cohort_pct.index.tolist(),
        text=[["" if pd.isna(v) else f"{v:.0f}%" for v in row] for row in z],
        texttemplate="%{text}",
        colorscale="Blues", zmin=0, zmax=12,
        hoverongaps=False,  # censored (NaN) cells show no tooltip
        hovertemplate="Cohort: <b>%{y}</b><br>%{x}<br>Retention: %{z:.1f}%<extra></extra>",
    ))
    fig.update_layout(
        xaxis_title="Weeks Since First Session", yaxis_title="Cohort Week",
        height=440, yaxis_autorange="reversed",
        paper_bgcolor="white", margin=dict(t=20),
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
        marker_color=["#dc2626" if v < view_rate - 5 else "#4C78A8" for v in d["view_pct"]],
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
CATEGORY_COLOURS = {"below": "#ea580c", "above": "#4C78A8", "neutral": "#cbd5e1"}


def category(category_tbl: pd.DataFrame, site_average: float) -> go.Figure:
    """Seen → carted per category, worst at the top, with Wilson 95% whiskers.
    Orange = interval entirely below the site average, blue = entirely above,
    grey = cannot be told apart from average."""
    t = category_tbl
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
                  annotation_text=f"site average {site_average:.1f}%",
                  annotation_position="top")
    fig.update_layout(
        height=max(300, 30 * len(t) + 80), showlegend=False,
        paper_bgcolor="white", plot_bgcolor="white",
        margin=dict(l=10, r=40, t=30, b=10),
        yaxis=dict(autorange="reversed"),   # lowest rate at the top
        xaxis=dict(title="% of sessions that saw the category and carted an item from it",
                   range=[0, (float(t["ci_hi"].max()) if len(t) else 10) * 1.15]),
    )
    return fig
