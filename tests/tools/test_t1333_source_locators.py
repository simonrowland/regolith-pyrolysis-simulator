"""Keep every top-level extract locator tied to the corpus files it cites."""

from __future__ import annotations

from decimal import Decimal
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from simulator.battery.extract_gate import _as_decimal
from simulator.battery.migrate import discover_extracts


REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACTS_DIR = REPO_ROOT / "data" / "literature" / "extracts"
DEFAULT_CORPUS_ROOT = Path.home() / "Repos" / "regolith-corpus"
LEGACY_CORPUS_ROOT = Path.home() / "Repos" / ".regolith-main-review" / "corpus-main"


def _all_extracts() -> list[Path]:
    # Keep discovery exclusions identical to migrate.discover_extracts.
    return sorted(
        path
        for path in EXTRACTS_DIR.glob("*.yaml")
        if path.is_file()
        and not path.name.startswith("_")
        and not path.name.upper().startswith("SCHEMA")
    )


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
    relative_path = Path(source_path)
    if source_path.startswith("corpus/"):
        relative_path = Path(source_path.removeprefix("corpus/"))
    for root in roots:
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


@pytest.fixture(scope="module")
def pdf_page_counts() -> dict[Path, int]:
    return {}


def _pdf_page_numbers(page, extract: Path, locator: dict) -> list[int]:
    numeric_page = _as_decimal(page)
    assert numeric_page is not None and numeric_page == numeric_page.to_integral_value(), (
        f"{extract.name}: PDF page is not one numeric integer: {locator}"
    )
    return [int(numeric_page)]


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


def test_extract_discovery_matches_migrator() -> None:
    extracts = _all_extracts()
    assert len(extracts) == 849  # b261: main join adds 67 extracts
    assert extracts == discover_extracts(EXTRACTS_DIR)


@pytest.mark.parametrize("page", ["23, 24", "22, 6", "23; 24", "1-7", "1–7"])
def test_pdf_page_is_one_numeric_token(page: str) -> None:
    with pytest.raises(AssertionError, match="one numeric integer"):
        _pdf_page_numbers(page, Path("extract.yaml"), {"page": page})


def test_tables_only_root_does_not_resolve_raw_locator(tmp_path: Path, monkeypatch) -> None:
    tables_root = tmp_path / "tables-only"
    raw_locator = "corpus/raw/example/example.pdf"
    decoy = tables_root / "tables" / "example.pdf"
    decoy.parent.mkdir(parents=True)
    decoy.touch()
    monkeypatch.setattr(sys.modules[__name__], "LEGACY_CORPUS_ROOT", tmp_path / "absent-legacy")
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(tables_root))
    extract = tmp_path / "example.yaml"
    extract.write_text(
        yaml.safe_dump(
            {
                "source_id": "example",
                "locator": {"source_path": raw_locator, "page": 1},
            }
        ),
        encoding="utf-8",
    )

    assert _source_file(raw_locator, [tables_root], "example") is None
    assert _source_file("tables/example.pdf", [tables_root]) == decoy
    with pytest.raises(pytest.skip.Exception, match="example.yaml: cited corpus source absent"):
        test_extract_locators_resolve_to_corpus_sources(extract, {})


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


