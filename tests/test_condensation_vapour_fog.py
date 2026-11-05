"""Stage fog flag. The molecular eta is kept; the notice says when it is not the capture."""

from __future__ import annotations

import math

import pytest

from simulator.condensation import (
    CondensationModel,
    DepositionInputRefusal,
    _stage_cold_end_temperature_K,
    _stage_midpoint_temperature_K,
    _stage_vapour_fog_notice,
)
from simulator.nucleation import (
    ETA_ROLE_UPPER_BOUND,
    FOG_RATE_THRESHOLD_M3_S,
    NOTICE_FOG,
    NOTICE_UNEVALUABLE,
    fog_capture_code,
    nucleation_rate,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.state import (
    CondensationStage,
    CondensationTrain,
    EvaporationFlux,
    MeltState,
)

_P_PA = 5.89
_INLET_KG_HR = 0.01


def _route_alkali():
    model = CondensationModel(CondensationTrain.create_default())
    # A small area keeps eta well below 1, so cold stages still see vapour.
    # The flag is about that vapour, not about a fully stripped stage 4.
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={
            "Na": _P_PA / 100.0,
            "K": _P_PA / 100.0,
        },
        gas_temperature_C=1450.0,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0e-3 for stage in model.train.stages
        },
    )
    route = model.route(
        EvaporationFlux(
            species_kg_hr={"Na": _INLET_KG_HR, "K": _INLET_KG_HR},
            total_kg_hr=2.0 * _INLET_KG_HR,
        ),
        MeltState(temperature_C=1450.0, hour=4, campaign_hour=4),
    )
    return model, route


def _notices(model, species: str, stage_number: int, sub_order: int):
    return [
        notice
        for notice in model.vapour_fog_history
        if notice["species"] == species
        and notice["stage_number"] == stage_number
        and notice["sub_order"] == sub_order
    ]


def test_small_collector_keeps_hot_bulk_and_samples_the_cold_wall() -> None:
    """1e-3 m2 does not cool the 1450 C inlet. The 250 C wall stays clear.
    The 40 C trap wall is the coldest gas that parcel touches, and Na there fogs.
    """

    model, route = _route_alkali()
    collector = _notices(model, "Na", 4, 0)
    trap = _notices(model, "Na", 4, 1)
    assert len(collector) == 1
    assert len(trap) == 1
    collector_bulk = next(
        row for row in model.last_stage_bulk_gas
        if row["stage_number"] == 4 and row["sub_order"] == 0
    )
    trap_bulk = next(
        row for row in model.last_stage_bulk_gas
        if row["stage_number"] == 4 and row["sub_order"] == 1
    )
    train_inlet_K = 1450.0 + CELSIUS_TO_KELVIN_OFFSET

    assert collector[0]["temperature_basis"] == "stage_bulk"
    assert collector[0]["bulk_temperature_basis"] == "stage_bulk"
    assert collector[0]["T_K"] == collector_bulk["T_bulk_mean_K"]
    assert collector[0]["bulk_temperature_K"] == collector[0]["T_K"]
    assert collector[0]["wall_temperature_basis"] == "stage_wall"
    assert collector[0]["wall_temperature_K"] == pytest.approx(
        collector_bulk["T_wall_K"],
    )
    assert collector_bulk["T_in_K"] == pytest.approx(train_inlet_K, abs=40.0)
    assert collector[0]["hour"] == 4
    assert collector[0]["campaign_hour"] == 4
    assert collector[0]["bulk_clearance_code"] is None
    assert collector[0]["wall_clearance_code"] is None
    assert collector[0]["code"] is None
    assert collector[0]["bulk_J_m3_s"] == 0.0
    assert collector[0]["J_m3_s"] == 0.0
    assert collector[0]["saturation_authority"] == "extrapolated"
    assert collector[0]["eta_role"] is None

    assert trap[0]["bulk_temperature_K"] == pytest.approx(trap_bulk["T_bulk_mean_K"])
    assert trap[0]["wall_temperature_K"] == pytest.approx(trap_bulk["T_wall_K"])
    assert trap[0]["wall_temperature_basis"] == "stage_wall"
    assert trap[0]["bulk_clearance_code"] is None
    assert trap[0]["wall_clearance_code"] == NOTICE_FOG
    assert trap[0]["code"] == NOTICE_FOG
    assert trap[0]["clearance_temperature_basis"] == "stage_wall"
    assert trap[0]["bulk_J_m3_s"] == 0.0
    assert trap[0]["J_m3_s"] > FOG_RATE_THRESHOLD_M3_S
    assert trap[0]["eta_role"] == ETA_ROLE_UPPER_BOUND
    assert trap[0]["eta_molecular"] is not None
    assert 0.0 <= trap[0]["eta_molecular"] <= 1.0

    na_captured = sum(
        species_kg.get("Na", 0.0)
        for species_kg in route.capture_kg_by_stage_identity.values()
    )
    na_wall = route.wall_deposit_by_species.get("Na", 0.0)
    assert (
        na_captured + na_wall + route.remaining_by_species["Na"]
        == pytest.approx(_INLET_KG_HR, rel=0.0, abs=1.0e-12)
    )
    for notice in model.vapour_fog_history:
        if notice["code"] != NOTICE_FOG or notice["stage_number"] == 3:
            continue
        identity = (notice["stage_number"], notice["sub_order"])
        deposit = route.capture_kg_by_stage_identity.get(identity, {}).get(
            notice["species"], 0.0,
        )
        assert deposit == pytest.approx(
            notice["supply_kg_hr"] * notice["eta_molecular"],
            rel=1.0e-9,
            abs=1.0e-15,
        )
    assert NOTICE_FOG not in str(model.last_condensation_refusals_by_species)
    for entry in model.operating_history:
        assert "vapour_fog" not in entry
        assert NOTICE_FOG not in str(entry)


