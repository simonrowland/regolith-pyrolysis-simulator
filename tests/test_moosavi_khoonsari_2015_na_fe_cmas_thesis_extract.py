from __future__ import annotations

import csv
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml
import pytest

from simulator.battery.enums import Phase, PhaseRole, Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract

SOURCE_ID = "moosavi-khoonsari-2015-na-fe-cmas-thesis"
EXTRACT = REPO_ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
CELL_AUDIT = EXTRACT.parent / "audits" / f"{SOURCE_ID}.cell_audit.csv"

# Independent pins from the rendered printed table cells. Pages are absolute
# PDF pages; table labels are the labels printed in the table captions.
RENDER_PINS = {
    "mk15_t71_naf1_1_glass_na2o_wt_percent": (
        "32.80", "0.67", 186, "7.1", "NAF1-1", "Glass", "%"
    ),
    "mk15_t72_naf2_1_glass_feo_wt_percent": (
        "59.58", "1.03", 188, "7.2", "NAf2-1", "Glass", "%"
    ),
    "mk15_t72_naf9_1_glass_al2o3_mole_fraction": (
        "0.10", "0.01", 188, "7.2", "NAf9-1", "Glass", "dimensionless"
    ),
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _source_rows(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for body in document["species"].values()
        for kind in ("observations", "context")
        for row in body.get(kind, [])
    }


def _assert_render_pins(document: dict) -> None:
    rows = _source_rows(document)
    for observation_id, (amount, sigma, page, table, sample, phase, units) in RENDER_PINS.items():
        row = rows[observation_id]
        assert row["values"]["component_amount"] == amount
        assert row["uncertainty"]["value"] == sigma
        assert row["units"] == units
        assert row["locator"]["page"] == page
        assert row["locator"]["table"] == table
        assert row["locator"]["sample"] == sample
        assert row["locator"]["row"] == phase

    spinel = next(
        row
        for row in rows.values()
        if row.get("values", {}).get("sample_id_as_published") == "NAF13-2"
        and row["values"].get("phase_label_as_published") == "Spinel"
        and row["values"].get("formula") == "Na2O"
    )
    assert spinel["locator"]["page"] == 186
    assert spinel["locator"]["table"] == "7.1"
    assert spinel["values"]["phase_composition_cells_as_published"]["mole_fraction"] == "0.00 (.00)"


def test_render_pins_and_complete_printed_row_audit() -> None:
    document = _document()
    _assert_render_pins(document)

    rows = _source_rows(document)
    phase_rows = {
        row.get("values", {}).get("phase_row_id")
        for row in rows.values()
        if row.get("values", {}).get("phase_row_id")
    }
    assert len(phase_rows) == 63
    assert sum(row_id.startswith("mk15_t71_") for row_id in phase_rows) == 37
    assert sum(row_id.startswith("mk15_t72_") for row_id in phase_rows) == 26

    numeric_phase_rows = {
        row["values"]["phase_row_id"]
        for row in rows.values()
        if row.get("values", {}).get("phase_row_id")
        and (
            "component_amount" in row["values"]
            or "phase_composition_cells_as_published" in row["values"]
        )
    }
    assert len(numeric_phase_rows) == 58
    assert len(phase_rows - numeric_phase_rows) == 5

    with CELL_AUDIT.open(newline="", encoding="utf-8") as handle:
        audit = list(csv.DictReader(handle))
    assert len(audit) == 984
    assert sum(row["cell"] not in {"sample", "phase_label", "phase_composition"} for row in audit) == 889


def test_render_pins_reject_an_extract_cell_mutation() -> None:
    mutated = deepcopy(_document())
    row = _source_rows(mutated)["mk15_t71_naf1_1_glass_na2o_wt_percent"]
    row["values"]["component_amount"] = "32.81"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_naf7_quench_area_mapping_recovery_is_pinned() -> None:
    document = _document()
    table72_experiments = [
        experiment for experiment in document["experiments"]
        if experiment["experiment_id"].startswith("mk15_t72_")
    ]
    assert len(table72_experiments) == 11
    for experiment in table72_experiments:
        note = experiment["quench_statement"]["note"]
        assert all(sample in note for sample in ("NAF6-1", "NAF7-1", "NAF7-2"))
        assert "EPMA area mapping of liquid pools including quench crystals" in note
        assert "analyzed liquid compositions equal to the starting compositions" in note
        assert "did not influence that analysis" in note
        assert "does not state whether quench crystals were included" in note
        assert "other Table 7.2 samples" in note
        assert "physical page 179, published page 150" in note

    glass_rows = [
        row for row in _source_rows(document).values()
        if row.get("type") == "equilibrium_phase_composition"
        and row.get("values", {}).get("sample_id_as_published") in {"NAf7-1", "NAf7-2"}
    ]
    assert len(glass_rows) == 12
    for row in glass_rows:
        statement = row["values"]["quench_statement"]
        assert statement["preservation"] == "asserted_altered"
        text = statement["text"]
        assert "Physical quench crystals composed of Fe-rich phases" in text
        assert all(sample in text for sample in ("NAF6-1", "NAF7-1", "NAF7-2"))
        assert "Separately, page 179 (published page 150)" in text
        assert "EPMA area mapping including quench crystals" in text
        assert "analyzed liquid compositions equal to the starting compositions" in text
        assert "did not influence that analysis" in text


def test_isolated_migration_preserves_values_conditions_and_unknown_pressure_axes() -> None:
    document = _document()
    source_rows = _source_rows(document)
    migrator = migrate_one_extract(REPO_ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result

    assert not result.registry_issues
    assert result.validation is not None
    assert not result.validation.hard_issues
    assert len(result.observations) == 72
    assert sum(len(rows) for rows in result.context_by_work.values()) == 147
    assert not result.queue

    expected_issue_paths = {
        f"observation[{SOURCE_ID}::{observation_id}].identity.{axis}"
        for observation_id, row in source_rows.items()
        if row.get("type") == "equilibrium_phase_composition"
        for axis in ("fO2_Pa", "total_pressure_Pa")
    }
    assert {issue.path for issue in result.validation.issues} == expected_issue_paths
    assert len(result.validation.issues) == 144
    assert all(issue.reason.value == "identity_incomplete" for issue in result.validation.issues)

    for observation_id, row in source_rows.items():
        if row.get("type") != "equilibrium_phase_composition":
            continue
        observation = result.observations[f"{SOURCE_ID}::{observation_id}"]
        assert observation.value.kind is ValueKind.POINT
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        assert observation.identity.species.phase.is_value
        assert observation.identity.species.phase.value is Phase.GLASS
        assert observation.identity.phase_role.is_value
        assert observation.identity.phase_role.value is PhaseRole.GLASS

        expected_conditions = {
            "temperature_K": Decimal(row["T_K"]),
            "duration_h": Decimal(row["point_conditions"]["duration_h"]["state"]["value"]),
        }
        for axis in ("temperature_K", "duration_h", "composition"):
            condition = observation.point_conditions[axis]
            assert condition.state.is_value
            assert condition.locator is not None
            assert condition.locator.page == row["locator"]["page"]
            assert condition.locator.table == row["locator"]["table"]
            if axis in expected_conditions:
                assert condition.state.value == expected_conditions[axis]

        for axis in ("fO2_Pa", "total_pressure_Pa"):
            assert not getattr(observation.identity, axis).is_value
            assert not observation.point_conditions[axis].state.is_value

    for observation_id, (amount, _sigma, page, table, _sample, _phase, units) in RENDER_PINS.items():
        raw = source_rows[observation_id]
        observation = result.observations[f"{SOURCE_ID}::{observation_id}"]
        assert observation.locator.page == page
        assert observation.locator.table == table
        assert observation.value.point == Decimal(amount)
        assert observation.identity.subtype.value == raw["values"]["basis"]
        assert raw["units"] == units

    percent_row = source_rows["mk15_t71_naf1_1_glass_na2o_wt_percent"]
    assert percent_row["units"] == "%"
    assert result.observations[
        f"{SOURCE_ID}::mk15_t71_naf1_1_glass_na2o_wt_percent"
    ].value.point == Decimal("32.80")

    experiment = next(
        row for row in document["experiments"]
        if row["sample_id_as_published"] == "NAF1-1"
    )
    printed_charge = experiment["starting_composition_as_published"]["components"]
    assert printed_charge == [["Na2O", "57.64"], ["Al2O3", "14.02"], ["Fe2O3", "28.34"]]
    migrated_charge = result.observations[
        f"{SOURCE_ID}::mk15_t71_naf1_1_glass_na2o_wt_percent"
    ].identity.composition
    assert migrated_charge.is_value
    assert dict(migrated_charge.value.components) == {
        "Na2O": Decimal("0.5764"),
        "Al2O3": Decimal("0.1402"),
        "Fe2O3": Decimal("0.2834"),
    }

    argon_row = source_rows["mk15_t72_naf2_1_glass_feo_wt_percent"]
    argon_observation = result.observations[f"{SOURCE_ID}::mk15_t72_naf2_1_glass_feo_wt_percent"]
    assert "5 x 10^-14 bar" in argon_row["values"]["external_furnace_gas_as_published"]
    assert not argon_observation.identity.fO2_Pa.is_value
    assert not argon_observation.identity.total_pressure_Pa.is_value

    for experiment in document["experiments"]:
        glass_rows = [
            row for row in source_rows.values()
            if row.get("experiment") == experiment["experiment_id"]
            and row.get("type") == "equilibrium_phase_composition"
        ]
        for row in glass_rows:
            statement = row["values"]["quench_statement"]
            assert statement["preservation"] == "asserted_altered"
            quench_page = 176 if experiment["experiment_id"].startswith("mk15_t71_") else 181
            assert statement["locator"]["page"] == quench_page
