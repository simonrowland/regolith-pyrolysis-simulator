"""Stage fog flag. The molecular eta is kept; the notice says when it is not the capture."""

from __future__ import annotations

import pytest

from simulator.condensation import CondensationModel, _stage_vapour_fog_notice
from simulator.nucleation import (
    ETA_ROLE_UPPER_BOUND,
    FOG_RATE_THRESHOLD_M3_S,
    NOTICE_FOG,
    NOTICE_UNEVALUABLE,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
from simulator.state import CondensationTrain, EvaporationFlux, MeltState

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


def test_small_collector_keeps_hot_bulk_and_molecular_eta() -> None:
    """1e-3 m2 does not cool the 1450 C inlet, so neither cold wall fogs."""

    model, route = _route_alkali()
    collector = _notices(model, "Na", 4, 0)
    trap = _notices(model, "Na", 4, 1)
    assert len(collector) == 1
    assert len(trap) == 1
    collector_bulk = next(
        row for row in model.last_stage_bulk_gas
        if row["stage_number"] == 4 and row["sub_order"] == 0
    )
    train_inlet_K = 1450.0 + CELSIUS_TO_KELVIN_OFFSET

    assert collector[0]["temperature_basis"] == "stage_bulk"
    assert collector[0]["T_K"] == collector_bulk["T_bulk_mean_K"]
    assert collector_bulk["T_in_K"] == pytest.approx(train_inlet_K, abs=40.0)
    assert collector[0]["hour"] == 4
    assert collector[0]["campaign_hour"] == 4
    assert collector[0]["code"] is None
    assert collector[0]["J_m3_s"] == 0.0
    assert collector[0]["saturation_authority"] == "certified"
    assert collector[0]["eta_role"] is None

    assert trap[0]["code"] is None
    assert trap[0]["T_K"] == pytest.approx(collector_bulk["T_out_K"], abs=40.0)
    assert trap[0]["J_m3_s"] == 0.0
    assert trap[0]["eta_role"] is None
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
    assert collector["T_K"] == pytest.approx(250.0 + CELSIUS_TO_KELVIN_OFFSET)
    assert collector["code"] is None
    assert collector["J_m3_s"] <= FOG_RATE_THRESHOLD_M3_S
    assert collector["saturation_authority"] == "extrapolated"
    assert "Browning-Potter" in collector["saturation_notice"]
    assert "400-600 K" in collector["saturation_notice"]
    assert _HANDBOOK_CAUTION in collector["saturation_notice"]

    assert trap["code"] == NOTICE_FOG
    assert trap["saturation_authority"] == "extrapolated"
    assert trap["saturation_notice"]
    assert _HANDBOOK_CAUTION not in trap["saturation_notice"]

    model, _route = _route_alkali()
    routed = _notices(model, "Na", 4, 0)[0]
    assert routed["saturation_authority"] == "certified"
    assert _HANDBOOK_CAUTION not in (routed["saturation_notice"] or "")


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


def test_reset_clears_fog_history() -> None:
    model, _route = _route_alkali()
    assert model.vapour_fog_history
    assert model.stage_bulk_gas_history
    model.reset_run_sticking_authority()
    assert model.vapour_fog_history == []
    assert model.last_vapour_fog_notice == []
    assert model.stage_bulk_gas_history == []
    assert model.last_stage_bulk_gas == []
