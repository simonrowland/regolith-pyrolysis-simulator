"""Melt-speciation tests for the builtin OVERHEAD_GAS_EQUILIBRIUM provider."""

from __future__ import annotations

import copy
import math

import pytest

from simulator.chemistry.kernel import ChemistryIntent
from simulator.campaigns import CampaignManager
from simulator.state import CampaignPhase, GAS_CONSTANT
from tests.chemistry.conftest import _build_sim


def test_partial_campaign_condenser_geometry_deep_merges_once_at_campaign_start(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    setpoints = copy.deepcopy(setpoints_data)
    setpoints["condenser_geometry"]["stage_area_ratios"]["terminal"] = 9.0
    setpoints["condenser_geometry"]["stage_area_ratio_sources"]["terminal"] = (
        "certified-geometry: terminal ratio 9 test anchor"
    )
    setpoints["campaigns"]["C0"]["condenser_geometry"] = {
        "stage_area_ratios": {"fe_stage1": 1.5},
    }
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints,
    )

    sim.start_campaign(CampaignPhase.C0)
    ratios_at_campaign_start = dict(sim.overhead_model.stage_area_ratios)
    provenance_at_campaign_start = (
        sim.overhead_model.stage_area_geometry_provenance_notice()
    )
    terminal_provenance = provenance_at_campaign_start[
        "stage_area_ratio_provenance_by_stage"
    ]["terminal"]

    assert ratios_at_campaign_start["fe_stage1"] == pytest.approx(1.5)
    assert ratios_at_campaign_start["terminal"] == pytest.approx(9.0)
    assert terminal_provenance["status"] == "sourced"

    sim._get_turbine_spec()

    assert sim._equipment.pipe.stage_area_ratios["fe_stage1"] == pytest.approx(1.5)
    assert sim._equipment.pipe.stage_area_ratios["terminal"] == pytest.approx(9.0)
    assert sim.overhead_model.stage_area_ratios == pytest.approx(
        ratios_at_campaign_start
    )
    assert (
        sim.overhead_model.stage_area_geometry_provenance_notice()
        == provenance_at_campaign_start
    )


