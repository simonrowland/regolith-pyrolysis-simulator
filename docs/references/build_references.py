#!/usr/bin/env python3
"""Validate the reference registry and render static HTML."""

from __future__ import annotations

import argparse
import html
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
REF_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = REF_DIR / "references.yaml"
TOKEN_RE = re.compile(r"\bREF-\d{3,}\b")
REF_ID_RE = re.compile(r"^REF-\d{3,}$")
TOPIC_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SUPERSEDED_RE = re.compile(r"^superseded_by:(REF-\d{3,})$")

ROLES = {
    "DATA",
    "MODEL",
    "MEASUREMENT",
    "THEORY",
    "REVIEW",
    "OBSERVATION",
    "TEXTBOOK",
}
STATUSES = {"current", "disputed", "preliminary"}
SCAN_PATTERNS = [
    "data/**/*.yaml",
    "simulator/**/*.py",
    "engines/**/*.py",
    "docs/**/*.md",
]


@dataclass(frozen=True)
class CitationUse:
    file: str
    line: int
    context: str


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    references = data.get("references")
    if not isinstance(references, dict):
        raise ValueError("references.yaml must contain a mapping named 'references'")
    return references


def validate_registry(references: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    required = {"authors", "title", "year", "role", "topic", "status", "doi", "url", "found", "pull_quotes"}

    for ref_id, entry in sorted(references.items()):
        if not REF_ID_RE.match(str(ref_id)):
            errors.append(f"{ref_id}: ID must match REF-NNN")
        if not isinstance(entry, dict):
            errors.append(f"{ref_id}: entry must be a mapping")
            continue

        missing = sorted(required - set(entry))
        if missing:
            errors.append(f"{ref_id}: missing required fields: {', '.join(missing)}")

        forbidden = sorted(set(entry) & {"cited_by"})
        if forbidden:
            errors.append(f"{ref_id}: cited_by is generated; do not hand-edit it")

        for field in ("authors", "title", "found"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{ref_id}: {field} must be a non-empty string")
            elif re.search(r"\b(TBD|TODO|unknown|placeholder)\b", value, re.I):
                errors.append(f"{ref_id}: {field} looks like a placeholder")

        year = entry.get("year")
        if not isinstance(year, int) or year < 1500 or year > 2100:
            errors.append(f"{ref_id}: year must be a plausible integer")

        role = entry.get("role")
        if role not in ROLES:
            errors.append(f"{ref_id}: role must be one of {', '.join(sorted(ROLES))}")

        topic = entry.get("topic")
        if not isinstance(topic, str) or not TOPIC_RE.match(topic):
            errors.append(f"{ref_id}: topic must be kebab-case")

        doi = entry.get("doi")
        url = entry.get("url")
        if doi is None:
            doi = ""
        if url is None:
            url = ""
        if not isinstance(doi, str) or not isinstance(url, str):
            errors.append(f"{ref_id}: doi and url must be strings")
        else:
            if doi and not doi.startswith("10."):
                errors.append(f"{ref_id}: doi must start with 10.")
            if url and not re.match(r"^https?://", url):
                errors.append(f"{ref_id}: url must start with http:// or https://")
            if not doi and not url:
                warnings.append(f"{ref_id}: doi and url are both blank; verify this is genuinely unavailable")

        status = entry.get("status")
        if status not in STATUSES:
            match = SUPERSEDED_RE.match(str(status))
            if not match:
                errors.append(f"{ref_id}: status must be current, disputed, preliminary, or superseded_by:REF-NNN")
            elif match.group(1) not in references:
                errors.append(f"{ref_id}: supersession target {match.group(1)} is missing")

        pull_quotes = entry.get("pull_quotes")
        if not isinstance(pull_quotes, list):
            errors.append(f"{ref_id}: pull_quotes must be a list")
        elif pull_quotes:
            for index, item in enumerate(pull_quotes, start=1):
                if not isinstance(item, dict):
                    errors.append(f"{ref_id}: pull_quotes[{index}] must be a mapping")
                    continue
                for field in ("quote", "page", "grounds"):
                    value = item.get(field)
                    if not isinstance(value, str) or not value.strip():
                        errors.append(f"{ref_id}: pull_quotes[{index}].{field} must be non-empty")
        elif entry.get("needs_quote") is not True:
            errors.append(f"{ref_id}: empty pull_quotes requires needs_quote: true")

    return errors, warnings


def scan_files(root: Path = ROOT) -> dict[str, list[CitationUse]]:
    cited_by: dict[str, list[CitationUse]] = defaultdict(list)
    seen_files: set[Path] = set()

    for pattern in SCAN_PATTERNS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file() or path in seen_files:
                continue
            if _is_generated_reference_doc(path):
                continue
            seen_files.add(path)
            rel = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            for index, line in enumerate(lines, start=1):
                refs = sorted(set(TOKEN_RE.findall(line)))
                if not refs:
                    continue
                context = line_context(path, lines, index)
                for ref in refs:
                    cited_by[ref].append(CitationUse(file=rel, line=index, context=context))

    for uses in cited_by.values():
        uses.sort(key=lambda use: (use.file, use.line, use.context))
    return dict(sorted(cited_by.items()))


def _is_generated_reference_doc(path: Path) -> bool:
    try:
        rel = path.relative_to(REF_DIR)
    except ValueError:
        return False
    return rel.parts[0] in {"README.md", "pdfs"} or rel.name.endswith(".html") or rel.parts[0] == "topics"


def line_context(path: Path, lines: list[str], line_number: int) -> str:
    suffix = path.suffix.lower()
    line = lines[line_number - 1].strip()
    if suffix in {".yaml", ".yml"}:
        prefix = yaml_path_context(lines[:line_number])
        return f"{prefix}: {line}" if prefix else line
    if suffix == ".py":
        scope = python_scope_context(lines[:line_number])
        return f"{scope}: {line}" if scope else line
    if suffix == ".md":
        heading = markdown_heading_context(lines[:line_number])
        return f"{heading}: {line}" if heading else line
    return line


def yaml_path_context(lines: list[str]) -> str:
    stack: list[tuple[int, str]] = []
    key_re = re.compile(r"^(\s*)(?:-\s*)?([A-Za-z0-9_.-]+):(?:\s|$)")
    for raw in lines:
        match = key_re.match(raw)
        if not match:
            continue
        indent = len(match.group(1).replace("\t", "  "))
        key = match.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
    return ".".join(key for _, key in stack)


def python_scope_context(lines: list[str]) -> str:
    for raw in reversed(lines):
        stripped = raw.strip()
        if stripped.startswith("def ") or stripped.startswith("class "):
            return stripped.rstrip(":")
    return ""


def markdown_heading_context(lines: list[str]) -> str:
    for raw in reversed(lines):
        stripped = raw.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def validate_citations(
    references: dict[str, dict[str, Any]], cited_by: dict[str, list[CitationUse]]
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    defined = set(references)
    cited = set(cited_by)

    for missing in sorted(cited - defined):
        use_list = ", ".join(f"{use.file}:{use.line}" for use in cited_by[missing][:8])
        errors.append(f"{missing}: cited but missing from references.yaml ({use_list})")

    for orphan in sorted(defined - cited):
        warnings.append(f"{orphan}: defined but never cited")

    for ref_id, entry in sorted(references.items()):
        status = str(entry.get("status", ""))
        match = SUPERSEDED_RE.match(status)
        if match:
            uses = cited_by.get(ref_id, [])
            if uses:
                use_list = ", ".join(f"{use.file}:{use.line}" for use in uses)
                warnings.append(f"{ref_id}: superseded by {match.group(1)}; still cited by {use_list}")
            else:
                warnings.append(f"{ref_id}: superseded by {match.group(1)}; no current citations")

    return errors, warnings


def render_html(references: dict[str, dict[str, Any]], cited_by: dict[str, list[CitationUse]]) -> None:
    topics_dir = REF_DIR / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)

    by_topic: dict[str, list[str]] = defaultdict(list)
    for ref_id, entry in sorted(references.items()):
        by_topic[str(entry["topic"])].append(ref_id)

    index = render_page(
        title="Reference Registry",
        body=render_index_body(references, cited_by, by_topic),
        depth=0,
    )
    (REF_DIR / "index.html").write_text(index, encoding="utf-8")

    for topic in sorted(by_topic):
        body = [
            f"<p><a href=\"../index.html\">Back to master index</a></p>",
            f"<h1>{escape(topic)}</h1>",
        ]
        for ref_id in by_topic[topic]:
            body.append(render_entry(ref_id, references[ref_id], cited_by.get(ref_id, []), depth=1))
        page = render_page(title=f"{topic} references", body="\n".join(body), depth=1)
        (topics_dir / f"{topic}.html").write_text(page, encoding="utf-8")


def render_page(title: str, body: str, depth: int) -> str:
    css = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.5; margin: 2rem auto; max-width: 980px; padding: 0 1rem; color: #1f2933; background: #fff; }
a { color: #075985; }
nav { margin: 1rem 0 2rem; }
.entry { border-top: 1px solid #d9e2ec; padding: 1.25rem 0; }
.citation { font-size: 1.05rem; }
.meta { margin: .5rem 0; }
.badge { display: inline-block; border: 1px solid #b6c2cf; border-radius: 4px; padding: .08rem .4rem; margin-right: .35rem; font-size: .82rem; background: #f7f9fb; }
.status-current { border-color: #8bb174; background: #f1f8ec; }
.status-disputed, .status-preliminary { border-color: #d9a441; background: #fff8e6; }
.status-superseded { border-color: #c2410c; background: #fff1eb; }
blockquote { border-left: 4px solid #bcccdc; margin: .75rem 0; padding: .25rem 1rem; color: #334e68; background: #f8fafc; }
code { background: #f1f5f9; padding: .1rem .25rem; border-radius: 3px; }
ul { padding-left: 1.4rem; }
""".strip()
    return "\n".join(
        [
            "<!doctype html>",
            "<html lang=\"en\">",
            "<head>",
            "  <meta charset=\"utf-8\">",
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
            f"  <title>{escape(title)}</title>",
            f"  <style>{css}</style>",
            "</head>",
            "<body>",
            body,
            "</body>",
            "</html>",
            "",
        ]
    )


def render_index_body(
    references: dict[str, dict[str, Any]],
    cited_by: dict[str, list[CitationUse]],
    by_topic: dict[str, list[str]],
) -> str:
    parts = ["<h1>Reference Registry</h1>", "<nav><strong>Topics:</strong> "]
    topic_links = [
        f"<a href=\"topics/{escape(topic)}.html\">{escape(topic)}</a> ({len(refs)})"
        for topic, refs in sorted(by_topic.items())
    ]
    parts.append(" | ".join(topic_links))
    parts.append("</nav>")
    parts.append("<h2>Master List</h2>")
    for ref_id, entry in sorted(references.items()):
        parts.append(render_entry(ref_id, entry, cited_by.get(ref_id, []), depth=0))
    return "\n".join(parts)


def render_entry(ref_id: str, entry: dict[str, Any], uses: list[CitationUse], depth: int) -> str:
    topic_href = f"topics/{entry['topic']}.html" if depth == 0 else f"{entry['topic']}.html"
    pieces = [
        f"<section class=\"entry\" id=\"{escape(ref_id)}\">",
        f"<h2>{escape(ref_id)}</h2>",
        f"<p class=\"citation\">{format_citation(entry)}</p>",
        "<p class=\"meta\">"
        f"{badge(str(entry['role']))}"
        f"{badge(str(entry['status']), status_class(str(entry['status'])))}"
        f"<a class=\"badge\" href=\"{escape(topic_href)}\">{escape(str(entry['topic']))}</a>"
        "</p>",
    ]
    links = format_links(entry)
    if links:
        pieces.append(f"<p>{links}</p>")
    pieces.append(render_quotes(entry))
    pieces.append(render_cited_by(uses))
    pieces.append("</section>")
    return "\n".join(pieces)


def format_citation(entry: dict[str, Any]) -> str:
    volume = f" <strong>{escape(str(entry.get('volume', '')))}</strong>" if entry.get("volume") else ""
    pages = f": {escape(str(entry.get('pages', '')))}" if entry.get("pages") else ""
    journal = f" <em>{escape(str(entry.get('journal', '')))}</em>{volume}{pages}." if entry.get("journal") else ""
    return (
        f"{escape(str(entry['authors']))} ({escape(str(entry['year']))}). "
        f"{escape(str(entry['title']))}.{journal}"
    )


def format_links(entry: dict[str, Any]) -> str:
    links: list[str] = []
    doi = str(entry.get("doi") or "").strip()
    url = str(entry.get("url") or "").strip()
    if doi:
        links.append(f"<a href=\"https://doi.org/{escape(doi)}\">doi:{escape(doi)}</a>")
    if url:
        links.append(f"<a href=\"{escape(url)}\">source URL</a>")
    return " | ".join(links)


def badge(text: str, class_name: str = "") -> str:
    classes = "badge" + (f" {class_name}" if class_name else "")
    return f"<span class=\"{classes}\">{escape(text)}</span>"


def status_class(status: str) -> str:
    if status == "current":
        return "status-current"
    if status.startswith("superseded_by:"):
        return "status-superseded"
    return f"status-{status}"


def render_quotes(entry: dict[str, Any]) -> str:
    quotes = entry.get("pull_quotes") or []
    if not quotes:
        if entry.get("needs_quote") is True:
            return "<p><em>Quote needed: no verified pull quote recorded.</em></p>"
        return ""
    blocks = []
    for item in quotes:
        blocks.append(
            "<blockquote>"
            f"<p>{escape(str(item['quote']))}</p>"
            f"<footer>{escape(str(item['page']))}; {escape(str(item['grounds']))}</footer>"
            "</blockquote>"
        )
    return "\n".join(blocks)


def render_cited_by(uses: list[CitationUse]) -> str:
    if not uses:
        return "<p><strong>Cited by:</strong> none</p>"
    items = [
        f"<li><code>{escape(use.file)}:{use.line}</code> - {escape(use.context)}</li>"
        for use in uses
    ]
    return "<p><strong>Cited by:</strong></p>\n<ul>\n" + "\n".join(items) + "\n</ul>"


def escape(value: str) -> str:
    return html.escape(value, quote=True)


def run(check: bool = False) -> int:
    try:
        references = load_registry()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    errors, warnings = validate_registry(references)
    cited_by = scan_files()
    citation_errors, citation_warnings = validate_citations(references, cited_by)
    errors.extend(citation_errors)
    warnings.extend(citation_warnings)

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if not check:
        render_html(references, cited_by)
        print(f"rendered {len(references)} references across {len({entry['topic'] for entry in references.values()})} topics")
    else:
        print(f"validated {len(references)} references; cited refs={len(cited_by)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate only; do not render HTML")
    args = parser.parse_args(argv)
    return run(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
