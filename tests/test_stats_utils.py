"""Wilson intervals and chi-square against values worked out by hand."""
import math

import pytest

from stats_utils import chi_square, format_p, wilson_ci


def test_wilson_half_proportion():
    # p = 0.5, n = 10: the textbook Wilson 95% interval is 23.66%–76.34%.
    lo, hi = wilson_ci(5, 10)
    assert lo == pytest.approx(23.66, abs=0.01)
    assert hi == pytest.approx(76.34, abs=0.01)


def test_wilson_zero_successes_stays_inside_0_100():
    # Unlike the normal approximation, Wilson gives a usable upper bound at 0/n.
    lo, hi = wilson_ci(0, 10)
    assert lo == pytest.approx(0.0, abs=1e-9)
    assert hi == pytest.approx(27.75, abs=0.01)


def test_wilson_is_symmetric_around_complement():
    lo, hi = wilson_ci(3, 40)
    clo, chi = wilson_ci(37, 40)
    assert lo == pytest.approx(100 - chi)
    assert hi == pytest.approx(100 - clo)


def test_wilson_narrows_with_sample_size():
    small = wilson_ci(8, 1_000)
    large = wilson_ci(800, 100_000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_wilson_empty_sample_is_nan():
    assert all(math.isnan(x) for x in wilson_ci(0, 0))


def test_chi_square_two_by_two():
    # 10/100 vs 20/100. By hand: chi2 = 200·(10·80 − 90·20)² / (30·170·100·100) = 3.9216.
    r = chi_square([10, 20], [100, 100])
    assert r["chi2"] == pytest.approx(3.9216, abs=1e-4)
    assert r["dof"] == 1
    assert r["p"] == pytest.approx(0.0477, abs=1e-4)
    assert r["cramers_v"] == pytest.approx(math.sqrt(3.9216 / 200), abs=1e-4)


def test_chi_square_identical_rates_is_not_significant():
    r = chi_square([50, 100, 25], [1_000, 2_000, 500])
    assert r["chi2"] == pytest.approx(0.0, abs=1e-12)
    assert r["p"] == pytest.approx(1.0)
    assert r["dof"] == 2


@pytest.mark.parametrize("p, text", [(0.2587, "p = 0.259"), (0.001, "p = 0.001"), (0.0009, "p < 0.001"), (0.0, "p < 0.001")])
def test_format_p(p, text):
    assert format_p(p) == text
