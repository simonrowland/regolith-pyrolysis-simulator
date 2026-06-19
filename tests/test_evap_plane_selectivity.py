from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.campaigns import CampaignManager
from simulator.core import PyrolysisSimulator
from simulator.runner import build_per_hour_summary
from simulator.state import CampaignPhase, EvaporationFlux, HourSnapshot


def _staged_setpoints() -> dict:
    return {
        "furnace_max_T_C": 1800.0,
        "campaigns": {
            "C2A_staged": {
                "depletion_flux_decay_fraction": 0.0,
                "stages": [
                    {
                        "name": "alkali_early_fe",
                        "duration_h": 2,
                        "target_C": 1200.0,
                        "ramp_rate_C_per_hr": 100.0,
                        "target_species": ["Na", "K"],
                    },
                    {
                        "name": "sio_window",
                        "duration_h": 2,
                        "target_C": 1450.0,
                        "ramp_rate_C_per_hr": 50.0,
                        "target_species": ["SiO", "minor_Fe"],
                    },
                    {
                        "name": "fe_hot_hold",
                        "duration_h": 2,
                        "target_C": 1650.0,
                        "ramp_rate_C_per_hr": 25.0,
                        "target_species": ["Fe"],
                    },
                ],
            },
        },
    }


def _sim_for_selectivity(
    *,
    campaign: CampaignPhase = CampaignPhase.C2A_STAGED,
    campaign_hour: int = 0,
    campaign_mgr: object | None = None,
) -> PyrolysisSimulator:
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = SimpleNamespace(campaign=campaign, campaign_hour=campaign_hour)
    sim.campaign_mgr = campaign_mgr or SimpleNamespace(
        _c2a_staged_active_stage=lambda _hour: {
            "name": "sio_window",
            "target_species": ["SiO", "minor_Fe"],
        },
    )
    return sim


def test_hour_snapshot_has_evap_plane_selectivity_default() -> None:
    snapshot = HourSnapshot()

    assert snapshot.evap_plane_selectivity == {}


def test_evap_plane_selectivity_uses_hour_based_stage_on_default_staged_path() -> None:
    manager = CampaignManager(_staged_setpoints())
    sim = _sim_for_selectivity(campaign_hour=2, campaign_mgr=manager)
    flux = EvaporationFlux(
        species_kg_hr={
            "Na": 10.0,
            "K": 5.0,
            "SiO": 2.0,
            "Fe": 1.0,
            "Mg": 8.0,
        },
    )

    sio_window = sim._evap_plane_selectivity_diagnostic(flux)
    sim.melt.campaign_hour = 4
    fe_hot_hold = sim._evap_plane_selectivity_diagnostic(flux)

    assert manager._c2a_staged_stage_idx == 0
    assert sio_window["target_species"] == ["SiO"]
    assert sio_window["target_flux_kg_hr"] == pytest.approx(2.0)
    assert fe_hot_hold["target_species"] == ["Fe"]
    assert fe_hot_hold["target_flux_kg_hr"] == pytest.approx(1.0)


def test_evap_plane_selectivity_targets_staged_flux_fraction() -> None:
    stage = {
        "name": "sio_window",
        "target_species": ["SiO", "minor_Fe"],
    }
    sim = _sim_for_selectivity(
        campaign_mgr=SimpleNamespace(
            _c2a_staged_active_stage=lambda _hour: stage,
        ),
    )
    flux = EvaporationFlux(species_kg_hr={"SiO": 2.0, "Fe": 1.0, "Mg": 1.0})

    diagnostic = sim._evap_plane_selectivity_diagnostic(flux)

    assert diagnostic["total_flux_kg_hr"] == pytest.approx(4.0)
    assert sum(diagnostic["per_species_fraction"].values()) == pytest.approx(1.0)
    assert diagnostic["target_species"] == ["SiO"]
    assert set(diagnostic["target_species"]).issubset(stage["target_species"])
    assert diagnostic["target_flux_kg_hr"] == pytest.approx(2.0)
    assert 0.0 <= diagnostic["target_selectivity"] <= 1.0
    assert diagnostic["target_selectivity"] == pytest.approx(0.5)


def test_evap_plane_selectivity_does_not_expand_alkali_target_to_mg() -> None:
    stage = {
        "name": "alkali_early_fe",
        "target_species": ["Na", "K"],
    }
    sim = _sim_for_selectivity(
        campaign_mgr=SimpleNamespace(
            _c2a_staged_active_stage=lambda _hour: stage,
        ),
    )
    flux = EvaporationFlux(species_kg_hr={"Na": 1.0, "K": 1.0, "Mg": 10.0})

    diagnostic = sim._evap_plane_selectivity_diagnostic(flux)

    assert diagnostic["target_species"] == ["Na", "K"]
    assert "Mg" not in diagnostic["target_species"]
    assert set(diagnostic["target_species"]).issubset(stage["target_species"])


