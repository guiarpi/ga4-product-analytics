"""ci_utils.py — the scheduled workflow commits only on a real change."""
from ci_utils import meaningful_change, without_dates

PAGE = '<span>Generated 2026-11-22</span><p>0.77% ended in a purchase</p><p class="t">generated 2026-11-22</p>'


def test_a_new_date_alone_is_not_a_change():
    assert not meaningful_change(PAGE, PAGE.replace("2026-11-22", "2026-12-07"))


def test_a_changed_number_is_a_change():
    assert meaningful_change(PAGE, PAGE.replace("0.77%", "0.78%"))


def test_only_generated_stamps_are_ignored():
    # A date inside the analysis (e.g. a tracking gap) must still count.
    a = PAGE + "<p>silent from 2020-11-30</p>"
    assert meaningful_change(a, a.replace("2020-11-30", "2020-12-07"))
    assert "Generated <date>" in without_dates(PAGE) and "generated <date>" in without_dates(PAGE)
