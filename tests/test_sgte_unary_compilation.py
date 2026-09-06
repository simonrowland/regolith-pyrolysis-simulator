"""SGTE Unary 5.0 compilation ingest — round-trip numbers and feedstock coverage.

Compilations are engine reference functions, not measurements. These tests pin
that every published FUNCTION interval re-evaluates from the parsed polynomial
to the original expression string, and that every manifest record loads back
to the same numbers as a fresh parse of unary50.tdb.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from simulator.chemistry.sgte_unary import (
    COMPILATION_ROOT,
    RECORDS_DIR,
    SOURCE_TDB,
    ROUND_TRIP_REL_TOL,
    expression_from_interval_dict,
    feedstock_elements,
    function_round_trip_failures,
    function_round_trip_ok,
    iter_record_paths,
    load_manifest,
    load_record_yaml,
    load_tdb,
    build_records,
    coverage_table,
    sha256_file,
)

EXPECTED_SHA256 = "8e38dcefbeaad1f8ed83ed1f8ccceb0e1701fb584f1bf3798f217488253d4b3f"
# Unary 5.0 has no H, F, Cl, Br, I. Pinned so a later harvest that drops more
# feedstock elements, or that silently adds guessed records for these five, goes red.
PINNED_MISSING_FEEDSTOCK_ELEMENTS = ("Br", "Cl", "F", "H", "I")


def test_source_tdb_sha256_matches_sidecar_and_corpus():
    assert SOURCE_TDB.is_file()
    digest = sha256_file(SOURCE_TDB)
    assert digest == EXPECTED_SHA256
    sidecar = yaml.safe_load((COMPILATION_ROOT / "source" / "sidecar.yaml").read_text(encoding="utf-8"))
    assert sidecar["sha256"] == EXPECTED_SHA256
    assert sidecar["files"][0]["sha256"] == EXPECTED_SHA256


def test_every_function_interval_midpoint_round_trips_to_expression_string():
    database = load_tdb()
    assert database.functions, "unary50.tdb published FUNCTION statements"
    failures = function_round_trip_failures(database)
    assert failures == []
    # Spot-check a GHSER interval that includes T**(-9) and T*LN(T).
    ghserag = database.functions["GHSERAG"]
    for interval in ghserag.intervals:
        ok, parsed, string = function_round_trip_ok(interval.expression, interval.midpoint_K())
        assert ok
        scale = max(abs(parsed), abs(string))
        if scale:
            assert abs(parsed - string) <= ROUND_TRIP_REL_TOL * scale


def test_manifest_records_parse_back_to_tdb_numbers():
    database = load_tdb()
    parsed_records = {record.record_id: record for record in build_records(database)}
    manifest = load_manifest()
    entries = manifest["entries"]
    assert len(entries) == len(parsed_records)
    assert len(iter_record_paths()) == len(entries)

    for entry in entries:
        record_id = entry["record_id"]
        path = Path(entry["path"])
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        loaded = load_record_yaml(path)
        parsed = parsed_records[record_id]
        assert loaded["record_id"] == record_id
        assert loaded["formula"] == parsed.formula
        assert loaded["phase"] == parsed.phase
        assert loaded["source"]["sha256"] == EXPECTED_SHA256
        assert entry["sha256"] == EXPECTED_SHA256
        assert entry["coefficient_count"] == parsed.coefficient_count()
        assert loaded["coefficient_count"] == parsed.coefficient_count()
        assert entry["ambiguity_count"] == len(parsed.ambiguities)

        if parsed.element is not None:
            element = loaded["element"]
            assert element["mass"]["as_published"] == parsed.element.mass_as_published
            assert element["mass"]["value"] == parsed.element.mass
            assert element["H298_minus_H0"]["as_published"] == parsed.element.h298_minus_h0_as_published
            assert element["H298_minus_H0"]["value"] == parsed.element.h298_minus_h0
            assert element["S298"]["as_published"] == parsed.element.s298_as_published
            assert element["S298"]["value"] == parsed.element.s298

        if parsed.g_parameter is not None:
            loaded_intervals = loaded["g_parameter"]["intervals"]
            assert len(loaded_intervals) == len(parsed.g_parameter.intervals)
            for loaded_interval, parsed_interval in zip(loaded_intervals, parsed.g_parameter.intervals, strict=True):
                assert loaded_interval["expression_as_published"] == parsed_interval.expression.text
                assert loaded_interval["T_low"]["as_published"] == parsed_interval.t_low_as_published
                assert loaded_interval["T_high"]["as_published"] == parsed_interval.t_high_as_published
                assert loaded_interval["T_low"]["value"] == parsed_interval.t_low
                assert loaded_interval["T_high"]["value"] == parsed_interval.t_high
                loaded_expr = expression_from_interval_dict(loaded_interval)
                assert len(loaded_expr.terms) == len(parsed_interval.expression.terms)
                for loaded_term, parsed_term in zip(loaded_expr.terms, parsed_interval.expression.terms, strict=True):
                    assert loaded_term.t_exponent == parsed_term.t_exponent
                    assert loaded_term.lnT_power == parsed_term.lnT_power
                    assert loaded_term.function == parsed_term.function
                    assert loaded_term.coefficient == parsed_term.coefficient
                ok, _, _ = function_round_trip_ok(loaded_expr, parsed_interval.midpoint_K())
                assert ok

        if parsed.functions:
            assert len(loaded["functions"]) == len(parsed.functions)
            for loaded_fn, parsed_fn in zip(loaded["functions"], parsed.functions, strict=True):
                assert loaded_fn["name"] == parsed_fn.name
                assert len(loaded_fn["intervals"]) == len(parsed_fn.intervals)
                for loaded_interval, parsed_interval in zip(
                    loaded_fn["intervals"], parsed_fn.intervals, strict=True
                ):
                    loaded_expr = expression_from_interval_dict(loaded_interval)
                    assert loaded_expr.text == parsed_interval.expression.text
                    for loaded_term, parsed_term in zip(
                        loaded_expr.terms, parsed_interval.expression.terms, strict=True
                    ):
                        assert loaded_term.coefficient == parsed_term.coefficient
                        assert loaded_term.t_exponent == parsed_term.t_exponent
                        assert loaded_term.lnT_power == parsed_term.lnT_power
                        assert loaded_term.function == parsed_term.function


def test_feedstock_element_coverage_is_complete_except_pinned_halogens_and_hydrogen():
    database = load_tdb()
    records = build_records(database)
    elements = feedstock_elements()
    assert elements, "data/feedstocks.yaml must declare elements"
    coverage = coverage_table(records, elements)
    missing = tuple(sorted(element for element, row in coverage.items() if not row["present"]))
    assert missing == PINNED_MISSING_FEEDSTOCK_ELEMENTS
    present = [element for element, row in coverage.items() if row["present"]]
    assert present
    for element in present:
        assert coverage[element]["record_count"] >= 1
        assert coverage[element]["phases"]
    # The unary file covers the metals/oxides the pyrolysis ledger actually
    # shuttles; H/F/Cl/Br/I are feedstock-declared but absent from unary50.tdb.
    for element in ("Si", "Fe", "Mg", "Al", "Ca", "Ti", "O", "Na", "K", "P", "S"):
        assert coverage[element]["present"], element


def test_ingest_is_complete_not_curated():
    database = load_tdb()
    records = build_records(database)
    assert len(database.elements) == 80
    assert len(database.functions) == 305
    assert len(database.phases) == 34
    assert sum(1 for item in database.parameters if item.kind == "G") == 396
    # SPECIES N2/O2 and ELEMENT /- / VA are kept; boron BCC/FCC/HCP functions
    # remain even though PARAMETER G for those phases was removed in v5.0.
    record_ids = {record.record_id for record in records}
    assert "N2-GAS" in record_ids
    assert "O2-GAS" in record_ids
    assert "ELECTRON_GAS" in record_ids
    assert "VA-VACUUM" in record_ids
    assert "B-BCC_A2" in record_ids
    assert "B-FCC_A1" in record_ids
    assert "B-HCP_A3" in record_ids
    boron_bcc = next(record for record in records if record.record_id == "B-BCC_A2")
    assert boron_bcc.g_parameter is None
    assert any(item.code == "function_without_g_parameter" for item in boron_bcc.ambiguities)
    # Commented superseded G expressions are ambiguities, not live records.
    os_bcc = next(record for record in records if record.record_id == "OS-BCC_A2")
    assert os_bcc.g_parameter is not None
    assert os_bcc.g_parameter.comment_superseded
    assert any(item.code == "commented_parameter_superseded" for item in os_bcc.ambiguities)


def test_compilation_refuses_validation_and_scoring():
    manifest = load_manifest()
    role = manifest["compilation_role"]
    assert role["engine_reference_input"] is True
    assert role["validation_measurement"] is False
    assert role["scoring_eligible"] is False
    assert role["battery_refusal"] == "gibbs_table_not_runtime_observable"
    sample = load_record_yaml(RECORDS_DIR / "FE-BCC_A2.yaml")
    assert sample["compilation_role"]["scoring_eligible"] is False
    assert sample["compilation_role"]["validation_measurement"] is False
    magnetic = sample["magnetic_parameters"]
    kinds = {item["kind"] for item in magnetic}
    assert "TC" in kinds
    assert "BM" in kinds


def test_nothing_is_typed_measured():
    for path in iter_record_paths():
        text = path.read_text(encoding="utf-8")
        assert "typed: measured" not in text
        assert "provenance_class: measured" not in text
        payload = yaml.safe_load(text)
        assert payload["compilation_role"]["validation_measurement"] is False
