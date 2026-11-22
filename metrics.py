"""
metrics.py
----------
Pure, BigQuery-free calculations shared by ga4_funnel_analysis.ipynb and
generate_dashboard.py, kept here so they can be unit-tested (tests/).

- site_average:   pooled rate across categories (volume-weighted), in %.
- flag_categories: Wilson 95% interval per category, a below / above / neutral
  flag against the site average, and carts at stake for flagged categories.
- listing_levels: product type / brand / collection / parent for each
  item_category, from its parent node in the raw paths.
- weak_landing_pages: landing pages with real volume whose product-view rate
  sits well under the site's.
- mix_decomposition: how much of a change in a rate between two periods comes
  from a change in segment mix, and how much from within the segments.
- diagnose_landing_page: tracking, page or entry traffic, from the weekly
  landing_diagnosis_sql rows of one page.
- purchase_path_summary, recovery_curve: cross-session purchase paths and how
  often abandoned carts come back in a later session (E10).
- segment_spread: is a funnel rate uniform across segments (channels,
  countries)? Spread, chi-square, Cramér's V and a practical-size verdict.
- compare_periods: each funnel rate in two periods, the change in points with
  a 95% interval on the difference (E12).
"""

import pandas as pd

from stats_utils import wilson_ci


def site_average(successes: pd.Series, totals: pd.Series) -> float:
    """Pooled rate in % (sum of successes / sum of totals), so one tiny category
    with an extreme rate cannot move it. 0.0 when there is no volume."""
    total = float(totals.sum())
    return float(successes.sum()) / total * 100 if total else 0.0


def flag_categories(df: pd.DataFrame, average_pct: float,
                    carting: str = "carting_sessions",
                    viewing: str = "viewing_sessions",
                    rate: str = "atc_pct") -> pd.DataFrame:
    """Return a copy of df with ci_lo, ci_hi, status and carts_at_stake added.

    status: "below" only when the whole Wilson 95% interval lies under the site
    average, "above" when it lies wholly over it, otherwise "neutral" — a
    category whose interval overlaps the average cannot be told apart from it.

    carts_at_stake: the extra carting sessions a "below" category would gain if
    it converted at the site average, (average − rate) × sessions, rounded.
    Zero for every other category, because an unflagged gap may be noise. This
    is the prioritisation key: a mildly weak category with heavy traffic
    outranks a terrible one nobody visits.
    """
    out = df.copy()
    ci = [wilson_ci(k, n) for k, n in zip(out[carting], out[viewing])]
    out["ci_lo"] = [c[0] for c in ci]
    out["ci_hi"] = [c[1] for c in ci]
    out["status"] = ["below" if hi < average_pct else "above" if lo > average_pct else "neutral"
                     for lo, hi in zip(out["ci_lo"], out["ci_hi"])]
    out["carts_at_stake"] = [
        int(round((average_pct - r) / 100 * n)) if st == "below" else 0
        for r, n, st in zip(out[rate], out[viewing], out["status"])
    ]
    return out


# ── Listing levels ────────────────────────────────────────────────────────────
LEVELS = ("product type", "brand", "collection", "parent")
BRAND_PARENT = "Shop by Brand"
ROOT_PARENT = "Home"


def listing_levels(categories: pd.Series, parents: pd.Series) -> pd.Series:
    """Level of each listing page, from the path segment above it.

    - parent:       a label that is itself the parent of another label
                    (Apparel, Lifestyle, Stationery, Shop by Brand) — a mixed page;
    - brand:        directly under "Shop by Brand" (Google, YouTube, Android);
    - collection:   directly under the store root "Home" and not a parent
                    (Sale, New, Eco-Friendly, Campus Collection);
    - product type: everything else — under a product parent (Hats under
                    Apparel, Bags under Lifestyle) or a flat label with no path.
    """
    parents = parents.where(parents.notna(), None)
    parent_names = {p for p in parents if p}
    def level(cat, parent):
        if cat in parent_names:
            return "parent"
        if parent == BRAND_PARENT:
            return "brand"
        if parent == ROOT_PARENT:
            return "collection"
        return "product type"
    return pd.Series([level(c, p) for c, p in zip(categories, parents)], index=categories.index)


# ── Landing pages ─────────────────────────────────────────────────────────────
# Pages that are not meant to show products: account, cart, checkout and policy
# pages. A low product-view rate there is expected, not a discovery problem.
NON_SHOPPING_PAGES = r"(?i)sign-?in|log-?in|register|my-?account|basket|cart|checkout|store-policies|faq"


