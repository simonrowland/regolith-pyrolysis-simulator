from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.battery.test_migrate import _migrate_real_extract, _series_census
from tools.validate_literature_extracts import check_all_fidelity_samples_match


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/fredrickson-chasanov-1973-na2o-enthalpy-1300k.yaml"
SOURCE_ID = "fredrickson-chasanov-1973-na2o-enthalpy-1300k"

# Full Table 1 cell grid, transcribed from the two machine reads and adjudicated
# against PDF page 3 where the reads disagreed or the cell was flagged.
TABLE1 = [
    ("511.8", "380.37", "113.79", "3718.04", "3731.48", "+0.36"),
    ("520.0", "394.46", "118.28", "3851.94", "3882.74", "+0.80"),
    ("567.5", "487.14", "144.34", "4781.10", "4770.69", "−0.22"),
    ("568.0", "488.04", "144.62", "4789.75", "4780.14", "−0.20"),
    ("614.1", "576.79", "170.03", "5673.16", "5661.21", "−0.21"),
    ("653.1", "658.16", "191.63", "6506.79", "6421.27", "−1.31"),
    ("697.1", "739.50", "216.10", "7299.82", "7294.93", "−0.07"),
    ("697.4", "741.52", "216.28", "7325.63", "7300.94", "−0.34"),
    ("709.7", "765.39", "223.14", "7562.87", "7548.27", "−0.19"),
    ("729.7", "808.21", "234.31", "8004.30", "7953.28", "−0.64"),
    ("802.5", "952.61", "275.19", "9448.11", "9457.42", "+0.10"),
    ("876.4", "1102.50", "316.99", "10955.66", "11032.26", "+0.70"),
    ("951.3", "1261.66", "359.68", "12578.56", "12582.77", "+0.03"),
    ("951.8", "1255.68", "359.97", "12492.64", "12593.19", "+0.81"),
    ("975.4", "1313.36", "373.49", "13108.55", "13092.45", "−0.12"),
    ("991.4", "1347.45", "382.67", "13455.98", "13439.55", "−0.12"),
    ("1007.0", "1381.17", "391.64", "13801.17", "13785.07", "−0.12"),
    ("1025.5", "1422.36", "402.29", "14227.12", "14204.34", "−0.16"),
    ("1050.1", "1478.21", "416.49", "14808.02", "14778.76", "−0.20"),
    ("1074.9", "1532.01", "430.84", "15358.23", "15378.56", "+0.13"),
    ("1099.9", "1595.07", "445.33", "16035.65", "16005.56", "−0.19"),
    ("1123.4", "1649.29", "459.00", "16601.21", "16616.61", "+0.09"),
    ("1149.3", "1715.16", "474.09", "17309.45", "17315.75", "+0.04"),
    ("1176.4", "1781.54", "489.92", "18014.48", "18077.67", "+0.35"),
    ("1200.8", "1852.99", "504.22", "18811.56", "18791.66", "−0.11"),
    ("1216.8", "1896.28", "513.61", "19284.37", "19274.87", "−0.05"),
    ("1234.5", "1943.68", "524.01", "19800.42", "19823.76", "+0.12"),
    ("1243.5", "1974.00", "529.31", "20149.37", "20108.79", "−0.20"),
    ("1254.4", "2003.12", "535.73", "20465.98", "20459.45", "−0.03"),
    ("1266.6", "2039.28", "542.93", "20869.89", "20859.17", "−0.05"),
    ("1285.1", "2092.55", "553.86", "21460.41", "21480.21", "+0.09"),
]

# Table 2 columns in scope: temperature, enthalpy increment, and heat capacity.
TABLE2 = [
    ("298.15", "0", "16.52"),
    ("300", "30.58", "16.54"),
    ("400", "1728.46", "17.42"),
    ("500", "3514.85", "18.31"),
    ("600", "5389.73", "19.19"),
    ("700", "7353.11", "20.08"),
    ("800", "9404.99", "20.96"),
    ("900", "11545.37", "21.85"),
    ("1000", "13629.14", "22.7"),
    ("1100", "16008.11", "25.4"),
    ("1200", "18767.82", "30.0"),
    ("1300", "21993.78", "36.5"),
]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observation(document: dict, observation_id: str) -> dict:
    return next(
        row
        for row in document["species"]["Na2O"]["observations"]
        if row["observation_id"] == observation_id
    )


