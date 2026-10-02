"""Shared engine-origin rules for solved oxygen-balance notices."""

from __future__ import annotations

from typing import Sequence

from simulator.battery.enums import Engine, NoticeKind
from simulator.battery.records import Notice

IMCC_ENGINES: frozenset[Engine] = frozenset({Engine.OPENIMCC})
OXYGEN_BALANCE_EFFUSION_ENGINES: frozenset[Engine] = frozenset(
    {Engine.OPENIMCC, Engine.INTERNAL_ANALYTICAL}
)
OXYGEN_BALANCE_NOTICE_PREFIX = "fo2_oxygen_balance_effusion_solved:"


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
