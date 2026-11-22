"""
ci_utils.py
-----------
Helpers for the scheduled regeneration workflow (.github/workflows/
regenerate_dashboard.yml, roadmap E8).

    python ci_utils.py dashboard-changed

compares the freshly generated dashboard.html with the committed one, ignoring
the "Generated YYYY-MM-DD" stamps. It prints changed=true/false (and writes it
to $GITHUB_OUTPUT when run in Actions), so the workflow commits only when the
page really changed — on a static dataset, a weekly run changes nothing else.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

DATE_STAMP = re.compile(r"\b([Gg]enerated) \d{4}-\d{2}-\d{2}\b")


def without_dates(html: str) -> str:
    """The page with its generated-date stamps neutralised."""
    return DATE_STAMP.sub(r"\1 <date>", html)


def meaningful_change(old_html: str, new_html: str) -> bool:
    """True if the pages differ in anything but their generated dates."""
    return without_dates(old_html) != without_dates(new_html)


def dashboard_changed(path: str = "dashboard.html") -> bool:
    committed = subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, text=True, check=True).stdout
    return meaningful_change(committed, Path(path).read_text(encoding="utf-8"))


def main(argv) -> int:
    if argv[1:] != ["dashboard-changed"]:
        print("usage: python ci_utils.py dashboard-changed", file=sys.stderr)
        return 2
    changed = dashboard_changed()
    line = f"changed={'true' if changed else 'false'}"
    print(line)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
