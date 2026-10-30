"""Markdown (FR-RND-3) and basic HTML (FR-RND-4, minus nh3 and the link policy). No PDF."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .content import DocumentContent, ErrorBlock, MarkdownBlock, MetricBlock, TableBlock
from .errors import RenderError

# ---------------------------------------------------------------- markdown


def _block_md(b) -> str:
    if isinstance(b, MarkdownBlock):
        return b.text
    if isinstance(b, TableBlock):
        esc = lambda s: str(s).replace("|", "\\|")  # noqa: E731
        lines = ["| " + " | ".join(map(esc, b.columns)) + " |",
                 "| " + " | ".join("---" for _ in b.columns) + " |"]
        lines += ["| " + " | ".join(map(esc, r)) + " |" for r in b.rows]
        if b.caption:
            lines += ["", f"*{b.caption}*"]
        return "\n".join(lines)
    if isinstance(b, MetricBlock):
        s = f"**{b.label}:** {b.value}" + (f" {b.unit}" if b.unit else "")
        return s + (f" ({b.delta})" if b.delta else "")
    if isinstance(b, ErrorBlock):
        return f"> **Generation failed:** {b.message}"
    raise RenderError(f"unknown block {b!r}")


def blocks_to_markdown(blocks) -> str:
    return "\n\n".join(_block_md(b) for b in blocks)


def to_markdown(content: DocumentContent) -> str:
    out = [f"# {content.title}"]
    if content.preamble:
        out.append(blocks_to_markdown(content.preamble))

    def rec(secs):
        for s in secs:
            out.append("#" * min(6, s.level + 1) + " " + s.title)
            if s.blocks:
                out.append(blocks_to_markdown(s.blocks))
            rec(s.subsections)

    rec(content.sections)
    return "\n\n".join(out) + "\n"


# ---------------------------------------------------------------- html

DEFAULT_TEMPLATE = """\
{%- macro table_block(b) -%}
<table><thead><tr>{% for c in b.columns %}<th>{{ c }}</th>{% endfor %}</tr></thead>
<tbody>{% for r in b.rows %}<tr>{% for v in r %}<td>{{ v }}</td>{% endfor %}</tr>{% endfor %}</tbody>
{% if b.caption %}<caption>{{ b.caption }}</caption>{% endif %}</table>
{%- endmacro -%}
{%- macro metric_block(b) -%}
<div class="metric"><span class="label">{{ b.label }}</span>
<span class="value">{{ b.value }}{% if b.unit %} {{ b.unit }}{% endif %}</span>
{% if b.delta %}<span class="delta">{{ b.delta }}</span>{% endif %}</div>
{%- endmacro -%}
{%- macro error_block(b) -%}<blockquote class="error"><strong>Generation failed:</strong> {{ b.message }}</blockquote>{%- endmacro -%}
{%- macro block(b) -%}
{%- if b.type == "markdown" %}{{ md(b.text) }}{% elif b.type == "table" %}{{ table_block(b) }}
{%- elif b.type == "metric" %}{{ metric_block(b) }}{% else %}{{ error_block(b) }}{% endif -%}
{%- endmacro -%}
{%- macro section_html(s) -%}
<section id="{{ s.id }}"><h{{ [s.level + 1, 6]|min }}>{{ s.title }}</h{{ [s.level + 1, 6]|min }}>
{% for b in s.blocks %}{{ block(b) }}
{% endfor %}{% for sub in s.subsections %}{{ section_html(sub) }}{% endfor %}</section>
{%- endmacro -%}
<!doctype html>
<html lang="{{ metadata.get('lang', 'en') }}" dir="{{ metadata.get('dir', 'ltr') }}">
<head><meta charset="utf-8"><title>{{ document.title }}</title>
<style>
body{font-family:system-ui,"Noto Sans",sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem;line-height:1.55;color:#1d1d1f}
table{border-collapse:collapse;margin:1rem 0}td,th{border:1px solid #ccc;padding:.3rem .6rem}
caption{caption-side:bottom;font-style:italic;font-size:.9em}
.metric{display:inline-block;margin:.3rem 1.2rem .3rem 0}.metric .label{display:block;font-size:.8em;color:#666}
.metric .value{font-size:1.4em;font-weight:600}.error{color:#a00}
nav.toc{font-size:.9em;border-left:3px solid #ddd;padding-left:1rem}
</style></head>
<body>
<h1>{{ document.title }}</h1>
{% for b in document.preamble %}{{ block(b) }}{% endfor %}
<nav class="toc"><ul>{% for s in toc %}<li style="margin-left:{{ (s.level - 1) * 1.2 }}rem"><a href="#{{ s.id }}">{{ s.title }}</a></li>{% endfor %}</ul></nav>
{% for s in sections %}{{ section_html(s) }}{% endfor %}
<footer><small>Generated {{ generated_at }}</small></footer>
</body></html>
"""


def to_html(content: DocumentContent, template=None, **options) -> str:
    try:
        from jinja2.sandbox import SandboxedEnvironment
        from markdown_it import MarkdownIt
        from markupsafe import Markup
    except ImportError as e:  # pragma: no cover
        raise RenderError(f"HTML needs jinja2 and markdown-it-py ({e}).",
                          hint="run through `uv run` in spike/") from e

    md = MarkdownIt("commonmark", {"html": False}).enable("table")  # raw HTML off: spike stand-in for nh3
    env = SandboxedEnvironment(autoescape=True)

    src = DEFAULT_TEMPLATE
    if template not in (None, "default"):
        p = Path(template)
        p = p / "document.html.j2" if p.is_dir() else p
        if not p.exists():
            raise RenderError(f"template {template!r} not found.",
                              hint="the spike accepts 'default', a .j2 file or a directory "
                                   "holding document.html.j2")
        src = p.read_text(encoding="utf-8")
    env.globals["md"] = lambda text: Markup(md.render(text))
    tmpl = env.from_string(src)
    toc = list(content.walk())
    return tmpl.render(document=content, sections=content.sections, toc=toc,
                       metadata=content.metadata, run=content.run, options=options,
                       generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))


# ---------------------------------------------------------------- registry-ish

def render(content: DocumentContent, format: str, *, template=None, **options):
    if format == "md":
        return to_markdown(content)
    if format == "html":
        return to_html(content, template, **options)
    if format == "json":
        return content.to_json(include_provenance=options.get("include_provenance", False))
    if format == "pdf":
        raise RenderError("PDF is not in the spike.", hint="save .html and print it, or wait "
                                                           "for WP-402")
    raise RenderError(f"unknown format {format!r}.", hint="use md, html or json")


def save(content: DocumentContent, path, *, format=None, template=None, **options) -> Path:
    path = Path(path)
    fmt = format or {".md": "md", ".html": "html", ".htm": "html", ".json": "json",
                     ".pdf": "pdf"}.get(path.suffix.lower())
    if fmt is None:
        raise RenderError(f"cannot infer a format from {path.name!r}.", hint="pass format=")
    out = render(content, fmt, template=template, **options)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(out, encoding="utf-8")
    return path