_HANDBOOK_CAUTION = (
    "fog clear at nominal P_sat; extrapolated, within the handbook caution of the threshold"
)


def test_sodium_collector_forwards_extrapolated_saturation_and_the_caution() -> None:
    """250 C Na is clear at nominal P_sat. A 25% lower P_sat crosses 1e6."""

    train = CondensationTrain.create_default()
    collector_stage = next(
        stage for stage in train.stages
        if stage.stage_number == 4 and stage.sub_order == 0
    )
    trap_stage = next(
        stage for stage in train.stages
        if stage.stage_number == 4 and stage.sub_order == 1
    )
    melt = MeltState(temperature_C=1450.0, hour=4, campaign_hour=4)
    collector = _stage_vapour_fog_notice(
        collector_stage, "Na", melt, _P_PA, 0.1, _INLET_KG_HR,
        bulk_temperature_K=250.0 + CELSIUS_TO_KELVIN_OFFSET,
    )
    trap = _stage_vapour_fog_notice(
        trap_stage, "Na", melt, _P_PA, 0.1, _INLET_KG_HR,
        bulk_temperature_K=40.0 + CELSIUS_TO_KELVIN_OFFSET,
    )

    assert collector["temperature_basis"] == "stage_bulk"
    assert collector["bulk_temperature_basis"] == "stage_bulk"
    assert collector["T_K"] == pytest.approx(250.0 + CELSIUS_TO_KELVIN_OFFSET)
    assert collector["bulk_temperature_K"] == collector["T_K"]
    assert collector["wall_temperature_basis"] == "stage_wall"
    assert collector["wall_temperature_K"] == pytest.approx(
        250.0 + CELSIUS_TO_KELVIN_OFFSET,
    )
    assert collector["code"] is None
    assert collector["J_m3_s"] <= FOG_RATE_THRESHOLD_M3_S
    assert collector["saturation_authority"] == "extrapolated"
    assert "Browning-Potter" in collector["saturation_notice"]
    assert "400-600 K" in collector["saturation_notice"]
    assert _HANDBOOK_CAUTION in collector["saturation_notice"]

    assert trap["code"] == NOTICE_FOG
    assert trap["wall_temperature_K"] == pytest.approx(40.0 + CELSIUS_TO_KELVIN_OFFSET)
    assert trap["saturation_authority"] == "extrapolated"
    assert trap["saturation_notice"]
    assert _HANDBOOK_CAUTION not in trap["saturation_notice"]

    model, _route = _route_alkali()
    routed = _notices(model, "Na", 4, 0)[0]
    assert routed["bulk_temperature_basis"] == "stage_bulk"
    assert routed["wall_temperature_basis"] == "stage_wall"
    assert routed["saturation_authority"] == "extrapolated"
    assert _HANDBOOK_CAUTION in (routed["saturation_notice"] or "")


def test_potassium_fog_check_is_unevaluable_on_the_cold_trap() -> None:
    model, _route = _route_alkali()
    trap = _notices(model, "K", 4, 1)
    assert len(trap) == 1
    assert trap[0]["code"] == NOTICE_UNEVALUABLE
    assert trap[0]["J_m3_s"] is None
    assert trap[0]["eta_role"] is None
    assert trap[0]["eta_molecular"] is not None
    assert trap[0]["saturation_authority"] is None
    assert trap[0]["saturation_notice"] is None


def _band_stage(temp_range_C: tuple[float, float]) -> CondensationStage:
    return CondensationStage(
        5, "Vortex Dust Filter", temp_range_C, [],
        stage_key="stage_5_vortex_dust_filter",
    )


def _band_notice(temp_range_C: tuple[float, float], bulk_K: float) -> dict:
    melt = MeltState(temperature_C=1450.0, hour=4, campaign_hour=4)
    return _stage_vapour_fog_notice(
        _band_stage(temp_range_C), "Na", melt, _P_PA, 0.1, _INLET_KG_HR,
        bulk_temperature_K=bulk_K,
    )


