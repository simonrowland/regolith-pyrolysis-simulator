"""Pin the shared effusion closure used by every engine.

The Stolyarova values follow the shared tier-3 closure in f2e84a99f and use
the Table Ia pO2 cell for X(SiO2)=0.50 (physical PDF p. 2).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

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


def test_costa_and_stolyarova_effusion_numbers_match_shared_closure() -> None:
    context = load_score_context(
        _ROOT,
        sources=("kems-007-costa-2015", "kems-053-stolyarova-1991"),
    )
    for (observation_id, engine), (value, bar) in _PIN.items():
        observation = context.observations[observation_id]
        experiment = context.experiments[observation.experiment_id]
        bench = _bench_for_score(experiment, context.benches)
        prediction = predict_with_engine(
            engine,
            observation,
            experiment=experiment,
            bench=bench,
            isolated=False,
        )
        assert prediction.refusal_reason is None, prediction.refusal_detail
        assert prediction.value == value
        assert _solved_effusion_po2_bar(prediction) == bar
        assert any(
            notice.reason == "fo2_tier=3; route=effusion_oxygen_balance"
            for notice in prediction.notices
        )
