from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery import migrate as battery_migrate
from tests.battery import factories as battery_factories
from tools import validate_literature_extracts as vle


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/banya-chiba-hikosaka-1980-feto-binary-solid-iron.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_tables_1_to_4_render_pins_and_run_count_migrate_cleanly() -> None:
    document = _document()
    composition_rows = [
        row
        for row in document["species"]["Fe_tO"]["observations"]
        if row["type"] == "composition_series"
    ]
    activity_rows = [
        row
        for row in document["species"]["Fe_tO"]["observations"]
        if row["type"] == "activity_coefficient"
    ]
    ratio_rows = document["species"]["Fe"]["observations"]
    assert len(composition_rows) == len(activity_rows) == len(ratio_rows) == 69
    assert len(document["experiments"]) == 69

    by_id = {
        row["observation_id"]: row
        for rows in document["species"].values()
        for row in rows["observations"]
    }
    # Independent reads from rendered PDF pages 1485–1487 (PDF pages 2–4).
    assert by_id["bch80_t1_c_b_1_activity"]["values"]["activity"] == 0.985
    assert by_id["bch80_t1_c_b_1_valence_ratio"]["values"]["value"] == 0.107
    assert by_id["bch80_t2_c_a_1"]["values"]["PH2O_over_PH2_as_printed"] == 0.876
    assert by_id["bch80_t3_c_c_1"]["values"]["composition_wt_pct"]["Fe2O3"] == 8.55

    assert vle.validate_extract_document(
        document, expected_source_id="banya-chiba-hikosaka-1980-feto-binary-solid-iron"
    ) == []
    migrator = battery_factories.migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert not result.validation.hard_issues
    assert len(result.observations) == 207
    assert all(row.identity.quantity.state.is_value for row in result.observations.values())
    assert all(row.phase.state.is_value for row in result.observations.values())

    for source_id in (
        "bch80_t1_c_b_1_activity",
        "bch80_t2_c_a_1_valence_ratio",
        "bch80_t3_c_c_1",
    ):
        row = result.observations[f"banya-chiba-hikosaka-1980-feto-binary-solid-iron::{source_id}"]
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["composition"].state.is_value
        assert "total_pressure_Pa" not in row.point_conditions

    assert all("pressure_environment" not in experiment for experiment in document["experiments"])

    assert not any(
        row.identity.quantity.state.value is Quantity.UNKNOWN
        for row in result.observations.values()
    )


def test_render_fidelity_samples_reject_mutated_values() -> None:
    document = _document()
    assert vle.check_all_fidelity_samples_match(document, label="banya80") == []
    mutated = yaml.safe_load(yaml.safe_dump(document))
    row = next(
        item
        for item in mutated["species"]["Fe_tO"]["observations"]
        if item["observation_id"] == "bch80_t1_c_b_1_activity"
    )
    row["values"]["activity"] = 0.984
    errors = vle.check_all_fidelity_samples_match(mutated, label="banya80-mutated")
    assert any("mismatch" in error for error in errors)
