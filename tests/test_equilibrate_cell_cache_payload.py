from __future__ import annotations

from decimal import Decimal

import pytest

from simulator.battery.enums import NoticeKind
from simulator.diagnostic_helpers.binary_pot_battery import (
    BATTERY_ENGINE_NAMES,
    ARM_HEADLINE,
    EquilibrateCell,
    Po2Request,
    close_battery_engine_handle,
    equilibrate_cell,
    load_binary_pots,
    open_battery_engine,
)


_ENGINE_NAMES = tuple(name for name in BATTERY_ENGINE_NAMES if name != "cached-real")
_POTS, _ = load_binary_pots()
_POT_BY_ID = {pot.pot_id: pot for pot in _POTS}
_REAL_POTS = (
    _POT_BY_ID["mgo_sio2_40_60"],
    _POT_BY_ID["feo_mgo_sio2_30_20_50"],
)


def _assert_cache_round_trip(cell: EquilibrateCell) -> None:
    encode = getattr(cell, "to_cache_payload", None)
    assert callable(encode), "EquilibrateCell must own its cache payload serializer"
    payload = encode()
    restored = EquilibrateCell.from_cache_payload(payload)
    assert restored == cell
    assert restored.to_cache_payload() == payload


@pytest.mark.parametrize("engine_name", _ENGINE_NAMES)
def test_cache_payload_round_trips_real_engine_cells(engine_name: str) -> None:
    handle = open_battery_engine(engine_name)
    if not handle.available:
        reason = handle.unavailable_reason or "engine binary or package is absent"
        close_battery_engine_handle(handle)
        pytest.skip(f"{engine_name} unavailable: {reason}")

    try:
        for pot in _REAL_POTS:
            cell = equilibrate_cell(
                handle,
                pot,
                temperature_K=1700.0,
                po2=Po2Request(mode="engine_default", po2_bar=None),
                timeout_s=30.0,
                isolated=False,
            )
            _assert_cache_round_trip(cell)

        if engine_name == "internal-analytical":
            refusal = equilibrate_cell(
                handle,
                _REAL_POTS[0],
                temperature_K=1700.0,
                po2=Po2Request(
                    mode="oxygen_balance_effusion",
                    po2_bar=None,
                    cell_material="invalid-cell-material",
                ),
                timeout_s=30.0,
                isolated=False,
            )
            assert refusal.status == "refusal"
            assert refusal.refusal_reason == "oxygen_balance_cell_material_invalid"
            _assert_cache_round_trip(refusal)
    finally:
        close_battery_engine_handle(handle)


def test_cache_payload_preserves_nested_types_and_presence() -> None:
    cell = EquilibrateCell(
        pot_id="typed-payload",
        engine="internal-analytical",
        temperature_K=1700.0,
        po2=Po2Request(mode="engine_default", po2_bar=None),
        status="refusal",
        refusal_reason="typed_test_refusal",
        engine_status="input_refusal",
        engine_reason="test refusal details",
        melt_activities={},
        gas_partial_pressures_Pa={},
        liquid_fraction=None,
        wall_s=0.0,
        cpu_s=0.0,
        hostname="test",
        arm=ARM_HEADLINE,
        notices=[
            {
                "kind": NoticeKind.INPUT_OMITTED,
                "decimal": Decimal("1.2300"),
                "tuple": ("component", None),
                "list": ["component", None],
                "signed_zero": -0.0,
                "present_none": None,
            }
        ],
    )

    payload = cell.to_cache_payload()
    restored = EquilibrateCell.from_cache_payload(payload)

    assert restored == cell
    assert restored.to_cache_payload() == payload
    notice = restored.notices[0]
    assert notice["kind"] is NoticeKind.INPUT_OMITTED
    assert type(notice["decimal"]) is Decimal
    assert str(notice["decimal"]) == "1.2300"
    assert type(notice["tuple"]) is tuple
    assert notice["tuple"] == ("component", None)
    assert type(notice["list"]) is list
    assert notice["list"] == ["component", None]
    assert notice["signed_zero"].hex() == "-0x0.0p+0"
    assert "present_none" in notice and notice["present_none"] is None
    assert "absent" not in notice
