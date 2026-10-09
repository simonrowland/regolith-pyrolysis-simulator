"""Pins of the pre-fix t-1139 oxygen numbers.

These capture the outputs the b-747 / b-748 / b-749 commits change.
Expected values are the production functions' current results, measured
before those commits. Later commits replace an assertion only when the
ruling changes that output.
"""

from __future__ import annotations

import math
from copy import deepcopy

import pytest
import yaml
from pathlib import Path

from engines.builtin.overhead_gas_equilibrium import (
    BuiltinOverheadGasEquilibriumProvider,
)
from simulator.campaigns import CampaignManager
from simulator.core import PyrolysisSimulator
from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.equilibrium import oxygen_potential_mode_for_atmosphere
from simulator.fe_redox import (
    KRESS91_LIQUID_CALIBRATION_MIN_T_C,
    feo_iw_log10_fO2_bar,
    intrinsic_melt_fO2,
    melt_fO2_seed_without_ferric_iron,
)
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import Atmosphere, CampaignPhase, MeltState


DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# RH03 hour-0 holdup measured on this branch before the pressure bound:
# 823.2141246549886 mol O2 in 0.07409407407407409 m3 at 2473.15 K.
_RH03_O2_MOL = 823.2141246549886
_RH03_VOLUME_M3 = 0.07409407407407409
_RH03_TEMPERATURE_K = 2473.15
_RH03_IDEAL_O2_BAR = 2284.6193155497276


def _setpoints() -> dict:
    return yaml.safe_load((DATA_DIR / "setpoints.yaml").read_text()) or {}


def _ferrous_lunar_composition() -> dict[str, float]:
    return {
        "SiO2": 45.0,
        "FeO": 16.5,
        "Na2O": 0.4,
        "K2O": 0.1,
    }


def _n2_lab_schedule() -> dict:
    return {
        "id": "t1139-pin-schedule",
        "duration_h": 1.0,
        "interpolation": "piecewise_linear",
        "interpolation_source_class": "test_fixture",
        "furnace_ceiling_C": 1700.0,
        "melt_temperature_C": [
            {"t_h": 0.0, "value": 1200.0, "unit": "C"},
            {"t_h": 1.0, "value": 1300.0, "unit": "C"},
        ],
        "chamber_pressure_mbar": [
            {"t_h": 0.0, "value": 13.0, "unit": "mbar"},
            {"t_h": 1.0, "value": 13.0, "unit": "mbar"},
        ],
        "gas_boundary": {
            "background_gas": {
                "species": "N2",
                "mole_fraction": 1.0,
                "source_class": "test_fixture",
                "citation_id": "unit_test",
            },
            "imposed_flow": {
                "reported_status": "not_reported",
                "source_class": "test_fixture",
                "citation_id": "unit_test",
                "digest": "not_applicable",
                "reason": "unit test",
            },
            "pressure_control": {
                "mode": "flow_through_with_pump",
                "source_class": "test_fixture",
                "citation_id": "unit_test",
            },
        },
    }


def _sim() -> PyrolysisSimulator:
    return PyrolysisSimulator(
        InternalAnalyticalBackend(),
        _setpoints(),
        yaml.safe_load((DATA_DIR / "feedstocks.yaml").read_text()) or {},
        yaml.safe_load((DATA_DIR / "vapor_pressures.yaml").read_text()) or {},
    )


def test_melt_seed_is_holzheid_iw_plus_alkali_without_a_vacuum_floor() -> None:
    composition = _ferrous_lunar_composition()
    # Lunar Na2O 0.4 + K2O 0.1 = 0.50 wt% alkali. The seed's alkali term is
    # 0.01 dex per wt%, capped at 0.15, so this composition is +0.005 dex.
    alkali_offset_dex = 0.005
    seeded = intrinsic_melt_fO2(composition, 1338.15)
    no_alkali = intrinsic_melt_fO2({"SiO2": 45.0, "FeO": 16.5}, 1338.15)

    assert no_alkali == pytest.approx(feo_iw_log10_fO2_bar(1338.15))
    assert seeded - no_alkali == pytest.approx(alkali_offset_dex)
    assert intrinsic_melt_fO2(composition, 298.15) == pytest.approx(
        feo_iw_log10_fO2_bar(298.15) + alkali_offset_dex
    )
    assert seeded != pytest.approx(math.log10(DEFAULT_VACUUM_FLOOR_BAR))
    assert melt_fO2_seed_without_ferric_iron(composition) is True
    assert melt_fO2_seed_without_ferric_iron(
        {"FeO": 2.0, "Fe2O3": 1.0}
    ) is False


