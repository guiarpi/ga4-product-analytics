"""
metrics.py
----------
Pure, BigQuery-free calculations shared by ga4_funnel_analysis.ipynb and
generate_dashboard.py, kept here so they can be unit-tested (tests/).

- site_average:   pooled rate across categories (volume-weighted), in %.
- flag_categories: Wilson 95% interval per category, a below / above / neutral
  flag against the site average, and carts at stake for flagged categories.
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
