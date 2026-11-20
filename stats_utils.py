"""
stats_utils.py
--------------
Small, dependency-light statistics shared by ga4_funnel_analysis.ipynb and
generate_dashboard.py, so both report the same intervals and tests.

- wilson_ci: Wilson score interval for a binomial proportion. Preferred over the
  normal approximation because it stays inside [0, 1] and behaves well for
  small counts and rates near 0 — e.g. a 0.8% purchase rate.
- chi_square: Pearson chi-square test of independence on a groups × outcome
  table of [successes, failures] rows (scipy.stats.chi2_contingency).
"""

from math import sqrt

from scipy.stats import chi2_contingency

Z95 = 1.959963984540054   # two-sided 95%


def wilson_ci(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for successes / n, returned as percentages."""
    if n <= 0:
        return float("nan"), float("nan")
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (centre - half) * 100, (centre + half) * 100


def chi_square(successes, totals) -> dict:
    """Chi-square test that the success rate is the same in every group.

    successes, totals: per-group counts (same order). Returns chi2, dof, p and
    Cramér's V (effect size: 0 = no association, 1 = perfect), because with
    hundreds of thousands of sessions almost any difference is "significant" —
    the effect size says whether it is big enough to matter.
    """
    table = [[int(s), int(t) - int(s)] for s, t in zip(successes, totals)]
    chi2, p, dof, _ = chi2_contingency(table, correction=False)
    n = sum(int(t) for t in totals)
    cramers_v = sqrt(chi2 / n) if n else float("nan")   # min(rows, cols) - 1 = 1
    return {"chi2": float(chi2), "dof": int(dof), "p": float(p), "cramers_v": float(cramers_v)}


def format_p(p: float) -> str:
    """Readable p-value: exact above 0.001, bounded below it."""
    return f"p = {p:.3f}" if p >= 0.001 else "p < 0.001"