def _pinned_liquidus(liquidus_T_C: float) -> dict[str, float | str]:
    """Freeze-gate curve owned by the test, not by engines.local.toml."""

    return {
        "liquidus_T_C": float(liquidus_T_C),
        "source": "test_pinned_liquidus",
    }


def _c2a_campaign_temperatures(sim: PyrolysisSimulator) -> list[float]:
    """Hourly C2A temperatures from the production ramp, through max hold."""

    from simulator.runner import _prepare_sio_campaign_start

    sim.melt.campaign = CampaignPhase.C2A
    sim.campaign_mgr.configure_campaign(sim.melt, CampaignPhase.C2A)
    _prepare_sio_campaign_start(sim)
    hours = int(sim.campaign_mgr._max_hold_hr(CampaignPhase.C2A))
    temperatures: list[float] = []
    for hour in range(hours):
        sim.melt.campaign_hour = hour
        sim.melt.hour = hour
        sim._update_temperature()
        temperatures.append(float(sim.melt.temperature_C))
    return temperatures


def test_reservoir_tracks_iw_until_the_first_liquid_tick() -> None:
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    composition = sim._melt_oxide_wt_pct()
    notice = sim.melt_fO2_seed_run_notice()
    assert notice is not None
    assert notice["code"] == "melt_fO2_seed_without_ferric_iron"
    assert notice["authority"] == "IW buffer, no Fe3+/Fe2+"

    # The Kress floor is the liquidus. 1100 C is below it. 1215 C is the
    # first temperature this test puts above it, so that tick adopts.
    floor = _pinned_liquidus(KRESS91_LIQUID_CALIBRATION_MIN_T_C)
    sim.melt.temperature_C = 1100.0
    sim._re_reference_melt_fO2_to_temperature(gate_authority=floor)
    subliquid = intrinsic_melt_fO2(composition, 1100.0 + 273.15)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        subliquid
    )
    assert sim.melt.oxygen_reservoir.reference_T_K is None

    sim.melt.temperature_C = 1215.0
    sim._re_reference_melt_fO2_to_temperature(gate_authority=floor)
    fixed = intrinsic_melt_fO2(composition, 1215.0 + 273.15)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(fixed)
    assert sim.melt.oxygen_reservoir.reference_T_K == pytest.approx(1215.0 + 273.15)

    sim.melt.temperature_C = 1230.0
    sim._re_reference_melt_fO2_to_temperature(gate_authority=floor)
    fresh = intrinsic_melt_fO2(composition, 1230.0 + 273.15)
    assert sim.melt.oxygen_reservoir.reference_T_K == pytest.approx(1230.0 + 273.15)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log != pytest.approx(fresh)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log != pytest.approx(fixed)

    # A projected liquidus above the C2A campaign peak never adopts.
    # Every hour keeps tracking IW(T), and reference_T_K stays unset.
    schedule = _sim()
    schedule.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    temperatures = _c2a_campaign_temperatures(schedule)
    peak_T_C = max(temperatures)
    assert peak_T_C > KRESS91_LIQUID_CALIBRATION_MIN_T_C
    projected = _pinned_liquidus(peak_T_C + 1.0)
    assert float(projected["liquidus_T_C"]) > peak_T_C

    tracking = _sim()
    tracking.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    tracking_composition = tracking._melt_oxide_wt_pct()
    for temperature_C in temperatures:
        tracking.melt.temperature_C = temperature_C
        tracking._re_reference_melt_fO2_to_temperature(gate_authority=projected)
        assert tracking.melt.oxygen_reservoir.reference_T_K is None
        assert tracking.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
            intrinsic_melt_fO2(tracking_composition, temperature_C + 273.15)
        )


