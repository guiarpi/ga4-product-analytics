"""config.py validates the dataset and dates at import time, before any query runs."""
import importlib
import sys

import pytest

ENV = ("GA4_PROJECT_ID", "GA4_DATASET", "GA4_DATE_START", "GA4_DATE_END",
       "GA4_MAX_BYTES_BILLED", "GA4_COMPARE_START", "GA4_COMPARE_END")


def load_config(monkeypatch, **env):
    """Import config.py afresh with exactly these environment variables set."""
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    # A dummy project keeps the import from reading the local gcloud config.
    monkeypatch.setenv("GA4_PROJECT_ID", "test-project")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("config", None)
    return importlib.import_module("config")


def test_defaults_are_the_public_sample(monkeypatch):
    cfg = load_config(monkeypatch)
    assert cfg.IS_PUBLIC_SAMPLE
    assert (cfg.DATE_START, cfg.DATE_END) == ("20201101", "20210131")
    assert cfg.PERIOD_LABEL == "Nov 2020 – Jan 2021"


def test_custom_dataset_and_window(monkeypatch):
    cfg = load_config(monkeypatch, GA4_DATASET="my-project.analytics_123456",
                      GA4_DATE_START="20240101", GA4_DATE_END="20240331")
    assert not cfg.IS_PUBLIC_SAMPLE
    assert cfg.DATASET_LABEL == "my-project.analytics_123456"
    assert cfg.PERIOD_LABEL == "Jan 2024 – Mar 2024"


@pytest.mark.parametrize("dataset", [
    "analytics_123456",                       # no project part
    "my project.analytics_123456",            # space
    "my-project.analytics; DROP TABLE x",     # SQL injection attempt
    "my-project.analytics_123456`",           # backtick would break out of the identifier
])
def test_bad_dataset_is_rejected(monkeypatch, dataset):
    with pytest.raises(ValueError, match="GA4_DATASET"):
        load_config(monkeypatch, GA4_DATASET=dataset)


@pytest.mark.parametrize("var, value", [
    ("GA4_DATE_START", "2021-01-01"),   # wrong format
    ("GA4_DATE_START", "20210231"),     # not a real date
    ("GA4_DATE_END", "2021013"),        # too short
    ("GA4_DATE_END", "20210131' OR '1'='1"),
])
def test_bad_date_is_rejected(monkeypatch, var, value):
    with pytest.raises(ValueError, match=var):
        load_config(monkeypatch, **{var: value})


def test_start_after_end_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="after"):
        load_config(monkeypatch, GA4_DATE_START="20210201", GA4_DATE_END="20210131")


def test_describe_leaves_out_the_project_by_default(monkeypatch):
    cfg = load_config(monkeypatch)

    class Client:
        project = "secret-project"

    assert "secret-project" not in cfg.describe(Client())
    assert "secret-project" in cfg.describe(Client(), include_project=True)


# ── Cost cap ──────────────────────────────────────────────────────────────────

def test_cost_cap_default_and_override(monkeypatch):
    assert load_config(monkeypatch).MAX_BYTES_BILLED == 5 * 10**9
    assert load_config(monkeypatch, GA4_MAX_BYTES_BILLED="20GB").MAX_BYTES_BILLED == 20 * 10**9


@pytest.mark.parametrize("value, expected", [
    ("5GB", 5 * 10**9), ("500 MB", 500 * 10**6), ("1.5tb", 15 * 10**11),
    ("2G", 2 * 10**9), ("123456789", 123456789),
])
def test_parse_bytes(monkeypatch, value, expected):
    assert load_config(monkeypatch).parse_bytes("X", value) == expected


@pytest.mark.parametrize("value", ["", "five GB", "5PB", "-1GB", "1MB", "0"])
def test_bad_cost_cap_is_rejected(monkeypatch, value):
    with pytest.raises(ValueError, match="GA4_MAX_BYTES_BILLED"):
        load_config(monkeypatch, GA4_MAX_BYTES_BILLED=value)


class _FakeJob:
    def __init__(self, exc=None):
        self.exc = exc
    def to_dataframe(self):
        if self.exc:
            raise self.exc
        return "frame"


class _FakeClient:
    """Records the job config of each query instead of calling BigQuery."""
    def __init__(self, exc=None):
        self.exc, self.configs = exc, []
    def query(self, sql, job_config=None):
        self.configs.append(job_config)
        return _FakeJob(self.exc)


def test_run_query_always_applies_the_cap(monkeypatch):
    cfg = load_config(monkeypatch, GA4_MAX_BYTES_BILLED="3GB")
    client = _FakeClient()
    assert cfg.run_query(client, "SELECT 1") == "frame"
    assert client.configs[0].maximum_bytes_billed == 3 * 10**9


def test_run_query_explains_a_refused_query(monkeypatch):
    from google.api_core import exceptions as gexc
    cfg = load_config(monkeypatch)
    refused = gexc.BadRequest("Query exceeded limit for bytes billed: 5000000000.",
                              errors=[{"reason": "bytesBilledLimitExceeded"}])
    with pytest.raises(RuntimeError, match="Nothing was billed.*GA4_MAX_BYTES_BILLED"):
        cfg.run_query(_FakeClient(refused), "SELECT 1")
    other = gexc.BadRequest("Syntax error", errors=[{"reason": "invalidQuery"}])
    with pytest.raises(gexc.BadRequest):          # other errors pass through unchanged
        cfg.run_query(_FakeClient(other), "SELECT 1")


# ── Period comparison (E12) ───────────────────────────────────────────────────

def test_public_sample_compares_january_with_november(monkeypatch):
    cfg = load_config(monkeypatch)
    assert cfg.HAS_COMPARISON
    assert (cfg.CURRENT_START, cfg.CURRENT_END) == ("20210101", "20210131")
    assert (cfg.COMPARE_START, cfg.COMPARE_END) == ("20201101", "20201130")
    assert (cfg.CURRENT_LABEL, cfg.COMPARE_LABEL) == ("Jan 2021", "Nov 2020")


def test_other_data_has_no_comparison_unless_set(monkeypatch):
    cfg = load_config(monkeypatch, GA4_DATASET="my-project.analytics_1", GA4_DATE_START="20240101", GA4_DATE_END="20240331")
    assert not cfg.HAS_COMPARISON and cfg.COMPARE_LABEL is None
    cfg = load_config(monkeypatch, GA4_DATASET="my-project.analytics_1", GA4_DATE_START="20240101", GA4_DATE_END="20240331",
                      GA4_COMPARE_START="20231001", GA4_COMPARE_END="20231231")
    assert cfg.HAS_COMPARISON and cfg.CURRENT_START == "20240301" and cfg.COMPARE_LABEL == "Oct 2023 – Dec 2023"


@pytest.mark.parametrize("env, message", [
    ({"GA4_COMPARE_START": "20210110", "GA4_COMPARE_END": "20210120"}, "overlaps"),
    ({"GA4_COMPARE_START": "20201101"}, "both"),
    ({"GA4_COMPARE_START": "20201130", "GA4_COMPARE_END": "20201101"}, "after"),
    ({"GA4_COMPARE_START": "2020-11-01", "GA4_COMPARE_END": "20201130"}, "GA4_COMPARE_START"),
])
def test_bad_comparison_window_is_rejected(monkeypatch, env, message):
    with pytest.raises(ValueError, match=message):
        load_config(monkeypatch, **env)

