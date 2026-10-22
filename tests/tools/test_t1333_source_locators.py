"""Keep t-1333 source coordinates tied to the corpus files they cite."""

from __future__ import annotations

from decimal import Decimal
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MERGE_BASE = "6f90106c6"
DEFAULT_CORPUS_ROOT = Path.home() / "Repos" / "regolith-corpus"
LEGACY_CORPUS_ROOT = Path.home() / "Repos" / ".regolith-main-review" / "corpus-main"


def _changed_extracts() -> list[Path]:
    names = subprocess.check_output(
        ["git", "diff", "--name-only", f"{MERGE_BASE}...HEAD", "--", "data/literature/extracts"],
        cwd=REPO_ROOT,
        text=True,
    ).splitlines()
    return [REPO_ROOT / name for name in names]


def _walk(value, ancestors=()):
    if isinstance(value, dict):
        yield value, ancestors
        next_ancestors = ancestors + (value,)
        for child in value.values():
            yield from _walk(child, next_ancestors)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, ancestors)


def _numeric_values(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        yield str(value)
    elif isinstance(value, str):
        yield from re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", value, re.I)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _numeric_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _numeric_values(child)


def _text_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _text_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _text_values(child)


def _numeric_tokens(value: str) -> set[tuple[int, Decimal]]:
    value = re.sub(r"(?<=\d)(?=[pP])", " ", value.casefold())
    tokens = re.findall(
        r"(?<![A-Za-z0-9_.+-])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?(?![A-Za-z0-9_.])",
        value,
    )
    return {
        (-1 if token.startswith("-") else 1, Decimal(token.lstrip("+-")).normalize())
        for token in tokens
    }


def _line_ranges(value: str) -> list[tuple[int, int]]:
    ranges = []
    for item in str(value).split(","):
        match = re.fullmatch(r"\s*(\d+)(?:\s*[-–]\s*(\d+))?\s*", item)
        assert match, f"unsupported line_range: {value!r}"
        start = int(match.group(1))
        end = int(match.group(2) or start)
        assert 1 <= start <= end, f"invalid line_range: {value!r}"
        ranges.append((start, end))
    return ranges


def _source_file(source_path: str, roots: list[Path], source_id: str | None = None) -> Path | None:
    relative_paths = [source_path]
    if source_path.startswith("corpus/"):
        relative_paths.append(source_path.removeprefix("corpus/"))
    for root in roots:
        for relative_path in relative_paths:
            candidate = root / relative_path
            if candidate.is_file():
                return candidate
        if source_id and source_path.startswith("docs/references/pdfs/"):
            raw_dir = root / "raw" / source_id
            for filename in (Path(source_path).name, f"{source_id}.pdf"):
                candidate = raw_dir / filename
                if candidate.is_file():
                    return candidate
    return None


def _citation_matches(locator: dict, text: str, cited_values=()) -> bool | None:
    normalized = " ".join(text.split()).casefold()
    normalized = re.sub(
        r"(\d+(?:\.\d+)?)\s*(?:\\times|×)\s*10\s*\$?\s*\^\s*\$?\s*\{\s*([-+]?\d+)\s*\}",
        r"\1e\2",
        normalized,
    )
    normalized = re.sub(r"\\sim", "~", normalized)
    normalized = re.sub(r"[${}]", "", normalized)
    normalized = re.sub(r"e([+-])0+(\d+)", r"e\1\2", normalized)
    has_direct_anchor = False
    has_table_or_figure = False
    for kind in ("table", "figure", "equation"):
        label = locator.get(kind)
        if label is None:
            continue
        has_direct_anchor = True
        if kind in {"table", "figure"}:
            has_table_or_figure = True
        label = str(label).strip()
        label_kind = {
            "table": "table",
            "figure": r"(?:figure|fig\.?)",
            "equation": r"(?:equation|eqn\.?)",
        }[kind]
        if re.search(r"\b" + label_kind + r"\s*" + re.escape(label) + r"\b", normalized, re.I):
            return True
        if kind == "equation" and re.search(r"\\tag\s*(?:\{\s*)?" + re.escape(label) + r"\b", normalized, re.I):
            return True
        if kind == "figure" and not label.isdigit():
            description_words = re.findall(r"[a-z]{4,}", label.casefold())
            for index in range(len(description_words) - 1):
                phrase = " ".join(description_words[index : index + 2])
                if phrase in normalized:
                    return True
        if label.isdigit():
            roman = {"1": "i", "2": "ii", "3": "iii", "4": "iv", "5": "v", "6": "vi", "7": "vii", "8": "viii", "9": "ix", "10": "x"}.get(label)
            if roman and re.search(r"\b" + label_kind + r"\s*" + roman + r"\b", normalized, re.I):
                return True

    # Numeric data elsewhere in the cited range cannot stand in for a missing
    # table or figure label (for example, a shared sample ID).
    if has_table_or_figure:
        return False

    for key in ("quote", "paragraph"):
        citation = locator.get(key)
        if isinstance(citation, str) and len(citation.strip()) >= 12:
            citation_normalized = " ".join(citation.split()).casefold()
            citation_normalized = re.sub(r"\\sim", "~", citation_normalized)
            citation_normalized = re.sub(r"[${}]", "", citation_normalized)
            has_direct_anchor = True
            if citation_normalized in normalized:
                return True
            if key == "paragraph":
                citation_numbers = re.findall(
                    r"(?<![A-Za-z])\d+(?:\.\d+)?(?:e[-+]?\d+)?", citation_normalized
                )
                source_numbers = _numeric_tokens(normalized)
                citation_words = [
                    word
                    for word in re.findall(r"[a-z]{2,}", citation_normalized)
                    if word
                    not in {
                        "with", "from", "until", "into", "only", "where", "used", "range",
                        "was", "were", "the", "and", "for", "of", "in",
                    }
                ]
                if sum(word in normalized for word in set(citation_words)) >= 2 and (
                    not citation_numbers
                    or any(number in source_numbers for number in _numeric_tokens(citation_normalized))
                ):
                    return True
    note = locator.get("note")
    if isinstance(note, str):
        if _numeric_tokens(note) & _numeric_tokens(normalized):
            return True
        note_words = re.findall(r"[a-z]{4,}", note.casefold())
        if sum(word in normalized for word in set(note_words)) >= 2:
            return True
    for value in cited_values:
        for citation in _text_values(value):
            citation = " ".join(citation.split()).casefold()
            if re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", citation):
                if citation in _numeric_tokens(normalized):
                    return True
                continue
            if citation and citation in normalized:
                return True
            words = re.findall(r"[a-z]{4,}", citation)
            source_words = re.findall(r"[a-z]{4,}", normalized)
            if sum(any(word.startswith(token[:5]) for word in source_words) for token in set(words)) >= 2:
                return True
            formulas = re.findall(r"\b[A-Z][a-z]?\d*(?:O\d*)?\b", citation)
            if any(formula.casefold() in normalized for formula in formulas):
                return True
        for number in _numeric_values(value):
            has_direct_anchor = True
            if _numeric_tokens(number) & _numeric_tokens(normalized):
                return True
    if not has_direct_anchor and re.search(r"\b(?:table|fig(?:ure)?\.?)\s*(?:\d+|[ivx]+)\b", normalized, re.I):
        return True
    return False if has_direct_anchor else None


def _pdf_page_count(path: Path, cache: dict[Path, int]) -> int:
    if path not in cache:
        output = subprocess.check_output(["pdfinfo", str(path)], text=True, stderr=subprocess.STDOUT)
        match = re.search(r"^Pages:\s+(\d+)\s*$", output, re.M)
        assert match, f"pdfinfo did not report page count for {path}"
        cache[path] = int(match.group(1))
    return cache[path]


def _pdf_page_numbers(page, extract: Path, locator: dict) -> list[int]:
    if isinstance(page, str):
        page = re.sub(r"^\s*(?:PDF\s*)?p(?:age)?\.?\s*", "", page, flags=re.I)
        pages = [item.strip() for item in re.split(r"[,;]", page) if item.strip()]
        page_numbers = [int(item) for item in pages if item.isdigit()]
        assert len(page_numbers) == len(pages) and page_numbers, (
            f"{extract.name}: PDF page is not numeric: {locator}"
        )
        return page_numbers
    assert isinstance(page, int), f"{extract.name}: PDF page is not numeric: {locator}"
    return [page]


def _record_demaria_table_8_pin() -> None:
    extract = REPO_ROOT / "data/literature/extracts/kems-022-demaria-1971.yaml"
    document = yaml.safe_load(extract.read_text(encoding="utf-8"))
    matches = [
        locator
        for locator, _ in _walk(document)
        if locator.get("source_path") == "text/kems-022-demaria-1971/mineru/source/vlm/source.md"
        and str(locator.get("table", "")).strip() == "8"
    ]
    assert matches, "DeMaria Table 8 locator not found"
    assert any(_line_ranges(locator["line_range"]) == [(328, 330)] for locator in matches)


def test_locator_citation_requires_the_cited_anchor() -> None:
    table_8 = {"table": "8", "paragraph": "Table 8 reports sample 12065 results."}
    assert _citation_matches(table_8, "Table 7 sample 12065 results.") is False
    assert _citation_matches(table_8, "Table 8 sample 12065 results.") is True

    text_only = {"paragraph": "Sample 12065 was heated to 2360 K."}
    assert _citation_matches(text_only, "Sample 12065 was held for a separate experiment.") is False
    assert _citation_matches(text_only, "Sample 12065 was heated to 2360 K.") is True
    assert _citation_matches(text_only, "Sample 12065 was heated to 2360 K.", [2360]) is True
    assert _citation_matches({"quote": "heated to 2360 K"}, "Sample was heated to 2360 K.") is True
    richter_locator = {"paragraph": "2.5 and 6 mm samples"}
    unrelated_richter_range = "Temperature 4.6; evaporation fluxes -6.6 and -6.7."
    assert _citation_matches(richter_locator, unrelated_richter_range, [[2.5, 6.0]]) is False


def _record_lamoreaux_page_pins() -> None:
    extract = REPO_ROOT / "data/literature/extracts/lamoreaux-hildenbrand-1984.yaml"
    document = yaml.safe_load(extract.read_text(encoding="utf-8"))
    pages = {
        locator.get("page")
        for locator, _ in _walk(document)
        if locator.get("source_path", "").endswith("lamoreaux-hildenbrand-1984-t383.pdf")
    }
    assert {1, 2, 5} <= pages
    corpus_roots = [Path(os.environ.get("REGOLITH_CORPUS_ROOT", DEFAULT_CORPUS_ROOT)), LEGACY_CORPUS_ROOT]
    pdf = _source_file("raw/lamoreaux-hildenbrand-1984-t383/lamoreaux-hildenbrand-1984-t383.pdf", corpus_roots)
    if pdf is None:
        pytest.skip("Lamoreaux PDF unavailable for pdftotext page pins")
    for page, expected in {
        1: "HIGH TEMPERATURE VAPORIZATION BEHAVIOR OF OXIDES",
        2: "Table 2",
        5: "Table 4",
    }.items():
        text = subprocess.check_output(["pdftotext", "-f", str(page), "-l", str(page), str(pdf), "-"], text=True)
        assert expected.casefold() in text.casefold(), f"Lamoreaux PDF page {page} lacks {expected!r}"


def test_changed_extract_locators_resolve_to_corpus_sources() -> None:
    configured_root = Path(os.environ.get("REGOLITH_CORPUS_ROOT", DEFAULT_CORPUS_ROOT))
    if not configured_root.is_dir() and not LEGACY_CORPUS_ROOT.is_dir():
        pytest.skip(f"corpus absent: checked {configured_root} and {LEGACY_CORPUS_ROOT}")

    roots = [configured_root, LEGACY_CORPUS_ROOT]
    missing_sources: dict[str, int] = {}
    missing_citation_anchors = 0
    checked_lines = checked_pdfs = 0
    pdf_pages: dict[Path, int] = {}

    extracts = _changed_extracts()
    assert extracts, f"no changed extracts found from {MERGE_BASE}"
    for extract in extracts:
        document = yaml.safe_load(extract.read_text(encoding="utf-8"))
        assert isinstance(document, (dict, list)), f"unexpected YAML root in {extract}"
        for locator, ancestors in _walk(document):
            source_path = locator.get("source_path")
            if not isinstance(source_path, str):
                continue
            source = _source_file(source_path, roots, document.get("source_id"))
            if source is None:
                missing_sources[source_path] = missing_sources.get(source_path, 0) + 1
                continue

            if "line_range" in locator and source.suffix.lower() in {".md", ".txt"}:
                ranges = _line_ranges(locator["line_range"])
                lines = source.read_text(encoding="utf-8").splitlines()
                for start, end in ranges:
                    assert end <= len(lines), f"{extract.name}: {source_path}:{start}-{end} exceeds {len(lines)} lines"
                cited_text = "\n".join("\n".join(lines[start - 1 : end]) for start, end in ranges)
                cited_values = [
                    ancestor[key]
                    for ancestor in ancestors
                    for key in ("value", "quote")
                    if key in ancestor and ancestor is not locator
                ]
                citation_matches = _citation_matches(locator, cited_text, cited_values)
                assert citation_matches is not False, (
                    f"{extract.name}: {source_path}:{locator['line_range']} contains no cited table/figure, "
                    f"quote, paragraph, or note value; locator={locator}"
                )
                if citation_matches is None:
                    missing_citation_anchors += 1
                checked_lines += 1

            if source.suffix.lower() == ".pdf" and ("page" in locator or "pdf_page_index" in locator):
                page = locator.get("page")
                index = locator.get("pdf_page_index")
                if page is None:
                    assert isinstance(index, int), f"{extract.name}: PDF locator has no numeric page: {locator}"
                    page = index + 1
                pages = _pdf_page_numbers(page, extract, locator)
                count = _pdf_page_count(source, pdf_pages)
                assert all(1 <= page <= count for page in pages), (
                    f"{extract.name}: PDF page(s) {pages} outside 1..{count}: {source_path}"
                )
                if index is not None:
                    assert isinstance(index, int) and pages == [index + 1], (
                        f"{extract.name}: page(s) {pages} != pdf_page_index {index} + 1: {source_path}"
                    )
                checked_pdfs += 1

    _record_demaria_table_8_pin()
    _record_lamoreaux_page_pins()
    if missing_sources:
        print(
            f"Skipped {sum(missing_sources.values())} locators with unavailable sources "
            f"across {len(missing_sources)} distinct paths."
        )
    print(
        f"Checked {len(extracts)} changed extracts: {checked_lines} markdown/text line locators, "
        f"{checked_pdfs} PDF page locators; {sum(missing_sources.values())} source locators unavailable, "
        f"{missing_citation_anchors} line locators have no explicit table/figure/value anchor to match."
    )
