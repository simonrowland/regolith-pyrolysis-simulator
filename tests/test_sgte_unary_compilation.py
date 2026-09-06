"""SGTE Unary 5.0 compilation ingest — round-trip numbers and feedstock coverage.

Compilations are engine reference functions, not measurements. These tests pin
that every published FUNCTION interval re-evaluates from the parsed polynomial
to the original expression string, and that every manifest record loads back
to the same numbers as a fresh parse of unary50.tdb.
"""

from __future__ import annotations

from pathlib import Path

import yaml
import pytest

from simulator.chemistry.sgte_unary import (
    COMPILATION_ROOT,
    RECORDS_DIR,
    SOURCE_TDB,
    ROUND_TRIP_REL_TOL,
    SgteUnaryError,
    TemperatureOutOfIntervalError,
    UnresolvedFunctionError,
    evaluate_function,
    evaluate_expression_string,
    expression_from_interval_dict,
    feedstock_elements,
    function_round_trip_failures,
    function_round_trip_ok,
    iter_record_paths,
    load_manifest,
    load_record_yaml,
    load_tdb,
    parse_tdb,
    build_records,
    coverage_table,
    sha256_file,
)

EXPECTED_SHA256 = "8e38dcefbeaad1f8ed83ed1f8ccceb0e1701fb584f1bf3798f217488253d4b3f"
# Unary 5.0 has no H, F, Cl, Br, I. Pinned so a later harvest that drops more
# feedstock elements, or that silently adds guessed records for these five, goes red.
PINNED_MISSING_FEEDSTOCK_ELEMENTS = ("Br", "Cl", "F", "H", "I")


@pytest.fixture(scope="module")
def record_functions():
    return {fn["name"]: fn for path in iter_record_paths()
            for fn in load_record_yaml(path).get("functions", [])}


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
    assert [(fn.name, i.t_low, i.t_high) for fn in database.functions.values()
            for i in fn.intervals if i.t_low > i.t_high] == [("GHCPHG", 298.15, 234.32)]
    failures = function_round_trip_failures(database)
    assert failures == []
    # Spot-check a GHSER interval that includes T**(-9) and T*LN(T).
    ghserag = database.functions["GHSERAG"]
    for interval in ghserag.intervals:
        ok, parsed, string = function_round_trip_ok(
            interval.expression, interval.midpoint_K(),
            {name: fn.as_dict() for name, fn in database.functions.items()},
        )
        assert ok
        scale = max(abs(parsed), abs(string))
        if scale:
            assert abs(parsed - string) <= ROUND_TRIP_REL_TOL * scale


def test_manifest_records_parse_back_to_tdb_numbers(record_functions):
    database = load_tdb(SOURCE_TDB)
    parsed_records = {record.record_id: record for record in build_records(database)}
    manifest = load_manifest()
    entries = manifest["entries"]
    entry_ids = [entry["record_id"] for entry in entries]
    root = Path(__file__).resolve().parents[1]
    entry_paths = []
    for entry in entries:
        path = Path(entry["path"])
        entry_paths.append((path if path.is_absolute() else root / path).resolve())
    record_paths = [path.resolve() for path in iter_record_paths()]

    assert len(entries) == len(parsed_records)
    assert len(entry_ids) == len(set(entry_ids))
    assert set(entry_ids) == set(parsed_records)
    assert len(entry_paths) == len(set(entry_paths))
    assert set(entry_paths) == set(record_paths)

    for entry, path in zip(entries, entry_paths, strict=True):
        record_id = entry["record_id"]
        loaded = load_record_yaml(path)
        parsed = parsed_records[record_id]
        expected = parsed.as_dict()
        assert loaded.keys() == expected.keys()
        for field, value in expected.items():
            assert loaded[field] == value, f"{record_id}.{field}"
        assert loaded["source"]["sha256"] == EXPECTED_SHA256
        assert entry["sha256"] == EXPECTED_SHA256
        assert entry["coefficient_count"] == parsed.coefficient_count()
        assert loaded["coefficient_count"] == parsed.coefficient_count()
        assert entry["ambiguity_count"] == len(parsed.ambiguities)
        if parsed.g_parameter is not None:
            for loaded_interval, parsed_interval in zip(
                loaded["g_parameter"]["intervals"], parsed.g_parameter.intervals, strict=True
            ):
                loaded_expression = expression_from_interval_dict(loaded_interval)
                assert loaded_expression == parsed_interval.expression
                ok, _, _ = function_round_trip_ok(
                    loaded_expression, parsed_interval.midpoint_K(), record_functions
                )
                assert ok
        for loaded_function, parsed_function in zip(
            loaded.get("functions", []), parsed.functions, strict=True
        ):
            for loaded_interval, parsed_interval in zip(
                loaded_function["intervals"], parsed_function.intervals, strict=True
            ):
                assert expression_from_interval_dict(loaded_interval) == parsed_interval.expression


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


