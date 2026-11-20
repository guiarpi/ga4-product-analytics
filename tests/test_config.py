"""config.py validates the dataset and dates at import time, before any query runs."""
import importlib
import sys

import pytest

ENV = ("GA4_PROJECT_ID", "GA4_DATASET", "GA4_DATE_START", "GA4_DATE_END")


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
