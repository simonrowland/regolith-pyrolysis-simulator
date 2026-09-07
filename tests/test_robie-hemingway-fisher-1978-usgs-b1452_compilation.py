"""Coverage and source-losslessness checks for USGS Bulletin 1452."""

import copy
import hashlib
import json
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from simulator.reference_data.robie_hemingway_fisher_1978_usgs_b1452_loader import (
    COMPILATION_ROOT,
    ROLE,
    SOURCE_ID,
    SOURCE_SHA256,
    OcrSuspectGridTokenError,
    OcrSuspectTableValueError,
    TemperatureNotInPrintedGridError,
    feedstock_coverage,
    iter_published_numbers,
    load_manifest,
    load_records,
    lookup_temperature,
    parse_mineru_table_html,
    token_has_printed_shape,
)

MINERU_ROOT = Path(
    "/Users/simonrowland/Repos/regolith-corpus/text/"
    "robie-hemingway-fisher-1978-usgs-b1452/mineru"
)
MINERU_CHUNKS = [
    ("chunk-p001-p090", 1),
    ("chunk-p091-p180", 91),
    ("chunk-p181-p270", 181),
    ("chunk-p271-p360", 271),
    ("chunk-p361-p450", 361),
    ("chunk-p451-p464", 451),
]


SOURCE_PDF = Path(
    "/Users/simonrowland/Repos/regolith-corpus/raw/"
    "robie-hemingway-fisher-1978-usgs-b1452/"
    "robie-hemingway-fisher-1978-usgs-b1452.pdf"
)


@pytest.fixture(scope="module")
def compilation():
    return load_manifest(), list(load_records())


