"""Category label normalisation.

The real normalisation runs in BigQuery SQL (in the category query), because it
has to happen before the per-session grouping — merging rows afterwards in
pandas would double-count a session that saw both labels. BigQuery can't run
here, so this file tests a line-for-line Python MIRROR of that expression on
the label shapes seen in the data. It documents the intended behaviour; it
does not execute the SQL itself.

    IF(raw = '(not set)', raw,
       COALESCE(NULLIF(TRIM(REPLACE(
         ARRAY_REVERSE(SPLIT(TRIM(REPLACE(raw, ' / ', ' | '), '/'), '/'))[SAFE_OFFSET(0)],
         ' | ', ' / ')), ''), '(not set)'))
"""
import pytest


def normalise(raw: str) -> str:
    if raw == "(not set)":
        return raw
    protected = raw.replace(" / ", " | ")          # " / " is part of a name, not a separator
    last = protected.strip("/").split("/")[-1]      # TRIM(x, '/'), SPLIT, ARRAY_REVERSE[0]
    name = last.replace(" | ", " / ").strip()       # restore " / ", TRIM whitespace
    return name or "(not set)"                      # NULLIF('') → COALESCE


@pytest.mark.parametrize("raw, expected", [
    ("Bags", "Bags"),                                         # flat label
    ("Lifestyle/Bags/", "Bags"),                              # path, trailing slash
    ("Home/Shop by Brand/YouTube/", "YouTube"),               # deeper path
    ("Home/Apparel/Men's / Unisex/", "Men's / Unisex"),       # " / " inside a name survives
    ("Men's / Unisex", "Men's / Unisex"),
    ("Home/Apparel/", "Apparel"),                             # parent node
    ("/", "(not set)"),                                       # nothing left
    ("(not set)", "(not set)"),
])
def test_normalise(raw, expected):
    assert normalise(raw) == expected


def test_path_and_flat_labels_merge():
    assert normalise("Lifestyle/Bags/") == normalise("Bags")
