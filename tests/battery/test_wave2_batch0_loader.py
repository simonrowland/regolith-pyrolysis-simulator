"""Loader: rows and non-yield points explode on the series point path.

Explicit ``point_conditions`` on the row overwrite inferred conditions.
Yield tables stay on their existing path and are not emitted twice.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import ValueKind
from simulator.battery.identity import atm_to_pa
from simulator.battery.migrate import migrate
from simulator.battery.records import as_decimal
from simulator.battery.waypoints import GapReason, consumer_readiness
from tests.battery import factories
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree
from tests.battery.test_waypoints import _bench


def _migrate_obs(tmp_path: Path, values: dict):
    extract = deepcopy(FIXTURE_EXTRACT)
    row = extract["species"]["Na"]["observations"][0]
    row["values"] = {
        "quantity": "pure_Psat",
        "method_class": "measured_direct",
        "admission_status": "admitted",
        **values,
    }
    root = tmp_path / "tree"
    root.mkdir(parents=True)
    result = migrate(_write_min_tree(root, extract), write=False)
    return row, result


def test_rows_explode_through_series_point_path(tmp_path: Path) -> None:
    parent, result = _migrate_obs(
        tmp_path,
        {
            "rows": [
                {"T_K": 1400, "pressure_atm": 1, "run": "a"},
                {"T_K": 1500, "pressure_atm": 2, "run": "b"},
            ]
        },
    )
    children = [
        obs
        for obs in result.observations.values()
        if obs.observation_id.startswith("fixture-source::na_psat")
    ]
    assert len(children) == 2
    assert "fixture-source::na_psat" not in result.observations
    temperatures = sorted(
        obs.identity.temperature_K.value for obs in children if obs.identity.temperature_K.is_value
    )
    assert temperatures == [Decimal("1400"), Decimal("1500")]
    pressures = sorted(obs.value.point for obs in children)
    assert pressures == [atm_to_pa("1"), atm_to_pa("2")]
    assert all(
        obs.point_conditions and obs.point_conditions["temperature_K"].state.value == obs.identity.temperature_K.value
        for obs in children
    )


def test_nonyield_points_explode_once_and_yield_points_stay_single(tmp_path: Path) -> None:
    _, points = _migrate_obs(
        tmp_path / "points",
        {"points": [{"T_K": 1200, "pressure_atm": 1}, {"T_K": 1300, "pressure_atm": 2}]},
    )
    exploded = [
        obs
        for obs in points.observations.values()
        if obs.observation_id.startswith("fixture-source::na_psat")
    ]
    assert len(exploded) == 2
    assert "fixture-source::na_psat" not in points.observations

    _, yielded = _migrate_obs(
        tmp_path / "yield",
        {
            "points": [
                {"T_C": 1000, "mass_loss_pct": 10},
                {"T_C": 1100, "mass_loss_pct": 20},
            ]
        },
    )
    yield_children = [
        obs
        for obs in yielded.observations.values()
        if "na_psat" in obs.observation_id
    ]
    assert len(yield_children) == 2


def test_explicit_row_point_conditions_win_over_inferred(tmp_path: Path) -> None:
    _, result = _migrate_obs(
        tmp_path,
        {
            "rows": [
                {
                    "T_K": 1400,
                    "total_pressure_Pa": 5,
                    "run": "explicit",
                    "point_conditions": {
                        "temperature_K": {
                            "state": {"tag": "value", "value": {"kind": "point", "point": "1500"}}
                        },
                        "total_pressure_Pa": {
                            "state": {
                                "tag": "value",
                                "value": {
                                    "kind": "interval",
                                    "interval_low": "10",
                                    "interval_high": "20",
                                },
                            }
                        },
                    },
                }
            ]
        },
    )
    obs = next(iter(result.observations.values()))
    temperature = obs.point_conditions["temperature_K"].state.value
    assert getattr(temperature, "point", temperature) == Decimal("1500")
    pressure = obs.point_conditions["total_pressure_Pa"]
    assert pressure.state.value.kind is ValueKind.INTERVAL
    assert pressure.state.value.interval_low == as_decimal("10")
    assert pressure.state.value.interval_high == as_decimal("20")
    experiment = factories.kems_experiment()
    readiness = consumer_readiness(
        experiment, _bench(), replace(obs, experiment_id=experiment.experiment_id)
    )
    pressure_gap = next(
        item
        for item in readiness
        if item.consumer == "engine_point" and item.engine == "internal-analytical"
    )
    assert any(
        gap.waypoint == "pressure_boundary" and gap.reason is GapReason.INTERVAL_NEEDS_POINT
        for gap in pressure_gap.gaps
    )