def test_liquid_suffix_is_not_a_sublattice():
    database = load_tdb(SOURCE_TDB)
    suffixes = {phase.name_as_published.split(":", 1)[1]
                for phase in database.phases.values() if ":" in phase.name_as_published}
    assert suffixes == {"L"}
    for phase in database.phases.values():
        assert len(phase.constituents) == phase.n_sublattices
        assert all(group != (suffix,) for group in phase.constituents for suffix in suffixes)
    liquid = database.phases["LIQUID"]
    assert liquid.name_as_published == "LIQUID:L"
    assert liquid.n_sublattices == 1
    assert len(liquid.constituents) == 1
    assert len(liquid.constituents[0]) == 78
    assert liquid.constituents[0][0] == "AG"
    assert liquid.constituents[0][-1] == "ZR"
    assert "FE" in liquid.constituents[0]
    records = build_records(database)
    liquids = [record.record_id for record in records if record.phase == "LIQUID"]
    assert len(liquids) == 78
    assert {"AG-LIQUID", "FE-LIQUID"} <= set(liquids)


def test_source_phase_sublattice_count_mismatch_refuses():
    source = SOURCE_TDB.read_text(encoding="utf-8")
    declaration = "PHASE LIQUID:L % 1 1 !"
    malformed = source.replace(declaration, "PHASE LIQUID:L % 2 1 1 !", 1)
    assert malformed != source
    with pytest.raises(SgteUnaryError, match="LIQUID:L: constituent group count"):
        parse_tdb(malformed, source_path=SOURCE_TDB.as_posix(), source_sha256=EXPECTED_SHA256)


def test_source_function_intervals_and_element_reference_phase_are_structural():
    database = load_tdb(SOURCE_TDB)
    ghserfe = database.functions["GHSERFE"]
    assert len(ghserfe.intervals) == 2
    assert [(interval.t_low, interval.t_high) for interval in ghserfe.intervals] == [
        (298.15, 1811.0),
        (1811.0, 6000.0),
    ]
    assert database.elements["FE"].reference_phase == "BCC_A2"
    assert database.elements["AG"].reference_phase == "FCC_A1"


@pytest.mark.parametrize("temperature", [100, 2000, 10000])
def test_chosen_interval_refuses_extrapolation(temperature):
    database = load_tdb(SOURCE_TDB)
    expression = database.functions["GHSERFE"].intervals[0].expression
    assert expression.evaluate(1000) == pytest.approx(-41450.417956569676, rel=1e-9)
    with pytest.raises(TemperatureOutOfIntervalError) as caught:
        expression.evaluate(temperature)
    assert caught.value.temperature_K == temperature
    assert caught.value.certified_band == ((298.15, 1811.0),)


def test_function_selects_interval_and_refuses_outside_union():
    database = load_tdb(SOURCE_TDB)
    functions = {name: function.as_dict() for name, function in database.functions.items()}
    for temperature in (100, 10000):
        with pytest.raises(TemperatureOutOfIntervalError) as caught:
            evaluate_function("GHSERFE", temperature, functions)
        assert caught.value.temperature_K == temperature
        assert caught.value.certified_band == ((298.15, 1811.0), (1811.0, 6000.0))
    second = database.functions["GHSERFE"].intervals[1].expression
    assert evaluate_function("GHSERFE", 2000, functions) == second.evaluate(2000)


def test_unresolved_reference_refuses_and_records_resolve(record_functions):
    record = load_record_yaml(RECORDS_DIR / "FE-BCC_A2.yaml")
    interval = record["g_parameter"]["intervals"][0]
    expression = expression_from_interval_dict(interval)
    for evaluate in (lambda: expression.evaluate(1000),
                     lambda: evaluate_expression_string(expression.text, 1000)):
        with pytest.raises(UnresolvedFunctionError) as caught:
            evaluate()
        assert caught.value.symbol == "GHSERFE"
    assert evaluate_function("GHSERFE", 1000, record_functions) == pytest.approx(-41450.417956569676, rel=1e-9)
    values = {name: evaluate_function(name, 1000, record_functions) for name in expression.function_names}
    assert expression.evaluate(1000, values) == pytest.approx(-41450.417956569676, rel=1e-9)
    # GLIQFE itself references GHSERFE; a missing dependency must refuse recursively.
    incomplete = dict(record_functions)
    del incomplete["GHSERFE"]
    with pytest.raises(UnresolvedFunctionError) as caught:
        evaluate_function("GLIQFE", 1000, incomplete)
    assert caught.value.symbol == "GHSERFE"


def test_missing_interval_bound_refuses(record_functions):
    interval = dict(record_functions["GHSERFE"]["intervals"][0])
    del interval["T_high"]
    with pytest.raises(SgteUnaryError, match="certified temperature bounds"):
        expression_from_interval_dict(interval)


def test_published_reversed_mercury_interval_refuses():
    database = load_tdb(SOURCE_TDB)
    function = database.functions["GHCPHG"]
    expression = function.intervals[0].expression
    with pytest.raises(TemperatureOutOfIntervalError):
        expression.evaluate((298.15 + 234.32) / 2)
    functions = {name: parsed.as_dict() for name, parsed in database.functions.items()}
    assert evaluate_function("GHCPHG", 300, functions) == function.intervals[1].expression.evaluate(300)


@pytest.mark.parametrize("coefficient", ["1E-3", "1.E-3", ".1E-2"])
def test_scientific_notation_is_not_a_function_reference(coefficient):
    assert evaluate_expression_string(f"{coefficient}*T", 1000) == pytest.approx(1)
