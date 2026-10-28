from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import yaml

import simulator.battery.migrate as migrate_module
from simulator.battery.enums import EvidenceClass, Phase, Polymorph, Quantity, ValueKind


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXTRACT = (
    _REPO_ROOT
    / "data/literature/extracts/kelley-1953-alkali-aluminosilicate-thermodynamics.yaml"
)
_CELLS_AUDIT = (
    _REPO_ROOT
    / "data/literature/extracts/audits/kelley-1953-alkali-aluminosilicate-thermodynamics.cells.csv"
)
_SOURCE_ID = "kelley-1953-alkali-aluminosilicate-thermodynamics"


def _migrated():
    with patch.object(migrate_module, "discover_extracts", return_value=[_EXTRACT]):
        migrator = migrate_module.Migrator(root=_REPO_ROOT)
        migrator.migrate_extracts()
        migrator.finalize()
    return migrator.result


def _point(result, *, observation_fragment: str, temperature: str):
    matches = [
        observation
        for observation in result.observations.values()
        if observation.source_id == _SOURCE_ID
        and observation.observation_id.split("::", 1)[-1].startswith(observation_fragment)
        and observation.point_conditions["temperature_K"].state.value
        == Decimal(temperature)
    ]
    assert len(matches) == 1
    return matches[0]


def _source_observation_id(observation):
    return observation.observation_id.split("::", 2)[1]


