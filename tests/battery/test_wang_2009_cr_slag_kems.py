"""Render-checked extraction and migration pins for Wang et al. 2009 Tables 3–5."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from simulator.battery.enums import (
    EvidenceClass,
    MEASURED_EVIDENCE,
    Phase,
    Quantity,
    RefusalReason,
    ValueKind,
)
from simulator.battery.records import State
from simulator.battery.validity import run_validity_gates
from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root


SID = "wang-2009-cr-slag-kems-original"
ROOT = Path(__file__).resolve().parents[2]
CORPUS = regolith_corpus_root()
SOURCE = CORPUS / "raw" / SID / f"{SID}.pdf"
EXTRACT = ROOT / "data/literature/extracts" / f"{SID}.yaml"

_EXPECTED_TABLE5_ACTIVITIES = {
    "wang_2009_table5_cr2o3_sample_01_activities_at_1950_2035_k": (
        ("1950", "0.010"), ("2035", "0.005")
    ),
    "wang_2009_table5_cr2o3_sample_02_activities_at_1950_2035_k": (
        ("1950", "0.120"), ("2035", "0.910")
    ),
    "wang_2009_table5_cr2o3_sample_03_activities_at_1950_2035_k": (
        ("1950", "0.006"), ("2035", "0.082")
    ),
    "wang_2009_table5_cr2o3_sample_04_activities_at_1950_2035_k": (
        ("1950", "0.043"), ("2035", "0.500")
    ),
    "wang_2009_table5_cr2o3_sample_05_activities_at_1950_2035_k": (
        ("1950", "0.037"), ("2035", "0.380")
    ),
    "wang_2009_table5_cr2o3_sample_06_activities_at_1950_2035_k": (
        ("1950", "0.008"), ("2035", "0.073")
    ),
    "wang_2009_table5_cr2o3_sample_07_activities_at_1950_2035_k": (
        ("1950", "0.006"), ("2035", "0.071")
    ),
    "wang_2009_table5_cr2o3_sample_08_activities_at_1950_2035_k": (
        ("1950", "0.005"), ("2035", "0.032")
    ),
    "wang_2009_table5_cr2o3_sample_09_activities_at_1950_2035_k": (
        ("1950", "0.032"), ("2035", "0.190")
    ),
    "wang_2009_table5_cr2o3_sample_10_activities_at_1950_2035_k": (
        ("1950", "0.032"), ("2035", "0.260")
    ),
    "wang_2009_table5_cr2o3_sample_11_activities_at_1950_2035_k": (
        ("1950", "0.056"), ("2035", "0.780")
    ),
    "wang_2009_table5_cr2o3_sample_12_activities_at_1950_2035_k": (
        ("1950", "0.007"), ("2035", "0.070")
    ),
    "wang_2009_table5_cr2o3_sample_13_activities_at_1950_2035_k": (
        ("1950", "0.057"), ("2035", "0.710")
    ),
    "wang_2009_table5_cr2o3_sample_14_activities_at_1950_2035_k": (
        ("1950", "0.026"), ("2035", "0.290")
    ),
    "wang_2009_table5_cr2o3_sample_15_activities_at_1950_2035_k": (
        ("1950", "0.009"), ("2035", "0.100")
    ),
    "wang_2009_table5_cr2o3_sample_16_activities_at_1950_2035_k": (
        ("1950", "0.004"), ("2035", "0.036")
    ),
    "wang_2009_table5_cr2o3_sample_17_activities_at_1950_2035_k": (
        ("1950", "0.003"), ("2035", "0.022")
    ),
    "wang_2009_table5_cr2o3_sample_18_activities_at_1950_2035_k": (
        ("1950", "0.0005"), ("2035", "0.008")
    ),
    "wang_2009_table5_cr2o3_sample_19_activities_at_1950_2035_k": (
        ("1950", "0.0016"), ("2035", "0.012")
    ),
    "wang_2009_table5_cr2o3_sample_20_activities_at_1950_2035_k": (
        ("1950", "0.002"), ("2035", "0.018")
    ),
}


def _assert_table5_activity_cells(extract: dict) -> None:
    activity_rows = {
        row["observation_id"]: row
        for row in extract["species"]["Cr2O3"]["observations"]
        if row["observation_id"].endswith("_activities_at_1950_2035_k")
    }
    assert set(activity_rows) == set(_EXPECTED_TABLE5_ACTIVITIES)
    for observation_id, expected_points in _EXPECTED_TABLE5_ACTIVITIES.items():
        row = activity_rows[observation_id]
        sample = int(observation_id.split("_sample_")[1][:2])
        assert row["units"] == "dimensionless activity as printed"
        assert (
            row["locator"]["page"],
            row["locator"]["published_page"],
            row["locator"]["table"],
            row["locator"]["table_row_number"],
            row["locator"]["table_column_number"],
        ) == (5, 2237, "5", sample + 1, 5)
        assert tuple(
            (
                str(point["T_K"]),
                point["activity"],
                (
                    point["locator"]["page"],
                    point["locator"]["published_page"],
                    point["locator"]["table"],
                    point["locator"]["table_row_number"],
                    point["locator"]["table_column_number"],
                ),
            )
            for point in row["values"]["points"]
        ) == tuple(
            (
                temperature,
                activity,
                (5, 2237, "5", sample + 1, 5 if temperature == "1950" else 6),
            )
            for temperature, activity in expected_points
        )


def _page_text(page_number: int) -> str:
    return subprocess.run(
        [
            "pdftotext",
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-layout",
            str(SOURCE),
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _by_id(extract: dict, species: str, observation_id: str) -> dict:
    return next(
        row
        for row in extract["species"][species]["observations"]
        if row["observation_id"] == observation_id
    )


def _assert_source_rows_are_measured_reduced(extract: dict) -> None:
    rows = [
        row
        for species in extract["species"].values()
        for row in species["observations"]
    ]
    assert rows
    assert all(row["values"]["method_class"] == "measured_reduced" for row in rows)
    assert all("attribution" not in row["values"] for row in rows)
    for row in rows:
        equation = "22" if "table4_" in row["observation_id"] else "21"
        derivation = row["values"]["derivation"]
        assert derivation["raw_inputs"]["state"]["value"] == "not_printed"
        assert derivation["raw_inputs"]["locator"]["equation"] == equation


def _assert_activity_evidence_is_measured(observations: list) -> None:
    activities = [
        observation
        for observation in observations
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.ACTIVITY
    ]
    assert activities
    assert all(
        observation.evidence.class_.is_value
        and observation.evidence.class_.value in MEASURED_EVIDENCE
        and observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        for observation in activities
    )


def _assert_render_pins(extract: dict, page2: str, page4: str, page5: str) -> None:
    # The expected strings below were checked against rendered PDF pages 2, 4, and 5.
    page5 = page5.replace("\x01", "−").replace("\x02", "±")
    page2 = re.sub(r"\s+", " ", page2)
    page4_flat = re.sub(r"\s+", " ", page4)
    page5_flat = re.sub(r"\s+", " ", page5)
    table4_text = page5.split("Table 4.", 1)[1].split("Table 5.", 1)[0]
    table5_text = page5.split("Table 5.", 1)[1].split("Copyright", 1)[0]
    sample_rows = re.compile(r"^\s*(\d{1,2})\s+0\.\d{3}\s+[-−]?\d", re.MULTILINE)
    table4_samples = {int(value) for value in sample_rows.findall(table4_text)}
    table5_samples = {int(value) for value in sample_rows.findall(table5_text)}
    observations = {
        species: block["observations"]
        for species, block in extract["species"].items()
    }
    table4_ids = {
        f"wang_2009_table4_{oxide}_sample_{sample:02d}_temperature_fit"
        for oxide in ("cro", "cro2")
        for sample in table4_samples
    }
    table5_ids = {
        f"wang_2009_table5_cr2o3_sample_{sample:02d}_{kind}"
        for sample in table5_samples
        for kind in (
            "log_activity_temperature_fit",
            "activities_at_1950_2035_k",
        )
    }
    source_ids = {
        row["observation_id"]
        for rows in observations.values()
        for row in rows
    }
    assert source_ids == table4_ids | table5_ids
    _assert_table5_activity_cells(extract)

    assert "Table 1." in page2
    assert re.search(r"\b1\s+0\.062\b", page2)
    assert "1 0.066 0.712 0.099 0.013 0.111" in page4_flat
    assert re.search(r"\b1\s+0\.062\b", table4_text)
    assert "−36066.0 ± 2827.4" in page5_flat
    assert "−41668.5 ± 1781.7" in page5_flat
    assert "0.010" in page5_flat and "0.005" in page5_flat and "0.018" in page5_flat

    cro_a = _by_id(extract, "CrO", "wang_2009_table4_cro_sample_01_temperature_fit")
    cro2_a = _by_id(extract, "CrO2", "wang_2009_table4_cro2_sample_01_temperature_fit")
    activity_1 = _by_id(
        extract,
        "Cr2O3",
        "wang_2009_table5_cr2o3_sample_01_activities_at_1950_2035_k",
    )
    activity_20 = _by_id(
        extract,
        "Cr2O3",
        "wang_2009_table5_cr2o3_sample_20_activities_at_1950_2035_k",
    )
    table5_fit_1 = _by_id(
        extract,
        "Cr2O3",
        "wang_2009_table5_cr2o3_sample_01_log_activity_temperature_fit",
    )
    assert cro_a["values"]["A_as_printed"] == "-36066.0"
    assert cro_a["values"]["A_uncertainty_as_printed"] == "2827.4"
    assert cro_a["values"]["temperature_fit_equation_as_printed"] == (
        "log(p_i, Pa) = −A/T + B"
    )
    assert cro_a["values"]["sign_convention_ambiguity"].startswith(
        "Taken literally, Table 4's printed log(p_i, Pa) = −A/T + B"
    )
    assert "+|A|/T" in cro_a["values"]["sign_convention_ambiguity"]
    assert "falls with increasing temperature" in cro_a["values"]["sign_convention_ambiguity"]
    assert cro_a["locator"]["page"] == 5
    assert cro_a["locator"]["table"] == "4"
    assert cro_a["locator"]["table_row_number"] == 3
    assert cro_a["values"]["cr2o3_mole_fraction_as_printed_table4"] == "0.062"
    assert cro_a["values"]["composition_mol"]["Cr2O3"] == "0.066"
    assert cro2_a["values"]["A_as_printed"] == "-41668.5"
    assert cro2_a["values"]["temperature_fit_equation_as_printed"] == (
        "log(p_i, Pa) = −A/T + B"
    )
    assert table5_fit_1["values"]["cr2o3_mole_fraction_as_printed_table5"] == "0.062"
    assert table5_fit_1["values"]["composition_mol"]["Cr2O3"] == "0.066"
    assert activity_1["values"]["points"][0]["activity"] == "0.010"
    assert activity_1["values"]["points"][1]["activity"] == "0.005"
    assert activity_20["values"]["points"][1]["activity"] == "0.018"
    assert activity_1["values"]["points"][0]["locator"]["page"] == 5
    assert activity_1["values"]["points"][0]["locator"]["table"] == "5"
    assert activity_1["values"]["points"][0]["locator"]["table_row_number"] == 2
    assert activity_1["values"]["points"][0]["locator"]["table_column_number"] == 5


def test_render_pins_and_printed_rows() -> None:
    if not SOURCE.is_file():
        pytest.skip(f"source PDF is absent: {SOURCE}")

    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_source_rows_are_measured_reduced(extract)
    page2 = _page_text(2)
    page4 = _page_text(4)
    page5 = _page_text(5)
    _assert_render_pins(extract, page2, page4, page5)

    # Mutation proof: flipping the printed Table 4 sign must fail the pin.
    mutated = deepcopy(extract)
    mutated_row = _by_id(
        mutated, "CrO", "wang_2009_table4_cro_sample_01_temperature_fit"
    )
    mutated_row["values"]["A_as_printed"] = "36066.0"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated, page2, page4, page5)

    # Mutation proof: a quoted-attributed row fails the evidence-class pin.
    mutated = deepcopy(extract)
    mutated["species"]["CrO"]["observations"][0]["values"][
        "method_class"
    ] = "quoted_attributed"
    with pytest.raises(AssertionError):
        _assert_source_rows_are_measured_reduced(mutated)


def test_isolated_migration_keeps_conditions_and_only_refuses_fit_coefficients() -> None:
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    experiment = next(iter(migrator.result.experiments.values()))
    assert experiment.apparatus is not None
    assert experiment.apparatus.calibration is not None
    assert experiment.apparatus.calibration["method"].state.is_value
    assert experiment.apparatus.calibration["method"].locator.page == 4
    assert experiment.apparatus.calibration["method"].locator.equation == "23"
    assert experiment.apparatus.calibration["reference_substance"].state.value == "gold vapor"
    assert experiment.apparatus.calibration["reference_substance"].locator.equation == "23"

    rows = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == SID
    ]
    fit_ids = {
        row["observation_id"]
        for species in extract["species"].values()
        for row in species["observations"]
        if "temperature_fit" in row["observation_id"]
    }
    unavailable_ids = {
        observation.observation_id.split("::", 1)[1]
        for observation in rows
        if observation.value.kind is ValueKind.UNAVAILABLE
    }
    expected_activity_points = {
        (observation_id, temperature, activity)
        for observation_id, points in _EXPECTED_TABLE5_ACTIVITIES.items()
        for temperature, activity in points
    }
    actual_activity_points = {
        (
            observation.observation_id.split("::", 1)[1].split("::T=", 1)[0],
            str(observation.identity.temperature_K.value),
            str(observation.value.point),
        )
        for observation in rows
        if observation.value.kind is ValueKind.POINT
    }
    assert unavailable_ids == fit_ids
    assert actual_activity_points == expected_activity_points
    unknown_quantity_ids = {
        observation.observation_id.split("::", 1)[1]
        for observation in rows
        if not observation.identity.quantity.is_value
    }
    assert unknown_quantity_ids == fit_ids
    unavailable_reasons = {
        observation.observation_id.split("::", 1)[1]: observation.value.unavailable_reason
        for observation in rows
        if observation.value.kind is ValueKind.UNAVAILABLE
    }
    assert unavailable_reasons == {
        observation_id: (
            "declared quantity is unknown; unsupported quantity "
            "'log_partial_pressure_temperature_fit_coefficients'; refusing to pick a number"
            if observation_id.startswith("wang_2009_table4_")
            else "declared quantity is unknown; unsupported quantity "
            "'log_activity_temperature_fit_coefficients'; refusing to pick a number"
        )
        for observation_id in fit_ids
    }
    assert all(observation.identity.species.phase.is_value for observation in rows)

    for observation in rows:
        identity = observation.identity
        assert identity.composition.is_value
        assert identity.total_pressure_Pa is None or identity.total_pressure_Pa.is_unknown
        assert identity.fO2_Pa is None or identity.fO2_Pa.is_unknown
        conditions = observation.point_conditions or {}
        assert conditions["composition"].state.is_value
        assert conditions["composition"].locator.page == 4
        assert conditions["composition"].locator.table == "3"
        assert conditions["fO2_Pa"].state.is_unknown
        pressure_condition = conditions.get("total_pressure_Pa")
        assert pressure_condition is None or pressure_condition.state.is_unknown
        temperature = conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.locator.page == 5
        assert temperature.locator.table in {"4", "5"}
        assert identity.composition.value == conditions["composition"].state.value
        if not identity.quantity.is_value:
            assert identity.temperature_K.is_unknown
            assert temperature.state.value.kind is ValueKind.INTERVAL
            assert str(temperature.state.value.interval_low) == "1950"
            assert str(temperature.state.value.interval_high) == "2035"

    activities = [
        observation for observation in rows if observation.identity.quantity.is_value
    ]
    _assert_activity_evidence_is_measured(rows)
    for observation in activities:
        identity = observation.identity
        assert identity.quantity.value is Quantity.ACTIVITY
        assert identity.species.formula == "Cr2O3"
        assert identity.species.phase.value is Phase.L
        assert identity.temperature_K.is_value
        assert (
            identity.total_pressure_Pa is None
            or identity.total_pressure_Pa.is_unknown
        )
        assert identity.fO2_Pa is None or identity.fO2_Pa.is_unknown
        reference = identity.reference_state
        assert reference is not None and reference.is_value
        assert reference.value.endmember.formula == "Cr2O3"
        assert reference.value.endmember.phase.is_value
        assert reference.value.endmember.phase.value is Phase.CR

    # The comparison provenance and located gold standard let the measured
    # activity reach the apparatus and calibration gates while regime stays unknown.
    sample_1_1950 = next(
        observation
        for observation in activities
        if "wang_2009_table5_cr2o3_sample_01_activities_at_1950_2035_k::T=1950:"
        in observation.observation_id
    )
    outcome = run_validity_gates(experiment, sample_1_1950)
    check_by_name = {check.name: check for check in outcome.checks}
    assert check_by_name["comparison_method_cell_constant_cancels"].passed
    assert check_by_name["kems_calibration"].passed
    assert outcome.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED
    assert outcome.primary_check == "in_cell_partial_pressure_sum"
    assert any(
        not check.passed and check.name == "in_cell_partial_pressure_sum"
        for check in outcome.checks
    )

    # Mutation proof: a migrated activity relabelled quoted-attributed fails
    # the measured-evidence pin used to guard validity-gate applicability.
    mutated_activity = replace(
        sample_1_1950,
        evidence=replace(
            sample_1_1950.evidence,
            class_=State.of(EvidenceClass.QUOTED_ATTRIBUTED),
        ),
    )
    mutated_activities = list(activities)
    mutated_activities[mutated_activities.index(sample_1_1950)] = mutated_activity
    with pytest.raises(AssertionError):
        _assert_activity_evidence_is_measured(mutated_activities)

    first_three = sorted(activities, key=lambda row: row.observation_id)[:3]
    for observation in first_three:
        assert observation.identity.composition.is_value
        assert (
            observation.identity.total_pressure_Pa is None
            or observation.identity.total_pressure_Pa.is_unknown
        )
        assert (
            observation.identity.fO2_Pa is None
            or observation.identity.fO2_Pa.is_unknown
        )