def test_wide_band_fogs_at_the_cold_end() -> None:
    """Stage 5 is [200, 350] C. 5.89 Pa of Na fogs at 200 C and is clear at 275 C.

    The fog wall is min(temp_range_C). Capture's wall stays the midpoint.
    An isothermal band still samples its only temperature.
    """

    wide = _band_stage((200.0, 350.0))
    cold_K = min(wide.temp_range_C) + CELSIUS_TO_KELVIN_OFFSET
    midpoint_K = sum(wide.temp_range_C) / 2.0 + CELSIUS_TO_KELVIN_OFFSET
    hot_bulk_K = 1450.0 + CELSIUS_TO_KELVIN_OFFSET
    cold = nucleation_rate("Na", cold_K, _P_PA)
    midpoint = nucleation_rate("Na", midpoint_K, _P_PA)
    bulk = nucleation_rate("Na", hot_bulk_K, _P_PA)
    assert fog_capture_code(cold) == NOTICE_FOG
    assert fog_capture_code(midpoint) is None
    assert fog_capture_code(bulk) is None

    # Capture wall, unchanged: the [200, 350] C midpoint is 548.15 K.
    assert _stage_midpoint_temperature_K(wide).hex() == "0x1.1213333333333p+9"
    assert _stage_cold_end_temperature_K(wide) == pytest.approx(cold_K)

    notice = _band_notice((200.0, 350.0), hot_bulk_K)
    assert notice["code"] == NOTICE_FOG
    assert notice["clearance_temperature_basis"] == "stage_wall"
    assert notice["wall_temperature_basis"] == "stage_wall"
    assert notice["wall_temperature_K"] == pytest.approx(cold_K)
    assert notice["wall_clearance_code"] == NOTICE_FOG
    assert notice["wall_J_m3_s"] == pytest.approx(cold.rate_m3_s)
    assert notice["bulk_temperature_basis"] == "stage_bulk"
    assert notice["bulk_temperature_K"] == pytest.approx(hot_bulk_K)
    assert notice["bulk_clearance_code"] is None
    assert notice["bulk_J_m3_s"] == pytest.approx(bulk.rate_m3_s)
    assert notice["p_Pa"] == pytest.approx(_P_PA)
    assert notice["eta_role"] == ETA_ROLE_UPPER_BOUND
    assert notice["J_m3_s"] > FOG_RATE_THRESHOLD_M3_S

    clear_iso = _band_notice((275.0, 275.0), hot_bulk_K)
    assert clear_iso["code"] is None
    assert clear_iso["wall_temperature_K"] == pytest.approx(midpoint_K)
    assert clear_iso["wall_temperature_K"] == pytest.approx(
        _stage_midpoint_temperature_K(_band_stage((275.0, 275.0))),
    )
    assert clear_iso["wall_clearance_code"] is None
    assert clear_iso["clearance_temperature_basis"] == "stage_bulk"

    fog_iso = _band_notice((200.0, 200.0), hot_bulk_K)
    assert fog_iso["code"] == NOTICE_FOG
    assert fog_iso["wall_temperature_K"] == pytest.approx(cold_K)
    assert fog_iso["wall_clearance_code"] == NOTICE_FOG
    assert fog_iso["clearance_temperature_basis"] == "stage_wall"


@pytest.mark.parametrize(
    "band",
    [(math.nan, 600.0), (500.0, math.inf), (600.0, 500.0)],
)
def test_cold_end_refuses_a_bad_band_without_moving_the_midpoint(band) -> None:
    stage = _band_stage(band)
    with pytest.raises(DepositionInputRefusal, match="stage_temperature"):
        _stage_cold_end_temperature_K(stage)
    with pytest.raises(DepositionInputRefusal, match="stage_temperature"):
        _stage_midpoint_temperature_K(stage)


def test_cold_end_below_absolute_zero_refuses_while_the_midpoint_can_remain() -> None:
    """[-300, 100] C is ordered. Its mean is above 0 K. Its cold end is not."""

    stage = _band_stage((-300.0, 100.0))
    assert _stage_midpoint_temperature_K(stage) > 0.0
    with pytest.raises(DepositionInputRefusal, match="T_surface_K"):
        _stage_cold_end_temperature_K(stage)


def test_capture_wall_midpoint_kelvin_pin() -> None:
    """Capture wall. Midpoint kelvin pinned before the fog cold-end split.

    [200, 350] C is 548.15 K. [250, 250] C is 523.15 K.
    """

    wide = CondensationStage(5, "Vortex Dust Filter", (200.0, 350.0), [])
    iso = CondensationStage(4, "Alkali/Mg Cyclone", (250.0, 250.0), ["Na"])
    assert _stage_midpoint_temperature_K(wide).hex() == "0x1.1213333333333p+9"
    assert _stage_midpoint_temperature_K(iso).hex() == "0x1.0593333333333p+9"


def test_reset_clears_fog_history() -> None:
    model, _route = _route_alkali()
    assert model.vapour_fog_history
    assert model.stage_bulk_gas_history
    model.reset_run_sticking_authority()
    assert model.vapour_fog_history == []
    assert model.last_vapour_fog_notice == []
    assert model.stage_bulk_gas_history == []
    assert model.last_stage_bulk_gas == []
