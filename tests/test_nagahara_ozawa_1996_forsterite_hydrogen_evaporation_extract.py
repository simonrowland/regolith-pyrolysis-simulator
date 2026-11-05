from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Phase, Quantity, ValueKind
from simulator.battery.identity import profile_for
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/"
    "nagahara-ozawa-1996-forsterite-hydrogen-evaporation.yaml"
)
SID = "nagahara-ozawa-1996-forsterite-hydrogen-evaporation"
RUN_IDS = (
    "754", "753", "752", "755", "769", "770", "768", "767", "771",
    "772", "773", "806", "800", "797", "795", "790", "789", "787",
    "788", "765", "756", "766", "764", "780", "781", "783", "786",
    "805", "799", "791", "792", "777", "776", "775", "778", "774",
    "785", "779", "784", "782", "793", "804", "794", "796", "798",
)
OPTIMIZED_IDS = {
    f"{SID}::{suffix}"
    for suffix in (
        "nagahara_ozawa_1996_optimized_J_pvacuum_r2_c14",
        "nagahara_ozawa_1996_optimized_J_p2p00Em07_r6_c14",
        "nagahara_ozawa_1996_optimized_J_p2p00Em06_r10_c14",
        "nagahara_ozawa_1996_optimized_J_p3p95Em06_r13_c14",
        "nagahara_ozawa_1996_optimized_J_p7p89Em06_r19_c14",
        "nagahara_ozawa_1996_optimized_J_p2p00Em05_r21_c14",
        "nagahara_ozawa_1996_optimized_J_p4p00Em05_r29_c14",
        "nagahara_ozawa_1996_optimized_J_p8p60Em05_r33_c14",
    )
}


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in document["species"]["Mg2SiO4"]["observations"]
    }


def _migrated_observation(observations: list, observation_id: str):
    return next(
        row for row in observations
        if row.observation_id.endswith(f"::{observation_id}")
    )


def _assert_rendered_table_values(document: dict) -> None:
    rows = _observations(document)
    # PDF p.4, 600 dpi render: Table 1, run 754, f, loss, J and optimized J.
    rendered = {
        "nagahara_ozawa_1996_run_754_mass_loss_fraction_r2_c11": (
            "mass_loss_percent", "11", 4, "1"
        ),
        "nagahara_ozawa_1996_run_754_mass_loss_g_r2_c10": (
            "mass_loss_g_as_printed", "0.00156", 4, "1"
        ),
        "nagahara_ozawa_1996_run_754_evaporation_flux_r2_c12": (
            "value", "5.92E-07", 4, "1"
        ),
        "nagahara_ozawa_1996_optimized_J_pvacuum_r2_c14": (
            "value", "9.84E-07", 4, "1"
        ),
    }
    for observation_id, (field, expected, page, table) in rendered.items():
        row = rows[observation_id]
        assert row["values"][field] == expected
        assert row["locator"]["page"] == page
        assert row["locator"]["table"] == table

    # PDF p.8, 600 dpi render: printed alpha endpoints in prose.
    for observation_id, expected in (
        ("nagahara_ozawa_1996_alpha_vacuum", "0.06"),
        ("nagahara_ozawa_1996_alpha_1e-4_bar", "0.2"),
    ):
        row = rows[observation_id]
        assert row["values"]["alpha"] == expected
        assert row["values"]["alpha_as_printed"] == expected
        assert row["values"]["source_term_as_printed"] == "condensation coefficient"
        assert row["values"]["ratio_as_printed"] == "J_exp^Fo/J_vac^Fo"
        assert row["values"]["definition_as_printed"] == (
            "The condensation coefficient is obtained by dividing the "
            "experimentally obtained evaporation rate J_exp^Fo by the maximum "
            "evaporation rate J_vac^Fo (Fig. 6)."
        )
        assert row["locator"]["page"] == 8
        assert row["locator"]["section"] == "4.3. Condensation Coefficient"


