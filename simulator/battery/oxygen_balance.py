"""Shared engine-origin rules for solved oxygen-balance notices."""

from __future__ import annotations

from collections.abc import Sequence

from simulator.battery.enums import Engine, NoticeKind
from simulator.battery.records import Notice
from simulator.chemistry.langmuir_knudsen import (
    OxygenBalanceBracketBoundary,
    _VacuumOxygenChannel,
    _VacuumOxygenBalance,
    _net_evaporation_pressure_Pa,
    _solve_vacuum_oxygen_balance,
)

IMCC_ENGINES: frozenset[Engine] = frozenset({Engine.OPENIMCC})
OXYGEN_BALANCE_EFFUSION_ENGINES: frozenset[Engine] = frozenset(
    {
        Engine.OPENIMCC,
        Engine.INTERNAL_ANALYTICAL,
        Engine.VAPOROCK,
        Engine.ALPHAMELTS,
    }
)
OXYGEN_BALANCE_NOTICE_PREFIX = "fo2_oxygen_balance_effusion_solved:"
_OXYGEN_GAS_ALPHA = 1.0
_OXYGEN_GAS_ALPHA_SOURCE = (
    "declared common-unity sensitivity alpha=1.0 for O/O2 free-molecular gas escape; "
    "absolute oxygen-gas alpha is not measured"
)


def has_own_engine_solved_oxygen_balance(
    engine: Engine | None,
    notices: Sequence[Notice],
) -> bool:
    return engine in OXYGEN_BALANCE_EFFUSION_ENGINES and any(
        notice.kind is NoticeKind.SOURCE_DISAGREEMENT
        and notice.origin == f"engine:{engine.value}"
        and notice.reason.startswith(OXYGEN_BALANCE_NOTICE_PREFIX)
        for notice in notices
    )
