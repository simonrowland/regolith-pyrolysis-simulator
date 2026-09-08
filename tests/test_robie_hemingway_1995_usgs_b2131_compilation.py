"""Native record coverage, OCR fidelity, and exact-grid refusal contract."""

import copy
import json
import re

import pytest

from simulator.reference_data import robie_hemingway_1995_usgs_b2131_loader as b2131_loader
from simulator.reference_data.robie_hemingway_1995_usgs_b2131_loader import (
    COMPILATION_ROOT,
    OcrSuspectTableValueError,
    PrintedTemperatureUnavailable,
    UntranscribedTableError,
    load_manifest,
    load_records,
    lookup_temperature,
)

IMAGE_VERIFIED_CORRECTIONS = (
    ("high-temperature-p189", 195, 10, "temperature", "3.200", "1200", 1200.0),
    ("high-temperature-p199", 205, 15, "temperature", "1100", "1700", 1700.0),
    ("high-temperature-p201", 207, 1, "enthalpy_function", "0.153", "0.63", 0.63),
    ("high-temperature-p205", 211, 16, "temperature", "1.800", "1800", 1800.0),
    ("high-temperature-p217", 223, 16, "temperature", ".1800", "1800", 1800.0),
    ("high-temperature-p219", 225, 5, "temperature", "100", "700", 700.0),
    ("high-temperature-p223", 229, 3, "temperature", ".500", "500", 500.0),
)

PRINTED_NONSUSPECT_REVERSALS = (
    ("high-temperature-p067", "gibbs_function", 60.32, 60.31, 73),
    ("high-temperature-p073", "gibbs_function", 28.45, 28.44, 79),
    ("high-temperature-p082", "gibbs_function", 37.51, 37.49, 88),
    ("high-temperature-p089", "temperature", 1600.0, 17.0, 95),
    ("high-temperature-p093", "gibbs_function", 29.09, 29.08, 99),
    ("high-temperature-p093", "gibbs_function", 58.68, 58.67, 99),
    ("high-temperature-p295", "gibbs_function", 149.60, 149.56, 301),
)


def numeric_cells(value):
    if isinstance(value, dict):
        if "raw" in value and "value" in value:
            yield value
        for child in value.values():
            yield from numeric_cells(child)
    elif isinstance(value, list):
        for child in value:
            yield from numeric_cells(child)


def _assert_image_verified_corrections(records):
    by_id = {record["record_id"]: record for record in records}
    provenances = []
    for record_id, page, row_index, column, raw, printed, value in IMAGE_VERIFIED_CORRECTIONS:
        record = by_id[record_id]
        assert record["pdf_page"] == page
        cell = record["rows"][row_index]["cells"][column]
        assert cell["raw"] == raw
        assert cell["printed_token"] == printed
        assert cell["value"] == value
        assert cell["ocr_suspect"] is True
        correction = next(item for item in record["corrections"] if item["column"] == column)
        assert correction["raw_token"] == raw
        assert correction["printed_token"] == printed
        assert printed in correction["image_quote"]
        separator_only = raw.replace(".", "").replace(",", "") == printed.replace(
            ".", ""
        ).replace(",", "")
        assert correction["correction_provenance"] == (
            "separator_repair" if separator_only else "image_read_reconstruction"
        )
        provenances.append(correction["correction_provenance"])
    assert provenances.count("separator_repair") == 3
    assert provenances.count("image_read_reconstruction") == 4


def test_image_verified_corrections_and_mutation_probe():
    records = list(load_records(include_ocr_suspect=True))
    _assert_image_verified_corrections(records)
    mutated = copy.deepcopy(records)
    cell = next(r for r in mutated if r["record_id"] == "high-temperature-p201")["rows"][1][
        "cells"
    ]["enthalpy_function"]
    cell["printed_token"] = "0.153"
    cell["value"] = 0.153
    with pytest.raises(AssertionError):
        _assert_image_verified_corrections(mutated)


def _nonsuspect_adjacent_reversals(records):
    result = []
    for record in records:
        for column in ("temperature", "gibbs_function"):
            for left, right in zip(record.get("rows") or [], (record.get("rows") or [])[1:]):
                if not isinstance(left, dict) or not isinstance(right, dict):
                    continue
                left_cells = left.get("cells")
                right_cells = right.get("cells")
                if not isinstance(left_cells, dict) or not isinstance(right_cells, dict):
                    continue
                left_cell = left_cells.get(column)
                right_cell = right_cells.get(column)
                if not left_cell or not right_cell:
                    continue
                if left_cell["value"] is None or right_cell["value"] is None:
                    continue
                if left_cell["ocr_suspect"] or right_cell["ocr_suspect"]:
                    continue
                if right_cell["value"] < left_cell["value"]:
                    result.append(
                        (
                            record["record_id"], column, left_cell["value"],
                            right_cell["value"], record["pdf_page"],
                        )
                    )
    return tuple(result)


