from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from simulator.campaigns import CampaignManager
from simulator.state import (
    BatchRecord,
    CampaignPhase,
    CondensationTrain,
    EvaporationFlux,
    MeltState,
)


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _setpoints() -> dict:
    return yaml.safe_load((DATA_DIR / "setpoints.yaml").read_text()) or {}


def _melt(
    campaign: CampaignPhase,
    hour: int,
    *,
    temperature_C: float = 25.0,
    composition_kg: dict[str, float] | None = None,
    voltage_V: float = 0.0,
    current_A: float = 100.0,
    low_current_hours: int = 0,
) -> MeltState:
    return MeltState(
        campaign=campaign,
        campaign_hour=hour,
        temperature_C=temperature_C,
        composition_kg=composition_kg or {
            "SiO2": 50.0,
            "Al2O3": 20.0,
            "CaO": 30.0,
        },
        mre_voltage_V=voltage_V,
        mre_current_A=current_A,
        mre_low_current_hours=low_current_hours,
    )


def _flux(total_kg_hr: float = 0.0, **species_kg_hr: float) -> EvaporationFlux:
    return EvaporationFlux(
        total_kg_hr=total_kg_hr,
        species_kg_hr=species_kg_hr,
    )


def test_t155_delta_materializes_once_at_phase_entry_and_defaults_stay_equal():
    defaults = _setpoints()
    manager = CampaignManager(copy.deepcopy(defaults))
    melt = _melt(CampaignPhase.C2B, 0)
    assert manager._get_base_temp_target(CampaignPhase.C2B, 0, melt) == (1480.0, 10.0)
    assert manager._get_base_temp_target(CampaignPhase.C5, 0, melt) == (1575.0, 5.0)
    assert manager._get_base_temp_target(CampaignPhase.C6, 0, melt)[0] == 1400.0

    patched = copy.deepcopy(defaults)
    patched["campaigns"]["C2B"]["target_delta_below_ceiling_C"] = 20.0
    patched["campaigns"]["C5"]["target_delta_below_ceiling_C"] = 50.0
    before = copy.deepcopy(patched)
    manager = CampaignManager(patched)
    manager.configure_campaign(melt, CampaignPhase.C2B)
    assert manager._get_base_temp_target(CampaignPhase.C2B, 0, melt) == (1460.0, 10.0)
    manager.configure_campaign(melt, CampaignPhase.C5)
    assert manager._get_base_temp_target(CampaignPhase.C5, 0, melt) == (1600.0, 5.0)
    assert patched == before


def test_t155_delta_translates_below_low_hardware_ceiling_without_low_clamp():
    patched = _setpoints()
    patched["furnace_max_T_C"] = 1300.0
    patched["campaigns"]["C2B"]["target_delta_below_ceiling_C"] = 20.0
    manager = CampaignManager(patched)
    melt = _melt(CampaignPhase.C2B, 0)
    manager.configure_campaign(melt, CampaignPhase.C2B)
    assert manager._get_base_temp_target(CampaignPhase.C2B, 0, melt)[0] == 1280.0


def test_c2a_staged_endpoint_refuses_unknown_species():
    manager = CampaignManager(_setpoints())

    with pytest.raises(ValueError, match="unknown species"):
        manager._c2a_staged_log_slope_depletion_complete(
            species=("TypoSpecies",),
            evap_flux=_flux(),
            epsilon_per_hr=0.01,
        )


def test_c2a_staged_endpoint_prevalidates_all_species_before_yield_mutation():
    manager = CampaignManager(_setpoints())
    manager._c2a_staged_cumulative_yield_mol_by_species = {"Na": 2.0}
    manager._c2a_staged_last_log_slope_by_species = {"Na": 0.25}
    before_yield = dict(manager._c2a_staged_cumulative_yield_mol_by_species)
    before_slopes = dict(manager._c2a_staged_last_log_slope_by_species)

    with pytest.raises(ValueError, match="unknown species"):
        manager._c2a_staged_log_slope_depletion_complete(
            species=("Na", "TypoSpecies"),
            evap_flux=_flux(Na=1.0),
            epsilon_per_hr=0.01,
        )

    assert manager._c2a_staged_cumulative_yield_mol_by_species == before_yield
    assert manager._c2a_staged_last_log_slope_by_species == before_slopes


