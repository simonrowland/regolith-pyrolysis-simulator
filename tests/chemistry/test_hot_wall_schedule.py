"""d-148 furnace-temperature upstream walls and their physical constraints."""

from copy import deepcopy

import pytest

from simulator import condensation
from simulator.condensation import CondensationModel, CondensationTrain
from simulator.overhead import OverheadGasModel
from simulator.state import CampaignPhase, MeltState


NAMES = tuple(f"stage_{number}_to_stage_{number + 1}" for number in range(7))


@pytest.mark.parametrize("campaign", [CampaignPhase.C0, CampaignPhase.C2A, CampaignPhase.C2A_STAGED])
def test_default_upstream_wall_tracks_each_melt_temperature(setpoints_data, campaign):
    model = OverheadGasModel(setpoints_data["overhead_headspace"])
    melt = MeltState(campaign=campaign, campaign_hour=3.0)
    for temperature in (1500.0, 2100.0, 2200.0):
        melt.temperature_C = temperature
        assert model.resolve_pipe_temperature_C(melt) == temperature
        assert model.resolve_pipe_segment_temperatures_C(NAMES, melt)[NAMES[0]] == temperature


@pytest.mark.parametrize("key", ["liner_temperature_C", "pipe_segment_temperatures_C"])
def test_explicit_scalar_wall_override_is_preserved(setpoints_data, key):
    config = deepcopy(setpoints_data["overhead_headspace"])
    config[key] = 1234.0
    model = OverheadGasModel(config)
    melt = MeltState(temperature_C=2100.0, campaign=CampaignPhase.C2A)
    assert model.resolve_pipe_segment_temperatures_C(NAMES, melt)[NAMES[0]] == 1234.0


def test_explicit_upstream_recipe_schedule_is_preserved(setpoints_data):
    config = deepcopy(setpoints_data["overhead_headspace"])
    config["pipe_segment_temperatures_C"]["segments"][NAMES[0]] = {
        "default_C": 1250.0,
        "schedule": [{"campaign": "C2A", "from_campaign_hour": 0.0,
                      "to_campaign_hour": 6.0, "start_C": 1200.0, "end_C": 1800.0}],
    }
    model = OverheadGasModel(config)
    melt = MeltState(temperature_C=2100.0, campaign=CampaignPhase.C2A, campaign_hour=3.0)
    assert model.resolve_pipe_segment_temperatures_C(NAMES, melt)[NAMES[0]] == 1500.0


def test_upstream_material_limits_are_separate_notices_without_clamping(setpoints_data):
    model = CondensationModel(CondensationTrain.create_default())
    model.apply_setpoints_overrides(setpoints_data)
    model.configure_operating_conditions(pipe_segment_temperatures_C={NAMES[0]: 2100.0})
    melt = MeltState(temperature_C=2100.0)
    notices = model._upstream_wall_temperature_notices(melt)
    material_notices = [item for item in notices if item["type"] == "UpstreamWallMaterialLimitExceeded"]
    assert {item["constraint"]: item["limit_C"] for item in material_notices} == {
        "hot_duct": 1750.0, "stage_0_surface": 1600.0,
    }
    assert all(item["wall_temperature_C"] == 2100.0 for item in material_notices)
    assert all(item["source"] for item in material_notices)
    assert model.pipe_segments[0].wall_temperature_C == 2100.0


def test_deliberately_colder_upstream_wall_is_flagged(setpoints_data):
    model = CondensationModel(CondensationTrain.create_default())
    model.apply_setpoints_overrides(setpoints_data)
    model.configure_operating_conditions(pipe_segment_temperatures_C={NAMES[0]: 1500.0})
    notices = model._upstream_wall_temperature_notices(MeltState(temperature_C=2100.0))
    risk = next(item for item in notices if item["type"] == "UpstreamWallCoatingRisk")
    assert risk["melt_temperature_C"] == 2100.0
    assert risk["limit_C"] == 2100.0
    assert risk["source"] == "MeltState.temperature_C"
    assert risk["wall_temperature_C"] == 1500.0
    assert risk["segment"] == NAMES[0]