def test_image_verified_printed_reversal_allowlist_and_mutation_probe():
    records = list(load_records(include_ocr_suspect=True))
    assert _nonsuspect_adjacent_reversals(records) == PRINTED_NONSUSPECT_REVERSALS
    p107 = next(record for record in records if record["record_id"] == "high-temperature-p107")
    entropies = [row["cells"]["entropy"]["value"] for row in p107["rows"]]
    assert any((left, right) == (162.39, 153.84) for left, right in zip(entropies, entropies[1:]))
    mutated = copy.deepcopy(records)
    target = next(record for record in mutated if record["record_id"] == "high-temperature-p089")
    next(row for row in target["rows"] if row["cells"]["temperature"]["value"] == 17.0)[
        "cells"
    ]["temperature"]["value"] = 1700.0
    assert _nonsuspect_adjacent_reversals(mutated) != PRINTED_NONSUSPECT_REVERSALS


def test_every_record_and_numeric_token_is_internally_consistent():
    manifest = load_manifest()
    for entry, record in zip(
        manifest["entries"], load_records(include_ocr_suspect=True), strict=True
    ):
        if record.get("table_kind") == "high_temperature":
            for row in record["rows"]:
                assert re.split(r"\s{2,}", row["raw"].strip()) == [cell["raw"] for cell in row["cells"].values()]
        if "source_text" in record:
            source_lines = record["source_text"].splitlines()
            for row in record["rows"]:
                for cell in numeric_cells(row):
                    if cell.get("start_offset") is not None:
                        assert source_lines[row["source_line_index"]][cell["start_offset"]:cell["end_offset"]] == cell["raw"]
        for cell in numeric_cells(record):
            assert isinstance(cell["raw"], str)
            if cell["value"] is not None:
                token = cell.get("printed_token") or cell.get("numeric_token") or cell["raw"]
                assert cell["value"] == float(token)
                if cell.get("printed_token"):
                    assert cell["ocr_suspect"]
            elif cell["raw"].strip():
                assert cell["ocr_suspect"]


_PRINTED_SUBSTANCE_NAME = re.compile(
    r"^[A-Z][A-Z'·.\-]{4,}(?:\s+[A-Z][A-Z'·.\-]+)*(?:\s*\([^)]*\))?\s*$"
)


def merged_neighbour_markers(record):
    """A second substance table absorbed into this record: T-grid restart or an in-row header."""
    markers = []
    rows = record.get("rows") or []
    kind = record.get("table_kind")
    name = (record.get("name_as_published") or "").strip()
    record_id = record.get("record_id")
    if kind == "high_temperature":
        n298 = 0
        for row in rows:
            raw = str(((row.get("cells") or {}).get("temperature") or {}).get("raw") or "").strip()
            if re.fullmatch(r"298[.,]15", raw):
                n298 += 1
        if n298 > 1:
            markers.append(f"{record_id}: T grid restarts ({n298} printed 298.15 rows)")
        return markers
    if kind == "reference_state_298K":
        weights = []
        for row in rows:
            value = ((row.get("cells") or {}).get("weight") or {}).get("value")
            if isinstance(value, (int, float)) and value > 10:
                weights.append(value)
        if len(set(weights)) > 1:
            markers.append(f"{record_id}: multiple formula weights {weights}")
    for index, row in enumerate(rows):
        if index < 2:
            continue
        label = (row.get("label_raw") or "").strip()
        if not label or label == name or re.search(r"\d", label):
            continue
        if "STD" in label and "STATE" in label:
            continue
        if _PRINTED_SUBSTANCE_NAME.match(label):
            markers.append(f"{record_id}: substance header {label!r} inside rows at index {index}")
    return markers