def test_seed_notice_is_on_the_ranked_surfaces_and_the_sio_report() -> None:
    from simulator.optimize.evaluate import _cache_trace_payload
    from simulator.optimize.objective import product_summary
    from simulator.optimize.profiles import load_profile
    from simulator.runner import build_sio_yield_report

    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    notice = sim.melt_fO2_seed_run_notice()
    assert notice is not None

    class _Execution:
        simulator = sim
        trace = None
        per_hour = ()
        session = None
        reduced_real_cache = None
        refusal_diagnostic = None
        reason = ""

    summary = product_summary(_Execution(), load_profile("lunar_mare_low_ti"))
    payload = _cache_trace_payload(_Execution(), {})
    assert summary["melt_fO2_seed_notice"]["code"] == notice["code"]
    assert summary["melt_fO2_seed_notice"]["authority"] == notice["authority"]
    assert payload["melt_fO2_seed_notice"]["code"] == notice["code"]

    report, diagnostics = build_sio_yield_report(
        feedstock_id="lunar_mare_low_ti",
        hours=1,
        include_diagnostics=True,
        allow_unmeasured_alpha_fallback=True,
    )
    assert report["melt_fO2_seed_notice"]["code"] == notice["code"]
    assert diagnostics["melt_fO2_seed_notice"]["code"] == notice["code"]


def test_zero_o2_argon_schedule_is_a_closed_sweep() -> None:
    schedule = deepcopy(_n2_lab_schedule())
    schedule["gas_boundary"]["background_gas"]["species"] = "Ar"
    manager = CampaignManager(_setpoints())
    manager.overrides["C2A"] = {"lab_schedule": schedule}
    melt = MeltState()

    manager.configure_campaign(melt, CampaignPhase.C2A)
    manager.apply_lab_schedule_controls(
        melt,
        CampaignPhase.C2A,
        sample_time_h=0.0,
    )

    assert melt.pO2_mbar == pytest.approx(0.0)
    assert melt.atmosphere is Atmosphere.PN2_SWEEP
    assert melt.background_gas_species == "Ar"


_RH03_FE_MOL = 581.861950497938


def _load_overhead(sim: PyrolysisSimulator, species_mol: dict[str, float]) -> None:
    sim._overhead_headspace_config["enabled"] = True
    sim._overhead_headspace_config["volume_m3"] = _RH03_VOLUME_M3
    sim.melt.temperature_C = _RH03_TEMPERATURE_K - 273.15
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        species_mol,
        source="t1139 pressure-control bound",
        material_origin="feedstock",
    )


def _c2a_pressure_schedule(mode: str) -> dict:
    schedule = deepcopy(_n2_lab_schedule())
    schedule["gas_boundary"]["background_gas"]["species"] = "Ar"
    schedule["gas_boundary"]["pressure_control"]["mode"] = mode
    return schedule


def test_pressure_controlled_o2_partial_does_not_exceed_mole_fraction_times_total() -> None:
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.campaign_mgr.overrides["C2A"] = {
        "lab_schedule": _c2a_pressure_schedule("flow_through_with_pump"),
        "lab_schedule_pO2_setpoint_mbar": 1.0,
    }
    sim.melt.campaign = CampaignPhase.C2A
    sim.campaign_mgr.configure_campaign(sim.melt, CampaignPhase.C2A)
    _load_overhead(sim, {"O2": _RH03_O2_MOL, "Fe": _RH03_FE_MOL})

    holdup = sim.atom_ledger.mol_by_account("process.overhead_gas")
    n_o2 = float(holdup["O2"])
    n_total = sum(float(mol) for mol in holdup.values())
    p_controlled = sim.campaign_mgr.pressure_controlled_total_bar(sim.melt)
    assert p_controlled == pytest.approx(13.0 / 1000.0)
    cap = n_o2 / n_total * p_controlled
    ledger = sim._headspace_ledger_pO2_bar_from_o2_mol(n_o2)

    assert sim.melt.atmosphere is Atmosphere.CONTROLLED_O2
    assert ledger == pytest.approx(_RH03_IDEAL_O2_BAR, rel=1e-12)
    assert ledger > cap
    transport = sim._headspace_transport_pO2_bar_from_ledger(
        ledger,
        head_o2_mol=n_o2,
    )
    assert transport == pytest.approx(cap)
    sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
    assert sim.melt.oxygen_reservoir.headspace_ledger_pO2_bar == pytest.approx(
        ledger
    )
    assert sim._headspace_transport_pO2_bar() == pytest.approx(cap)
    # A zero stored transport falls through to the sealed diagnostic partial.
    # That read is still bounded; the diagnostic itself stays on n R T / V.
    sim.melt.oxygen_reservoir.headspace_transport_pO2_bar = 0.0
    assert sim._headspace_transport_pO2_bar() == pytest.approx(cap)
    diagnostic = sim._overhead_gas_equilibrium_diagnostic()
    assert float(diagnostic["partial_pressures_bar"]["O2"]) == pytest.approx(
        ledger,
        rel=1e-12,
    )


