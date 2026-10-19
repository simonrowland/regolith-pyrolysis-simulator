"""Render-grounded checks for Kress and Carmichael (1991)."""

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import EvidenceClass, Quantity
from simulator.battery.migrate import (
    Migrator,
    fe3_fe2_ratio_from_oxide_wt_pct,
    wt_pct_to_mole_fraction,
)
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / (
    "data/literature/extracts/"
    "kress-carmichael-1991-iron-redox-calibration.yaml"
)
SOURCE_ID = "kress-carmichael-1991-iron-redox-calibration"
TABLE3_OXIDES = (
    "SiO2",
    "TiO2",
    "Al2O3",
    "Fe2O3",
    "MnO",
    "MgO",
    "CaO",
    "Na2O",
    "K2O",
)

# Initial/final pairs transcribed independently from the 600 dpi PDF page 4
# render (printed folio 85), in the printed oxide-column order above.
TABLE3_RENDER = """
0011-0014 initial 46.87 0.00 0.00 28.78 0.00 0.00 25.60 0.00 0.00
0011-0014 final 46.45 0.00 2.03 26.08 0.00 0.00 24.81 0.00 0.00
0021-0025 initial 46.45 0.00 2.03 26.08 0.00 0.00 24.81 0.00 0.00
0021-0025 final 43.01 0.00 7.08 25.48 0.00 0.00 23.43 0.00 0.00
021-023 initial 39.61 0.00 11.94 23.65 0.00 0.00 22.18 0.00 0.00
021-023 final 38.67 0.00 18.11 22.84 0.00 0.00 20.96 0.00 0.00
031-036 initial 38.67 0.00 18.11 22.84 0.00 0.00 20.96 0.00 0.00
031-036 final 37.41 0.00 21.14 21.65 0.00 0.00 20.31 0.00 0.00
041 initial 37.41 0.00 21.14 21.65 0.00 0.00 20.31 0.00 0.00
041 final 34.62 0.00 27.07 20.27 0.00 0.00 18.95 0.00 0.00
051-056 initial 66.69 0.00 0.00 22.58 0.00 0.00 0.00 10.57 0.00
051-056 final 66.33 0.00 0.00 22.23 0.00 0.00 0.00 10.09 0.00
081-086 initial 65.31 0.00 0.01 22.26 0.00 0.00 0.00 10.47 0.00
081-086 final 62.60 0.00 6.24 19.26 0.00 0.00 0.00 10.45 0.00
091-0910 initial 32.80 0.00 0.00 37.70 0.00 0.00 0.00 29.50 0.00
091-0910 final 32.62 0.00 0.52 37.55 0.00 0.00 0.00 27.48 0.00
101-107 initial 32.62 0.00 0.52 37.55 0.00 0.00 0.00 27.48 0.00
101-107 final 29.27 0.00 9.21 34.25 0.00 0.00 0.00 26.13 0.00
111-117 initial 32.80 0.00 0.00 37.70 0.00 0.00 0.00 29.50 0.00
111-117 final 32.44 0.00 1.15 36.90 0.00 0.00 0.00 27.46 0.00
121-126 initial 32.44 0.00 1.15 36.90 0.00 0.00 0.00 27.46 0.00
121-126 final 31.45 0.00 4.52 35.94 0.00 0.00 0.00 26.56 0.00
131-137 initial 51.18 0.00 0.00 21.60 0.00 0.00 0.00 27.22 0.00
131-137 final 47.82 0.00 0.00 22.74 0.00 0.00 0.00 28.11 0.00
141-147 initial 47.82 0.00 0.00 22.74 0.00 0.00 0.00 28.11 0.00
141-147 final 47.97 0.00 0.96 22.66 0.00 0.00 0.00 27.49 0.00
151-157 initial 51.18 0.00 0.00 21.60 0.00 0.00 0.00 27.22 0.00
151-157 final 49.16 0.00 0.16 22.79 0.00 0.00 0.00 27.69 0.00
161-167 initial 49.16 0.00 0.16 22.79 0.00 0.00 0.00 27.69 0.00
161-167 final 47.80 0.00 3.02 22.06 0.00 0.00 0.00 26.44 0.00
171-174 initial 44.77 2.97 12.20 22.58 N.A. 5.36 8.90 2.70 0.61
171-174 final 43.46 2.66 17.98 19.99 0.15 5.04 8.45 2.35 0.59
181-182 initial 43.46 2.66 17.98 19.99 0.15 5.04 8.45 2.35 0.59
181-182 final 40.38 2.58 22.57 19.68 0.16 4.82 8.06 2.22 0.55
201-206 initial 47.25 3.44 14.07 18.74 N.A. 4.71 7.38 3.35 1.06
201-206 final 44.83 2.78 19.94 16.75 0.22 4.29 7.89 2.93 0.85
211-213 initial 47.25 3.44 14.07 18.74 N.A. 4.71 7.38 3.35 1.06
211-213 final 45.59 3.37 17.48 17.59 0.23 4.33 7.08 3.43 1.04
"""

