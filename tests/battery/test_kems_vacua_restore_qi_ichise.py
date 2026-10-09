"""Pin: BACKLOG 14 — KEMS chamber vacua follow-up after b-703 landed.

Qi, Hino & Azakami 1989 (kems-127) is a closed-token ``knudsen_effusion``
source. Its printed furnace vacuum, "The vacuum in the furnace could be
maintained at 7 x 10^-5 Pa" (published p. 577 / PDF page 3), was demoted to a
residual note by 915f55fb4 on review/extract-pressure-fields. Under the b-703
convention (data/literature/extracts/SCHEMA.md) the experiment-level
``total_pressure_Pa`` of a knudsen_effusion experiment IS the chamber
background the KEMS validity gates consume, and the migrator/waypoints keep it
out of observation identity and every prediction pressure route.

These tests pin, for the real Qi extract:
1. the value sits on the experiment field (point 7e-5 Pa, p. 577 locator);
2. it reaches the KEMS validity gate input (background_pressure_high) and the
   raw pressure_boundary waypoint the KEMS consumer reads;
3. it is NOT inherited into any observation identity or point_conditions, and
   it is NOT selected as prediction pressure (engine-point / oxygen routes).

Ichise, Ueshima & Yamana 1989 (kems-198) prints "both chambers were
differentially pumped to about 10^-6 to 10^-7 torr" (p. 297 / PDF page 3), but
its regime ``kems_ion_intensity_thermal_analysis`` is not the closed
``knudsen_effusion`` token, so b-703's rule does not cover it: as an experiment
total it would be skipped by the KEMS gates and offered to engine-point as a
candidate system pressure. It stays a located residual note until
regolith-main rules on the method token; the last test pins that hold.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.consumer_inputs import collect_consumer_inputs
from simulator.battery.enums import MethodToken, Quantity, ValueKind
from simulator.battery.generators.bench import engine_point_requests
from simulator.battery.records import Value
from simulator.battery.validity import background_pressure_high
from simulator.battery.waypoints import (
    _prediction_pressure_waypoint,
    oxygen_condition,
    pressure_boundary,
)
from tests.battery.test_migrate import _migrate_real_extract

QI = "kems-127-qi-1989.yaml"
ICHISE = "kems-198-ichise-ueshima-1989.yaml"
QI_FURNACE_VACUUM_PA = Decimal("0.00007")
QI_FURNACE_VACUUM_BAR = float(QI_FURNACE_VACUUM_PA / Decimal(100000))
KEMS_GATE_QUANTITIES = (
    Quantity.P_SAT,
    Quantity.P_PARTIAL,
    Quantity.P_REFERENCE,
    Quantity.ACTIVITY,
    Quantity.ACTIVITY_COEFFICIENT,
)


def _only_experiment(result):
    assert len(result.experiments) == 1
    return next(iter(result.experiments.values()))


def _bench(result, experiment):
    from scripts.bench_readiness import _implicit_bench

    if experiment.bench_id:
        return result.benches[experiment.bench_id]
    bench = _implicit_bench(experiment, result.works.get(experiment.work_id), ())
    assert bench is not None
    return bench


@pytest.fixture(scope="module")
def qi(tmp_path_factory):
    return _migrate_real_extract(tmp_path_factory.mktemp("qi"), QI)


def test_qi_1989_furnace_vacuum_is_on_the_experiment_field(qi) -> None:
    experiment = _only_experiment(qi)
    assert experiment.method.is_value
    assert experiment.method.value is MethodToken.KNUDSEN_EFFUSION
    total = experiment.pressure_environment.total_pressure_Pa
    assert total.state.is_value, total.state
    value = total.state.value
    assert isinstance(value, Value) and value.kind is ValueKind.POINT
    assert value.point == QI_FURNACE_VACUUM_PA
    assert total.locator is not None
    assert total.locator.published_page == 577
    assert total.locator.pdf_page_index == 3
    assert "vacuum in the furnace" in (total.locator.note or "")


def test_qi_1989_furnace_vacuum_reaches_the_kems_validity_gates(qi) -> None:
    experiment = _only_experiment(qi)
    for quantity in KEMS_GATE_QUANTITIES:
        outcome = background_pressure_high(experiment, quantity)
        checks = [check for check in outcome.checks if check.name == "background_pressure"]
        assert checks, f"{quantity.value}: gate did not read the experiment total"
        detail = checks[0].detail
        assert detail["upper_bound_Pa"] == str(QI_FURNACE_VACUUM_PA)
        assert detail["pressure_kind"] == "point"
        assert outcome.passed and checks[0].passed
    # The raw pressure_boundary waypoint (KEMS/exterior consumers) carries it.
    observation = next(iter(qi.observations.values()))
    boundary = pressure_boundary(experiment, _bench(qi, experiment), observation)
    assert boundary.selected is not None
    assert boundary.selected.route == "printed_run_pressure"
    assert boundary.selected.value.point == QI_FURNACE_VACUUM_PA


def test_qi_1989_furnace_vacuum_not_identity_nor_prediction_pressure(qi) -> None:
    experiment = _only_experiment(qi)
    bench = _bench(qi, experiment)
    assert qi.observations
    for observation in qi.observations.values():
        identity_pressure = observation.identity.total_pressure_Pa
        assert identity_pressure is None or not identity_pressure.is_value, (
            f"{observation.observation_id} inherited {identity_pressure!r}"
        )
        assert "total_pressure_Pa" not in (observation.point_conditions or {})
        boundary = pressure_boundary(experiment, bench, observation)
        prediction = _prediction_pressure_waypoint(boundary, experiment.method)
        assert prediction is None or prediction.route != "printed_run_pressure"
        oxygen = oxygen_condition(experiment, bench, observation)
        assert not [
            route
            for route in oxygen.routes
            if route.route == "vacuum_total_pressure_upper_bound"
        ]
        for generated in engine_point_requests(
            collect_consumer_inputs(experiment, bench, observation)
        ):
            if generated.payload is not None:
                assert generated.payload.get("pressure_bar") != QI_FURNACE_VACUUM_BAR


def test_ichise_ueshima_1989_chamber_vacuum_held_as_note_until_method_ruling(
    tmp_path: Path,
) -> None:
    result = _migrate_real_extract(tmp_path, ICHISE)
    experiment = _only_experiment(result)
    # Not the closed knudsen_effusion token: b-703's rule does not apply.
    assert not (
        experiment.method.is_value
        and experiment.method.value is MethodToken.KNUDSEN_EFFUSION
    )
    assert not experiment.pressure_environment.total_pressure_Pa.state.is_value
    for observation in result.observations.values():
        identity_pressure = observation.identity.total_pressure_Pa
        assert identity_pressure is None or not identity_pressure.is_value