def test_runtime_condenser_geometry_override_is_allowed_and_consumed(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.campaign_mgr.overrides["C0"] = {
        "condenser_geometry": {
            "stage_area_ratios": {"fe_stage1": 1.5},
        },
    }
    CampaignManager.validate_runtime_campaign_overrides(sim.campaign_mgr.overrides)

    sim.start_campaign(CampaignPhase.C0)

    assert sim.overhead_model.stage_area_ratios["fe_stage1"] == pytest.approx(1.5)
    assert sim.overhead_model.stage_area_ratios["terminal"] == pytest.approx(2.0)


def test_runtime_condenser_geometry_stage_aliases_canonicalize_before_consumers(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.campaign_mgr.overrides["C0"] = {
        "condenser_geometry": {
            "stage_area_ratios": {"stage_7": 9.0},
        },
    }
    CampaignManager.validate_runtime_campaign_overrides(sim.campaign_mgr.overrides)

    sim.start_campaign(CampaignPhase.C0)
    resolved_ratios = sim._overhead_condenser_geometry_config["stage_area_ratios"]

    assert "stage_7" not in resolved_ratios
    assert resolved_ratios["terminal"] == pytest.approx(9.0)
    assert sim.overhead_model.stage_area_ratios["terminal"] == pytest.approx(9.0)

    sim._get_turbine_spec()

    assert "stage_7" not in sim._equipment.pipe.stage_area_ratios
    assert sim._equipment.pipe.stage_area_ratios["terminal"] == pytest.approx(9.0)


def test_melt_composition_and_speciation_fill_missing_al_surface(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"Na": 1.0},
        source="test sodium reference gas",
    )

    result = sim._chem_kernel.dispatch(
        ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM,
        temperature_C=1626.85,
        pressure_bar=1e-9,
        control_inputs={
            "headspace_volume_m3": 1.0,
            "headspace_temperature_K": 1900.0,
            "melt_composition_wt_pct": {
                "Al2O3": 10.0,
                "Na2O": 1.0,
            },
            "melt_speciation": {
                "AlO": {
                    "parent_oxide": "Al2O3",
                    "reference_oxide": "Na2O",
                    "reference_species": "Na",
                    "activity_ratio_scale": 2.0e-9,
                    "fraction": 0.9,
                },
                "Al": {
                    "parent_oxide": "Al2O3",
                    "reference_oxide": "Na2O",
                    "reference_species": "Na",
                    "activity_ratio_scale": 2.0e-9,
                    "fraction": 0.1,
                },
            },
        },
    )

    diagnostic = dict(result.diagnostic or {})
    partials = dict(diagnostic["partial_pressures_bar"])
    melt_partials = dict(diagnostic["melt_speciation_partial_pressures_bar"])
    p_na = GAS_CONSTANT * 1900.0 / 1.0e5
    activity_ratio = (10.0 / 101.9613) / (1.0 / 61.9789)
    p_al_total = p_na * activity_ratio * 2.0e-9

    assert result.status == "ok"
    assert result.transition is None
    assert partials["Na"] == pytest.approx(p_na)
    assert melt_partials["AlO"] == pytest.approx(p_al_total * 0.9)
    assert melt_partials["Al"] == pytest.approx(p_al_total * 0.1)
    assert partials["AlO"] == pytest.approx(p_al_total * 0.9)
    assert partials["Al"] == pytest.approx(p_al_total * 0.1)
    assert diagnostic["element_partial_pressures_bar"]["Al"] == pytest.approx(
        p_al_total
    )
    assert diagnostic["melt_speciation_model"] == (
        "activity_ratio_fill_missing_species"
    )


def test_empty_melt_callers_preserve_ideal_gas_from_holdup(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"O2": 2.0, "SiO": 1.0},
        source="test overhead gas",
    )

    result = sim._chem_kernel.dispatch(
        ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM,
        temperature_C=1500.0,
        pressure_bar=1.0,
        control_inputs={
            "headspace_volume_m3": 0.085,
            "headspace_temperature_K": 1873.15,
        },
    )

    scale = GAS_CONSTANT * 1873.15 / (0.085 * 1.0e5)
    diagnostic = dict(result.diagnostic or {})
    partials = dict(diagnostic["partial_pressures_bar"])

    assert result.status == "ok"
    assert result.transition is None
    assert partials == {
        "O2": pytest.approx(2.0 * scale),
        "SiO": pytest.approx(1.0 * scale),
    }
    assert dict(diagnostic["melt_speciation_partial_pressures_bar"]) == {}
    assert diagnostic["melt_speciation_model"] == "ideal_gas_holdup_only"


def test_materialized_al_o_surface_moves_only_through_commit_batch(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"AlO": 0.25},
        source="test materialized alumina vapor",
    )

    oge_result = sim._chem_kernel.dispatch(
        ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM,
        temperature_C=1626.85,
        pressure_bar=1e-9,
        control_inputs={
            "headspace_volume_m3": 1.0,
            "headspace_temperature_K": 1900.0,
        },
    )
    bleed_result = sim._dispatch_and_commit(
        ChemistryIntent.OVERHEAD_BLEED,
        control_inputs={
            "force_drain_all": True,
            "dt_hr": 1.0,
            "p_total_bar": 0.0,
            "p_downstream_bar": 0.0,
            "bleed_conductance_kg_s": 0.0,
            "max_o2_flow_kg_hr": 0.0,
        },
    )

    assert oge_result.transition is None
    assert bleed_result.transition is not None
    assert dict(bleed_result.transition.atom_balance_proof) == {}
    assert sim.atom_ledger.mol_by_account("process.overhead_gas").get(
        "AlO", 0.0
    ) == pytest.approx(0.0)
    assert sim.atom_ledger.mol_by_account("terminal.offgas")["AlO"] == (
        pytest.approx(0.25)
    )
