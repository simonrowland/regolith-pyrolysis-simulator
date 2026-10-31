from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tools import validate_literature_extracts as vle


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "pankratz-1968-ri7201-sodium-calcium-silicates"
EXTRACT = ROOT / f"data/literature/extracts/{SOURCE_ID}.yaml"


def _source_observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for rows in document["species"].values()
        for row in rows["observations"]
    }


def _expected_entropy_ids() -> set[str]:
    rows_by_fit = {
        "naca1_alpha": [(400, None), (500, None), (600, None), (700, None), (800, None), (830, "α")],
        "naca1_beta": [(830, "β"), (900, None), (1000, None), (1100, None), (1200, None), (1300, None)],
        "naca2": [(400, None), (500, None), (600, None), (700, None), (800, None), (900, None), (1000, None), (1100, None), (1200, None), (1300, None)],
        "naca3_alpha": [(400, None), (500, None), (600, None), (700, None), (750, "α")],
        "naca3_beta": [(750, "β"), (800, None), (900, None), (1000, None), (1100, None), (1200, None), (1300, None), (1400, None), (1500, None)],
        "naca4": [(400, None), (500, None), (600, None), (700, None), (800, None), (900, None), (1000, None), (1100, None), (1200, None), (1300, None), (1400, None)],
    }
    return {
        f"pank68_{fit}_table3_relative_entropy_increment_T{temperature}"
        + (f"_{polymorph}" if polymorph else "")
        for fit, points in rows_by_fit.items()
        for temperature, polymorph in points
    }


def _expected_schema_gap_ids() -> set[str]:
    analysis_ids = {
        f"pank68_{formula}_table1_{kind}_chemical_analysis"
        for formula in ("naca1", "naca2", "naca3", "naca4")
        for kind in ("measured", "theoretical")
    }
    transition_ids = {
        "pank68_naca1_solid_solid_transition_enthalpy",
        "pank68_naca3_solid_solid_transition_enthalpy",
        "pank68_naca1_irreversible_structure_change_above_1309",
        "pank68_naca3_frischat_oel_transition_range",
        "pank68_naca3_fusion_slightly_above_last_measurement",
    }
    return analysis_ids | _expected_entropy_ids() | transition_ids


def _migrated_by_source_id(migrator) -> dict[str, object]:
    return {
        observation_id.rsplit("::", 1)[-1]: row
        for observation_id, row in migrator.result.observations.items()
    }


def _point_at(migrated, *, formula: str, table: str, temperature: str):
    matches = [
        row
        for row in migrated.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.H_MINUS_H298
        and row.identity.species.formula == formula
        and row.identity.temperature_K.is_value
        and row.identity.temperature_K.value == Decimal(temperature)
        and row.locator is not None
        and row.locator.table == table
    ]
    assert len(matches) == 1
    return matches[0]


