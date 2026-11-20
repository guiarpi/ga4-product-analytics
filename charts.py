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


def _funnel_view(steps: list, n: list) -> dict:
    """Bar values and labels for one funnel: each step's name carries its
    step-to-step rate ("21.2% continue"), each bar its full count, the hover the
    share of all sessions with two decimals (0.77% never reads 1%)."""
    rate = lambda v, prev: f"{v / prev * 100:.1f}%" if prev else "—"
    names = [steps[0]] + [f"{s}<br><span style='font-size:11px;color:#6b7280'>{rate(v, prev)} continue</span>"
                          for s, prev, v in zip(steps[1:], n, n[1:])]
    return {"x": n, "y": names, "text": [f"{v:,}" for v in n],
            "customdata": [f"{v / n[0] * 100:.2f}% of the segment's sessions" if n[0] else "" for v in n]}


def funnel(steps: list, segments: list) -> go.Figure:
    """Native go.Funnel with a segment menu. segments = [(label, [counts per
    step]), …], the first shown by default; the menu swaps the bars in the
    browser (Plotly update buttons — no server). Short bar labels keep it
    readable at phone width."""
    first = _funnel_view(steps, segments[0][1])
    fig = go.Figure(go.Funnel(
        **first, textinfo="text",
        textposition=["inside"] + ["outside"] * (len(steps) - 1),
        insidetextfont=dict(color="white"),
        marker=dict(color=MEASURE, line=dict(color="white", width=1)),
        connector=dict(line=dict(color="#e2e8f0", width=1), fillcolor="#f1f5f9"),
        hovertemplate="%{text} sessions<br>%{customdata}<extra></extra>",
    ))
    buttons = []
    for label, counts in segments:
        v = _funnel_view(steps, counts)
        buttons.append(dict(label=f"{label} · {counts[0]:,}", method="restyle",
                            args=[{k: [v[k]] for k in ("x", "y", "text", "customdata")}]))
    fig.update_layout(
        height=430, margin=dict(l=10, r=10, t=44, b=10),
        paper_bgcolor="white", plot_bgcolor="white", showlegend=False, font=dict(size=12),
        # One segment needs no menu (the notebook draws single funnels with this too).
        updatemenus=[dict(buttons=buttons, direction="down", x=0, xanchor="left", y=1.12, yanchor="top",
                          bgcolor="white", bordercolor="#cbd5e1", font=dict(size=11), showactive=True)]
                    if len(segments) > 1 else [],
    )
    if len(segments) == 1:
        fig.update_layout(margin=dict(t=10))
    return fig