def test_evap_plane_selectivity_zero_total_flux_has_no_targets() -> None:
    sim = _sim_for_selectivity(
        campaign_mgr=SimpleNamespace(
            _c2a_staged_active_stage=lambda _hour: {
            "name": "sio_window",
            "target_species": ["SiO", "minor_Fe"],
        },
        ),
    )
    flux = EvaporationFlux(species_kg_hr={"SiO": 0.0, "Fe": 0.0})

    diagnostic = sim._evap_plane_selectivity_diagnostic(flux)

    assert diagnostic["total_flux_kg_hr"] == pytest.approx(0.0)
    assert diagnostic["per_species_fraction"] == {}
    assert "target_species" not in diagnostic
    assert "target_flux_kg_hr" not in diagnostic
    assert "target_selectivity" not in diagnostic


def test_non_staged_campaign_summary_omits_evap_plane_selectivity() -> None:
    sim_for_diagnostic = _sim_for_selectivity(campaign=CampaignPhase.C2A)
    flux = EvaporationFlux(species_kg_hr={"SiO": 2.0})
    diagnostic = sim_for_diagnostic._evap_plane_selectivity_diagnostic(flux)
    snapshot = HourSnapshot(campaign=CampaignPhase.C2A)
    snapshot.evap_plane_selectivity = diagnostic
    sim = SimpleNamespace(
        campaign_mgr=SimpleNamespace(last_pO2_enforcement=None),
        product_ledger=lambda: {},
        record=SimpleNamespace(snapshots=[snapshot]),
    )

    summary = build_per_hour_summary(sim, snapshot)

    assert "evap_plane_selectivity" not in summary


def test_staged_selectivity_flows_to_per_hour_summary_when_targeted() -> None:
    snapshot = HourSnapshot(campaign=CampaignPhase.C2A_STAGED)
    snapshot.evap_plane_selectivity = {
        "total_flux_kg_hr": 4.0,
        "per_species_fraction": {"Fe": 0.25, "Mg": 0.25, "SiO": 0.5},
        "target_species": ["SiO"],
        "target_flux_kg_hr": 2.0,
        "target_selectivity": 0.5,
    }
    sim = SimpleNamespace(
        campaign_mgr=SimpleNamespace(last_pO2_enforcement=None),
        product_ledger=lambda: {},
        record=SimpleNamespace(snapshots=[snapshot]),
    )

    summary = build_per_hour_summary(sim, snapshot)

    selectivity = summary["evap_plane_selectivity"]
    assert selectivity["target_selectivity"] == pytest.approx(0.5)
    assert sum(selectivity["per_species_fraction"].values()) == pytest.approx(1.0)


def test_evap_plane_selectivity_uses_stage_index_on_depletion_path() -> None:
    # When depletion_flux_decay_fraction > 0 the active stage is resolved from
    # the (endpoint-advanced) stage INDEX, not elapsed hour. Freeze idx at 2
    # (fe_hot_hold) while campaign_hour=0 (which would be the alkali stage under
    # hour-based resolution) and confirm the diagnostic follows the index.
    setpoints = _staged_setpoints()
    setpoints["campaigns"]["C2A_staged"]["depletion_flux_decay_fraction"] = 0.05
    manager = CampaignManager(setpoints)
    manager._c2a_staged_stage_idx = 2  # fe_hot_hold
    sim = _sim_for_selectivity(campaign_hour=0, campaign_mgr=manager)
    flux = EvaporationFlux(species_kg_hr={"Na": 10.0, "K": 5.0, "Fe": 1.0})

    diagnostic = sim._evap_plane_selectivity_diagnostic(flux)

    assert diagnostic["target_species"] == ["Fe"]
    assert diagnostic["target_flux_kg_hr"] == pytest.approx(1.0)


def test_evap_plane_selectivity_omits_targets_for_pseudo_label_stage() -> None:
    # cool_for_na_shuttle declares only the pseudo-label "residual_FeO", which
    # is NOT an evaporation-flux species. The intersection is empty, so the
    # per-species spectrum is still reported but no target_* keys are emitted.
    sim = _sim_for_selectivity(
        campaign_mgr=SimpleNamespace(
            _c2a_staged_active_stage=lambda _hour: {
                "name": "cool_for_na_shuttle",
                "target_species": ["residual_FeO"],
            },
        ),
    )
    flux = EvaporationFlux(species_kg_hr={"Fe": 1.0, "Na": 2.0})

    diagnostic = sim._evap_plane_selectivity_diagnostic(flux)

    assert diagnostic["total_flux_kg_hr"] == pytest.approx(3.0)
    assert diagnostic["per_species_fraction"]
    assert "target_species" not in diagnostic
    assert "target_flux_kg_hr" not in diagnostic
    assert "target_selectivity" not in diagnostic
