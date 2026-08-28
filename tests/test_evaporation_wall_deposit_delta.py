from __future__ import annotations

import pytest

from simulator.evaporation import EvaporationMixin


class _WallDepositDeltaRecorder(EvaporationMixin):
    def __init__(self) -> None:
        self._last_wall_deposit_by_segment_species_delta: dict[
            tuple[str, str], float
        ] = {}


def _legacy_diagnostic(account_kg: dict[str, float]) -> dict[str, object]:
    return {
        "wall_deposit_accounts_kg_delta_by_species": "legacy-flat-payload",
        "credited_wall_deposit_accounts_kg": account_kg,
    }


def test_legacy_wall_deposit_delta_preserves_signed_deltas_zero_and_absence() -> None:
    recorder = _WallDepositDeltaRecorder()
    positive = ("positive_segment", "SiO2")
    consumed_substrate = ("reaction_segment", "SiO2")
    proven_zero = ("zero_segment", "SiO2")
    absent = ("absent_segment", "SiO2")

    recorder._record_wall_deposit_delta(
        "SiO2",
        _legacy_diagnostic(
            {
                positive[0]: 0.75,
                consumed_substrate[0]: 1.0,
                proven_zero[0]: 0.0,
            }
        ),
    )
    recorder._record_wall_deposit_delta(
        "SiO2",
        _legacy_diagnostic({consumed_substrate[0]: -0.75}),
    )

    cumulative = recorder._last_wall_deposit_by_segment_species_delta
    assert cumulative[positive] == pytest.approx(0.75)
    assert cumulative[consumed_substrate] == pytest.approx(0.25)
    assert cumulative[proven_zero] == 0.0
    assert absent not in cumulative