def _expected_quantity_unknown_ids() -> set[str]:
    expected = set()
    for row_number, run_id in enumerate(RUN_IDS, start=2):
        expected.update(
            {
                f"{SID}::nagahara_ozawa_1996_run_{run_id}_sample_weights_r{row_number}_c8",
                f"{SID}::nagahara_ozawa_1996_run_{run_id}_mass_loss_g_r{row_number}_c10",
                f"{SID}::nagahara_ozawa_1996_run_{run_id}_evaporation_flux_r{row_number}_c12",
            }
        )
    expected.update(OPTIMIZED_IDS)
    expected.update(
        {
            f"{SID}::nagahara_ozawa_1996_equation_2_pressure_dependent_flux_fit",
            f"{SID}::nagahara_ozawa_1996_alpha_vacuum",
            f"{SID}::nagahara_ozawa_1996_alpha_1e-4_bar",
        }
    )
    return expected


def test_table_rows_render_pins_migration_and_unprinted_conditions() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _observations(document)
    table_rows = [
        row for row in rows.values()
        if "_sample_weights_r" in row["observation_id"]
    ]
    assert len(table_rows) == 45
    assert tuple(row["values"]["run_id_as_printed"] for row in table_rows) == RUN_IDS

    _assert_rendered_table_values(document)
    assert check_all_fidelity_samples_match(document, label=SID) == []
    assert len(document["fidelity_samples"]) >= 3

    # A mutation of a render-pinned stored value must be detected by the same
    # independent fidelity checker used by the extraction validator.
    mutated = deepcopy(document)
    mutated_rows = _observations(mutated)
    mutated_rows[
        "nagahara_ozawa_1996_run_754_mass_loss_fraction_r2_c11"
    ]["values"]["mass_loss_percent"] = "12"
    assert check_all_fidelity_samples_match(mutated, label=f"{SID}-mutation")

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    observations = [
        row for row in result.observations.values() if row.source_id == SID
    ]
    assert len(observations) == 191
    assert not result.validation.hard_issues
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in observations) == 146
    assert all(row.identity.species.phase.is_value for row in observations)
    assert all(row.identity.species.phase.value is Phase.CR for row in observations)

    quantity_unknown = {
        row.observation_id
        for row in observations
        if not row.identity.quantity.is_value
    }
    assert quantity_unknown == _expected_quantity_unknown_ids()

    # These migrated locators bind rendered values to PDF pages and printed
    # table/equation locations after the isolated migration.
    for observation_id in (
        "nagahara_ozawa_1996_run_754_mass_loss_fraction_r2_c11",
        "nagahara_ozawa_1996_run_754_mass_loss_g_r2_c10",
        "nagahara_ozawa_1996_run_754_evaporation_flux_r2_c12",
        "nagahara_ozawa_1996_optimized_J_pvacuum_r2_c14",
    ):
        locator = _migrated_observation(observations, observation_id).locator
        assert locator.page == 4 and locator.table == "1"
    equation = _migrated_observation(
        observations, "nagahara_ozawa_1996_equation_2_pressure_dependent_flux_fit"
    )
    assert equation.locator.page == 5 and equation.locator.equation == "2"
    for observation_id in (
        "nagahara_ozawa_1996_alpha_vacuum",
        "nagahara_ozawa_1996_alpha_1e-4_bar",
    ):
        alpha_locator = _migrated_observation(observations, observation_id).locator
        assert alpha_locator.page == 8
        assert alpha_locator.section == "4.3. Condensation Coefficient"

    # Every source row carries located conditions; migration retains their
    # states and locators, including explicit unknowns for unprinted inputs.
    condition_names = {
        "temperature_C",
        "duration_h",
        "sample_geometry",
        "total_pressure_Pa",
        "fO2_Pa",
        "gas_mix",
        "H2_partial_pressure_Pa",
        "surface_area_cm2",
    }
    for raw_row in rows.values():
        raw_conditions = raw_row["point_conditions"]
        assert condition_names <= raw_conditions.keys()
        for condition in raw_conditions.values():
            assert "state" in condition and condition["locator"]
    for observation in observations:
        conditions = observation.point_conditions
        missing_pressure = "total_pressure_Pa" not in conditions
        expected_missing_pressure = observation.observation_id.endswith(
            (
                "::nagahara_ozawa_1996_optimized_J_pvacuum_r2_c14",
                "::nagahara_ozawa_1996_equation_2_pressure_dependent_flux_fit",
                "::nagahara_ozawa_1996_alpha_vacuum",
            )
        )
        assert missing_pressure is expected_missing_pressure
        raw_row = rows[observation.observation_id.split("::", 1)[-1]]
        pressure_state = raw_row["point_conditions"]["total_pressure_Pa"]["state"]
        if expected_missing_pressure:
            assert pressure_state["tag"] == "unknown"
        else:
            assert pressure_state["tag"] == "value"
        expected_conditions = condition_names | {"temperature_K"}
        if expected_missing_pressure:
            expected_conditions.remove("total_pressure_Pa")
        assert expected_conditions <= conditions.keys()
        for condition in conditions.values():
            assert condition.locator is not None
        assert conditions["temperature_K"].state.is_value
        assert conditions["temperature_K"].state.value == Decimal("1973.15")
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == Decimal("1973.15")
        assert conditions["H2_partial_pressure_Pa"].state.is_unknown
        assert conditions["fO2_Pa"].state.is_unknown
        assert conditions["surface_area_cm2"].state.is_unknown

    expected_pressure = {
        "754": Decimal("0.00026300000"),
        "769": Decimal("0.020000000"),
        "806": Decimal("0.39500000"),
    }
    required_axes = {"composition", "total_pressure_Pa", "fO2_Pa", "temperature_K"}
    for run_id, pressure in expected_pressure.items():
        observation = next(
            row for row in observations
            if f"run_{run_id}_mass_loss_fraction_" in row.observation_id
        )
        assert required_axes <= profile_for(observation.identity).required
        assert observation.identity.total_pressure_Pa.is_value
        assert observation.identity.total_pressure_Pa.value == pressure
        assert observation.identity.composition.is_unknown
        assert observation.identity.fO2_Pa.is_unknown

    h2_run = rows[
        "nagahara_ozawa_1996_run_769_sample_weights_r6_c8"
    ]
    assert h2_run["values"]["gas_as_printed"] == "H2"
    assert h2_run["point_conditions"]["H2_partial_pressure_Pa"]["state"]["tag"] == "unknown"
    migrated_h2_run = next(
        row for row in observations
        if row.observation_id.endswith("::" + h2_run["observation_id"])
    )
    assert migrated_h2_run.point_conditions["H2_partial_pressure_Pa"].state.is_unknown
    assert (
        migrated_h2_run.point_conditions["total_pressure_Pa"].state.value.point
        == Decimal("0.020000000")
    )

    fraction = _migrated_observation(
        observations, "nagahara_ozawa_1996_run_754_mass_loss_fraction_r2_c11"
    )
    assert fraction.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
    assert fraction.value.kind is ValueKind.POINT
    assert fraction.value.point == Decimal("0.11")
    assert fraction.evidence.class_.value == EvidenceClass.QUOTED_ATTRIBUTED.value

    run_798_fraction = rows[
        "nagahara_ozawa_1996_run_798_mass_loss_fraction_r46_c11"
    ]
    run_798_weights = rows["nagahara_ozawa_1996_run_798_sample_weights_r46_c8"]
    assert run_798_fraction["values"]["mass_loss_percent"] == "95"
    assert (
        run_798_weights["values"]["initial_mass_g"],
        run_798_weights["values"]["final_mass_g"],
    ) == ("0.01801", "0.00210")
    assert "imply 88.34%" in run_798_fraction["locator"]["note"]

    for suffix, value in (("alpha_vacuum", "0.06"), ("alpha_1e-4_bar", "0.2")):
        raw = rows[f"nagahara_ozawa_1996_{suffix}"]
        assert raw["values"]["alpha"] == value
        assert raw["values"]["source_term_as_printed"] == "condensation coefficient"
        assert raw["values"]["ratio_as_printed"] == "J_exp^Fo/J_vac^Fo"
        assert raw["locator"]["page"] == 8
        alpha = _migrated_observation(
            observations, f"nagahara_ozawa_1996_{suffix}"
        )
        assert alpha.identity.quantity.is_unknown
        assert alpha.value.kind is ValueKind.UNAVAILABLE
        assert "condensation" in alpha.value.unavailable_reason
