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
        return re.compile(rf"\b(?:fig(?:ure)?\.?\s*){re.escape(label)}\b", re.I)
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


def test_changed_table_and_figure_locators_name_their_physical_pdf_page(capsys):
    checked = skipped = 0
    skip_reasons = []
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
                                        for key in ("page", "published_page", "pdf_page_index")):
                continue
            if not ("page" in locator and "pdf_page_index" in locator):
                continue
            label_pattern = _label_pattern(locator)
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
            assert label_pattern.search(text), (
                f"{relative} {locator_path}: {label} absent from physical PDF page {page_number}"
            )
            checked += 1
    print(f"t1353 PDF locator labels: {checked} passed, {skipped} skipped")
    for reason in skip_reasons:
        print(f"SKIP {reason}")
    assert checked > 0
    captured = capsys.readouterr()
    print(captured.out, end="")