def weak_landing_pages(pages: pd.DataFrame, site_view_pct: float, total_sessions: int,
                       min_share: float = 1.0, min_gap_pp: float = 5.0) -> pd.DataFrame:
    """Landing pages that take at least min_share % of all sessions and whose
    saw-a-product rate is at least min_gap_pp points under the site's.

    pages needs landing_page, sessions and view_item; '(no page_view)' is not a
    page and is never returned, nor are pages matching NON_SHOPPING_PAGES
    (sign-in, cart, checkout, policies). total_sessions is every session in the funnel —
    not pages' own sum, since the landing query leaves out rare pages. Adds
    share, view_pct, gap_pp and extra_views — the product-viewing sessions the
    page would add at the site rate."""
    total = float(total_sessions)
    d = pages[(pages["landing_page"] != "(no page_view)")
              & ~pages["landing_page"].astype(str).str.contains(NON_SHOPPING_PAGES, regex=True)].copy()
    d["share"] = d["sessions"] / total * 100 if total else 0.0
    d["view_pct"] = d["view_item"] / d["sessions"] * 100
    d["gap_pp"] = site_view_pct - d["view_pct"]
    d = d[(d["share"] >= min_share) & (d["gap_pp"] >= min_gap_pp)].copy()
    d["extra_views"] = (d["gap_pp"] / 100 * d["sessions"]).round().astype(int)
    return d.sort_values(["extra_views", "landing_page"], ascending=[False, True]).reset_index(drop=True)


# ── Mix decomposition ─────────────────────────────────────────────────────────
def mix_decomposition(a: pd.DataFrame, b: pd.DataFrame,
                      n: str = "users", k: str = "successes") -> dict:
    """Split the change in a pooled rate from period a to period b.

    a and b are indexed by segment with counts n (base) and k (successes).
    rate_b_at_a_mix = b's within-segment rates weighted by a's segment shares:
    the rate b would have had if only the rates, not the mix, had changed.
    mix_pp is the part of the change explained by the mix, within_pp the rest.
    All rates in %. Segments missing from one period count as 0 there."""
    seg = a.index.union(b.index)
    a, b = a.reindex(seg, fill_value=0), b.reindex(seg, fill_value=0)
    rate = lambda d: float(d[k].sum()) / float(d[n].sum()) * 100 if d[n].sum() else float("nan")
    share_a = a[n] / a[n].sum()
    rates_b = (b[k] / b[n].where(b[n] > 0)).fillna(0) * 100
    rate_a, rate_b = rate(a), rate(b)
    b_at_a = float((share_a * rates_b).sum())
    return {"rate_a": rate_a, "rate_b": rate_b, "rate_b_at_a_mix": b_at_a,
            "change_pp": rate_b - rate_a, "within_pp": b_at_a - rate_a, "mix_pp": rate_b - b_at_a}


# ── Landing-page diagnosis ────────────────────────────────────────────────────
GAP_SHARE = 0.10      # a "silent" week: view_item rate under 10% of the page's working rate
WORKING_QUANTILE = 0.75   # the working rate: its 75th-percentile week, which a long gap cannot drag down
GAP_WEEKS = 3         # … for at least 3 consecutive weeks
GAP_MIN_SESSIONS = 100
SCROLL_KEPT = 0.5     # behaviour counts as unchanged if scrolling stays ≥ half its usual rate
INSIDE_LIFT_PP = 10   # the page "works" if inside visitors see products ≥ 10pp more often


