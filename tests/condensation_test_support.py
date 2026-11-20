from __future__ import annotations

from collections.abc import Mapping


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