def cohort_heatmap(cohort_pct: pd.DataFrame, cohort_users: pd.DataFrame, partial_label: str | None = None) -> go.Figure:
    """Weekly cohort retention from Week 1 on (Week 0 is 100% by definition and
    would only flatten the scale; the cohort size is in the row label instead).
    Rows are plain-text labels, so no axis reformats the dates. The partial first
    cohort is drawn in grey: it is excluded from the averages. A menu switches
    between % of the cohort and returning users. Censored cells are NaN → blank.

    cohort_users has the same index and "Week N" columns as cohort_pct."""
    cols = [c for c in cohort_pct.columns if c != "Week 0"]
    pct, users = cohort_pct[cols], cohort_users[cols]
    sizes = cohort_users["Week 0"].tolist()
    labels = [f"{pd.Timestamp(c):%d %b %y} · {int(n):,}" + (" · partial" if c == partial_label else "")
              for c, n in zip(pct.index, sizes)]
    is_partial = [c == partial_label for c in pct.index]
    def split(d):
        rows = d.values.tolist()
        full = [[None if p or pd.isna(v) else v for v in row] for row, p in zip(rows, is_partial)]
        part = [[v if p and not pd.isna(v) else None for v in row] for row, p in zip(rows, is_partial)]
        return full, part
    def text(z, fmt):
        return [["" if v is None else fmt(v) for v in row] for row in z]
    zmax = lambda z: max([v for row in z for v in row if v is not None] or [1])
    pf, pp = split(pct)
    uf, up = split(users)
    modes = {
        "%": dict(z=[pf, pp], text=[text(pf, lambda v: f"{v:.0f}%"), text(pp, lambda v: f"{v:.0f}%")],
                  zmax=[zmax(pf), None], hovertemplate=[
                      "Cohort <b>%{y}</b> users<br>Week %{x}: %{z:.1f}% returned<extra></extra>",
                      "Partial first week — excluded from averages<br>Week %{x}: %{z:.1f}%<extra></extra>"]),
        "users": dict(z=[uf, up], text=[text(uf, lambda v: f"{v:,.0f}"), text(up, lambda v: f"{v:,.0f}")],
                      zmax=[zmax(uf), None], hovertemplate=[
                          "Cohort <b>%{y}</b> users<br>Week %{x}: %{z:,.0f} returned<extra></extra>",
                          "Partial first week — excluded from averages<br>Week %{x}: %{z:,.0f} users<extra></extra>"]),
    }
    weeks = [c.replace("Week ", "") for c in cols]   # axis title says "weeks since first session"
    common = dict(x=weeks, y=labels, texttemplate="%{text}", textfont=dict(size=10),
                  hoverongaps=False, xgap=1, ygap=1)
    m = modes["%"]
    fig = go.Figure([
        go.Heatmap(z=m["z"][0], text=m["text"][0], colorscale="Blues", zmin=0, zmax=m["zmax"][0],
                   colorbar=dict(thickness=10, len=0.6), hovertemplate=m["hovertemplate"][0], **common),
        go.Heatmap(z=m["z"][1], text=m["text"][1], colorscale=[[0, "#e5e7eb"], [1, "#e5e7eb"]], showscale=False,
                   hovertemplate=m["hovertemplate"][1], **common),
    ])
    buttons = [dict(label=label, method="restyle",
                    args=[{"z": md["z"], "text": md["text"], "zmax": md["zmax"], "hovertemplate": md["hovertemplate"]}])
               for label, md in (("Show: % of cohort", modes["%"]), ("Show: returning users", modes["users"]))]
    fig.update_layout(
        xaxis_title="Weeks since first session", yaxis_title="Cohort (week starting · users)",
        height=40 * len(labels) + 150, yaxis=dict(autorange="reversed", type="category"),
        xaxis=dict(type="category", tickangle=0),
        paper_bgcolor="white", plot_bgcolor="white", margin=dict(t=44, l=10, r=10, b=10),
        updatemenus=[dict(buttons=buttons, direction="down", x=0, xanchor="left", y=1.0, yanchor="bottom",
                          pad=dict(b=6), bgcolor="white", bordercolor="#cbd5e1", font=dict(size=11))],
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


def _category_view(t: pd.DataFrame) -> dict:
    return {
        "y": t["item_category"].tolist(),
        "x": t["atc_pct"].round(1).tolist(),
        "text": [f"{v:.1f}%" for v in t["atc_pct"]],
        "marker.color": [CATEGORY_COLOURS[st] for st in t["status"]],
        "error_x.array": (t["ci_hi"] - t["atc_pct"]).round(2).tolist(),
        "error_x.arrayminus": (t["atc_pct"] - t["ci_lo"]).round(2).tolist(),
        "customdata": [[f"{int(v):,}", f"{int(k):,}", f"{lo:.1f}–{hi:.1f}%"] for v, k, lo, hi in
                       zip(t["viewing_sessions"], t["carts_at_stake"], t["ci_lo"], t["ci_hi"])],
    }


CATEGORY_SORTS = (
    ("Sort: carts at stake", ["carts_at_stake", "atc_pct"], [False, True]),
    ("Sort: seen → carted rate", ["atc_pct"], [True]),
    ("Sort: sessions that saw it", ["viewing_sessions"], [False]),
)


def category(category_tbl: pd.DataFrame, site_average: float) -> go.Figure:
    """Seen → carted per product-type listing page with Wilson 95% whiskers.
    Orange = interval entirely below the average, blue = entirely above, grey =
    cannot be told apart from average. Default order = carts at stake (as in the
    text and table); a menu re-sorts by rate or volume in the browser."""
    views = [_category_view(category_tbl.sort_values(cols, ascending=asc)) for _, cols, asc in CATEGORY_SORTS]
    v = views[0]
    fig = go.Figure(go.Bar(
        y=v["y"], x=v["x"], text=v["text"], orientation="h",
        texttemplate="%{text}", textposition="outside",
        marker_color=v["marker.color"],
        error_x=dict(type="data", symmetric=False, array=v["error_x.array"], arrayminus=v["error_x.arrayminus"],
                     color="#6b7280", thickness=1, width=3),
        customdata=v["customdata"],
        hovertemplate="<b>%{y}</b><br>Seen → carted: %{x:.1f}% (95% CI %{customdata[2]})"
                      "<br>Sessions that saw it: %{customdata[0]}"
                      "<br>Carts at stake: %{customdata[1]}<extra></extra>",
    ))
    t = category_tbl
    fig.add_vline(x=site_average, line_dash="dash", line_color="#6b7280",
                  annotation_text=f"product-type average {site_average:.1f}%",
                  annotation_position="top")
    fig.update_layout(
        height=max(300, 30 * len(t) + 110), showlegend=False,
        paper_bgcolor="white", plot_bgcolor="white",
        margin=dict(l=10, r=40, t=60, b=10),
        yaxis=dict(autorange="reversed"),   # first row of the chosen order at the top
        xaxis=dict(title="% of sessions that saw the page's products and carted one",
                   range=[0, (float(t["ci_hi"].max()) if len(t) else 10) * 1.15]),
        updatemenus=[dict(buttons=[dict(label=label, method="restyle", args=[{k: [val] for k, val in view.items()}])
                                   for (label, _, _), view in zip(CATEGORY_SORTS, views)],
                          direction="down", x=0, xanchor="left", y=1.0, yanchor="bottom", pad=dict(b=26),
                          bgcolor="white", bordercolor="#cbd5e1", font=dict(size=11))],
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