def diagnose_landing_page(rows: pd.DataFrame, site_view_pct: float) -> dict:
    """Classify why one landing page rarely shows a product.

    rows: landing_diagnosis_sql output for one page (week, entry, sessions,
    view_item_sessions, scrolled_sessions, untagged_sessions, eng_s_total).

    verdict:
    - "tracking": a run of ≥ GAP_WEEKS consecutive weeks (each with
      ≥ GAP_MIN_SESSIONS sessions) whose view_item rate is under GAP_SHARE of
      the page's working rate (its WORKING_QUANTILE weekly rate — a median would
      be dragged down by a long gap itself), while scrolling stays at
      ≥ SCROLL_KEPT of its rate outside the run — visitors behave as usual but
      the tag is silent;
    - "page": the same kind of run with behaviour collapsing too, or visitors
      who reach the page from inside the site also seeing products at under
      half the site rate — the page itself does not show them;
    - "entry traffic": otherwise, when inside visitors see products at least
      INSIDE_LIFT_PP points more often than landing ones — the page works and
      the gap is in who lands on it;
    - "unclear": none of the above.
    """
    rate = lambda d: float(d["view_item_sessions"].sum()) / float(d["sessions"].sum()) * 100 if d["sessions"].sum() else float("nan")
    scroll = lambda d: float(d["scrolled_sessions"].sum()) / float(d["sessions"].sum()) * 100 if d["sessions"].sum() else float("nan")
    landed, inside = rows[rows["entry"] == "landed"], rows[rows["entry"] == "inside"]
    weekly = rows.groupby("week")[["sessions", "view_item_sessions", "scrolled_sessions"]].sum().sort_index()
    weekly["rate"] = weekly["view_item_sessions"] / weekly["sessions"] * 100
    busy = weekly[weekly["sessions"] >= GAP_MIN_SESSIONS]

    # Longest run of consecutive busy weeks that are "silent" against the other weeks.
    gap = None
    if len(busy) > GAP_WEEKS:
        working = float(busy["rate"].quantile(WORKING_QUANTILE))
        best, run = [], []
        for w in busy.index:
            if busy.loc[w, "rate"] < GAP_SHARE * working:
                run.append(w)
                if len(run) > len(best):
                    best = list(run)
            else:
                run = []
        if len(best) >= GAP_WEEKS:
            in_gap, out_gap = busy.loc[best], busy.drop(index=best)
            gap = {"start": best[0], "end": best[-1], "weeks": len(best), "working_rate": working,
                   "rate_in": rate(in_gap), "rate_out": rate(out_gap),
                   "scroll_in": scroll(in_gap), "scroll_out": scroll(out_gap),
                   "sessions_in": int(in_gap["sessions"].sum())}

    out = {"landed_sessions": int(landed["sessions"].sum()), "inside_sessions": int(inside["sessions"].sum()),
           "landed_rate": rate(landed), "inside_rate": rate(inside),
           "landed_untagged_pct": (float(landed["untagged_sessions"].sum()) / float(landed["sessions"].sum()) * 100
                                   if landed["sessions"].sum() else float("nan")),
           "landed_eng_s": (float(landed["eng_s_total"].sum()) / float(landed["sessions"].sum())
                            if landed["sessions"].sum() else float("nan")),
           "inside_eng_s": (float(inside["eng_s_total"].sum()) / float(inside["sessions"].sum())
                            if inside["sessions"].sum() else float("nan")),
           "gap": gap}
    if gap and gap["scroll_in"] >= SCROLL_KEPT * gap["scroll_out"]:
        out["verdict"] = "tracking"
    elif gap or (out["inside_sessions"] >= GAP_MIN_SESSIONS and out["inside_rate"] < site_view_pct / 2):
        out["verdict"] = "page"
    elif out["inside_sessions"] >= GAP_MIN_SESSIONS and out["inside_rate"] >= out["landed_rate"] + INSIDE_LIFT_PP:
        out["verdict"] = "entry traffic"
    else:
        out["verdict"] = "unclear"
    return out


# ── Cross-session paths (E10) ─────────────────────────────────────────────────
PATH_ORDER = ("sequential, in one session", "carted in the session, out of order",
              "carted in an earlier session", "no cart seen in the window")


def purchase_path_summary(paths: pd.DataFrame) -> pd.DataFrame:
    """purchase_paths_sql rows → one row per path: purchasing sessions, share,
    revenue, and for cross-session purchases the exact median hours and
    sessions since the last earlier cart. Paths in PATH_ORDER."""
    total = len(paths)
    g = paths.groupby("path")
    out = pd.DataFrame({"sessions": g.size(), "revenue": g["revenue"].sum()}).reindex(list(PATH_ORDER)).fillna(0)
    out["sessions"] = out["sessions"].astype(int)
    out["share_pct"] = out["sessions"] / total * 100 if total else 0.0
    out["median_hours_since_cart"] = g["hours_since_cart"].median().reindex(out.index)
    out["median_sessions_since_cart"] = g["sessions_since_cart"].median().reindex(out.index)
    return out