@pytest.mark.parametrize(
    (
        "campaign",
        "hour",
        "temperature_C",
        "flux",
        "record",
        "composition_kg",
        "voltage_V",
        "current_A",
        "low_current_hours",
        "expected",
    ),
    [
        (CampaignPhase.C0, 9, 940.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C0, 10, 940.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C0, 25, 25.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C0B, 3, 1199.9, _flux(), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C0B, 3, 1200.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C2A, 18, 25.0, _flux(0.1), BatchRecord(), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C2A, 18, 25.0, _flux(0.099), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C2A, 30, 25.0, _flux(99.0), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C2A_STAGED, 7, 25.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C2A_STAGED, 8, 25.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C2B, 8, 25.0, _flux(Fe=0.05), BatchRecord(), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C2B, 8, 25.0, _flux(Fe=0.049), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C2B, 20, 25.0, _flux(Fe=9.0), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C3_K, 2, 25.0, _flux(), BatchRecord(path="A_staged"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C3_K, 3, 25.0, _flux(), BatchRecord(path="A_staged"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C3_K, 12, 25.0, _flux(), BatchRecord(path="A"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C3_K, 25, 25.0, _flux(), BatchRecord(path="B"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C3_NA, 3, 25.0, _flux(), BatchRecord(path="A_staged"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C3_NA, 18, 25.0, _flux(), BatchRecord(path="A"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C3_NA, 35, 25.0, _flux(), BatchRecord(path="B"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C4, 6, 25.0, _flux(Mg=0.02), BatchRecord(), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C4, 6, 25.0, _flux(Mg=0.019), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C4, 20, 25.0, _flux(Mg=9.0), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C5, 14, 25.0, _flux(), BatchRecord(branch="two"), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C5, 15, 25.0, _flux(), BatchRecord(branch="two"), None, 0.0, 100.0, 0, False),
        (CampaignPhase.C5, 10, 25.0, _flux(), BatchRecord(branch="two"), None, 1.6, 4.0, 1, False),
        (CampaignPhase.C5, 10, 25.0, _flux(), BatchRecord(branch="two"), None, 1.6, 4.0, 3, True),
        (CampaignPhase.C5, 800, 25.0, _flux(), BatchRecord(branch="two"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C5, 799, 25.0, _flux(), BatchRecord(branch="one"), None, 0.0, 100.0, 0, True),
        (CampaignPhase.C5, 800, 25.0, _flux(), BatchRecord(branch="one"), None, 0.0, 100.0, 0, True),
        (
            CampaignPhase.C6,
            0,
            25.0,
            _flux(),
            BatchRecord(),
            {"SiO2": 10.0, "Al2O3": 7.5, "CaO": 82.5},
            0.0,
            100.0,
            0,
            False,
        ),
        (
            CampaignPhase.C6,
            0,
            25.0,
            _flux(),
            BatchRecord(),
            {"SiO2": 10.0, "Al2O3": 7.4, "CaO": 82.6},
            0.0,
            100.0,
            0,
            True,
        ),
        (CampaignPhase.C6, 20, 25.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, True),
        (CampaignPhase.MRE_BASELINE, 0, 25.0, _flux(), BatchRecord(), None, 2.44, 9.0, 2, False),
        (CampaignPhase.MRE_BASELINE, 0, 25.0, _flux(), BatchRecord(), None, 2.45, 9.0, 2, True),
        (CampaignPhase.MRE_BASELINE, 120, 25.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, True),
    ],
)
def test_configured_campaign_endpoints_match_legacy_trip_points(
    campaign: CampaignPhase,
    hour: int,
    temperature_C: float,
    flux: EvaporationFlux,
    record: BatchRecord,
    composition_kg: dict[str, float] | None,
    voltage_V: float,
    current_A: float,
    low_current_hours: int,
    expected: bool,
):
    manager = CampaignManager(_setpoints())
    melt = _melt(
        campaign,
        hour,
        temperature_C=temperature_C,
        composition_kg=composition_kg,
        voltage_V=voltage_V,
        current_A=current_A,
        low_current_hours=low_current_hours,
    )

    assert manager.check_endpoint(melt, flux, CondensationTrain(), record) is expected


def test_zero_runtime_max_hours_uses_configured_campaign_default():
    manager = CampaignManager(_setpoints())
    manager.overrides["C2A"] = {"max_hours": 0.0}
    melt = _melt(CampaignPhase.C2A, 0)

    assert (
        manager.check_endpoint(
            melt,
            _flux(99.0),
            CondensationTrain(),
            BatchRecord(),
        )
        is False
    )


def test_c5_low_current_endpoint_is_gated_on_final_rung():
    # The C5 cell dispatches at the stage voltage cap for EVERY ladder hold
    # (2026-07-06 rung fix), so at-cap + low-current no longer means "ladder
    # done" by itself. The endpoint's low-current signal must only count on
    # the FINAL declared rung; earlier rungs' depletion belongs to the
    # ladder-advance logic. Tri-state contract: None (no ladder bookkeeping,
    # legacy states) keeps the legacy trip; explicit False blocks it.
    manager = CampaignManager(_setpoints())
    record = BatchRecord(branch="two")

    def melt_at_cap(low_hours: int) -> MeltState:
        return _melt(
            CampaignPhase.C5,
            10,
            voltage_V=1.6,
            current_A=4.0,
            low_current_hours=low_hours,
        )

    early_rung = melt_at_cap(3)
    early_rung.mre_c5_on_final_rung = False
    assert manager.check_endpoint(early_rung, _flux(), CondensationTrain(), record) is False

    final_rung = melt_at_cap(3)
    final_rung.mre_c5_on_final_rung = True
    assert manager.check_endpoint(final_rung, _flux(), CondensationTrain(), record) is True

    legacy = melt_at_cap(3)
    assert legacy.mre_c5_on_final_rung is None
    assert manager.check_endpoint(legacy, _flux(), CondensationTrain(), record) is True

    complete = _melt(CampaignPhase.C5, 10, voltage_V=0.0, current_A=0.0)
    complete.mre_c5_ladder_complete = True
    assert manager.check_endpoint(complete, _flux(), CondensationTrain(), record) is True


def test_campaign_endpoint_caps_and_classes_are_materialized():
    campaigns = _setpoints()["campaigns"]

    assert campaigns["C0"]["max_hold_hr"] == 25
    assert campaigns["C0b_p_cleanup"]["max_hold_hr"] == 3
    assert campaigns["C2A_continuous"]["max_hold_hr"] == 30
    assert campaigns["C2A_staged"]["max_hold_hr"] == 9
    assert campaigns["C2B"]["max_hold_hr"] == 20
    assert campaigns["C3"]["max_hold_hr"]["C3_K"] == {
        "A_staged": 3,
        "A": 12,
        "default": 25,
    }
    assert campaigns["C3"]["max_hold_hr"]["C3_NA"] == {
        "A_staged": 3,
        "A": 18,
        "default": 35,
    }
    assert campaigns["C4"]["max_hold_hr"] == 20
    assert campaigns["C5"]["max_hold_hr"] == {
        "branch_two": 800,
        "branch_one": 800,
    }
    assert campaigns["mre_baseline"]["max_hold_hr"] == 120
    assert campaigns["C6"]["max_hold_hr"] == 20

    assert {
        key: campaigns[key]["termination_class"]
        for key in (
            "C0",
            "C0b_p_cleanup",
            "C2A_continuous",
            "C2A_staged",
            "C2B",
            "C3",
            "C4",
            "C5",
            "mre_baseline",
            "C6",
        )
    } == {
        "C0": "C",
        "C0b_p_cleanup": "C",
        "C2A_continuous": "C",
        "C2A_staged": "A",
        "C2B": "C",
        "C3": "A",
        "C4": "C",
        "C5": "A",
        "mre_baseline": "C",
        "C6": "A",
    }
