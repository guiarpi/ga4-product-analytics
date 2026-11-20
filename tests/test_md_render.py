"""md_render.py: the brief's markdown → HTML, including the stray-asterisk bug."""
import re

from md_render import md_inline, md_to_html


def tags_balanced(html, tag):
    return len(re.findall(f"<{tag}>", html)) == len(re.findall(f"</{tag}>", html))


def test_stray_asterisk_in_indented_sub_bullet():
    # The bug fixed in Phase 2: "   * *So what*:" rendered as "So what*:".
    html = md_to_html("1. **Finding**: text\n   * *So what*: act on it")
    assert "<em>So what</em>: act on it" in html
    assert "*" not in re.sub(r"<[^>]+>", "", html)


def test_nested_lists_are_wrapped_and_balanced():
    md = ("1. First\n   - **So what:** a\n   - **Why:** b\n2. Second\n   - c\n\n"
          "## Next\n- one\n- two")
    html = md_to_html(md)
    assert html.count("<ol>") == 1 and html.count("<ul>") == 3
    for tag in ("ol", "ul", "li"):
        assert tags_balanced(html, tag)
    assert html.index("<h3>Next</h3>") > html.index("</ol>")


def test_llm_output_is_escaped():
    html = md_to_html("Watch <script>alert(1)</script> & **bold**")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "&amp;" in html
    assert "<strong>bold</strong>" in html


def test_inline_code_protects_asterisks_and_underscores():
    assert md_inline("`view_item*count*`") == "<code>view_item*count*</code>"


def test_links_bold_and_italic():
    out = md_inline("See [GA4](https://example.com/x) — **bold** and *italic*")
    assert '<a href="https://example.com/x" target="_blank" rel="noopener">GA4</a>' in out
    assert "<strong>bold</strong>" in out and "<em>italic</em>" in out


def test_lone_asterisk_and_multiplication_are_left_alone():
    assert md_inline("2 * 3 = 6") == "2 * 3 = 6"


def test_headings_rules_and_paragraphs():
    html = md_to_html("# Title\n## Section\n### Sub\n---\nPlain text.")
    assert html.split("\n") == ["<h2>Title</h2>", "<h3>Section</h3>", "<h4>Sub</h4>", "<hr>", "<p>Plain text.</p>"]