def recovery_curve(carts: pd.DataFrame, days=(1, 7, 14), checkout=None) -> pd.DataFrame:
    """Share of abandoned carts (cart_recovery_sql rows) whose user bought in a
    later session within N days. Right-censoring: for each N only carts with at
    least N days left in the window count, so late carts cannot look
    unrecovered for lack of time. checkout=True/False keeps only carts that
    did / did not reach checkout. Wilson 95% interval on each rate."""
    from stats_utils import wilson_ci
    d = carts if checkout is None else carts[carts["reached_checkout"] == checkout]
    rows = []
    for n in days:
        h = n * 24
        eligible = d[d["hours_left"] >= h]
        k = int((eligible["hours_to_next_purchase"] <= h).sum())
        m = len(eligible)
        lo, hi = wilson_ci(k, m) if m else (float("nan"), float("nan"))
        rows.append({"days": n, "eligible_carts": m, "recovered": k,
                     "recovered_pct": k / m * 100 if m else float("nan"), "ci_lo": lo, "ci_hi": hi})
    return pd.DataFrame(rows)


# ── Segment spread (channels, countries — E11) ───────────────────────────────
SPREAD_DRIVER_PP = 10      # same practical threshold as the channel verdict
SPREAD_BORDERLINE_PP = 7


def segment_spread(df: pd.DataFrame, segment: str, success: str = "view_item", base: str = "sessions",
                   min_share: float = 1.0, exclude=("(not set)",)) -> dict:
    """Is `success / base` uniform across segments? Only segments with at least
    min_share % of the base (and not in `exclude`, which are not real segments)
    are compared. Returns the rows compared, the best and worst segment, the
    spread in points, a chi-square test with Cramér's V, and a verdict on
    practical size: "driver" (≥ SPREAD_DRIVER_PP), "borderline"
    (≥ SPREAD_BORDERLINE_PP) or "uniform". Significance alone never decides it:
    with large samples almost any gap is significant."""
    from stats_utils import chi_square
    total = float(df[base].sum())
    d = df[~df[segment].isin(exclude)].copy()
    d["share"] = d[base] / total * 100 if total else 0.0
    d = d[d["share"] >= min_share].copy()
    d["rate"] = d[success] / d[base] * 100
    d = d.sort_values(["rate", segment], ascending=[False, True]).reset_index(drop=True)
    if len(d) < 2:
        return {"rows": d, "spread_pp": 0.0, "verdict": "too few segments", "test": None, "best": None, "worst": None}
    spread = float(d["rate"].iloc[0] - d["rate"].iloc[-1])
    verdict = ("driver" if spread >= SPREAD_DRIVER_PP else
               "borderline" if spread >= SPREAD_BORDERLINE_PP else "uniform")
    return {"rows": d, "spread_pp": spread, "verdict": verdict,
            "test": chi_square(d[success], d[base]),
            "best": (d[segment].iloc[0], float(d["rate"].iloc[0])),
            "worst": (d[segment].iloc[-1], float(d["rate"].iloc[-1]))}


# ── Period comparison (E12) ───────────────────────────────────────────────────
FUNNEL_RATES = (   # (label, numerator column, denominator column)
    ("Saw a product", "view_item", "sessions"),
    ("Saw → carted", "add_to_cart", "view_item"),
    ("Cart → checkout", "begin_checkout", "add_to_cart"),
    ("Checkout → purchase", "purchase", "begin_checkout"),
    ("Session → purchase", "purchase", "sessions"),
)


def compare_periods(current: dict, previous: dict) -> pd.DataFrame:
    """Each funnel rate in the current and the previous period, and the change
    in percentage points with Newcombe's 95% interval on the difference.
    current / previous map the STEP_COLUMNS (sessions, view_item, …, purchase)
    to counts. changed = the interval excludes zero (a real change at 95%);
    it says nothing about whether the change matters in practice."""
    from stats_utils import diff_ci
    rows = []
    for label, num, den in FUNNEL_RATES:
        d, lo, hi = diff_ci(int(current[num]), int(current[den]), int(previous[num]), int(previous[den]))
        rows.append({"rate": label,
                     "current_pct": current[num] / current[den] * 100 if current[den] else float("nan"),
                     "previous_pct": previous[num] / previous[den] * 100 if previous[den] else float("nan"),
                     "change_pp": d, "ci_lo": lo, "ci_hi": hi,
                     "changed": bool(lo > 0 or hi < 0)})
    return pd.DataFrame(rows)