def _migrated_point(result, source_record_id: str, temperature: str):
    matches = [
        row
        for row in result.observations.values()
        if row.observation_id.startswith(f"{SOURCE_ID}::{source_record_id}::")
        and row.identity.temperature_K.is_value
        and row.identity.temperature_K.value == Decimal(temperature)
    ]
    assert len(matches) == 1, (source_record_id, temperature, len(matches))
    return matches[0]


def _assert_full_table_transcription(document: dict) -> None:
    observations = document["species"]["Na2O"]["observations"]
    measured = [
        *_observation(document, "fredrickson_1973_table1_measured_low")["values"]["series"],
        *_observation(document, "fredrickson_1973_table1_measured_high")["values"]["series"],
    ]
    assert len(measured) == 31
    assert len(TABLE1) == 31
    assert [
        (
            row["T_K"],
            row["q_cal_th"],
            row["q_o_cal_th"],
            row["enthalpy_increment"],
            row["delta_percent_as_printed"],
        )
        for row in measured
    ] == [(t, q, q_o, h, delta) for t, q, q_o, h, _fit, delta in TABLE1]

    q_input = _observation(document, "fredrickson_1973_table1_q_input")["values"]["series"]
    q_o_input = _observation(document, "fredrickson_1973_table1_q_o_input")["values"]["series"]
    assert [(row["T_K"], row["value"]) for row in q_input] == [
        (t, q) for t, q, _q_o, _h, _fit, _delta in TABLE1
    ]
    assert [(row["T_K"], row["value"]) for row in q_o_input] == [
        (t, q_o) for t, _q, q_o, _h, _fit, _delta in TABLE1
    ]

    fitted = [
        *_observation(document, "fredrickson_1973_table1_fit_low")["values"]["series"],
        *_observation(document, "fredrickson_1973_table1_fit_high")["values"]["series"],
    ]
    assert [(row["T_K"], row["enthalpy_increment"]) for row in fitted] == [
        (t, fit) for t, _q, _q_o, _observed, fit, _delta in TABLE1
    ]

    table2_h = [
        *_observation(document, "fredrickson_1973_table2_enthalpy_low")["values"]["series"],
        *_observation(document, "fredrickson_1973_table2_enthalpy_high")["values"]["series"],
    ]
    table2_cp = [
        *_observation(document, "fredrickson_1973_table2_cp_29815_quoted")["values"]["series"],
        *_observation(document, "fredrickson_1973_table2_cp_300_900_model_derived")["values"]["series"],
        *_observation(document, "fredrickson_1973_table2_cp_high_measured_reduced")["values"]["series"],
    ]
    assert len(table2_h) == len(table2_cp) == 12
    assert [(row["T_K"], row["enthalpy_increment"]) for row in table2_h] == [
        (t, h) for t, h, _cp in TABLE2
    ]
    assert [(row["T_K"], row["cp"]) for row in table2_cp] == [
        (t, cp) for t, _h, cp in TABLE2
    ]

    transitions = [
        _observation(document, "fredrickson_1973_bouaziz_dta_transition_1023K_quoted")["values"]["T_m_K"],
        _observation(document, "fredrickson_1973_bouaziz_dta_transition_1243K_quoted")["values"]["T_m_K"],
    ]
    assert transitions == ["1023", "1243"]
    assert len(observations) == 14

    expected_equations = {
        "fredrickson_1973_table1_fit_low": (
            "{H°(T)−H°(298.15 K)}/cal_th mol−1 = 13.8814(T/K) + "
            "4.4249 × 10⁻³(T/K)² − 4532.09",
            "298 to 900 K",
            "41 cal_th mol−1 or 0.58 per cent",
        ),
        "fredrickson_1973_table1_fit_high": (
            "{H°(T)−H°(298.15 K)}/cal_th mol−1 = 35.4157(T/K) − "
            "2.80046 × 10⁻²(T/K)² + 1.42549 × 10⁻⁵(T/K)³ − 8036.87",
            "900 to 1300 K",
            "36 cal_th mol−1 or 0.25 per cent",
        ),
        "fredrickson_1973_table2_cp_300_900_model_derived": (
            "Cp°(T)/cal_th K−1 mol−1 = 13.8814 + 8.8498 × 10⁻³(T/K)",
            "300 to 900 K",
            None,
        ),
    }
    for observation_id, (equation, validity, standard_deviation) in expected_equations.items():
        values = _observation(document, observation_id)["values"]
        assert values["equation_as_printed"] == equation
        assert values["equation_validity_range_as_printed"] == validity
        if standard_deviation is not None:
            assert values["standard_deviation_as_printed"] == standard_deviation


