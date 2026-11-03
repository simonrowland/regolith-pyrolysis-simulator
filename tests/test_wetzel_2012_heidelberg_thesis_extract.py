from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Phase, StateTag, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/wetzel-2012-heidelberg-thesis.yaml"

ALPHA_ID = "wetzel_2012_sio_evaporation_coefficient_fit_eq_2_25"
PRESSURE_ID = "wetzel_2012_sio_equilibrium_pressure_fit_eq_2_26"
DH1400_ID = "wetzel_2012_sio_sublimation_enthalpy_1400K"
DH298_ID = "wetzel_2012_sio_sublimation_enthalpy_298K"


def test_wetzel_sio_fits_table_rows_and_migration() -> None:
    raw = yaml.safe_load(EXTRACT.read_text())
    observations = raw["species"]["SiO"]["observations"]
    rows = {row["observation_id"]: row for row in observations}

    assert set(rows) == {ALPHA_ID, PRESSURE_ID, DH1400_ID, DH298_ID}
    table_rows = [row for row in observations if row["locator"].get("table") == "2.2"]
    assert len(table_rows) == 2
    assert [row["locator"]["row"] for row in table_rows] == ["ΔH1400K", "ΔH298K"]

    experiments = raw["experiments"]
    assert len(experiments) == 3
    for experiment in experiments:
        sample = experiment["sample"]
        assert sample["material"] == "amorphous SiO powder"
        assert sample["form"]["state"]["value"] == "condensed_solid"
        assert sample["initial_composition"]["state"]["value"]["basis"] == "nominal_pure_substance"
        assert sample["initial_composition"]["state"]["value"]["components"] == [["SiO", "1"]]
        assert sample["purity"]["value"] == 99.99
        assert sample["purity"]["printed_form"] == "99,99% mass purity"
        assert sample["purity"]["note"] == "The remaining 0.01% impurity is unspecified and not modelled."

    # Render pins: Equation 2.25 on PDF p. 34.
    alpha = rows[ALPHA_ID]
    assert alpha["experiment"] == "wetzel_sio_measurement_series"
    alpha_coefficients = alpha["values"]["coefficients"]
    assert alpha["locator"]["page"] == 34
    assert alpha["locator"]["equation"] == "2.25"
    assert alpha_coefficients["constant"]["value"] == 0.20821
    assert alpha_coefficients["constant"]["printed_form"] == "0,20821"
    assert alpha_coefficients["linear_per_K"]["value"] == -0.000312269
    assert alpha_coefficients["linear_per_K"]["printed_form"] == "−3,12269 · 10⁻⁴"
    assert alpha_coefficients["quadratic_per_K2"]["value"] == 0.000000135599
    assert alpha_coefficients["quadratic_per_K2"]["printed_form"] == "1,35599 · 10⁻⁷"

    # Render pins: Equation 2.26 and Table 2.2 on PDF p. 36.
    pressure = rows[PRESSURE_ID]
    assert pressure["experiment"] == "wetzel_sio_measurement_series"
    pressure_coefficients = pressure["values"]["coefficients"]
    assert pressure["locator"]["page"] == 36
    assert pressure["locator"]["equation"] == "2.26"
    assert pressure_coefficients["A"]["value"] == -50400
    assert pressure_coefficients["A"]["printed_form"] == "(−50,4 ± 0,5) × 10³"
    assert pressure_coefficients["A"]["uncertainty"] == 500
    assert pressure_coefficients["B"]["value"] == 35.5
    assert pressure_coefficients["B"]["printed_form"] == "(35,5 ± 0,4)"
    assert pressure_coefficients["B"]["uncertainty"] == 0.4

    dh1400 = rows[DH1400_ID]
    dh298 = rows[DH298_ID]
    assert dh1400["locator"]["page"] == dh298["locator"]["page"] == 36
    assert dh1400["locator"]["table"] == dh298["locator"]["table"] == "2.2"
    assert dh1400["values"]["value"] == 419
    assert dh1400["values"]["uncertainty"] == 4
    assert dh1400["values"]["printed_form"] == "419 ± 4"
    assert dh298["values"]["value"] == 432
    assert dh298["values"]["uncertainty"] == 4
    assert dh298["values"]["printed_form"] == "432 ± 4"
    assert [cell["value"] for cell in dh298["values"]["comparisons"]] == [321, 410, 360, 351]
    assert [cell["printed_form"] for cell in dh298["values"]["comparisons"]] == [
        "321",
        "410",
        "360 ± 4",
        "351 ± 11",
    ]
    assert dh298["values"]["repeated_in_equation"] == "2.27"

    # The unlisted fit temperatures and experimental conditions remain unknown.
    for observation in (alpha, pressure):
        conditions = observation["point_conditions"]
        for key in ("temperature_K", "total_pressure_Pa", "fO2_Pa"):
            assert conditions[key]["state"]["tag"] == "unknown"
            assert conditions[key]["locator"]["page"] == observation["locator"]["page"]
    assert "total_pressure_Pa" not in pressure["values"]
    assert "total_pressure_Pa" not in alpha["values"]
    assert raw["benches"][0]["other_facts"][1]["name"] == "chamber_base_pressure_after_bakeout"

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    migrated = [row for row in result.observations.values() if row.source_id == raw["source_id"]]
    assert len(migrated) == 4
    assert not result.validation.hard_issues

    migrated_by_source_id = {
        row.observation_id.rsplit("::", 1)[-1]: row for row in migrated
    }
    assert set(migrated_by_source_id) == {ALPHA_ID, PRESSURE_ID, DH1400_ID, DH298_ID}
    assert migrated_by_source_id[ALPHA_ID].locator.page == 34
    assert migrated_by_source_id[PRESSURE_ID].locator.page == 36
    assert migrated_by_source_id[DH1400_ID].locator.page == 36
    assert migrated_by_source_id[DH1400_ID].locator.table == "2.2"
    assert migrated_by_source_id[DH298_ID].locator.page == 36
    assert migrated_by_source_id[DH298_ID].locator.table == "2.2"
    phase_unknown = {
        row.observation_id.rsplit("::", 1)[-1]
        for row in migrated
        if row.identity.species is not None and row.identity.species.phase.is_unknown
    }
    assert phase_unknown == set()
    assert all(row.identity.species is not None for row in migrated)
    assert all(row.identity.species.phase.is_value for row in migrated)
    assert all(row.value.kind is ValueKind.UNAVAILABLE for row in migrated)

    quantity_unknown_ids = {
        source_id
        for source_id, row in migrated_by_source_id.items()
        if row.identity.quantity.tag is StateTag.UNKNOWN
    }
    assert quantity_unknown_ids == {DH1400_ID, DH298_ID}
    assert all(
        row.identity.quantity.tag is not StateTag.UNKNOWN
        for source_id, row in migrated_by_source_id.items()
        if source_id in {ALPHA_ID, PRESSURE_ID}
    )

    for source_id, row in migrated_by_source_id.items():
        source = rows[source_id]
        assert "temperature_K" in row.point_conditions
        temperature_condition = row.point_conditions["temperature_K"]
        assert temperature_condition.locator is not None
        assert temperature_condition.locator.page == source["point_conditions"]["temperature_K"]["locator"]["page"]
        assert "total_pressure_Pa" not in row.point_conditions
        assert "fO2_Pa" in row.point_conditions
        assert row.point_conditions["fO2_Pa"].state.tag is StateTag.UNKNOWN
        assert row.identity.total_pressure_Pa is None or not row.identity.total_pressure_Pa.is_value
        assert row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value

    assert migrated_by_source_id[ALPHA_ID].point_conditions["temperature_K"].state.tag is StateTag.UNKNOWN
    assert migrated_by_source_id[PRESSURE_ID].point_conditions["temperature_K"].state.tag is StateTag.UNKNOWN
    assert migrated_by_source_id[DH1400_ID].point_conditions["temperature_K"].state.value == Decimal("1400")
    assert migrated_by_source_id[DH298_ID].point_conditions["temperature_K"].state.value == Decimal("298")
    assert migrated_by_source_id[ALPHA_ID].evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert migrated_by_source_id[PRESSURE_ID].evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert migrated_by_source_id[DH1400_ID].evidence.class_.value is EvidenceClass.MEASURED_REDUCED
    assert migrated_by_source_id[DH298_ID].evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    alpha_composition = migrated_by_source_id[ALPHA_ID].identity.composition
    assert alpha_composition is not None and alpha_composition.is_value
    assert alpha_composition.value.components == (("SiO", Decimal("1")),)
    pressure_reservoir = migrated_by_source_id[PRESSURE_ID].identity.reservoir
    assert pressure_reservoir is not None and pressure_reservoir.is_value
    assert pressure_reservoir.value.formula == "SiO"
    assert pressure_reservoir.value.phase.is_value
    assert pressure_reservoir.value.phase.value is Phase.CR

    unavailable_count = sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated)
    assert unavailable_count == 4
