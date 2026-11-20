"""
md_render.py
------------
A small, dependency-free markdown → HTML converter for the AI product brief,
which generate_dashboard.py embeds in the page. It covers what the brief uses:
headings, horizontal rules, paragraphs, nested ordered/unordered lists, bold,
italic, inline code and links.

The input is LLM output, so it is HTML-escaped before any markup is added.
"""

import html as _html
import re


def md_inline(text: str) -> str:
    """Inline markdown → HTML. Input is LLM output, so it is escaped first."""
    t = _html.escape(text, quote=False)
    codes = []
    def _keep_code(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"
    t = re.sub(r"`([^`]+)`", _keep_code, t)                 # protect code spans
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
               r'<a href="\2" target="_blank" rel="noopener">\1</a>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    # Single-asterisk emphasis only when the asterisks hug the text, so a list
    # marker or a stray "*" is never paired with the opening of "*So what*".
    t = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", r"<em>\1</em>", t)
    t = re.sub("\x00(\\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", t)
    return t


def md_to_html(text: str) -> str:
    """Block markdown → HTML: headings, rules, paragraphs and nested lists."""
    list_re = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
    out, stack = [], []          # stack entries: [indent, tag, li_open]

    def close_to(indent):
        while stack and stack[-1][0] > indent:
            level = stack.pop()
            if level[2]:
                out.append("</li>")
            out.append(f"</{level[1]}>")

    for raw in text.splitlines():
        m = list_re.match(raw)
        if m:
            indent = len(m.group(1).expandtabs(4))
            tag = "ol" if m.group(2)[0].isdigit() else "ul"
            close_to(indent)
            if stack and stack[-1][0] == indent:
                if stack[-1][1] != tag:              # list type changed at this level
                    close_to(indent - 1)
                elif stack[-1][2]:
                    out.append("</li>")
                    stack[-1][2] = False
            if not stack or stack[-1][0] < indent:
                out.append(f"<{tag}>")
                stack.append([indent, tag, False])
            out.append(f"<li>{md_inline(m.group(3))}")
            stack[-1][2] = True
            continue
        if not raw.strip():
            continue                                 # blank lines keep lists open
        if stack and raw[:1].isspace():              # wrapped continuation of an item
            out.append(" " + md_inline(raw.strip()))
            continue
        close_to(-1)
        line = raw.strip()
        if line.startswith("### "):  out.append(f"<h4>{md_inline(line[4:])}</h4>")
        elif line.startswith("## "): out.append(f"<h3>{md_inline(line[3:])}</h3>")
        elif line.startswith("# "):  out.append(f"<h2>{md_inline(line[2:])}</h2>")
        elif re.fullmatch(r"-{3,}|\*{3,}", line): out.append("<hr>")
        else:                        out.append(f"<p>{md_inline(line)}</p>")
    close_to(-1)
    return "\n".join(out)
