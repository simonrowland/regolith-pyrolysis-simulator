from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Phase, Quantity
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/norris-2016-trace-volatility-thesis.yaml"


def test_norris_2016_table56_residue_rows_and_render_pins() -> None:
    source = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    expected = {
        "norris_2016_ag_f006_table56_residue": ("304", "46"),
        "norris_2016_bi_f010_table56_residue": ("2.3", "0.3"),
        "norris_2016_ge_f019_table56_residue": ("6.1", "1.6"),
    }
    source_observations = {
        row["observation_id"]: row
        for item in source["species"].values()
        for row in item.get("observations", [])
    }
    for row in (
        row for row in source_observations.values()
        if row["values"].get("quantity") == "residue_component_composition"
        and row["locator"].get("table") == "5.6"
    ):
        oxygen = row["values"]["log10_fO2"]
        assert oxygen["units"] == "log10(atm)"
        assert oxygen["locator"]["page"] == 224
        assert oxygen["locator"]["table"] == "5.4"
        pressure = row["point_conditions"]["total_pressure_Pa"]["locator"]
        assert pressure["page"] == 196
        assert pressure["section"] == "5.2"
    # Render-checked cells from PDF p. 227, Table 5.6.
    for observation_id, (value, sigma) in expected.items():
        row = source_observations[observation_id]
        assert row["locator"]["page"] == 227
        assert row["locator"]["table"] == "5.6"
        assert row["values"]["residue_ppm"] == value
        assert row["uncertainty"]["sigma_ppm"] == sigma

    all_source_rows = [
        row
        for item in source["species"].values()
        for row in item.get("observations", [])
    ]
    assert len(all_source_rows) == 240
    assert len(source["experiments"]) == 11
    table57_rows = [
        row for row in all_source_rows
        if row["values"].get("quantity") == "element_fraction_remaining"
    ]
    expected_starting_oxides = {
        "Ag": ("Ag2O", "353", "11"),
        "Bi": ("Bi2O3", "226", "19"),
        "Cd": ("CdO", "405", "50"),
        "Cr": ("Cr2O3", "878", "129"),
        "Cu": ("Cu2O", "349", "26"),
        "Ga": ("Ga2O3", "424", "125"),
        "Ge": ("GeO2", "643", "176"),
        "In": ("In2O3", "457", "48"),
        "Mo": ("MoO2", "526", "54"),
        "Pb": ("PbO", "489", "22"),
        "Sb": ("Sb2O3", "578", "21"),
        "Sn": ("SnO2", "424", "8"),
        "Tl": ("Tl2O", "430", "14"),
        "V": ("V2O3", "1019", "113"),
        "W": ("WO3", "406", "65"),
        "Zn": ("ZnO", "666", "16"),
    }
    assert len(table57_rows) == 64
    assert not any(
        row["observation_id"] == "norris_2016_table57_volatility_factors"
        for row in source["species"]["basaltic_silicate_melt"]["context"]
    )
    assert all(row["admission_status"] == "admitted" for row in table57_rows)
    assert all("basis" not in row["values"] for row in table57_rows)
    assert all("normalising_element" not in row["values"] for row in table57_rows)
    assert all(row["values"]["method_class"] == "author_derived" for row in table57_rows)
    assert all(len(row["derived_from"]) == 2 for row in table57_rows)
    assert all(
        row["values"]["derivation"]["relation"]
        == "X_final/X_initial (Equation 5.1); authors use mole fraction single cation oxide basis, conversion not recomputed"
        for row in table57_rows
    )
    starting_trace_rows = {
        species_name: row
        for species_name, item in source["species"].items()
        for row in item.get("observations", [])
        if row["observation_id"].endswith("table52_starting_trace")
    }
    assert len(starting_trace_rows) == 16
    for element, (oxide, value, sigma) in expected_starting_oxides.items():
        row = starting_trace_rows[oxide]
        assert row["observation_id"] == f"norris_2016_{element.lower()}_table52_starting_trace"
        assert row["values"]["component_as_printed"] == oxide
        assert row["values"]["residue_ppm"] == value
        assert row["uncertainty"]["sigma_ppm"] == sigma
    assert all(row["values"]["quantity"] == "residue_component_composition" for row in starting_trace_rows.values())
    assert all(row["T_C"] == "1500" for row in starting_trace_rows.values())
    assert all(
        row["point_conditions"]["total_pressure_Pa"]["state"]["value"]["point"] == "1500000000"
        for row in starting_trace_rows.values()
    )
    assert all(row["values"]["duration"] == {"value": "5", "unit": "min"} for row in starting_trace_rows.values())
    table52_context = next(
        row for row in source["species"]["basaltic_silicate_melt"]["context"]
        if row["locator"].get("table") == "5.2"
    )
    assert set(table52_context["values"]["oxide_composition_and_sigma"]) == {"Ni2O"}
    assert "Ni" not in expected_starting_oxides
    expected_table57 = {
        "norris_2016_ag_f008_table57_fraction_remaining": ("0.646", "0.117"),
        "norris_2016_cr_f014_table57_fraction_remaining": ("1.192", "0.179"),
        "norris_2016_w_f018_table57_fraction_remaining": ("2.223", "0.365"),
        "norris_2016_zn_f019_table57_fraction_remaining": ("0.130", "0.087"),
    }
    rows_by_id = {row["observation_id"]: row for row in table57_rows}
    for observation_id, (ratio, sigma) in expected_table57.items():
        row = rows_by_id[observation_id]
        assert row["values"]["fraction_remaining"] == ratio
        assert row["uncertainty"]["sigma"] == sigma
        assert row["locator"]["page"] == 232
        assert row["locator"]["table"] == "5.7"

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues
    residue_rows = [
        row for row in result.observations.values()
        if row.source_id == source["source_id"]
        and row.identity.quantity.is_value
        and row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and row.locator.table == "5.6"
    ]
    assert len(residue_rows) == 160
    for row in residue_rows:
        assert row.identity.species.phase.value is Phase.GLASS
        assert row.identity.temperature_K.value == Decimal("1573.15")
        assert row.identity.composition.is_value
        assert row.identity.total_pressure_Pa.value == Decimal("101325")
        pressure = (
            result.experiments[row.experiment_id]
            .pressure_environment.total_pressure_Pa
        )
        assert pressure.locator.page == 196
        assert pressure.locator.section == "5.2"
        assert pressure.locator.table is None
        assert row.point_conditions["temperature_K"].state.value == Decimal("1573.15")
        assert row.point_conditions["fO2_log"].locator.page == 224
        assert row.point_conditions["fO2_log"].locator.table == "5.4"
        assert row.point_conditions["fO2_log"].state.value in {
            Decimal("-7"),
            Decimal("-9"),
            Decimal("-11"),
            Decimal("-13"),
        }
        assert row.point_conditions["starting_composition"].locator.page == 220

    migrated_table57 = [
        row for row in result.observations.values()
        if row.source_id == source["source_id"]
        and row.identity.quantity.is_value
        and row.identity.quantity.value.value == "element_fraction_remaining"
    ]
    assert len(migrated_table57) == 64
    assert all(row.admission.status.value == "admitted" for row in migrated_table57)
    for row in migrated_table57:
        assert row.identity.species.phase.value is Phase.GLASS
        assert row.identity.normalising_element.is_not_applicable
        assert row.identity.temperature_K.value == Decimal("1573.15")
        assert row.identity.composition.is_value
        assert row.identity.total_pressure_Pa.value == Decimal("101325")
        assert row.identity.exposure.value.duration_s.value == Decimal("3600")
        assert row.point_conditions["temperature_K"].state.value == Decimal("1573.15")
        assert row.point_conditions["fO2_log"].state.value in {
            Decimal("-7"), Decimal("-9"), Decimal("-11"), Decimal("-13")
        }
        assert row.point_conditions["fO2_log"].locator.table == "5.4"
        assert row.point_conditions["atmosphere"].state.value == "CO/CO2"
        assert row.point_conditions["total_pressure_Pa"].locator.section == "5.2"
        assert row.derivation is not None
        assert "wt_pct_to_mole_fraction" in row.derivation.relation
        assert any(
            parameter.locator is not None and parameter.locator.page == 220
            for _, parameter in row.derivation.parameters
        )
    for observation_id, (ratio, sigma) in expected_table57.items():
        row = next(
            row for row in migrated_table57
            if row.observation_id.endswith(f"::{observation_id}")
        )
        assert row.value.point == Decimal(ratio)
        assert row.uncertainty.verbatim["sigma"] == sigma
        assert len(row.derived_from) == 2
        assert row.derivation.relation == (
            "X_final/X_initial (Equation 5.1); authors use mole fraction "
            "single cation oxide basis, conversion not recomputed; "
            "identity.composition=wt_pct_to_mole_fraction"
        )

    migrated_starting_trace = [
        row for row in result.observations.values()
        if row.source_id == source["source_id"]
        and row.observation_id.endswith("table52_starting_trace")
    ]
    assert len(migrated_starting_trace) == 16
    assert all(row.admission.status.value == "admitted" for row in migrated_starting_trace)
    assert all(row.identity.temperature_K.value == Decimal("1773.15") for row in migrated_starting_trace)
    assert all(row.identity.total_pressure_Pa.value == Decimal("1500000000") for row in migrated_starting_trace)
    for row in migrated_starting_trace:
        element = row.observation_id.split("::")[-1].split("_")[2].capitalize()
        oxide, value, sigma = expected_starting_oxides[element]
        assert row.identity.species.formula == oxide
        assert row.value.point == Decimal(value)
        assert row.uncertainty.verbatim["sigma_ppm"] == sigma

    migrated_by_id = {row.observation_id: row for row in residue_rows}
    for observation_id, (value, _) in expected.items():
        row = next(
            value for key, value in migrated_by_id.items()
            if key.endswith(f"::{observation_id}")
        )
        assert row.value.point == Decimal(value)

    contexts = [
        row
        for rows in result.context_by_work.values()
        for row in rows
    ]
    assert len(contexts) == 3