def _expected_series() -> dict[str, tuple[str, list[tuple[str, str]], str]]:
    return {
        "fredrickson_1973_table1_measured_low": (
            "energy",
            [(t, h) for t, _q, _q_o, h, _fit, _delta in TABLE1[:12]],
            "H_minus_H298",
        ),
        "fredrickson_1973_table1_measured_high": (
            "energy",
            [(t, h) for t, _q, _q_o, h, _fit, _delta in TABLE1[12:]],
            "H_minus_H298",
        ),
        "fredrickson_1973_table1_fit_low": (
            "energy",
            [(t, fit) for t, _q, _q_o, _h, fit, _delta in TABLE1[:12]],
            "H_minus_H298",
        ),
        "fredrickson_1973_table1_fit_high": (
            "energy",
            [(t, fit) for t, _q, _q_o, _h, fit, _delta in TABLE1[12:]],
            "H_minus_H298",
        ),
        "fredrickson_1973_table2_enthalpy_low": (
            "energy",
            [(t, h) for t, h, _cp in TABLE2[:8]],
            "H_minus_H298",
        ),
        "fredrickson_1973_table2_enthalpy_high": (
            "energy",
            [(t, h) for t, h, _cp in TABLE2[8:]],
            "H_minus_H298",
        ),
        "fredrickson_1973_table2_cp_29815_quoted": (
            "heat_capacity",
            [(TABLE2[0][0], TABLE2[0][2])],
            "cp",
        ),
        "fredrickson_1973_table2_cp_300_900_model_derived": (
            "heat_capacity",
            [(t, cp) for t, _h, cp in TABLE2[1:8]],
            "cp",
        ),
        "fredrickson_1973_table2_cp_high_measured_reduced": (
            "heat_capacity",
            [(t, cp) for t, _h, cp in TABLE2[8:]],
            "cp",
        ),
    }


def test_table_fidelity_samples_and_interior_mutation() -> None:
    document = _document()
    _assert_full_table_transcription(document)
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    mutated = copy.deepcopy(document)
    _observation(mutated, "fredrickson_1973_table1_measured_high")["values"]["series"][5][
        "enthalpy_increment"
    ] = "14227.13"
    with pytest.raises(AssertionError):
        _assert_full_table_transcription(mutated)