def test_imposed_o2_cover_survives_a_fe_only_pressure_controlled_holdup() -> None:
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.campaign_mgr.overrides["C2A"] = {
        "lab_schedule": _c2a_pressure_schedule("flow_through_with_pump"),
        "lab_schedule_pO2_setpoint_mbar": 1.0,
    }
    sim.melt.campaign = CampaignPhase.C2A
    sim.campaign_mgr.configure_campaign(sim.melt, CampaignPhase.C2A)
    _load_overhead(sim, {"Fe": _RH03_FE_MOL})

    holdup = sim.atom_ledger.mol_by_account("process.overhead_gas")
    n_total = sum(float(mol) for mol in holdup.values())
    p_controlled = sim.campaign_mgr.pressure_controlled_total_bar(sim.melt)
    assert p_controlled == pytest.approx(0.013)
    assert float(holdup.get("O2", 0.0)) == pytest.approx(0.0)
    assert n_total > 0.0
    assert sim.melt.atmosphere is Atmosphere.CONTROLLED_O2
    assert oxygen_potential_mode_for_atmosphere(sim.melt.atmosphere) == "imposed"
    assert sim._commanded_pO2_bar() == pytest.approx(0.001)

    transport = sim._headspace_transport_pO2_bar_from_ledger(0.0, head_o2_mol=0.0)
    assert transport == pytest.approx(0.001)
    sim._refresh_oxygen_reservoir_transport_pO2_for_vapor()
    assert sim._headspace_transport_pO2_bar() == pytest.approx(0.001)


def test_pressure_controlled_bound_does_not_lift_an_underpressured_partial() -> None:
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.campaign_mgr.overrides["C2A"] = {
        "lab_schedule": _c2a_pressure_schedule("flow_through_with_pump"),
        "lab_schedule_pO2_setpoint_mbar": 1.0,
    }
    sim.melt.campaign = CampaignPhase.C2A
    sim.campaign_mgr.configure_campaign(sim.melt, CampaignPhase.C2A)
    sim._overhead_headspace_config["enabled"] = True
    sim._overhead_headspace_config["volume_m3"] = _RH03_VOLUME_M3
    sim.melt.temperature_C = _RH03_TEMPERATURE_K - 273.15
    o2_mol = sim._headspace_o2_mol_for_pO2_bar(0.005)
    _load_overhead(sim, {"O2": o2_mol})

    n_o2 = float(sim.atom_ledger.mol_by_account("process.overhead_gas")["O2"])
    ledger = sim._headspace_ledger_pO2_bar_from_o2_mol(n_o2)
    p_controlled = sim.campaign_mgr.pressure_controlled_total_bar(sim.melt)
    transport = sim._headspace_transport_pO2_bar_from_ledger(
        ledger,
        head_o2_mol=n_o2,
    )

    assert ledger == pytest.approx(0.005)
    assert p_controlled is not None and ledger < p_controlled
    assert transport == pytest.approx(ledger)


def test_sealed_headspace_keeps_the_ideal_gas_o2_partial() -> None:
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.pO2_mbar = 1.0
    sim.melt.p_total_mbar = 13.0
    assert sim.campaign_mgr.pressure_controlled_total_bar(sim.melt) is None
    _load_overhead(sim, {"O2": _RH03_O2_MOL, "Fe": _RH03_FE_MOL})

    n_o2 = float(sim.atom_ledger.mol_by_account("process.overhead_gas")["O2"])
    ledger = sim._headspace_ledger_pO2_bar_from_o2_mol(n_o2)
    transport = sim._headspace_transport_pO2_bar_from_ledger(
        ledger,
        head_o2_mol=n_o2,
    )

    assert ledger == pytest.approx(_RH03_IDEAL_O2_BAR, rel=1e-12)
    assert transport == pytest.approx(ledger)


def test_pin_sealed_ideal_gas_partial_matches_the_rh03_hour0_holdup() -> None:
    partials = BuiltinOverheadGasEquilibriumProvider.compute_partial_pressures_bar(
        {"O2": _RH03_O2_MOL},
        _RH03_VOLUME_M3,
        _RH03_TEMPERATURE_K,
    )

    assert partials["O2"] == pytest.approx(_RH03_IDEAL_O2_BAR, rel=1e-12)