TABLE4_Q01_COMPOSITION = {
    "SiO2": "38.86", "TiO2": "0.00", "Al2O3": "17.00",
    "Fe2O3": "16.93", "FeO": "5.41", "MnO": "0.00",
    "MgO": "0.00", "CaO": "20.89", "Na2O": "0.00",
    "K2O": "0.00", "P2O5": "0.00",
}
TABLE5_B27_COMPOSITION = {
    "SiO2": "50.46", "TiO2": "1.88", "Al2O3": "13.87",
    "Fe2O3": "9.54", "FeO": "3.42", "MnO": "0.17",
    "MgO": "6.84", "CaO": "10.65", "Na2O": "2.68", "K2O": "0.19",
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _expected_table3() -> dict[tuple[str, str], str]:
    expected = {}
    for line in TABLE3_RENDER.strip().splitlines():
        row = line.split()
        sample = " ".join(row[:2])
        assert len(row[2:]) == len(TABLE3_OXIDES)
        expected.update(zip(((sample, oxide) for oxide in TABLE3_OXIDES), row[2:]))
    return expected


def _actual_table3(document: dict) -> dict[tuple[str, str], str]:
    actual = {}
    for oxide in TABLE3_OXIDES:
        for observation in document["species"][oxide]["observations"]:
            locator = observation["locator"]
            if locator.get("table") != "3":
                continue
            values = observation["values"]
            printed = (
                values["unavailable"]["verbatim"]
                if "unavailable" in values
                else str(values["value"])
            )
            actual[(locator["row"], oxide)] = printed
    return actual


def _assert_table3_matches(document: dict, expected: dict) -> None:
    assert _actual_table3(document) == expected


def _migrated() -> tuple[dict, Migrator]:
    document = _document()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return document, migrator


def test_extract_validates_and_table3_matches_independent_render_grid() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    assert check_all_fidelity_samples_match(document, label=SOURCE_ID) == []

    expected = _expected_table3()
    actual = _actual_table3(document)
    assert len(expected) == 38 * 9
    assert actual == expected

    # Three render pins have printed physical-page locators (PDF page 4).
    first = next(
        observation
        for observation in document["species"]["SiO2"]["observations"]
        if observation["observation_id"] == "kc91_t3_0011_0014_initial_SiO2"
    )
    assert first["values"]["value"] == "46.87"
    assert first["locator"]["page"] == first["locator"]["pdf_page_index"] + 1 == 4
    assert actual[("021-023 initial", "Fe2O3")] == "23.65"
    assert actual[("211-213 final", "Na2O")] == "3.43"


def test_table3_render_grid_and_extract_cell_mutations_fail() -> None:
    document = _document()
    expected = _expected_table3()
    _assert_table3_matches(document, expected)

    mutated_grid = deepcopy(expected)
    mutated_grid[("0011-0014 initial", "SiO2")] = "46.88"
    with pytest.raises(AssertionError):
        _assert_table3_matches(document, mutated_grid)

    mutated_extract = deepcopy(document)
    cell = next(
        observation
        for observation in mutated_extract["species"]["SiO2"]["observations"]
        if observation["observation_id"] == "kc91_t3_0011_0014_initial_SiO2"
    )
    cell["values"]["value"] = "46.88"
    with pytest.raises(AssertionError):
        _assert_table3_matches(mutated_extract, expected)


def test_isolated_migration_preserves_species_conditions_and_reduced_ratios() -> None:
    document, migrator = _migrated()
    records = migrator.result.observations
    assert len(records) == 881
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues

    formula_by_id = {}
    rows_by_id = {}
    typed_count = 0
    unavailable_count = 0
    for formula, body in document["species"].items():
        for observation in body.get("observations", []):
            observation_id = observation["observation_id"]
            formula_by_id[observation_id] = formula
            values = observation.get("values", {})
            if values.get("quantity") or observation.get("type") == "concentration_series":
                typed_count += 1
            if "unavailable" in values:
                unavailable_count += 1
            rows_by_id[observation_id] = observation

    assert typed_count == 881
    assert unavailable_count == 3
    for key, record in records.items():
        source_observation_id = key.split("::", 1)[1].split("::", 1)[0]
        assert record.identity.species.formula == formula_by_id[source_observation_id]
        assert record.identity.temperature_K.is_value or record.identity.temperature_K.is_unknown
        assert record.point_conditions is not None
        assert record.point_conditions["temperature_K"].locator is not None
        assert all(condition.locator is not None for condition in record.point_conditions.values())

    for observation_id, observation in rows_by_id.items():
        values = observation.get("values", {})
        prefix = f"{SOURCE_ID}::{observation_id}"
        migrated = [record for key, record in records.items() if key == prefix or key.startswith(prefix + "::")]
        if not migrated:
            continue
        if "T_C" in values:
            expected_temperature = Decimal(values["T_C"]) + Decimal("273.15")
            assert all(record.identity.temperature_K.value == expected_temperature for record in migrated)
        elif observation.get("locator", {}).get("table") == "3":
            assert all(record.identity.temperature_K.is_unknown for record in migrated)
            assert all(
                record.point_conditions["temperature_K"].state.is_unknown
                and record.point_conditions["temperature_K"].locator.page == 4
                for record in migrated
            )
            assert all(record.point_conditions["total_pressure_Pa"].state.is_unknown for record in migrated)

        if "quench_statement" in observation:
            assert observation["quench_statement"]["preservation"] == "not_stated"

    quantities = Counter(
        record.identity.quantity.value for record in records.values()
    )
    assert quantities == {
        Quantity.EQUILIBRIUM_PHASE_COMPOSITION: 835,
        Quantity.FE3_FE2_RATIO: 46,
    }
    reduced = [
        record
        for key, record in records.items()
        if "kc91_fe3_t4_" in key or "kc91_fe3_t5_" in key
    ]
    assert len(reduced) == 46
    assert all(record.evidence.class_.value is EvidenceClass.MEASURED_REDUCED for record in reduced)
    assert all(record.identity.composition.is_value for record in reduced)
    assert all(
        record.identity.total_pressure_Pa.is_value
        and record.identity.total_pressure_Pa.value == Decimal("101325")
        for record in reduced
    )

    for observation_id, expected_composition in (
        ("kc91_fe3_t4_Q01", TABLE4_Q01_COMPOSITION),
        ("kc91_fe3_t5_B27", TABLE5_B27_COMPOSITION),
    ):
        source = rows_by_id[observation_id]
        assert source["values"]["composition_wt_pct"] == expected_composition
        record = next(item for item in reduced if observation_id in item.observation_id)
        expected_mole_fractions = wt_pct_to_mole_fraction(
            {oxide: Decimal(amount) for oxide, amount in expected_composition.items()}
        )
        assert record.identity.composition.value.as_map() == expected_mole_fractions.as_map()

    table4_ratio = next(record for record in reduced if "kc91_fe3_t4_Q01" in record.observation_id)
    assert table4_ratio.point_conditions["fO2_log"].state.is_unknown

    table5_ratio = next(record for record in reduced if "kc91_fe3_t5_B27" in record.observation_id)
    assert table5_ratio.point_conditions["fO2_log"].state.value == Decimal("-0.68")
    assert table5_ratio.point_conditions["fO2_log"].locator.page == 5
    assert table5_ratio.point_conditions["fO2_log"].locator.table == "5"
    assert table5_ratio.identity.fO2_Pa.is_value


def test_context_provenance_and_deferred_figures_are_explicit() -> None:
    document = _document()
    context = [
        item
        for body in document["species"].values()
        for item in body.get("context", [])
    ]
    table7_e = next(item for item in context if item["observation_id"] == "kc91_t7_e")
    assert table7_e["values"]["evidence_class"] == "quoted_attributed"
    assert table7_e["values"]["attribution"] == "Stebbins et al. (1984)"

    table9_rows = [item for item in context if item["observation_id"].startswith("kc91_t9_")]
    cited = [item for item in table9_rows if "column_2_value" in item["values"]]
    assert len(cited) == 20
    assert all(item["values"]["column_2_evidence_class"] == "quoted_attributed" for item in cited)
    assert all(item["values"]["column_2_attribution"] == "Lange and Carmichael (1987)" for item in cited)
    current = [item for item in table9_rows if "column_3_value" in item["values"]]
    assert all(item["values"]["column_3_evidence_class"] == "model_derived" for item in current)

    figures = [item for item in context if item.get("type") == "figure_record"]
    assert {item["observation_id"] for item in figures} == {"kc91_fig1", "kc91_fig2", "kc91_fig3"}
    assert all(item["values"]["numeric_points"] == "none" for item in figures)
    assert all(item["values"]["digitisation"] == "deferred to a figure pass" for item in figures)
    assert all(
        all(series["marker_count"] != "not counted" for series in item["values"]["series"])
        for item in figures
    )
    assert all("species_as_printed" in item["values"] for item in figures)

    acoustic_rows = [item for item in context if item.get("type") == "acoustic_velocity_row"]
    count_note = next(item for item in context if item.get("observation_id") == "kc91_t2_count_note")
    assert len(acoustic_rows) == 83
    assert count_note["values"]["printed_count"] == "86"
    assert "density, viscosity, cp" in count_note["values"]["schema_gap"]

    for observation_id, printed in (
        ("kc91_fe3_t5_B105", "-6,29"),
        ("kc91_fe3_t5_B120", "-4,74"),
    ):
        ratio = next(
            item
            for item in document["species"]["Fe"]["observations"]
            if item["observation_id"] == observation_id
        )
        assert ratio["point_conditions"]["fO2_log"]["as_published"] == printed
        assert ratio["values"]["series"][0]["log_fO2"]["as_published"] == printed
        assert ratio["point_conditions"]["fO2_log"]["state"]["value"] in {"-6.29", "-4.74"}
        assert ratio["locator"]["page"] == 5