@pytest.mark.parametrize("extract", _all_extracts(), ids=lambda path: path.name)
def test_extract_locators_resolve_to_corpus_sources(
    extract: Path, pdf_page_counts: dict[Path, int]
) -> None:
    configured_root = Path(os.environ.get("REGOLITH_CORPUS_ROOT", DEFAULT_CORPUS_ROOT))
    roots = [configured_root, LEGACY_CORPUS_ROOT]
    missing_sources: dict[str, int] = {}
    failures: list[tuple[str, str, str]] = []

    try:
        document = yaml.safe_load(extract.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        pytest.fail(f"other | {extract.name} | YAML could not be read: {exc}", pytrace=False)
    if not isinstance(document, (dict, list)):
        pytest.fail(f"other | {extract.name} | unexpected YAML root: {type(document).__name__}", pytrace=False)
    source_id = document.get("source_id") if isinstance(document, dict) else None

    for locator, ancestors in _walk(document):
        source_path = locator.get("source_path")
        if not isinstance(source_path, str):
            continue
        source = _source_file(source_path, roots, source_id)
        if source is None:
            missing_sources[source_path] = missing_sources.get(source_path, 0) + 1
            continue

        if "line_range" in locator and source.suffix.lower() in {".md", ".txt"}:
            try:
                ranges = _line_ranges(locator["line_range"])
                lines = source.read_text(encoding="utf-8").splitlines()
            except (AssertionError, OSError, UnicodeError) as exc:
                failures.append(("other", repr(locator), str(exc)))
            else:
                out_of_range = [(start, end) for start, end in ranges if end > len(lines)]
                if out_of_range:
                    failures.append((
                        "line locator out of range",
                        repr(locator),
                        f"{source_path}: {out_of_range} exceeds {len(lines)} lines",
                    ))
                else:
                    cited_text = "\n".join("\n".join(lines[start - 1 : end]) for start, end in ranges)
                    cited_values = [
                        ancestor[key]
                        for ancestor in ancestors
                        for key in ("value", "quote")
                        if key in ancestor and ancestor is not locator
                    ]
                    if _citation_matches(locator, cited_text, cited_values) is False:
                        failures.append((
                            "missing citation anchor",
                            repr(locator),
                            f"{source_path}:{locator['line_range']} contains no cited table/figure, quote, paragraph, or note value",
                        ))

        if source.suffix.lower() == ".pdf" and ("page" in locator or "pdf_page_index" in locator):
            page = locator.get("page")
            index = locator.get("pdf_page_index")
            if page is None:
                if type(index) is not int:
                    failures.append(("page range or non-numeric page", repr(locator), "PDF locator has no numeric page"))
                    continue
                page = index + 1
            try:
                pages = _pdf_page_numbers(page, extract, locator)
            except AssertionError as exc:
                failures.append(("page range or non-numeric page", repr(locator), str(exc)))
                continue
            try:
                count = _pdf_page_count(source, pdf_page_counts)
            except (OSError, subprocess.CalledProcessError, AssertionError) as exc:
                failures.append(("other", repr(locator), f"could not read PDF page count: {exc}"))
                continue
            if not all(1 <= page_number <= count for page_number in pages):
                failures.append((
                    "physical page beyond PDF page count",
                    repr(locator),
                    f"PDF page(s) {pages} outside 1..{count}: {source_path}",
                ))
            if index is not None and (type(index) is not int or pages != [index + 1]):
                failures.append((
                    "other",
                    repr(locator),
                    f"page(s) {pages} do not match pdf_page_index {index!r} + 1: {source_path}",
                ))

    if missing_sources:
        missing_count = sum(missing_sources.values())
        reason = (
            f"{extract.name}: cited corpus source absent from configured roots "
            f"({missing_count} locator(s), {len(missing_sources)} path(s)); "
            f"checked {configured_root} and {LEGACY_CORPUS_ROOT}"
        )
    else:
        reason = ""
    if failures:
        failure_counts: dict[str, int] = {}
        for kind, _, _ in failures:
            failure_counts[kind] = failure_counts.get(kind, 0) + 1
        details = "\n".join(
            f"{kind} | {extract.name} | {locator} | {detail}"
            for kind, locator, detail in failures
        )
        summary = ", ".join(f"{kind}: {count}" for kind, count in sorted(failure_counts.items()))
        if reason:
            details = f"{details}\n{reason}"
        pytest.fail(f"{len(failures)} locator failure(s) ({summary}):\n{details}", pytrace=False)
    if reason:
        pytest.skip(reason)


def test_demaria_table_8_locator_pin() -> None:
    _record_demaria_table_8_pin()


def test_lamoreaux_page_pins() -> None:
    _record_lamoreaux_page_pins()
