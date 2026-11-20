"""Make the repo-root modules (config, stats_utils, metrics, md_render) importable.

None of the tests needs BigQuery, credentials or network access.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
