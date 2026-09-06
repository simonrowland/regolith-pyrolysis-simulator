"""Lossless checks against the USGS B1544 OCR layer, not simulator predictions."""

import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.reference_data.hemingway_haas_robinson_1982_usgs_b1544_loader import (
    COMPILATION_ROOT,
    EXPECTED_PDF_SHA256,
    ROLE,
    SOURCE_PDF_NAME,
    AmbiguousPrintedGridNode,
    AmbiguousPrintedTemperature,
    TemperatureNotOnPrintedGrid,
    census_from_records,
    feedstock_coverage,
    load_records,
    lookup,
    parse_number_token,
    parse_source,
    sha256_file,
)

PDF = COMPILATION_ROOT / "source" / SOURCE_PDF_NAME


@pytest.fixture(scope="module")
def corpus():
    manifest = yaml.safe_load((COMPILATION_ROOT / "manifest.yaml").read_text(encoding="utf-8"))
    records = load_records()
    return manifest, records


def test_coverage_manifest_records_census(corpus):
    manifest, records = corpus
    census = manifest["census"]
    assert len(manifest["entries"]) == len(records) == len(census) == 33
    assert [e["record_id"] for e in manifest["entries"]] == [r["record_id"] for r in records]
    assert [c["record_id"] for c in census] == [r["record_id"] for r in records]
    assert census_from_records(records) == census
    assert manifest["summary"]["record_count"] == 33
    assert manifest["compilation_role"]["validation_measurement"] is False
    assert manifest["compilation_role"]["scoring_eligible"] is False
    assert manifest["compilation_role"]["battery_refusal"] == "gibbs_table_not_runtime_observable"


def test_source_hash_and_role(corpus):
    manifest, records = corpus
    assert sha256_file(PDF) == EXPECTED_PDF_SHA256
    pdf_entry = next(s for s in manifest["source_files"] if s["path"].endswith(".pdf"))
    assert pdf_entry["sha256"] == EXPECTED_PDF_SHA256
    for record in records:
        assert record["compilation_role"] == ROLE
        assert record["schema_version"] == "literature_compilation.v1"


def assert_nested_equal(loaded, fresh, path="record"):
    assert type(loaded) is type(fresh), f"{path}: type differs"
    if isinstance(loaded, dict):
        assert loaded.keys() == fresh.keys(), f"{path}: keys differ"
        for key in loaded:
            assert_nested_equal(loaded[key], fresh[key], f"{path}.{key}")
    elif isinstance(loaded, list):
        assert len(loaded) == len(fresh), f"{path}: length differs"
        for index, (left, right) in enumerate(zip(loaded, fresh, strict=True)):
            assert_nested_equal(left, right, f"{path}[{index}]")
    else:
        assert loaded == fresh, f"{path}: {loaded!r} != {fresh!r}"


