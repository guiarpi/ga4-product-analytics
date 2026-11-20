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
              & ~pages["landing_page"].str.contains(NON_SHOPPING_PAGES, regex=True)].copy()
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