@pytest.fixture(scope="module")
def source_layout(compilation):
    _, records = compilation
    assert SOURCE_PDF.is_file(), f"read-only corpus PDF is required: {SOURCE_PDF}"
    assert hashlib.sha256(SOURCE_PDF.read_bytes()).hexdigest() == SOURCE_SHA256
    pdftotext = shutil.which("pdftotext")
    assert pdftotext, "pdftotext is required for the independent source round trip"
    pages = sorted({page for record in records for page in record["source_locator"]["pdf_pages"]})
    return {
        page: subprocess.run(
            [pdftotext, "-f", str(page), "-l", str(page), "-layout", str(SOURCE_PDF), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        for page in pages
    }


def mineru_chunk_for_pdf_page(pdf_page: int) -> tuple[str, int]:
    for chunk, start in MINERU_CHUNKS:
        end = start + 89 if chunk != "chunk-p451-p464" else start + 13
        if start <= pdf_page <= end:
            return chunk, start
    raise AssertionError(f"no MinerU chunk for pdf page {pdf_page}")


@lru_cache(maxsize=None)
def load_mineru_content_list(chunk: str) -> list:
    files = [
        path
        for path in (MINERU_ROOT / chunk).glob("*_content_list.json")
        if "v2" not in path.name
    ]
    assert files, chunk
    return json.loads(files[0].read_text(encoding="utf-8"))


def mineru_tables_on_pdf_page(pdf_page: int) -> list[dict]:
    chunk, start = mineru_chunk_for_pdf_page(pdf_page)
    page_idx = pdf_page - start
    return [
        item
        for item in load_mineru_content_list(chunk)
        if item.get("type") == "table" and item.get("page_idx") == page_idx
    ]


def mineru_page_text(pdf_page: int) -> str:
    chunk, start = mineru_chunk_for_pdf_page(pdf_page)
    page_idx = pdf_page - start
    texts = [
        item.get("text") or ""
        for item in load_mineru_content_list(chunk)
        if item.get("page_idx") == page_idx and item.get("type") in {"text", "header"}
    ]
    return "\n".join(texts)


def mineru_grid_for_record(record) -> list[list[str]]:
    locator = record["source_locator"]
    if record["census_id"] == "thermodynamic-properties-at-298-15-k":
        grid = []
        for pdf_page in locator["pdf_pages"]:
            for table in mineru_tables_on_pdf_page(pdf_page):
                body = table.get("table_body") or ""
                if not body:
                    continue
                parsed = parse_mineru_table_html(body)
                width = max((len(row) for row in parsed), default=0)
                if width >= 7:
                    grid.extend(parsed)
        return grid
    tables = mineru_tables_on_pdf_page(locator["pdf_pages"][0])
    table = tables[locator["mineru_table_index"]]
    return parse_mineru_table_html(table.get("table_body") or "")


def assert_record_matches_mineru(record, layouts):
    grid = mineru_grid_for_record(record)
    page_text = "\n".join(mineru_page_text(page) for page in record["source_locator"]["pdf_pages"])
    page_lines = layouts[record["source_locator"]["pdf_pages"][0]]
    collapsed_layout = "\n".join(page_lines)
    for number in [record["formula_weight"], *record["uncertainty_values"]]:
        token = number["as_published"]
        if not token:
            continue
        in_mineru = token in page_text or any(token in cell for row in grid for cell in row)
        span = number.get("source_text_span")
        in_layout = False
        if span and span.get("line"):
            source_line = page_lines[span["line"] - 1]
            in_layout = source_line[span["start"] : span["end"]] == token
        assert in_mineru or in_layout or token in collapsed_layout, (
            record["record_id"],
            token,
        )
    if not record["rows"]:
        return
    for row in record["rows"]:
        for column, span in row["source_text_spans"].items():
            line_cells = grid[span["line"] - 1]
            raw = line_cells[span["start"]] if span["start"] < len(line_cells) else ""
            assert row["cells"][column]["as_published"] == raw, (
                record["record_id"],
                column,
                row["cells"][column]["as_published"],
                raw,
            )


def assert_record_matches_source(record, layouts):
    if "mineru" in record["source_locator"].get("extraction", ""):
        assert_record_matches_mineru(record, layouts)
        return
    for number in [record["formula_weight"], *record["uncertainty_values"]]:
        if number["as_published"]:
            span = number["source_text_span"]
            assert span is not None
            page_lines = layouts[record["source_locator"]["pdf_pages"][0]]
            raw = page_lines[span["line"] - 1][span["start"] : span["end"]]
            assert number["as_published"] == raw
    if not record["rows"]:
        return
    page_lines = layouts[record["source_locator"]["pdf_pages"][0]]
    for row in record["rows"]:
        for column, span in row["source_text_spans"].items():
            source_line = page_lines[span["line"] - 1]
            raw = source_line[span["start"] : span["end"]]
            assert row["cells"][column]["as_published"] == raw
            assert span["start"] == 0 or source_line[span["start"] - 1].isspace()
            assert span["end"] == len(source_line) or source_line[span["end"]].isspace()


def expected_summary(manifest, records):
    cells = [
        (row["cells"][column], column)
        for record in records
        for row in record["rows"]
        for column in record["column_ids"]
    ]
    transcribed_census = {
        record["census_id"] for record in records if record["transcription_status"] == "transcribed"
    }
    ambiguities = manifest["ambiguities"]
    return {
        "census_count": len({record["census_id"] for record in records}),
        "record_count": len(records),
        "transcribed_table_count": len(transcribed_census),
        "untranscribed_table_count": len({record["census_id"] for record in records})
        - len(transcribed_census),
        "transcribed_record_count": sum(
            1 for record in records if record["transcription_status"] == "transcribed"
        ),
        "untranscribed_record_count": sum(
            1 for record in records if record["transcription_status"] == "untranscribed"
        ),
        "temperature_row_count": sum(len(record["rows"]) for record in records),
        "phase_split_record_count": sum(1 for record in records if "-phase-" in record["record_id"]),
        "ocr_suspect_record_count": sum(1 for record in records if record["ocr_suspect"]),
        "ocr_suspect_numeric_cell_count": sum(1 for cell, _ in cells if cell["ocr_suspect"]),
        "printed_shape_failure_count": sum(
            1 for cell, column in cells if not token_has_printed_shape(cell["as_published"], column)
        ),
        "admitted_shape_failure_count": sum(
            1
            for cell, column in cells
            if not token_has_printed_shape(cell["as_published"], column) and cell["value"] is not None
        ),
        "identity_check_disagreement_count": sum(
            1 for item in ambiguities if item["kind"] == "thermodynamic_identity_disagreement"
        ),
        "image_temperature_disagreement_count": sum(
            1 for item in ambiguities if item["kind"] == "image_temperature_disagreement"
        ),
        "image_temperature_grid_unconfirmed_count": sum(
            1 for item in ambiguities if item["kind"] == "image_temperature_grid_unconfirmed"
        ),
        "ambiguity_count": len(ambiguities),
        "correction_count": len(manifest["corrections"]),
    }


def test_manifest_table_census_and_phase_record_count(compilation):
    manifest, records = compilation
    record_files = sorted((COMPILATION_ROOT / "records").glob("*.json"))
    assert manifest["summary"] == expected_summary(manifest, records)
    assert len(records) == len(record_files) == manifest["summary"]["record_count"]
    assert len({record["census_id"] for record in records}) == 400
    assert len(manifest["corpus_status"]["census_record_ids"]) == 400
    remainder = manifest["corpus_status"]["mineru_followup"]
    assert remainder["store_task"] == "t-852"
    assert len(remainder["untranscribed"]) == manifest["summary"]["untranscribed_table_count"]
    assert {item["record_id"] for item in remainder["untranscribed"]} == {
        record["record_id"]
        for record in records
        if record["transcription_status"] == "untranscribed"
        and "-phase-" not in record["record_id"]
    }
    assert manifest["corpus_status"]["excluded_non_table_page"] == {
        "printed_page": 427,
        "pdf_page": 433,
        "reason": "visually blank",
    }


def test_every_numeric_token_round_trips_to_page_layout(compilation, source_layout):
    _, records = compilation
    for record in records:
        assert_record_matches_source(record, source_layout)


def test_source_round_trip_rejects_consistent_token_value_mutation(compilation, source_layout):
    _, records = compilation
    record = copy.deepcopy(
        next(
            item
            for item in records
            if item["rows"] and "temperature" in item["column_ids"]
        )
    )
    cell = record["rows"][0]["cells"]["temperature"]
    cell["as_published"] = "299.15"
    cell["value"] = 299.15
    with pytest.raises(AssertionError):
        assert_record_matches_source(record, source_layout)

    metadata_record = copy.deepcopy(
        next(record for record in records if record["record_id"] == f"{SOURCE_ID}-0397")
    )
    metadata_record["formula_weight"]["as_published"] = "400"
    metadata_record["formula_weight"]["value"] = 400.0
    with pytest.raises(AssertionError):
        assert_record_matches_source(metadata_record, source_layout)


def test_shape_failures_are_suspect_and_never_admitted(compilation):
    manifest, records = compilation
    admitted_failures = []
    shape_failures = []
    suspect_count = 0
    for record in records:
        for row in record["rows"]:
            for column, cell in row["cells"].items():
                shape_ok = token_has_printed_shape(cell["as_published"], column)
                if not shape_ok:
                    shape_failures.append((record["record_id"], column, cell["as_published"]))
                    if cell["value"] is not None or not cell["ocr_suspect"]:
                        admitted_failures.append((record["record_id"], column, cell))
                suspect_count += cell["ocr_suspect"]
    assert not admitted_failures
    assert len(shape_failures) == manifest["summary"]["printed_shape_failure_count"]
    assert suspect_count == manifest["summary"]["ocr_suspect_numeric_cell_count"]
    assert manifest["summary"]["admitted_shape_failure_count"] == 0


def test_phase_records_have_strict_safe_grids_and_no_embedded_headers(compilation, source_layout):
    manifest, records = compilation
    for record in records:
        if "temperature" not in record["column_ids"]:
            continue
        values = [
            row["cells"]["temperature"]["value"]
            for row in record["rows"]
            if row["cells"]["temperature"]["value"] is not None
            and not row["cells"]["temperature"]["ocr_suspect"]
        ]
        assert all(right > left for left, right in zip(values, values[1:]))
        if not record["rows"]:
            continue
        if "mineru" in record["source_locator"].get("extraction", ""):
            continue
        page_lines = source_layout[record["source_locator"]["pdf_pages"][0]]
        first = record["rows"][0]["source_text_line"]
        last = record["rows"][-1]["source_text_line"]
        embedded = [
            line
            for line in page_lines[first:last]
            if re.match(r"^\s{0,3}[A-Za-z][A-Za-z0-9(){}]*\s*:", line)
        ]
        assert not embedded, (record["record_id"], embedded)
    assert manifest["summary"]["phase_split_record_count"] == sum(
        1 for record in records if "-phase-" in record["record_id"]
    )


def test_lookup_refuses_suspect_and_off_grid_values():
    with pytest.raises(OcrSuspectGridTokenError):
        lookup_temperature(f"{SOURCE_ID}-0005", 100)
    with pytest.raises(TemperatureNotInPrintedGridError):
        lookup_temperature(f"{SOURCE_ID}-0005", 700)
    with pytest.raises(OcrSuspectTableValueError):
        lookup_temperature(f"{SOURCE_ID}-0005", 400)
    rows = lookup_temperature(f"{SOURCE_ID}-0231-phase-02", 1700)
    assert len(rows) == 1
    assert rows[0]["cells"]["temperature"]["as_published"] == "1700"
    with pytest.raises(OcrSuspectTableValueError):
        lookup_temperature(f"{SOURCE_ID}-0010", 400)


def test_numeric_looking_formula_weight_disagreement_is_not_admitted(compilation):
    manifest, records = compilation
    record = next(record for record in records if record["record_id"] == f"{SOURCE_ID}-0397")
    assert record["formula_weight"]["as_published"] == "360.311"
    assert record["formula_weight"]["value"] is None
    assert record["formula_weight"]["ocr_suspect"] is True
    corrections = [
        item
        for item in manifest["corrections"]
        if item["record_id"] == record["record_id"] and item["column"] == "formula_weight"
    ]
    assert len(corrections) == 1
    assert corrections[0]["printed_token"] == "360.317"
    assert corrections[0]["action"] == "value_withheld"


def test_manifest_ambiguity_lists_reproduce_from_records(compilation):
    manifest, records = compilation
    by_id = {record["record_id"]: record for record in records}
    assert len(manifest["ambiguities"]) == manifest["summary"]["ambiguity_count"]
    flattened = []
    for entry in manifest["entries"]:
        record = by_id[entry["record_id"]]
        assert entry["ambiguities"] == record["ambiguities"]
        assert entry["ambiguity_count"] == len(record["ambiguities"])
        flattened.extend({"record_id": record["record_id"], **item} for item in record["ambiguities"])
    assert flattened == manifest["ambiguities"]
    for item in manifest["ambiguities"]:
        if item["kind"] == "untranscribed_table":
            assert by_id[item["record_id"]]["transcription_status"] == "untranscribed"


def test_phase_as_published_represented_per_record(compilation, source_layout):
    manifest, records = compilation
    by_id = {record["record_id"]: record for record in records}
    for record in records:
        assert "phase_as_published" in record
        entry = next(item for item in manifest["entries"] if item["record_id"] == record["record_id"])
        assert entry["phase"] == record["phase_as_published"]
    silver = by_id[f"{SOURCE_ID}-0004"]
    silver_liquid = by_id[f"{SOURCE_ID}-0004-phase-02"]
    assert "cubic" in silver["phase_as_published"]
    assert "1234" in silver_liquid["phase_as_published"]
    assert "cubic" not in silver_liquid["phase_as_published"]
    barium = by_id[f"{SOURCE_ID}-0010"]
    barium_liquid = by_id[f"{SOURCE_ID}-0010-phase-04"]
    assert "582.53" in barium["phase_as_published"]
    assert "Liquid" not in barium["phase_as_published"]
    assert "Liquid" in barium_liquid["phase_as_published"]
    indium = by_id[f"{SOURCE_ID}-0038"]
    indium_liquid = by_id[f"{SOURCE_ID}-0038-phase-02"]
    assert "Liquid" not in indium["phase_as_published"]
    assert "Liquid" in indium_liquid["phase_as_published"]
    iron_alpha_tail = by_id[f"{SOURCE_ID}-0028-phase-02"]
    assert iron_alpha_tail["phase_as_published"] == (
        "Alpha crystals (body-centered cubic) 298.15 to 1184 K.   Curie point\n"
        "         1042 K."
    )
    assert "Ga1111a" not in iron_alpha_tail["phase_as_published"]
    assert "Liquid" not in iron_alpha_tail["phase_as_published"]
    erbium_liquid = by_id[f"{SOURCE_ID}-0025-phase-02"]
    assert "Liquid" in erbium_liquid["phase_as_published"]
    assert "Huagoaal" not in erbium_liquid["phase_as_published"]
    cobalt_beta = by_id[f"{SOURCE_ID}-0020-phase-02"]
    cobalt_liquid = by_id[f"{SOURCE_ID}-0020-phase-03"]
    assert "Beta crystals" in cobalt_beta["phase_as_published"]
    assert "Liquid" not in cobalt_beta["phase_as_published"]
    assert cobalt_liquid["phase_as_published"] == "Liquid 1768 to 1800 K."
    assert "curie" not in cobalt_liquid["phase_as_published"].lower()
    assert "Beta crystals" not in cobalt_liquid["phase_as_published"]
    wo3 = by_id[f"{SOURCE_ID}-0219"]
    assert _phase_start_count(wo3["phase_as_published"]) == 2
    assert wo3["phase_span_as_published"] == _phase_clauses(wo3["phase_as_published"])
    for record in records:
        phase = record.get("phase_as_published")
        if not phase:
            continue
        header = re.split(r"(?<=\.)\s+", phase.strip(), maxsplit=1)[0]
        collapsed_header = re.sub(r"\s+", " ", header)
        if "mineru" in record["source_locator"].get("extraction", ""):
            page_lines = source_layout[record["source_locator"]["pdf_pages"][0]]
            collapsed_page = re.sub(r"\s+", " ", "\n".join(page_lines))
            mineru_text = re.sub(
                r"\s+",
                " ",
                mineru_page_text(record["source_locator"]["pdf_pages"][0]),
            )
            assert collapsed_header in collapsed_page or collapsed_header in mineru_text, (
                record["record_id"],
                collapsed_header,
            )
            continue
        page_lines = source_layout[record["source_locator"]["pdf_pages"][0]]
        collapsed_page = re.sub(r"\s+", " ", "\n".join(page_lines))
        assert collapsed_header in collapsed_page, (record["record_id"], collapsed_header)


_PHASE_WORD = (
    r"(?:"
    r"Alpha|Alp~a|Alpaa|llpha|Beta|Be~a|"
    r"Ga{1,4}[mn1l•]*a|Gamaa|Gaaaa|"
    r"Delta(?:\s+pri[a-z]+)?|Epsilon|"
    r"Liq[uy]id|Liguid|Liqu[ij1l]\.d|L~quid|LiCJuid|Liqu1\.d|LiquJ\.d|"
    r"Ideal|Crystals?|crrstals|crys~als|CrJstals?|Crysta[~1]s|"
    r"Glass|Diamond|Oiaaond|Graphite|"
    r"Hexagonal|He[zxk&]agonal|Hezagonal|HeKagonal|"
    r"Body[- ]centered|Face[- ]cente[\[r]ed|Pace[- ]centered|"
    r"Orthorhombic|Orthorho[a8]bic|orthorhoabic|"
    r"Monoclinic|llonoclinic|aonoclinic|"
    r"Rhombohedral|Rho[mn]bohedral|Rhoabohedral|"
    r"Tetragonal|Tetraqonal|Cubic|"
    r"Litharge|eassicot|a-eucrrptite|~-eucryptite|"
    r"High\s+tali|Lov\s+tali|Low\s+tali|"
    r"m'\s+crystals|m\s+crystals"
    r")"
)
_PHASE_START = re.compile(r"(?ix)(?:^|(?<=\.)(?=\s)|(?<=:))[ \t]*\n?[ \t]*(" + _PHASE_WORD + r")")


def _parent_record_id(record_id: str) -> str:
    return record_id.rsplit("-phase-", 1)[0] if "-phase-" in record_id else record_id


def _split_groups(records):
    groups = {}
    for record in records:
        groups.setdefault(_parent_record_id(record["record_id"]), []).append(record)
    return {
        parent: members
        for parent, members in groups.items()
        if any("-phase-" in record["record_id"] for record in members)
    }


def _phase_clauses(header: str) -> list[str]:
    if not header:
        return []
    starts = [match.start(1) for match in _PHASE_START.finditer(header)]
    if not starts:
        return [header] if header.strip() else []
    clauses = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(header)
        while end > start and header[end - 1] in " \t\n":
            end -= 1
        if end > start:
            clauses.append(header[start:end])
    return clauses


def _phase_start_count(text: str | None) -> int:
    if not text:
        return 0
    return sum(1 for _ in _PHASE_START.finditer(text))


def _assert_split_records_phase_clauses(records):
    """Each split record holds one phase slice unless it documents a printed span."""
    for members in _split_groups(records).values():
        headers = {record.get("header_as_published") for record in members}
        assert None not in headers
        assert len(headers) == 1
        header = members[0]["header_as_published"]
        clauses = _phase_clauses(header)
        unique_clause_sets = {}
        for record in members:
            phase = record.get("phase_as_published")
            assert record["header_as_published"] == header
            if not phase:
                continue
            collapsed_phase = re.sub(r"\s+", " ", phase).strip()
            collapsed_header = re.sub(r"\s+", " ", header)
            assert collapsed_phase in collapsed_header, record["record_id"]
            starts = _phase_start_count(phase)
            owned = [clause for clause in clauses if clause in phase]
            span = record.get("phase_span_as_published")
            if starts > 1:
                assert span, record["record_id"]
                assert list(span) == _phase_clauses(phase), record["record_id"]
                for sibling in members:
                    if sibling is record:
                        continue
                    other = sibling.get("phase_as_published")
                    if other and _phase_start_count(other) == 1:
                        assert other not in phase, record["record_id"]
            else:
                assert starts <= 1, record["record_id"]
            for sibling in members:
                if sibling is record:
                    continue
                other = sibling.get("phase_as_published")
                if other and other != phase:
                    assert other not in phase, (record["record_id"], sibling["record_id"])
            unique_clause_sets.setdefault(phase, owned)
        seen = []
        for owned in unique_clause_sets.values():
            for clause in owned:
                assert clause not in seen, clause
                seen.append(clause)


def test_split_records_phase_clauses_partition_header(compilation):
    """Each split record holds one phase slice; siblings cover the header once."""
    _, records = compilation
    _assert_split_records_phase_clauses(records)


def test_exclusive_multi_clause_without_phase_span_is_rejected(compilation):
    """A second exclusive clause is illegal unless phase_span_as_published lists it."""
    _, records = compilation
    mutated = copy.deepcopy(records)
    target = next(
        record
        for record in mutated
        if record["record_id"] == f"{SOURCE_ID}-0004-phase-02"
    )
    extra = "Epsilon crystals {bee) 753 to 913 K."
    target["phase_as_published"] = target["phase_as_published"].rstrip() + " " + extra
    parent = _parent_record_id(target["record_id"])
    for record in mutated:
        if _parent_record_id(record["record_id"]) == parent:
            record["header_as_published"] = record["header_as_published"].rstrip() + " " + extra
    with pytest.raises(AssertionError, match="0004-phase-02"):
        _assert_split_records_phase_clauses(mutated)
    target["phase_span_as_published"] = _phase_clauses(target["phase_as_published"])
    _assert_split_records_phase_clauses(mutated)


def test_corrections_ledger_covers_withheld_numeric_values(compilation):
    manifest, records = compilation
    by_id = {record["record_id"]: record for record in records}
    withheld = set()
    for item in manifest["corrections"]:
        record = by_id[item["record_id"]]
        if item["column"] == "formula_weight":
            field = record["formula_weight"]
            assert field["as_published"] == item["as_published"]
            assert field["value"] is None
            assert field["ocr_suspect"] is True
            withheld.add((item["record_id"], None, "formula_weight"))
            continue
        rows = [
            row for row in record["rows"] if row["source_text_line"] == item["source_text_line"]
        ]
        assert len(rows) == 1, item
        cell = rows[0]["cells"][item["column"]]
        assert cell["as_published"] == item["as_published"]
        assert cell["value"] is None
        assert cell["ocr_suspect"] is True
        if item["action"] == "documented_shape_failure":
            assert not token_has_printed_shape(cell["as_published"], item["column"])
        withheld.add((item["record_id"], item["source_text_line"], item["column"]))
    for record in records:
        for row in record["rows"]:
            for column, cell in row["cells"].items():
                if cell["value"] is None and token_has_printed_shape(cell["as_published"], column):
                    assert (record["record_id"], row["source_text_line"], column) in withheld
        field = record["formula_weight"]
        if field["value"] is None and token_has_printed_shape(field["as_published"], "formula_weight"):
            assert (record["record_id"], None, "formula_weight") in withheld


def test_bullet_token_is_not_admitted_as_a_different_number(compilation):
    _, records = compilation
    record = next(record for record in records if record["record_id"] == f"{SOURCE_ID}-0010-phase-04")
    row = next(
        row for row in record["rows"] if row["cells"]["temperature"]["as_published"] == "1200"
    )
    cell = row["cells"]["formation_gibbs_energy"]
    assert cell["as_published"] == "• 1100"
    assert cell["value"] is None
    assert cell["ocr_suspect"] is True
    assert "•" in cell["footnote_markers"]


def test_identity_detector_flags_without_nulling_numeric_values(compilation):
    _, records = compilation
    record = next(record for record in records if record["record_id"] == f"{SOURCE_ID}-0242")
    row = next(
        row for row in record["rows"] if row["cells"]["temperature"]["as_published"] == "400"
    )
    gibbs = row["cells"]["formation_gibbs_energy"]
    log_k = row["cells"]["log_kf"]
    assert gibbs["as_published"] == "-1495.288"
    assert gibbs["value"] == -1495.288
    assert gibbs["ocr_suspect"] is True
    assert log_k["as_published"] == "195.265"
    assert log_k["value"] == 195.265
    assert log_k["ocr_suspect"] is True


def test_feedstock_element_coverage_report(compilation):
    manifest, records = compilation
    coverage = feedstock_coverage(records)
    assert coverage == manifest["feedstock_element_coverage"]
    assert set(coverage) == set(
        "Ag Al As Au B Ba Bi Br C Ca Cd Ce Cl Co Cr Cs Cu Dy Er Eu F Fe Ga Gd Ge H Hf Ho I In Ir K "
        "La Li Lu Mg Mn Mo N Na Nb Nd Ni O Os P Pb Pr Pt Rb S Sb Sc Se Si Sm Sn Sr Tb Te Th Ti Tm U V "
        "W Y Yb Zn Zr".split()
    )
    chromium = next(record for record in records if record["record_id"] == f"{SOURCE_ID}-0021")
    assert chromium["formula_as_published"] == "cr"
    assert coverage["Cr"] > 0


def test_compilation_is_never_measurement_or_battery_scoring(compilation):
    manifest, records = compilation
    assert manifest["compilation_role"] == ROLE
    assert ROLE["validation_measurement"] is False
    assert ROLE["scoring_eligible"] is False
    assert ROLE["battery_refusal"] == "gibbs_table_not_runtime_observable"
    assert all(record["compilation_role"] == ROLE for record in records)
    assert all(list(iter_published_numbers(record)) for record in records)


def test_transcribed_tables_have_rows_and_untranscribed_tables_have_reasons(compilation):
    manifest, records = compilation
    by_census = {}
    for record in records:
        by_census.setdefault(record["census_id"], []).append(record)
    transcribed = 0
    untranscribed = 0
    for group in by_census.values():
        statuses = {record["transcription_status"] for record in group}
        if "transcribed" in statuses:
            transcribed += 1
            assert all(record["transcription_status"] == "transcribed" for record in group)
            assert any(record["rows"] for record in group)
            assert all(record["rows"] for record in group if record["transcription_status"] == "transcribed")
        else:
            untranscribed += 1
            for record in group:
                assert record["transcription_status"] == "untranscribed"
                assert record["untranscribed_reasons"]
                assert not record["rows"]
                assert any(item["kind"] == "untranscribed_table" for item in record["ambiguities"])
    assert transcribed == manifest["summary"]["transcribed_table_count"]
    assert untranscribed == manifest["summary"]["untranscribed_table_count"]
    assert transcribed + untranscribed == 400
    assert manifest["summary"] == expected_summary(manifest, records)


def test_compilation_yaml_files_parse():
    import yaml

    for path in COMPILATION_ROOT.rglob("*.yaml"):
        yaml.safe_load(path.read_text(encoding="utf-8"))
    access = COMPILATION_ROOT.parents[0] / "access-status.yaml"
    yaml.safe_load(access.read_text(encoding="utf-8"))