def test_census_coverage_includes_explicit_untranscribed_tables():
    manifest = load_manifest()
    records = list(load_records(include_ocr_suspect=True))
    ids = [record["record_id"] for record in records]
    assert len(ids) == len(set(ids))
    assert len(records) == manifest["summary"]["record_count"] == manifest["census"]["record_count"]
    assert set(ids) == set(manifest["census"]["record_ids"])
    independent_census = json.loads((COMPILATION_ROOT / "census.json").read_text())
    assert manifest["census"] == independent_census
    for page in independent_census["summary_page_record_counts"]:
        assert sum(record["pdf_page"] == page["pdf_page"] for record in records) == page["record_count"]
    assert {record["pdf_page"] for record in records if record.get("table_kind") == "high_temperature"} == set(range(73, 403))
    assert sum(record.get("transcription_status") == "untranscribed" for record in records) == manifest["summary"]["untranscribed_record_count"]
    assert manifest["compilation_role"]["scoring_eligible"] is False
    assert manifest["compilation_role"]["validation_measurement"] is False


def test_records_do_not_merge_neighbouring_substances():
    markers = []
    for record in load_records(include_ocr_suspect=True):
        markers.extend(merged_neighbour_markers(record))
    assert markers == []


def test_remerged_pair_fails_substance_boundary_census():
    records = {
        record["record_id"]: record for record in load_records(include_ocr_suspect=True)
    }
    parent = json.loads(json.dumps(records["reference-p029-03"]))
    child = json.loads(json.dumps(records["reference-p029-04"]))
    merged = json.loads(json.dumps(parent))
    merged["source_text"] = parent["source_text"] + "\n\n" + child["source_text"]
    offset = len(parent["source_text"].splitlines()) + 1
    extra = []
    for row in child["rows"]:
        row = dict(row)
        row["source_line_index"] = row["source_line_index"] + offset
        extra.append(row)
    merged["rows"] = list(parent["rows"]) + extra
    markers = merged_neighbour_markers(merged)
    with pytest.raises(AssertionError):
        assert markers == []


def test_page_29_thenardite_and_anglesite_are_separate_records():
    by_name = {
        record.get("name_as_published"): record
        for record in load_records(include_ocr_suspect=True)
        if record.get("page") == 29
    }
    thenardite = by_name["THENARDITE"]
    anglesite = by_name["ANGLESITE"]
    mascagnite = by_name["MASCAGNITE"]
    morenosite = by_name["MORENOSITE"]
    assert thenardite["formula_as_published"] == "Na2S04"
    assert anglesite["formula_as_published"] == "PbS04"
    assert thenardite["record_id"] != mascagnite["record_id"]
    assert anglesite["record_id"] != morenosite["record_id"]
    thenardite_weights = {
        row["cells"]["weight"]["value"]
        for row in lookup_temperature(
            thenardite["record_id"], 298.15, include_ocr_suspect=True
        )
        if row["cells"]["weight"]["value"] is not None
    }
    mascagnite_weights = {
        row["cells"]["weight"]["value"]
        for row in lookup_temperature(
            mascagnite["record_id"], 298.15, include_ocr_suspect=True
        )
        if row["cells"]["weight"]["value"] is not None
    }
    anglesite_weights = {
        row["cells"]["weight"]["value"]
        for row in lookup_temperature(
            anglesite["record_id"], 298.15, include_ocr_suspect=True
        )
        if row["cells"]["weight"]["value"] is not None
    }
    morenosite_weights = {
        row["cells"]["weight"]["value"]
        for row in lookup_temperature(
            morenosite["record_id"], 298.15, include_ocr_suspect=True
        )
        if row["cells"]["weight"]["value"] is not None
    }
    assert thenardite_weights == {142.043}
    assert mascagnite_weights == {132.141}
    assert anglesite_weights == {303.264}
    assert morenosite_weights == {280.861}


def test_feedstock_element_coverage_matches_existing_coverage_helper():
    from tools.harvest_atct_compilation import feedstock_coverage

    records = [
        {"formula": record.get("formula_as_published") or ""}
        for record in load_records(include_ocr_suspect=True)
    ]
    assert feedstock_coverage(records) == load_manifest()["feedstock_element_coverage"]


def test_silver_printed_anchor_and_transition_rows():
    rows = lookup_temperature("high-temperature-p067", 298.15, include_ocr_suspect=True)
    assert len(rows) == 1
    assert rows[0]["cells"]["cp"]["raw"] == "25.40"
    assert rows[0]["cells"]["cp"]["value"] == 25.4
    assert rows[0]["cells"]["entropy"]["value"] == 42.55
    # Source OCR reads o.oo; numeric zero must not be manufactured.
    assert rows[0]["cells"]["enthalpy_function"]["raw"] == "o.oo"
    assert rows[0]["cells"]["enthalpy_function"]["value"] is None
    assert rows[0]["cells"]["enthalpy_function"]["ocr_suspect"]
    transition = lookup_temperature(
        "high-temperature-p067", 1234.9, include_ocr_suspect=True
    )
    assert [row["cells"]["cp"]["value"] for row in transition] == [31.96, 33.47]


