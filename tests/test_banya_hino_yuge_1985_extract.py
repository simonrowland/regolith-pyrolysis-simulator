"""Source pins and migration checks for Ban-Ya et al. (1985)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.enums import ValueKind
from simulator.battery.migrate import Migrator, to_plain
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/"
    / "banya-hino-yuge-1985-tetsu-feto-sio2-mno-activity.yaml"
)
SOURCE_ID = "banya-hino-yuge-1985-tetsu-feto-sio2-mno-activity"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _by_run(document: dict, species: str) -> dict[str, dict]:
    rows = document["species"][species]["observations"]
    return {row["values"]["run_no_as_printed"]: row for row in rows}


def test_table_1_rows_and_render_pins() -> None:
    document = _document()
    activities = _by_run(document, "Fe_tO")
    ratios = _by_run(document, "Fe")

    assert len(activities) == 30
    assert len(ratios) == 30
    assert set(activities) == set(ratios)
    for run in ("101", "301", "901"):
        activity = activities[run]
        ratio = ratios[run]
        assert activity["values"]["activity"] == {
            "101": "0.100",
            "301": "0.299",
            "901": "0.899",
        }[run]
        assert ratio["values"]["value"] == {
            "101": "0.076",
            "301": "0.025",
            "901": "0.093",
        }[run]
        for row in (activity, ratio):
            assert row["locator"]["published_page"] == 854
            assert row["locator"]["table"] == "1"
            assert row["locator"]["row_label"] == run
            assert row["values"]["composition_wt_pct"]
            assert row["values"]["composition_unit_as_printed"] == "wt%"
            assert row["point_conditions"]["T_C"]["state"]["value"] == "1450"
            assert row["point_conditions"]["T_C"]["locator"]["published_page"] == 854
            assert row["point_conditions"]["composition"]["locator"]["row_label"] == run
            gas_ratio = row["point_conditions"]["fO2_control"]["state"]["value"]["gas_ratio"]
            assert gas_ratio["numerator_species"] == "H2O"
            assert gas_ratio["denominator_species"] == "H2"
            assert gas_ratio["basis"] == "partial_pressure"
            assert gas_ratio["value"]["locator"]["row_label"] == run
            assert "total_pressure_Pa" not in row["point_conditions"]
            assert "fO2_Pa" not in row["point_conditions"]

    assert activities["101"]["phase"] == "l"
    assert activities["101"]["standard_state"]["state"]["value"]["endmember"]["formula"] == "Fe_tO"
    assert ratios["101"]["phase"] == "glass"
    quench = ratios["101"]["point_conditions"]["quench_state"]
    assert quench["state"]["value"] == "not_stated"
    assert quench["locator"]["published_page"] == 853

    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    assert check_all_fidelity_samples_match(document, label=SOURCE_ID) == []
    mutated = deepcopy(document)
    _by_run(mutated, "Fe_tO")["101"]["values"]["activity"] = "0.101"
    assert any(
        "mismatch" in error
        for error in check_all_fidelity_samples_match(mutated, label=SOURCE_ID)
    )


def test_isolated_migration_maps_printed_conditions_and_leaves_unprinted_axes_unknown() -> None:
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    rows = list(migrator.result.observations.values())
    assert len(rows) == 60
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert not migrator.result.queue
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in rows) == 0

    for run in _by_run(_document(), "Fe_tO"):
        for suffix in ("activity", "fe3_fe2_ratio"):
            row = migrator.result.observations[
                f"{SOURCE_ID}::banya_hino_yuge_1985_run_{run}_{suffix}"
            ]
            identity = to_plain(row.identity)
            conditions = to_plain(row.point_conditions)
            assert identity["temperature_K"] == {"tag": "value", "value": "1723.15"}
            assert identity["composition"]["tag"] == "value"
            assert identity["fO2_Pa"]["tag"] == "unknown"
            assert identity["total_pressure_Pa"]["tag"] == "unknown"
            assert conditions["temperature_K"]["state"]["tag"] == "value"
            assert conditions["temperature_K"]["locator"]["published_page"] == 854
            assert conditions["composition"]["state"]["tag"] == "value"
            assert conditions["composition"]["locator"]["published_page"] == 854
            assert conditions["fO2_control"]["state"]["tag"] == "value"
            assert conditions["fO2_control"]["state"]["value"]["gas_ratio"]["value"]["state"]["tag"] == "value"
            if suffix == "fe3_fe2_ratio":
                assert identity["valence_basis"]["tag"] == "value"
                assert conditions["quench_state"]["state"]["value"] == "not_stated"


def test_experiment_binds_printed_charge_and_crucible() -> None:
    document = _document()
    equipment = _by_run(document, "Fe_tO")["101"]["equipment"]
    assert equipment["sample_mass_g"]["value"] == "4"
    assert equipment["sample_mass_g"]["locator"]["published_page"] == 853
    assert equipment["crucible_material"]["value"] == "pure iron"
    assert equipment["crucible_material"]["locator"]["published_page"] == 853

    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    experiment = next(iter(migrator.result.experiments.values()))
    migrated = to_plain(experiment)
    assert migrated["sample"]["mass_kg"]["state"]["value"] == {
        "kind": "point",
        "point": "0.004",
        "approximate": False,
    }
    assert migrated["sample"]["mass_kg"]["locator"]["published_page"] == 853
    assert migrated["apparatus"]["cell_material_and_liner"]["state"] == {
        "tag": "value",
        "value": "pure iron",
    }
    assert migrated["apparatus"]["cell_material_and_liner"]["locator"][
        "published_page"
    ] == 853
