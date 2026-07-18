from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from simulator.campaigns import (
    CampaignHoldAcquisitionRefusal,
    CampaignHoldTargetRefusal,
    CampaignManager,
)
from simulator.state import (
    BatchRecord,
    CampaignPhase,
    CondensationTrain,
    EvaporationFlux,
    HourSnapshot,
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
        # Inventory evidence in docs-private/research/2026-07-18-c6primary/
        # report.md:20-22 records zero provider transitions while every C6
        # snapshot remained in transport-limited preheat. Depletion and the
        # reaction-hold cap therefore cannot complete C6 at 25 C.
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
            False,
        ),
        (CampaignPhase.C6, 20, 25.0, _flux(), BatchRecord(), None, 0.0, 100.0, 0, False),
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


def test_c6_current_at_target_tick_can_satisfy_composition_endpoint() -> None:
    manager = CampaignManager(_setpoints())
    melt = _melt(
        CampaignPhase.C6,
        0,
        temperature_C=1400.0,
        composition_kg={"SiO2": 10.0, "Al2O3": 7.4, "CaO": 82.6},
    )

    assert manager.check_endpoint(
        melt,
        _flux(),
        CondensationTrain(),
        BatchRecord(),
    ) is True


def test_c6_max_hold_counts_only_provider_dispatch_ticks() -> None:
    manager = CampaignManager(_setpoints())
    melt = _melt(CampaignPhase.C6, 80, temperature_C=1400.0)
    preheat = [
        HourSnapshot(campaign=CampaignPhase.C6, temperature_C=1150.0)
        for _ in range(40)
    ]
    at_hold = [
        HourSnapshot(
            campaign=CampaignPhase.C6,
            temperature_C=1400.0,
            c6_at_hold_target=True,
        )
        for _ in range(19)
    ]

    assert manager.check_endpoint(
        melt,
        _flux(),
        CondensationTrain(),
        BatchRecord(snapshots=[*preheat, *at_hold[:-1]]),
    ) is False
    assert manager.check_endpoint(
        melt,
        _flux(),
        CondensationTrain(),
        BatchRecord(snapshots=[*preheat, *at_hold]),
    ) is True


def test_c6_ramping_schedule_counts_only_tick_dispatch_evidence() -> None:
    manager = CampaignManager(_setpoints())
    manager.overrides["C6"] = {
        "lab_schedule": {
            "id": "c6-ramping-hold-clock",
            "duration_h": 2.0,
            "interpolation": "piecewise_linear",
            "interpolation_source_class": "assumption_with_sensitivity_marker",
            "interpolation_citation_id": "test",
            "interpolation_extraction_note": "ramping C6 regression",
            "furnace_ceiling_C": 1600.0,
            "melt_temperature_C": [
                {"t_h": 0.0, "value": 1200.0, "unit": "C"},
                {"t_h": 2.0, "value": 1400.0, "unit": "C"},
            ],
            "chamber_pressure_mbar": [
                {"t_h": 0.0, "value": 1.0, "unit": "mbar"},
                {"t_h": 2.0, "value": 1.0, "unit": "mbar"},
            ],
            "gas_boundary": {
                "background_gas": {
                    "species": "Ar",
                    "mole_fraction": 1.0,
                    "source_class": "assumption_with_sensitivity_marker",
                    "citation_id": "test",
                    "digest": "c6-ramp-argon",
                },
                "imposed_flow": {
                    "value": 0.3,
                    "unit": "NL_min",
                    "source_class": "assumption_with_sensitivity_marker",
                    "citation_id": "test",
                    "digest": "c6-ramp-flow",
                },
                "pressure_control": {
                    "mode": "flow_through_with_pump",
                    "source_class": "assumption_with_sensitivity_marker",
                    "citation_id": "test",
                    "digest": "c6-ramp-pressure",
                },
            },
        }
    }
    manager.setpoints["campaigns"]["C6"]["max_hold_hr"] = 2.0
    prior_melt = _melt(CampaignPhase.C6, 0, temperature_C=1400.0)
    current_melt = _melt(CampaignPhase.C6, 1, temperature_C=1400.0)
    prior_target, _ = manager.get_temp_target(
        CampaignPhase.C6, 0, prior_melt
    )
    current_target, _ = manager.get_temp_target(
        CampaignPhase.C6, 1, current_melt
    )
    assert prior_target == pytest.approx(1300.0)
    assert current_target == pytest.approx(1400.0)
    assert manager.c6_at_hold_target(prior_target, 1400.0) is False
    assert manager.c6_at_hold_target(current_target, 1400.0) is True

    late_acquisition = HourSnapshot(
        campaign=CampaignPhase.C6,
        temperature_C=1400.0,
        c6_at_hold_target=False,
    )
    assert manager.check_endpoint(
        current_melt,
        _flux(),
        CondensationTrain(),
        BatchRecord(snapshots=[late_acquisition]),
    ) is False