@pytest.mark.parametrize("temperature", [0, 298.14, 299, 1800.01, float("nan"), float("inf")])
def test_refuses_every_non_grid_temperature(temperature):
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("high-temperature-p067", temperature)


def test_transcribed_records_have_rows_and_untranscribed_carry_a_reason():
    for record in load_records(include_ocr_suspect=True):
        status = record.get("transcription_status")
        if status == "untranscribed":
            reasons = record.get("ambiguities") or []
            assert reasons, record["record_id"]
            assert any(
                (isinstance(item, str) and item.strip())
                or (isinstance(item, dict) and (item.get("reason") or item.get("kind")))
                for item in reasons
            ), record["record_id"]
        else:
            assert record.get("rows"), record["record_id"]


def test_p176_printed_grid_is_transcribed():
    rows = lookup_temperature("high-temperature-p176", 298.15, include_ocr_suspect=True)
    assert len(rows) == 1
    assert rows[0]["cells"]["cp"]["raw"] == "62.60"
    assert rows[0]["cells"]["cp"]["value"] == 62.6
    assert rows[0]["cells"]["enthalpy_function"]["raw"] == "0.00"
    assert rows[0]["cells"]["enthalpy_function"]["value"] == 0.0


def test_unknown_record_has_no_default():
    with pytest.raises(KeyError):
        lookup_temperature("not-a-record", 298.15)


def test_atomic_weight_has_no_temperature_grid():
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("atomic-weight-001", 298.15)


def test_reference_table_accepts_only_its_single_printed_temperature():
    rows = lookup_temperature("reference-p005-01", 298.15, include_ocr_suspect=True)
    assert rows[0]["cells"]["volume"]["value"] == 10.272
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("reference-p005-01", 300)


def test_coefficient_bounds_are_not_a_printed_function_grid():
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("cp-p041-01", 298.15)


# Printed B2131 function grid: reference-state 298.15 K plus the 100 K steps used
# in the high-temperature tables (300 K through 1800 K). Transition temperatures
# are extra printed rows, not members of this heading grid.
PRINTED_TEMPERATURE_GRID = frozenset({298.15} | {float(t) for t in range(300, 1801, 100)})


def test_grid_values_are_printed_members_and_reference_state_looks_up_298_15(monkeypatch):
    records = list(load_records(include_ocr_suspect=True))
    manifest = load_manifest()
    monkeypatch.setattr(b2131_loader, "load_manifest", lambda root=COMPILATION_ROOT: manifest)
    for record in records:
        for cell in record.get("temperature_grid") or []:
            raw = cell.get("raw")
            value = cell.get("value")
            if value is not None:
                assert value in PRINTED_TEMPERATURE_GRID, (record["record_id"], cell)
            try:
                raw_is_printed = float(raw) in PRINTED_TEMPERATURE_GRID
            except (TypeError, ValueError):
                raw_is_printed = False
            if not raw_is_printed:
                assert cell["ocr_suspect"], record["record_id"]
                assert cell.get("printed_token"), record["record_id"]
                assert float(cell["printed_token"]) in PRINTED_TEMPERATURE_GRID
                assert value == float(cell["printed_token"])
                matching = [
                    item for item in record.get("corrections") or []
                    if item.get("raw_token") == raw and item.get("printed_token") == cell["printed_token"]
                ]
                assert matching, record["record_id"]
                assert matching[0]["page"] == record["page"]
                assert matching[0]["record_id"] == record["record_id"]
                assert matching[0].get("image_quote")
        if record.get("table_kind") == "reference_state_298K":
            rows = lookup_temperature(
                record["record_id"], 298.15, include_ocr_suspect=True
            )
            assert rows, record["record_id"]
    with pytest.raises(PrintedTemperatureUnavailable):
        lookup_temperature("reference-p039-13", 291.15)


def test_public_loaders_require_explicit_ocr_suspect_opt_in():
    with pytest.raises(OcrSuspectTableValueError):
        list(load_records())
    with pytest.raises(OcrSuspectTableValueError):
        lookup_temperature("high-temperature-p067", 298.15)
    rows = lookup_temperature(
        "high-temperature-p067", 298.15, include_ocr_suspect=True
    )
    assert rows[0]["cells"]["enthalpy_function"]["ocr_suspect"] is True