def test_pankratz_1968_tables_migrate_with_full_row_pins() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = _source_observations(document)

    table1 = [
        row
        for row in source_rows.values()
        if row["locator"].get("table") == "1"
        and row["type"] == "composition_series"
    ]
    assert len(table1) == 8
    assert sum(len(row["values"]["oxide_wt_percent"]) for row in table1) == 16
    actual = source_rows["pank68_naca1_table1_measured_chemical_analysis"]
    theory = source_rows["pank68_naca1_table1_theoretical_chemical_analysis"]
    assert actual["values"]["oxide_wt_percent"] == {"CaO": "31.48", "SiO2": "33.80"}
    assert theory["values"]["oxide_wt_percent"] == {"CaO": "31.48", "SiO2": "33.73"}
    assert actual["locator"]["page"] == theory["locator"]["page"] == 9
    assert actual["locator"]["published_page"] == theory["locator"]["published_page"] == 2
    assert actual["locator"]["table"] == theory["locator"]["table"] == "1"

    table2 = [
        row
        for row in source_rows.values()
        if row["locator"].get("table") == "2"
        and row["values"].get("quantity") == "H_minus_H298"
        and row["values"].get("method_class") == "measured_direct"
        and isinstance(row["values"].get("series"), list)
    ]
    assert sum(len(row["values"]["series"]) for row in table2) == 71
    assert sum(len(row["values"]["series"]) for row in table2) * 2 == 142

    table3_heat = [
        row
        for row in source_rows.values()
        if row["locator"].get("table") == "3"
        and row["values"].get("quantity") == "H_minus_H298"
        and row["values"].get("method_class") == "model_derived"
    ]
    table3_entropy = [
        row
        for row in source_rows.values()
        if row["values"].get("quantity") == "relative_entropy_increment_S_T_minus_S_298_15"
    ]
    equations = [
        row
        for row in source_rows.values()
        if row["type"] == "gibbs_table"
        and row["values"].get("fit_form") == "equation"
    ]
    assert sum(len(row["values"]["series"]) for row in table3_heat) == 47
    assert len(table3_entropy) == 47
    assert 47 * 3 == 141
    assert len(equations) == 6
    assert all(row["locator"]["page"] == 12 and row["locator"]["published_page"] == 5 for row in equations)
    assert all("table" not in row["locator"] for row in equations)
    assert all(row["values"]["reference_temperature_K"] == 298.15 for row in table3_heat)
    for equation in equations:
        parents = equation["derived_from"]
        assert len(parents) == 1
        measured_parent = source_rows[parents[0]]
        low, high = equation["values"]["fit_range_K"]
        parent_temperatures = [
            point["T_K"] for point in measured_parent["values"]["series"]
        ]
        assert parent_temperatures
        assert all(low <= temperature <= high for temperature in parent_temperatures)
        smooth_id = equation["observation_id"].replace(
            "_fit_equation", "_table3_smooth_heat_content"
        )
        assert source_rows[smooth_id]["derived_from"] == parents
    assert source_rows["pank68_naca4_table3_relative_entropy_increment_T1400"]["values"][
        "entropy_increment"
    ] == "149.35"
    assert source_rows["pank68_naca4_table3_relative_entropy_increment_T1400"]["values"][
        "footnote_marker_as_printed"
    ] == "1/"

    assert vle.validate_extract_document(document, expected_source_id=SOURCE_ID) == []
    assert vle.check_all_fidelity_samples_match(document, label=SOURCE_ID) == []
    mutated = deepcopy(document)
    mutated_rows = _source_observations(mutated)
    mutated_rows["pank68_naca1_alpha_table2_measured"]["values"]["series"][0][
        "enthalpy_increment"
    ] += 1
    assert any(
        "fidelity_samples[0]" in error and "mismatch" in error
        for error in vle.check_all_fidelity_samples_match(mutated, label="mutated")
    )

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    migrated = _migrated_by_source_id(migrator)
    assert len(migrated) == 186
    assert result.validation.hard_issues == ()
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 66
    quantity_unknown = {
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.quantity.is_unknown
    }
    assert quantity_unknown == _expected_schema_gap_ids()
    assert len(quantity_unknown) == 60
    assert not [
        observation_id
        for observation_id, row in migrated.items()
        if row.identity.species.phase.is_unknown
    ]
    transition_ids = (
        "pank68_naca1_polymorphic_transition_temperature",
        "pank68_naca3_polymorphic_transition_temperature",
    )
    expected_transitions = {
        transition_ids[0]: (Decimal("830"), 12, 5),
        transition_ids[1]: (Decimal("750"), 12, 5),
    }
    for observation_id, (expected, page, published_page) in expected_transitions.items():
        row = migrated[observation_id]
        assert row.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == expected
        assert row.locator.page == page
        assert row.locator.published_page == published_page
        assert row.identity.species.phase.value == "cr"

    # Render pins after isolated migration. The source rows are Table 2 p. 3 and Table 3 p. 6.
    render_pins = [
        ("Na2CaSiO4", "2", "395.1", Decimal("15.85736"), 10, 3),
        ("Na2CaSiO4", "3", "400", Decimal("16.77784"), 13, 6),
        ("Na2Ca2Si3O9", "3", "1500", Decimal("474.84216"), 13, 6),
    ]
    for formula, table, temperature, expected, page, published_page in render_pins:
        row = _point_at(migrated, formula=formula, table=table, temperature=temperature)
        assert row.value.kind is ValueKind.POINT
        assert row.value.point == expected
        assert row.locator.page == page
        assert row.locator.published_page == published_page
        assert row.locator.table == table
        assert row.identity.species.phase.value == "cr"
        assert row.identity.temperature_K.value == Decimal(temperature)
        assert row.point_conditions["temperature_K"].state.value == Decimal(temperature)
        assert row.point_conditions["temperature_K"].locator.page == page
        assert row.point_conditions["temperature_K"].locator.table == table
        assert not row.identity.total_pressure_Pa.is_value
        assert not row.identity.fO2_Pa.is_value
        assert set(row.point_conditions) == {"temperature_K"}

    # Every measured or smooth heat-content point carries its printed T condition and locator.
    heat_rows = [
        row
        for row in migrated.values()
        if row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.H_MINUS_H298
        and row.locator is not None
        and row.locator.table in {"2", "3"}
    ]
    assert len(heat_rows) == 118
    assert all(row.identity.temperature_K.is_value for row in heat_rows)
    assert all(row.point_conditions["temperature_K"].state.is_value for row in heat_rows)
    assert all(not row.identity.total_pressure_Pa.is_value for row in heat_rows)
    assert all(not row.identity.fO2_Pa.is_value for row in heat_rows)
    for row in table2:
        assert "point_conditions" not in row
        assert "total_pressure_Pa" not in row["values"]
        assert "fO2_Pa" not in row["values"]

    entropy_migrated = [
        migrated[row["observation_id"]]
        for row in table3_entropy
    ]
    assert len(entropy_migrated) == 47
    assert all(row.identity.quantity.is_unknown for row in entropy_migrated)
    assert all(row.identity.species.phase.value == "cr" for row in entropy_migrated)
    assert all(row.identity.temperature_K.is_value for row in entropy_migrated)
    assert all(row.point_conditions["temperature_K"].state.is_value for row in entropy_migrated)
    assert all(set(row.point_conditions) == {"temperature_K"} for row in entropy_migrated)
    assert all(
        "total_pressure_Pa" not in row["values"] and "fO2_Pa" not in row["values"]
        for row in table3_entropy
    )

    # The quoted setup pressure is explanatory notes only, never a row condition.
    helium_note = next(
        note for note in document["notes"] if "1 atmosphere of helium" in note["text"]
    )
    assert helium_note["locator"]["page"] == 10
    prep_context = {
        formula: block["context"][0]
        for formula, block in document["species"].items()
    }
    assert len(prep_context) == 4
    assert all(row["type"] == "sample_preparation" for row in prep_context.values())
    assert prep_context["Na2CaSiO4"]["values"]["total_heating_time_hours"] == 770
    assert prep_context["Na4CaSi3O9"]["values"]["maximum_heating_temperature_C"] == 950
    assert all(
        "total_pressure_Pa" not in row.get("point_conditions", {})
        and "fO2_Pa" not in row.get("point_conditions", {})
        for row in source_rows.values()
    )
