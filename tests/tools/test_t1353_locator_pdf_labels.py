"""Check changed table/figure locators against their cited physical PDF pages."""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

import yaml

BASE = "28af098ed04b8ca99f569381a2f38330cb66d7cd"
ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOTS = (
    Path("/Users/simonrowland/Repos/regolith-corpus/raw"),
    Path("/Users/simonrowland/Repos/.regolith-main-review/corpus-main/raw"),
)


def _walk_locators(value, path=()):
    if isinstance(value, dict):
        if ("table" in value or "figure" in value) and (
            "page" in value or "pdf_page_index" in value
        ):
            yield path, value
        for key, child in value.items():
            yield from _walk_locators(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_locators(child, path + (str(index),))


def _yaml(text):
    return yaml.safe_load(text) or {}


def _changed_extract_paths():
    result = subprocess.run(
        ["git", "diff", "--name-only", BASE, "--", "data/literature/extracts"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    paths = set(result.stdout.splitlines())
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", "data/literature/extracts"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    for line in status.stdout.splitlines():
        paths.add(line[3:])
    return sorted(path for path in paths if path.endswith(".yaml"))


def _pdf_path(source_id, corpus_source_id):
    for sid in dict.fromkeys((source_id, corpus_source_id)):
        if not sid:
            continue
        for root in CORPUS_ROOTS:
            candidate = root / sid / f"{sid}.pdf"
            if candidate.is_file():
                return candidate
    return None


def _label_pattern(locator):
    kind, label = next((key, locator[key]) for key in ("table", "figure") if key in locator)
    label = str(label).strip().strip("'\"")
    label = re.sub(r"\s*\(continued\)$", "", label, flags=re.I)
    if kind == "figure":
        return re.compile(
            rf"(?:\bfig(?:ure)?\.?\s*{re.escape(label)}\b|^\s*{re.escape(label)}\.)",
            re.I | re.M,
        )
    if label.lower() == "ia":
        return re.compile(r"\btable\s+(?:ia|la)\b", re.I)
    return re.compile(rf"\b(?:table|таблица)\s+{re.escape(label)}\b", re.I)


@lru_cache(maxsize=None)
def _page_text(pdf, page_number):
    result = subprocess.run(
        ["pdftotext", "-f", str(page_number), "-l", str(page_number), "-layout", pdf, "-"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return result.stdout


def _has_page_text(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    substantive = [
        line for line in lines
        if not re.fullmatch(r"\d{4}M&PS\.\.\..*", line)
        and not re.fullmatch(r"\d+", line)
    ]
    return bool(substantive)


def _assert_label_and_data(locator, page_text, previous_page_text=None):
    label_pattern = _label_pattern(locator)
    if locator.get("table_continued") is True:
        assert previous_page_text is not None
        assert label_pattern.search(previous_page_text), "continued table caption is absent from previous page"
        match = re.search(r"data cell:\s*([^.;]+)", locator.get("note", ""), re.I)
        assert match is not None, "continued table locator requires a data-cell record in its note"
        record = match.group(1)
        record_pattern = re.compile(r"\s+".join(re.escape(part) for part in record.split()))
        assert record_pattern.search(page_text), "continued table data-cell record is absent from cited page"
        return
    assert label_pattern.search(page_text), "table/figure label is absent from cited page"


def test_changed_table_and_figure_locators_name_their_physical_pdf_page(capsys):
    checked = skipped = 0
    skip_reasons = []
    failures = []
    for relative in _changed_extract_paths():
        current_path = ROOT / relative
        current = _yaml(current_path.read_text())
        old = subprocess.run(
            ["git", "show", f"{BASE}:{relative}"], cwd=ROOT,
            check=True, capture_output=True, text=True,
        ).stdout
        before = dict(_walk_locators(_yaml(old)))
        after = dict(_walk_locators(current))
        pdf = _pdf_path(current.get("source_id"), current.get("source", {}).get("corpus_source_id"))
        for locator_path, locator in after.items():
            prior = before.get(locator_path)
            if prior is None or not any(prior.get(key) != locator.get(key)
                                        for key in ("page", "published_page", "pdf_page_index", "table_continued")):
                continue
            if not ("page" in locator and "pdf_page_index" in locator):
                continue
            label = f"{next(k for k in ('table', 'figure') if k in locator)} {locator[next(k for k in ('table', 'figure') if k in locator)]}"
            if pdf is None:
                skipped += 1
                skip_reasons.append(f"{relative} {locator_path}: missing PDF ({label})")
                continue
            page_number = int(locator["pdf_page_index"]) + 1
            text = _page_text(str(pdf), page_number)
            if not _has_page_text(text):
                skipped += 1
                skip_reasons.append(f"{relative} {locator_path}: empty PDF text page {page_number} ({label})")
                continue
            previous_text = (
                _page_text(str(pdf), page_number - 1)
                if locator.get("table_continued") is True and page_number > 1
                else None
            )
            try:
                _assert_label_and_data(locator, text, previous_text)
            except AssertionError as exc:
                failures.append(f"{relative} {locator_path}: {label}: {exc}")
                continue
            checked += 1
    print(f"t1353 PDF locator labels: {checked} passed, {skipped} skipped, {len(failures)} failed")
    for reason in skip_reasons:
        print(f"SKIP {reason}")
    assert checked > 0, "no locator labels were checked"
    assert failures == [], "PDF locator label failures:\n" + "\n".join(failures)
    captured = capsys.readouterr()
    print(captured.out, end="")


def test_continued_table_checks_caption_on_previous_page_and_data_on_cited_page():
    locator = {
        "table": "877",
        "table_continued": True,
        "note": "Table 877 continues; data cell: 3300.000 64.806.",
    }
    _assert_label_and_data(
        locator,
        "T (K)  F°(T)\n3300.000 64.806 346.974",
        "Class accuracy\nTable 877",
    )