def numeric_cells(value, path="record"):
    if isinstance(value, dict):
        if "as_published" in value and "value" in value:
            yield path, value
        for key, child in value.items():
            yield from numeric_cells(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from numeric_cells(child, f"{path}[{index}]")


def test_round_trip_every_stored_field(corpus):
    _, records = corpus
    parsed = parse_source(PDF, verbose=False)
    for loaded, fresh in zip(records, parsed, strict=True):
        path = loaded["record_id"]
        assert_nested_equal(loaded, fresh, path)
        for cell_path, cell in numeric_cells(loaded, path):
            reparsed = parse_number_token(cell["as_published"])
            assert cell["value"] == reparsed["value"], f"{cell_path}: raw token does not parse back"


@pytest.mark.parametrize(
    ("record_id", "row_index", "field"),
    [
        ("usgs-b1544-corundum", 1, "heat_capacity"),
        ("usgs-b1544-prehnite", 4, "planck_function"),
        ("usgs-b1544-table-1", 9, "values.robie_1979"),
    ],
)
def test_round_trip_mutation_probe_rejects_any_nested_field(corpus, record_id, row_index, field):
    _, records = corpus
    fresh = next(record for record in parse_source(PDF) if record["record_id"] == record_id)
    mutated = copy.deepcopy(next(record for record in records if record["record_id"] == record_id))
    cell = mutated["rows"][row_index]
    for part in field.split("."):
        cell = cell[part]
    cell["as_published"] = "999.99"
    cell["value"] = "999.99"
    with pytest.raises(AssertionError, match=field.replace(".", r"\.")):
        assert_nested_equal(mutated, fresh, record_id)


def test_corundum_printed_298_nodes():
    records = load_records()
    corundum = next(r for r in records if r["record_id"] == "usgs-b1544-corundum")
    row0 = corundum["rows"][0]
    assert row0["temperature"]["as_published"] == "298.15"
    assert row0["entropy"]["as_published"] == "50.92"
    assert row0["planck_function"]["as_published"] == "50.92"
    formed = row0["formation"]["from_the_elements"]
    assert formed["enthalpy"]["as_published"] == "-1675.711"
    assert formed["gibbs_energy"]["as_published"] == "-1582.242"
    assert formed["log_kf"]["as_published"] == "277.203"
    assert lookup("usgs-b1544-corundum", "298.15", "entropy") == Decimal("50.92")
    assert lookup("usgs-b1544-corundum", Decimal("1800"), "heat_capacity") == Decimal(
        corundum["rows"][-1]["heat_capacity"]["value"]
    )


def test_lookup_refuses_off_grid_and_duplicates():
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not on the printed grid"):
        lookup("usgs-b1544-corundum", "350", "entropy")
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not on the printed grid"):
        lookup("usgs-b1544-corundum", "0", "entropy")
    with pytest.raises(TemperatureNotOnPrintedGrid, match="not on the printed grid"):
        lookup("usgs-b1544-corundum", "2500", "entropy")
    with pytest.raises(AmbiguousPrintedTemperature, match="occurs 2 times"):
        lookup("usgs-b1544-quartz", "844", "entropy")
    with pytest.raises(AmbiguousPrintedGridNode, match="ocr_suspect"):
        lookup("usgs-b1544-kaolinite", "198.15", "entropy")
    with pytest.raises(AmbiguousPrintedGridNode, match="identity check"):
        lookup("usgs-b1544-dickite", "1100", "entropy")


def test_every_ambiguous_grid_node_is_a_typed_refusal(corpus):
    _, records = corpus
    ambiguous_row_count = 0
    ambiguous_node_count = 0
    for record in records[1:]:
        rows_by_temperature = {}
        for row in record["rows"]:
            checks = row.get("identity_checks") or {}
            is_ambiguous = (row.get("temperature") or {}).get("ocr_suspect") or any(
                check.get("ok") is False for check in checks.values()
            )
            ambiguous_row_count += bool(is_ambiguous)
            value = (row.get("temperature") or {}).get("value")
            if value is not None:
                rows_by_temperature.setdefault(value, []).append(row)

        for temperature, matching_rows in rows_by_temperature.items():
            if not any(
                (row.get("temperature") or {}).get("ocr_suspect")
                or any(
                    check.get("ok") is False
                    for check in (row.get("identity_checks") or {}).values()
                )
                for row in matching_rows
            ):
                continue
            ambiguous_node_count += 1
            with pytest.raises(AmbiguousPrintedGridNode):
                lookup(record["record_id"], temperature, "entropy")

    assert ambiguous_row_count == 11
    assert ambiguous_node_count == 9


def test_table1_image_transcription_and_column_alignment(corpus):
    _, records = corpus
    table1 = next(record for record in records if record["record_id"] == "usgs-b1544-table-1")
    assert len(table1["rows"]) == 20
    assert table1["headnote_as_published"] == "[-,value not given]"
    assert table1["columns_as_published"][-1] == "Remley and others (1980)"
    corundum = table1["rows"][0]
    assert corundum["values"]["haas_1979"]["as_published"] == "-1675.711"
    assert corundum["values"]["haas_1979"]["value"] == "-1675.711"
    quartz = table1["rows"][1]
    assert "helgeson_1978" not in quartz["uncertainties"]
    assert quartz["uncertainties"]["hemley_1980"]["as_published"] == "±1.00"
    assert table1["rows"][4]["values"]["haas_1979"]["value"] == "-999.456"
    assert table1["rows"][9]["values"]["robie_1979"]["value"] == "-2591.730"


def test_marks_equations_metadata_and_formula_coverage(corpus):
    manifest, records = corpus
    substances = records[1:]
    assert all(record["formula_as_published"] for record in substances)
    coverage = feedstock_coverage(records)
    assert {element: coverage[element] for element in ("Al", "Ca", "H", "O", "Si")} == {
        "Al": 20,
        "Ca": 17,
        "H": 12,
        "O": 32,
        "Si": 25,
    }
    assert manifest["feedstock_element_coverage"] == coverage

    equations = [
        equation
        for record in substances
        for equation in record["heat_capacity_equations_as_published"]
    ]
    assert len(equations) == 42
    assert all(equation["ocr_suspect"] is False for equation in equations)
    assert sum(record["enthalpy_298_minus_0"] is not None for record in substances) == 13
    corundum = next(record for record in substances if record["record_id"] == "usgs-b1544-corundum")
    assert "- 2.46518x10^3 T^-0.5" in corundum["heat_capacity_equations_as_published"][0]["as_published"]
    assert corundum["enthalpy_298_minus_0"]["as_published"] == "10.016"

    oxide_records = [record for record in substances if "from_the_oxides" in record["formation_bases"]]
    assert len(oxide_records) == 24
    assert sum(record["row_count"] for record in oxide_records) == 319
    marks = [mark for record in oxide_records for row in record["rows"] for mark in row["marks"]]
    assert len(marks) == 638
    assert all(mark["formation_basis"] == "from_the_oxides" for mark in marks)
    assert all(mark["as_published"] == "*" for mark in marks)


def test_ocr_suspect_keeps_raw_token():
    records = load_records()
    corundum = next(r for r in records if r["record_id"] == "usgs-b1544-corundum")
    hht = corundum["rows"][0]["enthalpy_increment_over_T"]
    assert hht["as_published"] == "o.ooo"
    assert hht["ocr_suspect"] is True
    assert hht["value"] == "0.000"


def test_nothing_typed_measured(corpus):
    manifest, records = corpus
    for record in records:
        assert record["compilation_role"]["validation_measurement"] is False
        assert "measured" not in json_blob(record).lower()
    assert manifest["compilation_role"]["validation_measurement"] is False


def json_blob(record):
    import json

    return json.dumps(record)
