from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.battery.test_migrate import _migrate_real_extract
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/ohare-1972-na2o-formation-enthalpy.yaml"
SOURCE_ID = "ohare-1972-na2o-formation-enthalpy"

RUN_FIELDS = (
    "run_number",
    "mass_Na2O_g",
    "delta_t_c_K",
    "calorimeter_energy_equivalent_J_K",
    "calorimeter_heat_J",
    "delta_H_vap_J",
    "delta_H_over_M_J_g",
)
EXPECTED_RUNS = (
    ("1", "0.11829", "1.02087", "446.80", "-456.12", "-0.05", "-3856.37"),
    ("2", "0.11757", "1.01340", "446.69", "-452.68", "-0.05", "-3850.73"),
    ("3", "0.11834", "1.02229", "446.96", "-456.92", "-0.05", "-3861.50"),
    ("4", "0.11766", "1.01733", "446.80", "-454.54", "-0.05", "-3863.59"),
    ("5", "0.11244", "0.97067", "446.73", "-433.63", "-0.05", "-3856.99"),
    ("6", "0.11697", "1.00997", "446.01", "-450.46", "-0.05", "-3851.50"),
    ("7", "0.11822", "1.02200", "446.32", "-456.14", "-0.05", "-3858.82"),
    ("8", "0.11797", "1.02150", "446.19", "-455.78", "-0.05", "-3863.95"),
    ("9", "0.11586", "1.00207", "446.25", "-447.17", "-0.05", "-3860.00"),
)
TABLE_II_ROWS = (
    ("Forcrand", "11", "-94.90"),
    ("Matsui and Nakata", "8a", "-100.08"),
    ("Matsui and Oka", "8b", "-99.89"),
    ("Roth", "9", "-100.60"),
    ("Rengade", "10", "-100.09"),
    ("Gross and Wilson", "5", "-102.5"),
    ("This work", "absent", "-99.14"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> list[dict]:
    return [
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
    ]


def _by_id(rows: list[dict], observation_id: str) -> dict:
    return next(row for row in rows if row["observation_id"] == observation_id)


def _assert_table_i_cells(document: dict) -> None:
    observations = document["species"]["Na2O"]["observations"]
    runs = sorted(
        (
            row
            for row in observations
            if row["locator"].get("table") == "I"
            and "run_number" in row["values"]
        ),
        key=lambda row: int(row["values"]["run_number"]),
    )
    assert len(runs) == 9
    for row, expected in zip(runs, EXPECTED_RUNS, strict=True):
        values = row["values"]
        assert tuple(values[field] for field in RUN_FIELDS) == expected
        assert row["phase"] == "cr"
        assert row["T_C"] == 25.0
        assert row["point_conditions"]["temperature_K"]["state"]["value"] == 298.15
        assert values["reaction_solution_as_printed"] == "0.1M NaOH"
        assert values["table_footnote_markers"] == {
            "delta_t_c": "b",
            "calorimeter_energy_equivalent": "c",
        }


def test_render_pins_every_calorimetry_cell_and_printed_enthalpy_rows() -> None:
    document = _document()
    _assert_table_i_cells(document)
    observations = _observations(document)
    table_i = [
        row
        for row in observations
        if row["locator"].get("table") == "I" and "run_number" in row["values"]
    ]
    assert {row["locator"]["record"] for row in table_i} == {
        f"experiment column {n}" for n in range(1, 10)
    }

    mean = _by_id(observations, "ohare72_mean_calorimetric_heat_per_mass")
    assert mean["values"]["mean_delta_H_over_M_J_g"] == "-3858.16"
    assert mean["values"]["uncertainty_J_g"] == "1.59"
    assert mean["values"]["mean_delta_H_over_M_cal_g"] == "-922.12"
    assert mean["values"]["uncertainty_cal_g"] == "0.38"
    assert mean["values"]["uncertainty_footnote_marker"] == "d"

    table_ii = _by_id(
        document["species"]["Na2O"]["context"],
        "ohare72_table_ii_literature_formation_values",
    )
    rows = table_ii["values"]["rows"]
    assert len(rows) == 7
    assert tuple(
        (row["investigator"], row["reference_marker"], row["value"])
        for row in rows
    ) == TABLE_II_ROWS
    assert table_ii["values"]["caption_footnote_marker"] == "a"
    assert table_ii["values"]["footnote_as_printed"] == (
        "Results from Refs. 8–11 taken directly from JANAF.21a"
    )
    gross_wilson = _by_id(
        document["species"]["Na2O"]["context"],
        "ohare72_gross_wilson_formation_result_context",
    )
    assert gross_wilson["values"]["table_ii_duplicate_row"] == (
        "Gross and Wilson [5], Table II value -102.5"
    )

    sample = _by_id(
        document["species"]["Na2O"]["context"],
        "ohare72_sample_and_calorimetry_conditions",
    )["values"]
    assert sample["heat_treatment_temperature"] == "520±10 °C"
    assert sample["hydrolysis_product_yield"] == "99.9±0.2%"
    assert "not an assay of Na2O mass purity" in sample["hydrolysis_product_yield_basis"]
    assert sample["conditions_not_printed"] == "total pressure or fO2 for the calorimeter experiments"

    hf = _by_id(observations, "ohare72_na2o_delta_fH_29815")
    assert hf["values"]["value"] == "-414.82"
    assert hf["uncertainty"]["value"] == "0.28"
    assert hf["values"]["alternate_printed_value"] == "-99.14"
    assert hf["values"]["alternate_printed_uncertainty"] == "0.07"
    assert _by_id(observations, "ohare72_reaction_1_enthalpy")["values"]["delta_H_kJ_mol"] == "-239.13"
    assert _by_id(observations, "ohare72_reaction_2_dilution_enthalpy")["values"]["calculation_as_printed"] == (
        "(7.263)(−38) J mole−1 = −0.28 kJ mole−1"
    )
    assert _by_id(observations, "ohare72_reaction_3_enthalpy")["values"]["delta_H3_as_printed"] == (
        "2(+470.03) kJ mole−1"
    )
    assert _by_id(observations, "ohare72_gross_hcl_reaction_enthalpy")["values"]["delta_H_kcal_mol"] == "-82.5"
    assert _by_id(observations, "ohare72_na2o_gas_delta_fH_29815")["values"]["value"] == "-9.1"
    assert _by_id(observations, "ohare72_na2o_atomization_enthalpy_29815")["values"]["delta_H_atomization_kcal_mol"] == "120"
    assert _by_id(document["species"]["H2O"]["observations"], "ohare72_water_delta_fH_29815")["values"]["value"] == "-285.83"

    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []
    mutated = copy.deepcopy(document)
    row5 = _by_id(
        mutated["species"]["Na2O"]["observations"], "ohare72_run_5"
    )
    row5["values"]["calorimeter_heat_J"] = "-433.64"
    with pytest.raises(AssertionError):
        _assert_table_i_cells(mutated)

    mutated_sample = copy.deepcopy(document)
    mutated_sample["species"]["Na2O"]["observations"][0]["values"]["delta_H_over_M_J_g"] = "-3856.38"
    assert check_all_fidelity_samples_match(mutated_sample, label="mutated run")


def test_isolated_real_migration_pins_values_conditions_and_j01_series_census(tmp_path: Path) -> None:
    document = _document()
    result = _migrate_real_extract(
        tmp_path,
        EXTRACT.name,
        write=True,
        extract_doc=document,
    )
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 18

    observations = list(result.observations.values())
    unavailable = [row for row in observations if row.value.kind.value == "unavailable"]
    points = [row for row in observations if row.value.kind.value == "point"]
    assert len(points) == 3, [row.observation_id for row in points]
    assert len(unavailable) == 15

    by_suffix = {row.observation_id.rsplit("::", 1)[-1]: row for row in observations}
    expected_points = {
        "ohare72_na2o_delta_fH_29815": ("-414.82", "0.28", "298.15", "cr"),
        # The source prints -9.1 kcal/mol; isolated migration normalizes it to kJ/mol.
        "ohare72_na2o_gas_delta_fH_29815": ("-38.0744", "3.0", "298.15", "g"),
        "ohare72_water_delta_fH_29815": ("-285.83", "0.04", "298.15", "l"),
    }
    assert {by_suffix[sid].observation_id for sid in expected_points} == {
        row.observation_id for row in points
    }
    for observation_id, (value, uncertainty, temperature, phase) in expected_points.items():
        row = by_suffix[observation_id]
        assert row.value.point == Decimal(value)
        assert row.uncertainty.kind.value == "printed"
        assert row.uncertainty.verbatim["value"] == uncertainty
        assert row.uncertainty.verbatim["confidence_level"] == "not_stated"
        assert row.identity.temperature_K.value == Decimal(temperature)
        assert row.identity.species.phase.is_value
        assert row.identity.species.phase.value.value == phase
        assert row.point_conditions is not None
        assert row.point_conditions["temperature_K"].state.value == Decimal(temperature)

    typed_refusal_ids = {
        f"ohare72_run_{n}" for n in range(1, 10)
    } | {
        "ohare72_mean_calorimetric_heat_per_mass",
        "ohare72_reaction_1_enthalpy",
        "ohare72_reaction_2_dilution_enthalpy",
        "ohare72_reaction_3_enthalpy",
        "ohare72_gross_hcl_reaction_enthalpy",
        "ohare72_na2o_atomization_enthalpy_29815",
    }
    unknown_quantity_ids = {
        row.observation_id.rsplit("::", 1)[-1]
        for row in observations
        if not row.identity.quantity.is_value
    }
    assert unknown_quantity_ids == typed_refusal_ids
    assert all(row.identity.species.phase.is_value for row in observations)

    for row in observations:
        assert row.identity.total_pressure_Pa is None or not row.identity.total_pressure_Pa.is_value
        assert row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value
        conditions = row.point_conditions or {}
        assert "total_pressure_Pa" not in conditions
        assert "fO2_Pa" not in conditions
    for row in observations:
        suffix = row.observation_id.rsplit("::", 1)[-1]
        if suffix == "ohare72_gross_hcl_reaction_enthalpy":
            assert row.identity.temperature_K is None or not row.identity.temperature_K.is_value
            assert "temperature_K" not in (row.point_conditions or {})
            continue
        assert row.identity.temperature_K.is_value
        assert row.point_conditions is not None
        temperature = row.point_conditions["temperature_K"]
        assert temperature.state.value == row.identity.temperature_K.value
        assert temperature.locator is not None
    gross_reaction = by_suffix["ohare72_gross_hcl_reaction_enthalpy"]
    assert gross_reaction.identity.temperature_K is None or not gross_reaction.identity.temperature_K.is_value
    assert "temperature_K" not in (gross_reaction.point_conditions or {})

    # The rendered Table I and Table II locators survive the isolated migration.
    assert by_suffix["ohare72_run_1"].locator.page == 3
    assert by_suffix["ohare72_run_1"].locator.table == "I"
    assert by_suffix["ohare72_run_1"].locator.record == "experiment column 1"
    context_rows = [row for rows in result.context_by_work.values() for row in rows]
    table_ii = next(row for row in context_rows if row["observation_id"].endswith("ohare72_table_ii_literature_formation_values"))
    assert table_ii["locator"]["page"] == 4
    assert table_ii["locator"]["table"] == "II"

    assert check_all_fidelity_samples_match(document, label="post-migration source pins") == []