@pytest.mark.parametrize(
    "temperature_C",
    [float("nan"), float("inf"), float("-inf")],
)
def test_c6_nonfinite_temperature_refuses_before_endpoint(
    temperature_C: float,
) -> None:
    manager = CampaignManager(_setpoints())
    melt = _melt(CampaignPhase.C6, 0, temperature_C=temperature_C)

    with pytest.raises(CampaignHoldTargetRefusal, match="c6_hold_target_nonfinite"):
        manager.check_endpoint(
            melt,
            _flux(),
            CondensationTrain(),
            BatchRecord(),
        )


@pytest.mark.parametrize(
    "hold_target_C",
    [float("nan"), float("inf"), float("-inf")],
)
def test_c6_nonfinite_hold_target_refuses_shared_dispatch_predicate(
    hold_target_C: float,
) -> None:
    with pytest.raises(CampaignHoldTargetRefusal, match="c6_hold_target_nonfinite"):
        CampaignManager.c6_at_hold_target(hold_target_C, 1400.0)


def test_c6_operator_max_hours_is_typed_acquisition_refusal() -> None:
    manager = CampaignManager(_setpoints())
    manager.overrides["C6"] = {"max_hours": 2.0}
    transport = {
        "binding_cause": "controlled_o2_no_equipment",
        "saturation_pct": 202.0,
        "evap_exceeds_transport": True,
    }

    assert manager.check_endpoint(
        _melt(CampaignPhase.C6, 0, temperature_C=1150.0),
        _flux(),
        CondensationTrain(),
        BatchRecord(),
        transport_state=transport,
    ) is False
    with pytest.raises(CampaignHoldAcquisitionRefusal) as refusal:
        manager.check_endpoint(
            _melt(CampaignPhase.C6, 1, temperature_C=1150.0),
            _flux(),
            CondensationTrain(),
            BatchRecord(),
            transport_state=transport,
        )
    assert refusal.value.diagnostic["unacquired_hold_target_C"] == 1400.0
    assert refusal.value.diagnostic["acquisition_limit_source"] == (
        "operator_override:C6.max_hours"
    )
    assert refusal.value.diagnostic["binding_transport_state"] == transport

    # At target, the current tick has dispatched thermite before endpoint
    # evaluation, so the same operator wall clock is a successful escape.
    assert manager.check_endpoint(
        _melt(CampaignPhase.C6, 1, temperature_C=1400.0),
        _flux(),
        CondensationTrain(),
        BatchRecord(),
        transport_state=transport,
    ) is True


def test_c6_configured_target_acquisition_ceiling_is_default_backstop() -> None:
    manager = CampaignManager(_setpoints())

    assert manager.check_endpoint(
        _melt(CampaignPhase.C6, 118, temperature_C=1150.0),
        _flux(),
        CondensationTrain(),
        BatchRecord(),
    ) is False
    with pytest.raises(CampaignHoldAcquisitionRefusal) as refusal:
        manager.check_endpoint(
            _melt(CampaignPhase.C6, 119, temperature_C=1150.0),
            _flux(),
            CondensationTrain(),
            BatchRecord(),
        )
    assert refusal.value.diagnostic["acquisition_limit_hr"] == 120.0
    assert refusal.value.diagnostic["acquisition_limit_source"] == (
        "setpoint:C6.max_target_acquisition_hr"
    )


@pytest.mark.parametrize(
    "invalid_limit",
    [float("nan"), float("inf"), 0.0, -1.0],
)
def test_c6_configured_acquisition_ceiling_refuses_nonfinite_or_nonpositive(
    invalid_limit: float,
) -> None:
    setpoints = _setpoints()
    setpoints["campaigns"]["C6"]["max_target_acquisition_hr"] = invalid_limit
    manager = CampaignManager(setpoints)

    with pytest.raises(
        ValueError,
        match="max_target_acquisition_hr must be finite and positive",
    ):
        manager.check_endpoint(
            _melt(CampaignPhase.C6, 0, temperature_C=1150.0),
            _flux(),
            CondensationTrain(),
            BatchRecord(),
        )


@pytest.mark.parametrize("invalid_limit", [float("nan"), float("inf")])
def test_c6_operator_acquisition_ceiling_refuses_nonfinite(
    invalid_limit: float,
) -> None:
    manager = CampaignManager(_setpoints())
    manager.overrides["C6"] = {"max_hours": invalid_limit}

    with pytest.raises(ValueError, match="C6.max_hours must be finite"):
        manager.check_endpoint(
            _melt(CampaignPhase.C6, 0, temperature_C=1150.0),
            _flux(),
            CondensationTrain(),
            BatchRecord(),
        )


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
    assert campaigns["C6"]["max_target_acquisition_hr"] == 120
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
