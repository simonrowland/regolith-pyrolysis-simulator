import pytest

from simulator.core import CampaignPhase
from simulator.furnace_materials import (
    load_furnace_materials,
    resolve_furnace_max_T_C,
)
from simulator.runner import PyrolysisRun


def test_catalog_loads_grounded_enabled_materials():
    catalog = load_furnace_materials()

    assert catalog["dense_alumina_continuous"]["max_service_T_C"] == 1700
    assert catalog["dense_alumina_continuous"]["enabled"] is True
    assert catalog["dense_alumina_max"]["max_service_T_C"] == 1843
    assert catalog["zirconia_ysz"]["max_service_T_C"] == 2200
    assert catalog["zirconia_ysz"]["enabled"] is True
    assert catalog["plasma_sprayed_alumina"]["max_service_T_C"] == 1650
    assert catalog["plasma_sprayed_alumina"]["enabled"] is True


def test_catalog_disabled_materials_are_not_selectable():
    catalog = load_furnace_materials()

    assert catalog["fused_silica"]["enabled"] is False
    assert catalog["sintered_regolith"]["enabled"] is False
    assert catalog["graphite_inert"]["enabled"] is False


def test_resolver_clamps_requested_cap_to_material_max():
    assert resolve_furnace_max_T_C("dense_alumina_continuous", 1800) == 1700
    assert resolve_furnace_max_T_C("zirconia_ysz", 1800) == 1800


def test_resolver_defaults_to_material_max_when_no_request():
    assert resolve_furnace_max_T_C("dense_alumina_continuous") == 1700


def test_resolver_fails_loud_for_unknown_material():
    with pytest.raises(ValueError, match="unknown furnace material"):
        resolve_furnace_max_T_C("unknown_material")


def test_resolver_fails_loud_for_disabled_material():
    with pytest.raises(ValueError, match="not selectable yet"):
        resolve_furnace_max_T_C("fused_silica", 1200)


def test_resolver_fails_loud_for_enabled_material_with_non_numeric_cap():
    # An enabled material whose max_service_T_C is null/non-numeric must fail
    # loud rather than silently resolving to a bad ceiling.
    bad_catalog = {
        "bogus": {"id": "bogus", "enabled": True, "max_service_T_C": None},
    }
    with pytest.raises(ValueError, match="must be numeric"):
        resolve_furnace_max_T_C("bogus", 1800, catalog=bad_catalog)


def test_setpoints_patch_cap_flows_through_existing_campaign_manager():
    session = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=0,
        setpoints_patch={"furnace_max_T_C": 1700},
    )._start_session()

    target_T, _ = session.simulator.campaign_mgr.get_temp_target(
        CampaignPhase.C2A,
        0,
        session.simulator.melt,
    )

    assert session.simulator.campaign_mgr.furnace_max_T_C == pytest.approx(1700)
    assert target_T <= 1700
