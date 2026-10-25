from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator
from simulator.battery.validity import effusion_regime_unverified


ROOT = Path(__file__).resolve().parents[1]
EXTRACT_PATH = ROOT / "data/literature/extracts/drowart-1960-al2o3-kems.yaml"


def _extract() -> dict:
    return yaml.safe_load(EXTRACT_PATH.read_text(encoding="utf-8"))


def _table_points(extract: dict, table: str) -> list[tuple[str, dict]]:
    result = []
    experiment = f"drowart-1960-{'w' if table == 'I' else 'mo'}-al2o3"
    for formula, block in extract["species"].items():
        for observation in block["observations"]:
            if observation.get("experiment") != experiment:
                continue
            result.extend((formula, point) for point in observation["values"]["points"])
    return result


def test_rendered_table_cells_and_printed_row_counts() -> None:
    extract = _extract()
    table_i = _table_points(extract, "I")
    table_ii = _table_points(extract, "II")
    assert len({point["experiment_id_as_printed"] for _formula, point in table_i}) == 17
    assert len({point["experiment_id_as_printed"] for _formula, point in table_ii}) == 16
    assert len(table_i) == 73
    assert len(table_ii) == 68

    pins = {
        ("O", "I", "1a", "P_O"): ("1.5e-7", "1.5 × 10^-7 atm", 1368),
        ("AlO", "I", "7a", "P_AlO"): ("1.1e-6", "1.1 × 10^-6 atm", 1368),
        ("Al2O2", "II", "2a", "P_Al2O2"): ("6.7e-9", "6.7 × 10^-9 atm", 1370),
    }
    for (formula, table, row, column), (pressure_atm, printed, page) in pins.items():
        points = _table_points(extract, table)
        selected = [
            point
            for species, point in points
            if species == formula and point["experiment_id_as_printed"] == row
        ]
        assert len(selected) == 1
        point = selected[0]
        assert point["pressure_atm"] == pressure_atm
        assert point["pressure_as_printed"] == printed
        assert point["locator"]["published_page"] == page
        assert point["locator"]["column"] == column


def test_isolated_migration_preserves_each_point_and_effusion_gate() -> None:
    migrator = Migrator(ROOT)
    migrator._migrate_extract(EXTRACT_PATH)
    migrator.finalize()
    observations = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == "drowart-1960-al2o3-kems"
    ]
    assert len(observations) == 141
    assert all(observation.value.kind.value == "point" for observation in observations)
    assert all(observation.identity.quantity.value is Quantity.P_PARTIAL for observation in observations)
    assert all(
        observation.point_conditions
        and "temperature_K" in observation.point_conditions
        and observation.point_conditions["temperature_K"].state.is_value
        and observation.point_conditions["temperature_K"].locator is not None
        and "composition" in observation.point_conditions
        and observation.identity.composition.is_value
        for observation in observations
    )

    experiments = migrator.result.experiments
    grouped = {}
    for observation in observations:
        grouped.setdefault(observation.experiment_id, []).append(observation)
    assert sorted(len(rows) for rows in grouped.values()) == [68, 73]
    for experiment_id, rows in grouped.items():
        experiment = experiments[experiment_id]
        assert not experiment.pressure_environment.total_pressure_Pa.state.is_value
        for observation in rows:
            gate = effusion_regime_unverified(
                experiment,
                Quantity.P_PARTIAL,
                observation=observation,
                point_observations=rows,
            )
            assert gate.passed, (experiment_id, observation.observation_id, gate)

    assert not migrator.result.registry_issues
    identity_gaps = migrator.result.validation.issues
    assert len(identity_gaps) == 2 * len(observations)
    assert all(issue.reason.value == "identity_incomplete" for issue in identity_gaps)
    assert {
        issue.detail for issue in identity_gaps
    } == {
        "required axis total_pressure_Pa is unknown",
        "required axis fO2_Pa is unknown",
    }
    assert not [o for o in observations if o.value.kind.value == "unavailable"]
