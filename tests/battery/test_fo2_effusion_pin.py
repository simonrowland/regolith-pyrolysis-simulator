"""Pin the shared effusion closure used by every engine.

The Stolyarova values follow the shared tier-3 closure in f2e84a99f and use
the Table Ia pO2 cell for X(SiO2)=0.50 (physical PDF p. 2).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import Engine
from simulator.battery.score import (
    _bench_for_score,
    _solved_effusion_po2_bar,
    load_score_context,
    predict_with_engine,
)

_ROOT = Path(__file__).resolve().parents[2]
_COSTA = (
    "kems-007-costa-2015::costa_2015_fe_partial_pressure_mo_digitized"
    "::T=1700.48:h=f7794f317b6a"
)
_STOLYAROVA = (
    "kems-053-stolyarova-1991::"
    "stolyarova_1991_ca_partial_pressure_1993k_complete_evaporation"
    "::T=1993.00:h=152b6c6f5a56"
)
_CENSUS_CELLS = (
    "rys-2007-alkali-oxide-thermodynamics::"
    "rys_2007_appendix_na_partial_pressures_table5::T=1859.558:h=e95b8d89416d",
    "stolyarova-2021-ti-al-kems-ru::"
    "stolyarova_2021_table3_Al2O_partial_pressure_row_14",
    "stolyarova-2021-ti-al-kems-ru::"
    "stolyarova_2021_table3_TiO_partial_pressure_row_13",
)
_ORDINARY_CELLS = (
    "rys-2007-alkali-oxide-thermodynamics::"
    "rys_2007_appendix_na_partial_pressures_table5::T=1854.464:h=7a344a785dea",
    "stolyarova-2021-ti-al-kems-ru::"
    "stolyarova_2021_table3_Al2O_partial_pressure_row_13",
    "stolyarova-2021-ti-al-kems-ru::"
    "stolyarova_2021_table3_TiO_partial_pressure_row_14",
)
_SCORE_SOURCES = (
    "kems-007-costa-2015",
    "kems-053-stolyarova-1991",
    "rys-2007-alkali-oxide-thermodynamics",
    "stolyarova-2021-ti-al-kems-ru",
)


@pytest.fixture(scope="module")
def _score_context():
    return load_score_context(_ROOT, sources=_SCORE_SOURCES)
# value, solved pO2 bar. Costa's internal-analytical species pressure uses the
# offer 20 r8 vapour route at the same solved pO2.
_PIN = {
    (_COSTA, Engine.INTERNAL_ANALYTICAL): (
        Decimal("0.13890413464807302"),
        2.74738410411159e-10,
    ),
    (_COSTA, Engine.OPENIMCC): (
        Decimal("0.03760095446103834"),
        2.74738410411159e-10,
    ),
    (_STOLYAROVA, Engine.INTERNAL_ANALYTICAL): (
        Decimal("0.00007054458263120522"),
        1.99531976228266e-08,
    ),
    (_STOLYAROVA, Engine.OPENIMCC): (
        Decimal("0.00042677717378297335"),
        1.99531976228266e-08,
    ),
}


def test_costa_and_stolyarova_effusion_numbers_match_shared_closure(
    _score_context,
) -> None:
    context = _score_context
    for (observation_id, engine), (value, bar) in _PIN.items():
        observation = context.observations[observation_id]
        experiment = context.experiments[observation.experiment_id]
        bench = _bench_for_score(experiment, context.benches)
        prediction = predict_with_engine(
            engine,
            observation,
            experiment=experiment,
            bench=bench,
        )
        assert prediction.refusal_reason is None, prediction.refusal_detail
        assert prediction.value == value
        assert _solved_effusion_po2_bar(prediction) == bar
        assert any(
            notice.reason == (
                "fo2_tier=3; physics_rung=3:congruent_effusion_balance; "
                "route=effusion_oxygen_balance"
            )
            for notice in prediction.notices
        )


def test_internal_analytical_in_process_and_isolated_values_match(
    _score_context,
) -> None:
    context = _score_context
    for observation_id in (*_CENSUS_CELLS, *_ORDINARY_CELLS):
        observation = context.observations[observation_id]
        experiment = context.experiments[observation.experiment_id]
        bench = _bench_for_score(experiment, context.benches)
        in_process = predict_with_engine(
            Engine.INTERNAL_ANALYTICAL,
            observation,
            isolated=False,
            experiment=experiment,
            bench=bench,
        )
        isolated = predict_with_engine(
            Engine.INTERNAL_ANALYTICAL,
            observation,
            isolated=True,
            experiment=experiment,
            bench=bench,
        )
        assert in_process.value is not None, observation_id
        assert isolated.value == in_process.value, observation_id
        assert isolated.notices == in_process.notices, observation_id
        assert isolated.refusal_reason == in_process.refusal_reason, observation_id


def test_default_ia_isolation_ignores_other_engine_handle(
    monkeypatch: pytest.MonkeyPatch,
    _score_context,
) -> None:
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    observation = _score_context.observations[_CENSUS_CELLS[0]]
    experiment = _score_context.experiments[observation.experiment_id]
    isolated_timeouts: list[float] = []
    run_isolated = battery._run_cell_in_subprocess

    def capture_isolated_call(*args, **kwargs):
        isolated_timeouts.append(kwargs["timeout_s"])
        return run_isolated(*args, **kwargs)

    monkeypatch.setattr(battery, "_run_cell_in_subprocess", capture_isolated_call)
    handles = {
        Engine.VAPOROCK.value: battery.EngineHandle(
            name=Engine.VAPOROCK.value,
            backend=object(),
            available=True,
            unavailable_reason=None,
            takes_fo2=False,
            supports_intrinsic_fo2=False,
        )
    }
    try:
        prediction = predict_with_engine(
            Engine.INTERNAL_ANALYTICAL,
            observation,
            handles=handles,
            experiment=experiment,
            bench=_bench_for_score(experiment, _score_context.benches),
        )
    finally:
        battery.close_battery_engine_handles(handles)

    assert prediction.value is not None
    assert isolated_timeouts == [battery._ENGINE_OUTER_TIMEOUT_S["internal-analytical"]]


def test_whole_store_ia_uses_isolated_worker_with_other_engine_cached(
    monkeypatch: pytest.MonkeyPatch,
    _score_context,
) -> None:
    import simulator.battery.score as score_module
    from simulator.battery.score import score_store
    from simulator.diagnostic_helpers import binary_pot_battery as battery

    context = _score_context
    observation = context.observations[_CENSUS_CELLS[0]]
    monkeypatch.setattr(
        score_module, "comparison_candidates", lambda _context: (observation,)
    )
    isolated_timeouts: list[float] = []
    run_isolated = battery._run_cell_in_subprocess

    def capture_isolated_call(*args, **kwargs):
        isolated_timeouts.append(kwargs["timeout_s"])
        return run_isolated(*args, **kwargs)

    monkeypatch.setattr(battery, "_run_cell_in_subprocess", capture_isolated_call)
    cached_vaporock = battery.EngineHandle(
        name=Engine.VAPOROCK.value,
        backend=object(),
        available=True,
        unavailable_reason=None,
        takes_fo2=False,
        supports_intrinsic_fo2=False,
    )
    score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        include_diagnostics=False,
        handles={Engine.VAPOROCK.value: cached_vaporock},
    )

    assert isolated_timeouts == [battery._ENGINE_OUTER_TIMEOUT_S["internal-analytical"]]
