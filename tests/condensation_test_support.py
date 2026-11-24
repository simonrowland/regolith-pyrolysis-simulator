from __future__ import annotations

from collections.abc import Mapping

from simulator.reduced_real_determinism import (
    ENVIRONMENT_GATE_HOLD_MESSAGE,
    find_environment_gate_refusal,
)


def assert_no_sio_gate_environment_holds(report: Mapping) -> None:
    for hold in report.get("gate_environment_holds", ()) or ():
        if find_environment_gate_refusal(
            {"gate_provider_refusal": hold}
        ) is not None:
            raise AssertionError(ENVIRONMENT_GATE_HOLD_MESSAGE)


def assert_pfree_wall_prediction(route, species: str) -> None:
    """Require the d-402 flagged bound when the wall pO2 is unknown."""
    notices = [
        notice
        for key, notice in route.antoine_extrapolations.items()
        if str(key).split("#", 1)[0] == species
        and isinstance(notice, Mapping)
        and notice.get("reason") == "wall_pO2_unknown_pO2_free_route_used"
    ]
    assert notices
    assert all(
        notice.get("authority_level") == "status_bearing"
        and notice.get("status") == "status_bearing"
        for notice in notices
    )


def assert_synthetic_sio_route_authority(route) -> None:
    authority = route.condensation_authority_by_species["SiO"]
    assert authority["status"] == "status_bearing"
    assert authority["authoritative_for_condensation"] is False
    assert authority["carrier_authority"]["pressure"] == {
        "kind": "value",
        "pa": 100.0,
        "valid_range_K": [1323.0, 1324.0],
    }
    assert_pfree_wall_prediction(route, "SiO")