def test_isolated_migration_binds_every_value_and_condition_and_j01_census(
    tmp_path: Path,
) -> None:
    document = _document()
    result = _migrate_real_extract(
        tmp_path,
        EXTRACT.name,
        write=True,
        extract_doc=document,
    )
    assert result.validation is not None
    assert result.validation.hard_issues == (), [
        (issue.path, issue.detail) for issue in result.validation.hard_issues
    ]
    assert len(result.observations) == 151

    numeric = [row for row in result.observations.values() if row.value.kind.value == "point"]
    unavailable = [
        row for row in result.observations.values() if row.value.kind.value == "unavailable"
    ]
    assert len(numeric) == 88
    assert len(unavailable) == 63

    expected_numeric_ids: set[str] = set()
    for raw_id, (quantity_kind, expected_points, quantity) in _expected_series().items():
        for temperature, raw_value in expected_points:
            observation = _migrated_point(result, raw_id, temperature)
            expected_numeric_ids.add(observation.observation_id)
            assert observation.value.kind.value == "point"
            assert observation.identity.quantity.value.value == quantity
            assert observation.identity.species.phase.value.value == "cr"

            multiplier = Decimal("0.004184") if quantity_kind == "energy" else Decimal("4.184")
            assert observation.value.point == Decimal(raw_value) * multiplier
            assert observation.identity.temperature_K.value == Decimal(temperature)

            condition = (observation.point_conditions or {}).get("temperature_K")
            assert condition is not None
            assert condition.state.value == Decimal(temperature)
            assert condition.locator is not None
            assert condition.locator.page in {3, 4}
            assert condition.locator.table in {"1", "2"}
            assert f"row {temperature}, column T/K" in condition.locator.note
            assert not any(
                key in (observation.point_conditions or {})
                for key in ("total_pressure_Pa", "pressure_Pa", "fO2_Pa", "composition")
            )
            assert not observation.identity.total_pressure_Pa.is_value
            assert not observation.identity.fO2_Pa.is_value

            if raw_id in {
                "fredrickson_1973_table1_measured_low",
                "fredrickson_1973_table1_measured_high",
            }:
                parents = (
                    _migrated_point(result, "fredrickson_1973_table1_q_input", temperature),
                    _migrated_point(result, "fredrickson_1973_table1_q_o_input", temperature),
                )
                expected_parents = tuple(parent.observation_id for parent in parents)
                assert observation.derived_from == expected_parents
                assert observation.derivation is not None
                assert observation.derivation.inputs == expected_parents

            if raw_id in {
                "fredrickson_1973_table1_fit_low",
                "fredrickson_1973_table2_enthalpy_low",
                "fredrickson_1973_table2_cp_300_900_model_derived",
                "fredrickson_1973_table1_fit_high",
                "fredrickson_1973_table2_enthalpy_high",
                "fredrickson_1973_table2_cp_high_measured_reduced",
            }:
                band = "low" if raw_id.endswith("_low") or raw_id.endswith("derived") else "high"
                measured_id = f"fredrickson_1973_table1_measured_{band}"
                source_rows = TABLE1[:12] if band == "low" else TABLE1[12:]
                expected_parents = {
                    _migrated_point(result, measured_id, row[0]).observation_id
                    for row in source_rows
                }
                assert set(observation.derived_from or ()) == expected_parents
                assert observation.derivation is not None
                assert set(observation.derivation.inputs) == expected_parents

    q_input_ids = {
        _migrated_point(result, "fredrickson_1973_table1_q_input", row[0]).observation_id
        for row in TABLE1
    }
    q_o_input_ids = {
        _migrated_point(result, "fredrickson_1973_table1_q_o_input", row[0]).observation_id
        for row in TABLE1
    }
    expected_unknown_ids = q_input_ids | q_o_input_ids
    unknown_quantities = {
        row.observation_id
        for row in result.observations.values()
        if row.identity.quantity.is_unknown
    }
    unknown_phases = {
        row.observation_id
        for row in result.observations.values()
        if row.identity.species.phase.is_unknown
    }
    assert unknown_quantities == expected_unknown_ids
    assert len(unknown_quantities) == 62
    assert unknown_phases == set()

    for observation_id in expected_unknown_ids:
        row = result.observations[observation_id]
        assert row.value.kind.value == "unavailable"
        assert row.identity.species.phase.value.value == "cr"
        condition = row.point_conditions["temperature_K"]
        assert condition.state.is_value
        assert condition.locator is not None
        assert condition.locator.page == 3
        assert condition.locator.table == "1"

    # Pin render-adjudicated cells after migration, including both table locators.
    for record_id, temperature, expected_value, multiplier, page, table in (
        ("fredrickson_1973_table1_measured_low", "614.1", "5673.16", "0.004184", 3, "1"),
        ("fredrickson_1973_table1_fit_high", "1266.6", "20859.17", "0.004184", 3, "1"),
        ("fredrickson_1973_table2_enthalpy_low", "400", "1728.46", "0.004184", 4, "2"),
        ("fredrickson_1973_table2_cp_high_measured_reduced", "1100", "25.4", "4.184", 4, "2"),
    ):
        row = _migrated_point(result, record_id, temperature)
        assert row.value.point == Decimal(expected_value) * Decimal(multiplier)
        locator = row.point_conditions["temperature_K"].locator
        assert locator.page == page
        assert locator.table == table

    transition_values = {"1023", "1243"}
    transition_rows = [
        row
        for row in numeric
        if row.identity.quantity.value.value == "transition_temperature"
    ]
    assert {str(row.value.point) for row in transition_rows} == transition_values
    assert len(transition_rows) == 2
    assert all(row.identity.species.phase.value.value == "cr" for row in transition_rows)
    assert all(row.identity.temperature_K.is_not_applicable for row in transition_rows)
    expected_numeric_ids.update(row.observation_id for row in transition_rows)
    assert {row.observation_id for row in numeric} == expected_numeric_ids

    absent_transition = [row for row in unavailable if row.observation_id not in expected_unknown_ids]
    assert len(absent_transition) == 1
    assert absent_transition[0].identity.quantity.value.value == "transition_temperature"
    assert absent_transition[0].identity.species.phase.value.value == "cr"

    contexts = {
        row["observation_id"]: row
        for row in document["species"]["Na2O"]["context"]
    }
    sample = contexts["fredrickson_1973_sample_and_drop_calorimetry"]["values"]
    procedure = contexts["fredrickson_1973_sample_amount_and_procedure"]
    procedure_values = procedure["values"]
    assert procedure["locator"]["page"] == 3
    assert procedure["locator"]["published_page"] == 487
    assert sample["sample_formula_as_printed"] == "Na2O"
    assert procedure_values["sample_mass_g"] == "4.4438"
    assert procedure_values["molar_mass_g_mol"] == "61.979"
    assert procedure_values["capsule_filling"] == "The sample-containing capsule was sealed under vacuum."
    assert sample["sample_purity_statement_as_printed"].startswith("(99.9 ± 0.2)")
    assert sample["sample_purity_basis_note"].endswith(
        "Confidence level for ±0.2 is not stated."
    )
    assert sample["calorimeter_energy_equivalent_as_printed"] == "(1046.24 ± 0.33) cal_th K−1"
    assert "783 K for 40 h" in sample["preparation_as_printed"]
    assert "no numerical total" in sample["atmosphere_and_pressure_note"]
    assert sample["sample_purity_reference_marker_as_printed"] == "(5)"
    assert sample["apparatus_procedure_reference_marker_as_printed"] == "(6)"
    assert sample["sapphire_check_reference_marker_as_printed"] == "(7)"
    assert sample["calorimeter_energy_equivalent_marker_as_printed"] == "†"
    assert procedure_values["unit_temperature_footnote_as_printed"].endswith("IPTS-68.")
    cp_constraint = _observation(
        document, "fredrickson_1973_table2_cp_29815_quoted"
    )["values"]["note"]
    assert cp_constraint.endswith("superscript (8).")
    assert all(
        "[3]" in _observation(document, observation_id)["values"]["attribution"]
        for observation_id in (
            "fredrickson_1973_bouaziz_dta_transition_1023K_quoted",
            "fredrickson_1973_bouaziz_dta_transition_1243K_quoted",
        )
    )
    structure = contexts["fredrickson_1973_structure_and_melting_context"]
    assert structure["locator"]["page"] == 5
    assert "antifluorite" in structure["values"]["phase_as_printed"]
    assert "1453 K" in structure["values"]["melting_limit_as_printed"]

    literature_root = tmp_path / "tree" / "data" / "literature"
    census, mismatches, n_numeric, n_unavailable, per_source = _series_census(
        literature_root / "extracts",
        literature_root / "extracts-v2",
    )
    assert mismatches == []
    assert n_numeric == 86
    assert n_unavailable == 62
    assert sum(census.values()) == 86
    assert per_source[SOURCE_ID] == census