def test_kelley_printed_thermochemistry_rows_migrate_with_cells_and_conditions():
    result = _migrated()
    observations = [
        observation
        for observation in result.observations.values()
        if observation.source_id == _SOURCE_ID
    ]
    assert len(observations) == 507
    expected_quantity_unknown_ids = {
        *(f"kelley_1953_table25_s51_extrapolated_{name}" for name in (
            "albite", "jadeite", "nephelite", "adularia", "leucite", "kaliophilite"
        )),
        *(f"kelley_1953_table25_s298_minus_s51_measured_{name}" for name in (
            "albite", "jadeite", "nephelite", "adularia", "leucite", "kaliophilite"
        )),
        "kelley_1953_table27_entropy_increment",
        "kelley_1953_table29_entropy_increment",
        "kelley_1953_table31_entropy_increment_delta",
        "kelley_1953_table31_entropy_increment_beta",
        "kelley_1953_table31_entropy_increment_gamma",
        "kelley_1953_table33_entropy_increment_delta",
        "kelley_1953_table33_entropy_increment_beta",
    }
    quantity_unknown = [
        observation
        for observation in observations
        if observation.identity.quantity.is_unknown
    ]
    quantity_unknown_ids = {
        _source_observation_id(observation) for observation in quantity_unknown
    }
    assert quantity_unknown_ids == expected_quantity_unknown_ids
    assert len(quantity_unknown) == 67
    assert all(observation.value.kind is ValueKind.UNAVAILABLE for observation in quantity_unknown)
    assert sum(
        observation.value.kind is ValueKind.UNAVAILABLE for observation in observations
    ) == 67
    assert all(
        observation.identity.quantity.is_value
        for observation in observations
        if _source_observation_id(observation) not in expected_quantity_unknown_ids
    )
    assert all(observation.identity.species.phase.is_value for observation in observations)
    assert all(
        observation.point_conditions["temperature_K"].state.is_value
        for observation in observations
    )
    assert all(
        observation.identity.species.phase.value is Phase.CR
        for observation in observations
    )
    pressure_axes = [
        observation.identity.standard_pressure_Pa for observation in observations
    ]
    assert all(axis is None or axis.is_unknown for axis in pressure_axes)
    assert sum(axis is None for axis in pressure_axes) == 67
    assert sum(axis is not None and axis.is_unknown for axis in pressure_axes) == 440
    assert {
        issue.path.rsplit(".", 1)[-1]
        for issue in result.validation.issues
    } == {"standard_pressure_Pa"}
    assert len(result.validation.hard_issues) == 0
    assert {
        issue.path.rsplit(".", 1)[-1]
        for issue in result.validation.issues
    } <= {"standard_pressure_Pa"}

    pins = [
        (
            _point(result, observation_fragment="kelley_1953_table09_cp", temperature="53.92"),
            Quantity.CP,
            "36.69368",
            16,
            "9",
        ),
        (
            _point(result, observation_fragment="kelley_1953_table11_cp", temperature="53.84"),
            Quantity.CP,
            "13.200520",
            17,
            "11",
        ),
        (
            _point(result, observation_fragment="kelley_1953_table26_heat_content", temperature="472.7"),
            Quantity.H_MINUS_H298,
            "40.41744",
            28,
            "26",
        ),
        (
            _point(result, observation_fragment="kelley_1953_table32_heat_content", temperature="389.0"),
            Quantity.H_MINUS_H298,
            "11.52692",
            33,
            "32",
        ),
        (
            _point(result, observation_fragment="kelley_1953_table25_entropy_S_nephelite", temperature="298.16"),
            Quantity.S,
            "124.2648",
            24,
            "25",
        ),
    ]
    for observation, quantity, value, page, table in pins:
        assert observation.value.kind is ValueKind.POINT
        assert observation.value.point == Decimal(value)
        assert observation.identity.quantity.value is quantity
        assert observation.point_conditions["temperature_K"].state.value is not None
        assert observation.locator.page == page
        assert observation.locator.table == table

    nephelite_transition = [
        observation
        for observation in observations
        if "table31_heat_content" in observation.observation_id
        and observation.point_conditions["temperature_K"].state.value
        == Decimal("467")
    ]
    assert len(nephelite_transition) == 2
    assert {
        observation.identity.species.polymorph.value
        for observation in nephelite_transition
    } == {Polymorph.DELTA, Polymorph.BETA}

    carnegieite_transition = [
        observation
        for observation in observations
        if "table33_heat_content" in observation.observation_id
        and observation.point_conditions["temperature_K"].state.value
        == Decimal("980")
    ]
    assert len(carnegieite_transition) == 2
    assert {
        observation.identity.species.polymorph.value
        for observation in carnegieite_transition
    } == {Polymorph.DELTA, Polymorph.BETA}

    smooth = next(
        observation
        for observation in observations
        if "::kelley_1953_table10_cp::T=" in observation.observation_id
    )
    assert smooth.evidence.class_.value is EvidenceClass.MEASURED_REDUCED

    for table in (9, 15, 17, 19, 20):
        corrected_points = [
            observation
            for observation in observations
            if f"::kelley_1953_table{table:02}_cp::T="
            in observation.observation_id
        ]
        assert corrected_points
        assert all(
            observation.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
            and observation.derivation.raw_inputs.state.value.value == "not_printed"
            for observation in corrected_points
        )

    entropy_increment = _point(
        result,
        observation_fragment="kelley_1953_table27_entropy_increment",
        temperature="1400",
    )
    assert entropy_increment.identity.quantity.is_unknown
    assert entropy_increment.value.kind is ValueKind.UNAVAILABLE
    assert entropy_increment.locator.page == 28
    assert entropy_increment.locator.table == "27"

    extract = yaml.safe_load(_EXTRACT.read_text(encoding="utf-8"))
    table27_entropy = next(
        row
        for row in extract["species"]["NaAlSi3O8"]["observations"]
        if row["observation_id"] == "kelley_1953_table27_entropy_increment"
    )
    entropy_cell = next(
        point
        for point in table27_entropy["values"]["points"]
        if point["T_K"] == 1400
    )
    assert entropy_cell["value"] == 102.47
    assert entropy_cell["locator"]["page"] == 28
    assert entropy_cell["locator"]["table"] == "27"
    table33_smooth = next(
        row
        for row in extract["species"]["NaAlSiO4"]["observations"]
        if row["observation_id"] == "kelley_1953_table33_heat_content_beta"
    )
    reversion_points = [
        point
        for point in table33_smooth["values"]["points"]
        if point["T_K"] in {1200, 1300, 1400}
    ]
    assert len(reversion_points) == 3
    assert all("PDF p. 34" in point["note"] for point in reversion_points)
    reaction_context = [
        row
        for block in extract["species"].values()
        for row in block.get("context", [])
        if row.get("type") == "reaction_thermochemistry"
    ]
    assert len(reaction_context) == 3
    expected_reactions = {
        "34": "albite + nephelite = 2 jadeite",
        "36": "albite = jadeite + quartz",
        "37": "nephelite + quartz = jadeite",
    }
    assert {
        row["locator"]["table"]: row["values"]["reaction_as_printed"]
        for row in reaction_context
    } == expected_reactions
    assert sum(len(row["values"]["runs"]) * 2 for row in reaction_context) == 60
    assert all(row["units"] == "cal/mol" for row in reaction_context)
    assert all(row["values"]["pressure_as_printed"] == "1 atm. pressure" for row in reaction_context)
    assert all(row["values"]["schema_gap"] for row in reaction_context)
    assert all(
        row["values"]["candidate_types_and_quantities_considered"]
        == ["gibbs_table -> delta_fG", "log10_Kf"]
        for row in reaction_context
    )
    reaction_pins = {
        ("34", "298.16"): (-5530, -730),
        ("36", "298.16"): (140, 2610),
        ("37", "600"): (-6090, -820),
        ("37", "700"): (-6090, 60),
    }
    for (table, temperature), expected in reaction_pins.items():
        row = next(row for row in reaction_context if row["locator"]["table"] == table)
        run = next(
            run
            for run in row["values"]["runs"]
            if run["T_K"] == float(temperature)
        )
        assert (run["delta_r_H_cal_mol"], run["delta_r_F_cal_mol"]) == expected
    migrated_context = [
        row
        for rows in result.context_by_work.values()
        for row in rows
        if row.get("source_id") == _SOURCE_ID
        and row.get("type") == "reaction_thermochemistry"
    ]
    assert len(migrated_context) == 3
    assert {row["species"] for row in migrated_context} == {
        "reaction_albite_nephelite_to_jadeite",
        "reaction_albite_to_jadeite_quartz",
        "reaction_nephelite_quartz_to_jadeite",
    }
    with _CELLS_AUDIT.open(encoding="utf-8", newline="") as stream:
        audited_context_cells = [
            row
            for row in csv.DictReader(stream)
            if row["compare_status"] == "matched_context"
        ]
    assert len(audited_context_cells) == 60
    assert {
        table: sum(row["table"] == table for row in audited_context_cells)
        for table in ("34", "36", "37")
    } == {"34": 20, "36": 20, "37": 20}
    with _CELLS_AUDIT.open(encoding="utf-8", newline="") as stream:
        audited_table25_totals = [
            row
            for row in csv.DictReader(stream)
            if row["table"] == "25" and "_entropy_S_" in row["observation_id"]
        ]
    assert len(audited_table25_totals) == 6
    assert {row["compare_status"] for row in audited_table25_totals} == {
        "render_verified"
    }
    compare_audit = _CELLS_AUDIT.with_name(
        "kelley-1953-alkali-aluminosilicate-thermodynamics.compare.csv"
    )
    with compare_audit.open(encoding="utf-8", newline="") as stream:
        compare_mismatches = [
            row
            for row in csv.DictReader(stream)
            if row["status"] == "mismatch"
        ]
    assert len(compare_mismatches) == 6
    assert all("PDF page 24 render" in row["resolution"] for row in compare_mismatches)

    table19 = next(
        row
        for row in extract["species"]["KAlSi3O8"]["observations"]
        if row["observation_id"] == "kelley_1953_table19_cp"
    )
    correction = table19["values"]["composition_correction_as_printed"]
    assert "94.0 mol% KAlSi3O8 + 6.0 mol% NaAlSi3O8" in correction
    assert "Fe left in" in correction
    assert "mol wt 278.35" in correction
    assert "4.1833 int. joules" in extract["metadata"]["source_calorie_definition_as_printed"]
