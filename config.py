"""
config.py
---------
Single source of run settings for ga4_funnel_analysis.ipynb and
generate_dashboard.py. Nothing here needs editing: every value can be
overridden with an environment variable, so pointing the analysis at another
GA4 export is a shell setting, not a code change.

    export GA4_PROJECT_ID="your-gcp-project"            # runs (and pays for) the queries
    export GA4_DATASET="your-project.analytics_123456"  # GA4 BigQuery export dataset
    export GA4_DATE_START="20240101"                    # YYYYMMDD, inclusive
    export GA4_DATE_END="20240331"                      # YYYYMMDD, inclusive

GA4_PROJECT_ID falls back to GOOGLE_CLOUD_PROJECT, then to the default project
of your gcloud login (Application Default Credentials). Leave the dataset and
dates unset to run against Google's public GA4 sample (Nov 2020 – Jan 2021).
"""

import datetime as _dt
import os
import re

PUBLIC_SAMPLE_DATASET = "bigquery-public-data.ga4_obfuscated_sample_ecommerce"
PUBLIC_SAMPLE_URL = ("https://console.cloud.google.com/marketplace/product/"
                     "google-analytics-public-data/ga4_obfuscated_sample_ecommerce")

def _gcloud_default_project():
    """Project from the active gcloud configuration file, read directly.

    google-auth finds the gcloud default project by running the `gcloud` CLI,
    which fails when it is not on PATH — e.g. VS Code launched from the Dock
    or the debugger, neither of which loads ~/.zshrc. Reading the config file
    works in every environment.
    """
    import configparser
    cfg_dir = os.environ.get("CLOUDSDK_CONFIG") or (
        os.path.join(os.environ.get("APPDATA", ""), "gcloud") if os.name == "nt"
        else os.path.expanduser("~/.config/gcloud"))
    try:
        with open(os.path.join(cfg_dir, "active_config"), encoding="utf-8") as f:
            active = f.read().strip() or "default"
    except OSError:
        active = "default"
    parser = configparser.ConfigParser()
    parser.read(os.path.join(cfg_dir, "configurations", f"config_{active}"))
    return parser.get("core", "project", fallback=None) or None


PROJECT_ID = (os.environ.get("GA4_PROJECT_ID")
              or os.environ.get("GOOGLE_CLOUD_PROJECT")
              or os.environ.get("CLOUDSDK_CORE_PROJECT")
              or _gcloud_default_project()
              or None)                       # None → let google-auth try
DATASET    = os.environ.get("GA4_DATASET", PUBLIC_SAMPLE_DATASET).strip()
DATE_START = os.environ.get("GA4_DATE_START", "20201101").strip()
DATE_END   = os.environ.get("GA4_DATE_END",   "20210131").strip()


# ── Validation ────────────────────────────────────────────────────────────────
# DATASET and the dates are interpolated straight into SQL, so anything that
# is not a plain dataset path or a real date is rejected before a query runs.
if not re.fullmatch(r"[a-z0-9][a-z0-9.:-]*[a-z0-9]\.[A-Za-z0-9_]+", DATASET):
    raise ValueError(f"GA4_DATASET must look like 'project.dataset', got {DATASET!r}")

def _parse(name: str, value: str) -> _dt.date:
    try:
        return _dt.datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise ValueError(f"{name} must be a YYYYMMDD date, got {value!r}") from None

START = _parse("GA4_DATE_START", DATE_START)
END   = _parse("GA4_DATE_END", DATE_END)
if START > END:
    raise ValueError(f"GA4_DATE_START ({DATE_START}) is after GA4_DATE_END ({DATE_END})")


# ── Labels used in charts, the dashboard and the AI brief ─────────────────────
IS_PUBLIC_SAMPLE = DATASET == PUBLIC_SAMPLE_DATASET
PERIOD_LABEL  = f"{START:%b %Y} – {END:%b %Y}"
DATASET_LABEL = "GA4 public e-commerce sample" if IS_PUBLIC_SAMPLE else DATASET
DATASET_MD    = (f"[{DATASET_LABEL}]({PUBLIC_SAMPLE_URL})" if IS_PUBLIC_SAMPLE
                 else f"`{DATASET}`")


def make_client():
    """BigQuery client on PROJECT_ID, with a readable error if no project is set."""
    from google.cloud import bigquery
    try:
        client = bigquery.Client(project=PROJECT_ID)
    except Exception as exc:  # google.auth raises several types here
        raise RuntimeError(
            "No Google Cloud project found. Set one with\n"
            '  export GA4_PROJECT_ID="your-gcp-project"\n'
            "or `gcloud config set project your-gcp-project`, and log in with\n"
            "  gcloud auth application-default login\n"
            "(VS Code opened from the Dock does not load ~/.zshrc — launch it\n"
            "with `code .` from a terminal, or set the project with gcloud.)"
        ) from exc
    if not client.project:
        raise RuntimeError('No Google Cloud project found — export GA4_PROJECT_ID="your-gcp-project"')
    return client


def describe(client, include_project: bool = False) -> str:
    """One-line run summary. The project is left out by default because this line
    ends up in saved notebook output; pass include_project=True for console-only use."""
    project = f"project: {client.project}  |  " if include_project else ""
    return (f"{project}dataset: {DATASET}  |  "
            f"window: {DATE_START} → {DATE_END} ({PERIOD_LABEL})")
