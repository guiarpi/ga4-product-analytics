"""Every dashboard template parses, and only uses the filters the generator provides."""
from pathlib import Path

import jinja2
import jinja2.meta
import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def env():
    e = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), autoescape=True,
                           undefined=jinja2.StrictUndefined)
    e.filters["fmt"] = lambda value, spec: format(value, spec)
    e.filters["pval"] = lambda p: "p"
    return e


NAMES = sorted(p.relative_to(TEMPLATES).as_posix() for p in TEMPLATES.rglob("*") if p.suffix in (".j2", ".css"))


@pytest.mark.parametrize("name", NAMES)
def test_template_parses(name):
    e = env()
    e.parse(e.loader.get_source(e, name)[0])        # raises TemplateSyntaxError on bad syntax


def test_every_include_exists():
    e = env()
    for name in NAMES:
        for ref in jinja2.meta.find_referenced_templates(e.parse(e.loader.get_source(e, name)[0])):
            assert ref in NAMES, f"{name} includes missing template {ref}"


def test_fmt_filter_matches_python_format():
    e = env()
    out = e.from_string("{{ 354857|fmt(',') }} {{ 0.76679|fmt('.2f') }} {{ -0.33|fmt('+.2f') }}").render()
    assert out == "354,857 0.77 -0.33"


def test_values_are_escaped_unless_marked_safe():
    e = env()
    assert e.from_string("{{ x }}").render(x="<Other>") == "&lt;Other&gt;"
    assert e.from_string("{{ x|safe }}").render(x="<b>ok</b>") == "<b>ok</b>"