@pytest.mark.parametrize("pO2_bar", [1e-12, 1e-9, 1e-6, 1e-3])
def test_sio_equal_temperature_dew_criterion_uses_same_source(vapor_pressure_data, pO2_bar):
    catalog = condensation._condensation_catalog(
        vapor_pressure_data, condensation._authoritative_vapour_catalog_payload(vapor_pressure_data),
    )
    evaluator = catalog.evaluator_for("SiO")
    temperature_K = 2273.15
    saturated = evaluator.evaluate(temperature_K, source_activity=0.4, pO2_bar=pO2_bar).pressure_pa
    wall_saturation = condensation._sio_wall_saturation_pressure_pa(
        temperature_K, temperature_K, saturated, vapor_pressure_data=vapor_pressure_data,
    )
    assert saturated - wall_saturation <= 0.0
    assert wall_saturation == saturated


def test_fe_melt_temperature_wall_has_zero_driving_force(vapor_pressure_data):
    temperature_K = 2373.15
    saturated = condensation._antoine_psat_pa("Fe", temperature_K, vapor_pressure_data=vapor_pressure_data)
    assert saturated > 0.0
    assert condensation._wall_deposition_driving_pressure_pa(
        "Fe", saturated, temperature_K, vapor_pressure_data=vapor_pressure_data,
    ) == 0.0
    assert condensation._wall_deposition_driving_pressure_pa(
        "Fe", saturated, 1773.15, vapor_pressure_data=vapor_pressure_data,
    ) > 0.0


@pytest.mark.parametrize("wall_C", [2000.0, 1050.0])
def test_sio_upstream_wall_proves_zero_without_changing_cold_reaction(vapor_pressure_data, wall_C):
    from simulator.wall_deposition import wall_deposit_candidate_for_surface_kg

    model = CondensationModel(CondensationTrain.create_default(), vapor_pressure_data=vapor_pressure_data)
    catalog = condensation._condensation_catalog(
        vapor_pressure_data, condensation._authoritative_vapour_catalog_payload(vapor_pressure_data),
    )
    saturated = catalog.evaluator_for("SiO").evaluate(
        2273.15, source_activity=0.4, pO2_bar=1e-9,
    ).pressure_pa
    incoming = saturated
    if wall_C < 2000.0:
        # Undersaturation for reversed silica dissociation does not authorize
        # suppressing the separate, deferred cold-wall reactive-product model.
        incoming = 0.5 * condensation._sio_wall_saturation_pressure_pa(
            wall_C + 273.15, 2273.15, saturated, vapor_pressure_data=vapor_pressure_data,
        )
    model.configure_operating_conditions(
        wall_temperature_C=wall_C, gas_temperature_C=2000.0,
        overhead_pressure_mbar=saturated / 100.0 + 1.0,
        species_partial_pressures_mbar={"SiO": incoming / 100.0},
    )
    model._source_saturation_pressures_pa = {"SiO": saturated}
    candidate = wall_deposit_candidate_for_surface_kg(
        model, species="SiO", rate_kg_hr=1.0, T_cond_C=1050.0,
        melt_temperature_C=2000.0, wall_temperature_C=wall_C,
        surface_area_m2=1.0, segment=model.pipe_segments[0],
    )
    diagnostic = model.last_wall_deposition_rate_shadow_candidate[NAMES[0]]["SiO"]
    assert diagnostic["wall_saturation_pressure_refused"] is False
    if wall_C == 2000.0:
        assert candidate == 0.0
        assert diagnostic["zero_reason"] == "same_source_sio_dew_margin"
        assert diagnostic["supersaturated"] is False
    else:
        assert candidate > 0.0
        assert diagnostic["wall_saturation_pressure_status"] == "reactive_product_backstop"
