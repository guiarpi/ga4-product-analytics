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
    export GA4_MAX_BYTES_BILLED="20GB"                  # per-query cost cap (default 5GB)

GA4_PROJECT_ID falls back to GOOGLE_CLOUD_PROJECT, then to the default project
of your gcloud login (Application Default Credentials). Leave the dataset and
dates unset to run against Google's public GA4 sample (Nov 2020 – Jan 2021).

Every query runs with a bytes-billed cap (GA4_MAX_BYTES_BILLED): BigQuery
refuses, at no charge, any query that would bill more. On the public sample the
largest query scans about 1.6 GB, so the 5 GB default leaves room without
letting a mistyped dataset or window run up a bill.
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
MAX_BYTES_BILLED_TEXT = os.environ.get("GA4_MAX_BYTES_BILLED", "5GB").strip()


# ── Validation ────────────────────────────────────────────────────────────────
# DATASET and the dates are interpolated straight into SQL, so anything that
# is not a plain dataset path or a real date is rejected before a query runs.
if not re.fullmatch(r"[a-z0-9][a-z0-9.:-]*[a-z0-9]\.[A-Za-z0-9_]+", DATASET):
    raise ValueError(f"GA4_DATASET must look like 'project.dataset', got {DATASET!r}")

def _parse(name: str, value: str) -> _dt.date:
    # strptime alone is lenient: "%Y%m%d" accepts "2021013" as 3 Jan 2021, so a
    # typo would silently run the wrong window. Require exactly eight digits.
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError(f"{name} must be a YYYYMMDD date, got {value!r}")
    try:
        return _dt.datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise ValueError(f"{name} must be a YYYYMMDD date, got {value!r}") from None

_UNITS = {"": 1, "B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}

def parse_bytes(name: str, value: str) -> int:
    """'5GB', '500 MB', '1.5TB' or a plain byte count → bytes (decimal units)."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGT]?B?)", value.strip().upper())
    unit = m.group(2).rstrip("B") + "B" if m and m.group(2) else ""   # "G" → "GB"
    if not m or unit not in _UNITS:
        raise ValueError(f"{name} must be a size like '5GB' or '500MB', got {value!r}")
    n = int(float(m.group(1)) * _UNITS[unit])
    if n < 10**7:   # BigQuery bills at least 10 MB per query, so a lower cap blocks everything
        raise ValueError(f"{name} must be at least 10MB, got {value!r}")
    return n

def format_bytes(n: float) -> str:
    for unit in ("TB", "GB", "MB"):
        if n >= _UNITS[unit]:
            return f"{n / _UNITS[unit]:.1f} {unit}"
    return f"{n / _UNITS['MB']:.2f} MB"

START = _parse("GA4_DATE_START", DATE_START)
END   = _parse("GA4_DATE_END", DATE_END)
if START > END:
    raise ValueError(f"GA4_DATE_START ({DATE_START}) is after GA4_DATE_END ({DATE_END})")
MAX_BYTES_BILLED = parse_bytes("GA4_MAX_BYTES_BILLED", MAX_BYTES_BILLED_TEXT)


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


# ── Running queries under the cost cap ───────────────────────────────────────
def run_query(client, sql: str):
    """Run sql with the MAX_BYTES_BILLED cap and return a DataFrame. A query over
    the cap is refused by BigQuery before it runs (nothing is billed); the error
    is re-raised with what to change."""
    from google.api_core import exceptions as gexc
    from google.cloud import bigquery
    cfg = bigquery.QueryJobConfig(maximum_bytes_billed=MAX_BYTES_BILLED)
    try:
        return client.query(sql, job_config=cfg).to_dataframe()
    except gexc.GoogleAPICallError as exc:
        reasons = {e.get("reason") for e in (getattr(exc, "errors", None) or [])}
        if "bytesBilledLimitExceeded" in reasons or "bytesBilledLimitExceeded" in str(exc):
            raise RuntimeError(
                f"Query refused: it would bill more than the {format_bytes(MAX_BYTES_BILLED)} cap "
                f"(GA4_MAX_BYTES_BILLED). Nothing was billed. Check GA4_DATASET and the date "
                f"window, or raise the cap, e.g.  export GA4_MAX_BYTES_BILLED=\"20GB\""
            ) from exc
        raise


def estimate_bytes(client, sql: str) -> int:
    """Bytes the query would process, from a free dry run (nothing is billed)."""
    from google.cloud import bigquery
    cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    return client.query(sql, job_config=cfg).total_bytes_processed
