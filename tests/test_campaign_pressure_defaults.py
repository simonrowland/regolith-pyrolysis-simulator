from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

import simulator.campaigns as campaigns_module
from simulator.campaigns import CampaignManager, CampaignPressureSetpointRefusal
from simulator.core import Atmosphere, CampaignPhase, MeltState
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun
from simulator.transport_regime import single_species_mean_free_path


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _setpoints() -> dict:
    return yaml.safe_load((DATA_DIR / "setpoints.yaml").read_text()) or {}


def _stage(setpoints: dict, name: str) -> dict:
    for stage in setpoints["campaigns"]["C2A_staged"]["stages"]:
        if stage.get("name") == name:
            return stage
    raise AssertionError(f"missing C2A_staged stage {name}")


def _n2_lab_schedule() -> dict:
    return {
        "id": "unit-test-n2-lab-schedule",
        "duration_h": 1.0,
        "interpolation": "piecewise_linear",
        "interpolation_source_class": "test_fixture",
        "furnace_ceiling_C": 1700.0,
        "melt_temperature_C": [
            {"t_h": 0.0, "value": 1200.0, "unit": "C"},
            {"t_h": 1.0, "value": 1300.0, "unit": "C"},
        ],
        "chamber_pressure_mbar": [
            {"t_h": 0.0, "value": 10.0, "unit": "mbar"},
            {"t_h": 1.0, "value": 10.0, "unit": "mbar"},
        ],
        "gas_boundary": {
            "background_gas": {
                "species": "N2",
                "mole_fraction": 0.8,
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
                "mode": "controlled",
                "source_class": "test_fixture",
                "citation_id": "unit_test",
            },
        },
    }


@pytest.mark.parametrize(
    ("campaign", "pO2_mbar", "p_total_mbar", "atmosphere"),
    [
        (CampaignPhase.C0, 0.0, 0.0, Atmosphere.HARD_VACUUM),
        (CampaignPhase.C0B, 9.0, 9.0, Atmosphere.CONTROLLED_O2_FLOW),
        (CampaignPhase.C2A, 0.0, 10.0, Atmosphere.PN2_SWEEP),
        (CampaignPhase.C2A_STAGED, 0.0, 10.0, Atmosphere.PN2_SWEEP),
        (CampaignPhase.C2B, 1.5, 1.5, Atmosphere.CONTROLLED_O2),
        (CampaignPhase.C3_K, 1.0, 1.0, Atmosphere.CONTROLLED_O2),
        (CampaignPhase.C3_NA, 1.0, 1.0, Atmosphere.CONTROLLED_O2),
        (CampaignPhase.C4, 0.2, 0.2, Atmosphere.CONTROLLED_O2),
        (CampaignPhase.C5, 50.0, 50.0, Atmosphere.O2_BACKPRESSURE),
        (CampaignPhase.C6, 0.0, 10.0, Atmosphere.PN2_SWEEP),
        (CampaignPhase.MRE_BASELINE, 50.0, 50.0, Atmosphere.O2_BACKPRESSURE),
    ],
)
def test_configure_campaign_pressure_defaults_match_catalogue(
    campaign: CampaignPhase,
    pO2_mbar: float,
    p_total_mbar: float,
    atmosphere: Atmosphere,
):
    melt = MeltState()
    CampaignManager(_setpoints()).configure_campaign(melt, campaign)

    assert melt.pO2_mbar == pytest.approx(pO2_mbar)
    assert melt.p_total_mbar == pytest.approx(p_total_mbar)
    assert melt.atmosphere is atmosphere


def test_c6_continuum_derivation_uses_selected_static_hold_temperature():
    campaign = _setpoints()["campaigns"]["C6"]
    continuum = campaign["continuum_pressure_bounds"]

    # The validated sweep range is physics evidence and remains wider than the
    # selected recipe. Pressure provenance follows the 1400 C hold chosen when
    # within-noise Al-yield ties break toward Ellingham-margin headroom.
    assert continuum["gas_temperature_C"] == 1400
    assert campaign["default_hold_T_C"] == 1400
    assert campaign["temp_range_C"] == [1380, 1450]

    assert continuum["carrier_gas"] == "Ar"
    assert continuum["derived_pAr_floor_mbar"] == pytest.approx(
        _continuum_floor_mbar(continuum), abs=5e-7
    )
    assert campaign["carrier_gas"] == "Ar"
    assert campaign["p_total_mbar"] == [5, 15]
    assert "pO2_mbar" not in campaign
    assert "pO2_mbar_default" not in campaign


def test_c6_refuses_retired_oxygen_atmosphere():
    from simulator.recipe_errors import MalformedRecipeError

    setpoints = _setpoints()
    setpoints["campaigns"]["C6"]["atmosphere"] = "controlled_O2"
    with pytest.raises(MalformedRecipeError, match="d-203"):
        CampaignManager(setpoints).configure_campaign(MeltState(), CampaignPhase.C6)


@pytest.mark.parametrize("carried_o2_mol", [0.0, 20.0])
def test_c6_entry_bleeds_carried_oxygen_before_mg_contact(carried_o2_mol):
    from simulator.session import SimSession

    session = SimSession().start(PyrolysisRun(
        feedstock_id="lunar_mare_low_ti", campaign="C6",
        additives_kg={"Mg": 50.0}, force_builtin_vapor_pressure=True,
    )._session_config())
    sim = session.simulator
    if carried_o2_mol:
        sim.atom_ledger.load_external_mol(
            "process.overhead_gas", {"O2": carried_o2_mol},
            source="test carried oxygen", material_origin="feedstock")
    sim.start_campaign(CampaignPhase.C6)
    sim.melt.temperature_C = 1400.0
    step_thermite = sim._step_thermite
    contacts = []

    def contact_mg():
        assert sim._overhead_holdup_mol().get("O2", 0.0) == 0.0
        contacts.append(sim.melt.campaign_hour)
        step_thermite()

    sim._step_thermite = contact_mg
    snapshots = []
    for _ in range(10):
        snapshots.append(sim.step())
        if contacts:
            break
    sim.atom_ledger.assert_balanced()
    assert len(contacts) == 1
    assert sim.melt.background_gas_species == "Ar"
    assert sim.melt.pO2_mbar == 0.0
    assert sum(s.c6_at_hold_target for s in snapshots) == len(contacts)
    assert contacts[0] == len(snapshots) - len(contacts)
    assert (contacts[0] > 0) is bool(carried_o2_mol)
    bled = sum(s.O2_vented_mol_hr + s.overhead.turbine_flow_mol_hr
               for s in snapshots)
    # Passive melt uptake remains active during purge; the bleed must carry
    # a positive, ledger-conserving outlet rather than deleting the inventory.
    assert 0.0 <= bled <= carried_o2_mol
    assert (bled > 0.0) is bool(carried_o2_mol)
    assert all(s.overhead.pressure_mbar == pytest.approx(10.0)
               for s in snapshots if s.c6_at_hold_target)



def _campaigns_with_continuum_bounds() -> dict[str, dict]:
    """Every campaign declaring a continuum_pressure_bounds block."""
    out = {}
    for name, campaign in (_setpoints().get("campaigns") or {}).items():
        if not isinstance(campaign, dict):
            continue
        bounds = campaign.get("continuum_pressure_bounds")
        if isinstance(bounds, dict):
            out[name] = campaign
    return out


def _continuum_floor_mbar(bounds: dict) -> float:
    # Kn is inverse pressure: at 1 mbar the ratio Kn/Kn_max gives the
    # floor in mbar. Use the transport owner and its carrier constants.
    at_one_mbar = single_species_mean_free_path(
        bounds["carrier_gas"], 100.0,
        float(bounds["gas_temperature_C"]) + 273.15,
        float(bounds["pipe_diameter_m"]),
    )
    return at_one_mbar.knudsen_number / float(bounds["knudsen_max"])


@pytest.mark.parametrize("campaign_name", sorted(_campaigns_with_continuum_bounds()))
def test_declared_continuum_floor_matches_its_own_derivation(campaign_name):
    """Every declared floor must equal the formula at its OWN co-located inputs.

    2026-08-29: this derivation was pinned for C6 only
    (test_c6_continuum_derivation_uses_selected_static_hold_temperature), while
    C4 carries an identical block and had no check at all. Parametrising over
    the declaring campaigns closes that gap and gives any future campaign the
    same guard for free, instead of it depending on someone remembering to add
    a bespoke test.

    This asserts the block is INTERNALLY consistent. Whether the campaign's
    operating setpoint respects that floor is a separate question, pinned
    below.
    """
    bounds = _campaigns_with_continuum_bounds()[campaign_name]["continuum_pressure_bounds"]
    key = f"derived_p{bounds['carrier_gas']}_floor_mbar"
    assert bounds[key] == pytest.approx(_continuum_floor_mbar(bounds), abs=5e-7)



def test_campaign_default_pressure_versus_its_own_continuum_floor():
    """C4 remains below its declared floor; d-203 resolves C6 with Ar."""
    known_violations = {"C4"}

    violations = {}
    for name, campaign in _campaigns_with_continuum_bounds().items():
        default_mbar = campaign.get("p_total_mbar_default")
        if default_mbar is None:
            continue
        floor = _continuum_floor_mbar(campaign["continuum_pressure_bounds"])
        if float(default_mbar) < floor:
            violations[name] = (float(default_mbar), floor)

    assert set(violations) == known_violations, (
        f"the set of campaigns running below their own continuum floor has "
        f"CHANGED (b-326). Now {sorted(violations)}, previously "
        f"{sorted(known_violations)}. If a campaign was fixed, drop it from "
        f"known_violations; if a new one regressed, that is a live defect. "
        f"Detail: "
        + "; ".join(
            f"{n}: default {d} mbar vs floor {f:.6f} mbar ({f / d:.2f}x below)"
            for n, (d, f) in sorted(violations.items())
        )
    )


def test_campaign_pressure_default_override_reaches_melt_state():
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"]["C2B"]["pO2_mbar_default"] = 0.75

    melt = MeltState()
    CampaignManager(setpoints).configure_campaign(melt, CampaignPhase.C2B)

    assert melt.pO2_mbar == pytest.approx(0.75)
    assert melt.p_total_mbar == pytest.approx(1.5)


def test_c2a_continuous_ramp_rates_use_yaml_midpoints():
    setpoints = _setpoints()
    bands = setpoints["campaigns"]["C2A_continuous"]["dT_dt_C_per_hr"]
    assert (
        (float(bands["early_ramp_1050_1320C"][0])
         + float(bands["early_ramp_1050_1320C"][1])) / 2.0
    ) == 15.0
    assert (
        (float(bands["peak_SiO_window_1400_1600C"][0])
         + float(bands["peak_SiO_window_1400_1600C"][1])) / 2.0
    ) == 7.5

    manager = CampaignManager(setpoints)
    _, early_ramp = manager.get_temp_target(
        CampaignPhase.C2A,
        0,
        MeltState(campaign=CampaignPhase.C2A, temperature_C=1200.0),
    )
    _, peak_ramp = manager.get_temp_target(
        CampaignPhase.C2A,
        0,
        MeltState(campaign=CampaignPhase.C2A, temperature_C=1400.0),
    )

    assert early_ramp == 15.0
    assert peak_ramp == 7.5


def test_c2a_continuous_ramp_rate_band_malformed_fails_loud():
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"]["C2A_continuous"]["dT_dt_C_per_hr"][
        "early_ramp_1050_1320C"
    ] = [10]

    with pytest.raises(
        ValueError,
        match=r"C2A_continuous\.dT_dt_C_per_hr\.early_ramp_1050_1320C",
    ):
        CampaignManager(setpoints).get_temp_target(
            CampaignPhase.C2A,
            0,
            MeltState(campaign=CampaignPhase.C2A, temperature_C=1200.0),
        )


@pytest.mark.parametrize(
    ("phase", "config_key", "target", "ramp"),
    [
        (CampaignPhase.C0, "C0", 910.0, 37.0),
        (CampaignPhase.C2B, "C2B", 1460.0, 12.0),
    ],
)
def test_c0_and_c2b_temperature_ramps_follow_campaign_config(
    phase, config_key, target, ramp
):
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"][config_key]["temp_range_C"][-1] = target
    setpoints["campaigns"][config_key]["dT_dt_C_per_hr"] = ramp

    actual = CampaignManager(setpoints).get_temp_target(
        phase,
        0,
        MeltState(campaign=phase, temperature_C=25.0),
    )

    assert actual == (target, ramp)


def test_c2a_staged_shipped_stage_targets_and_ramps_are_pinned() -> None:
    # Golden values captured before removing the missing-key fallbacks.
    manager = CampaignManager(_setpoints())
    stage_samples = (
        ("alkali_early_fe", 0, (1250.0, 600.0)),
        ("sio_window", 4, (1600.0, 175.0)),
        ("fe_hot_hold", 7, (1750.0, 150.0)),
        ("cool_for_na_shuttle", 8, (1150.0, 600.0)),
    )

    for stage_name, hour, expected in stage_samples:
        actual = manager.get_temp_target(
            CampaignPhase.C2A_STAGED,
            hour,
            MeltState(campaign=CampaignPhase.C2A_STAGED, campaign_hour=hour),
        )
        assert actual == expected


def test_c2a_staged_missing_stage_target_refuses_with_setpoint_key() -> None:
    setpoints = deepcopy(_setpoints())
    _stage(setpoints, "sio_window").pop("target_C")
    manager = CampaignManager(setpoints)

    with pytest.raises(
        ValueError,
        match=r"Missing numeric campaign setpoint: C2A_staged\.stages\.sio_window\.target_C",
    ):
        manager.get_temp_target(
            CampaignPhase.C2A_STAGED,
            4,
            MeltState(campaign=CampaignPhase.C2A_STAGED, campaign_hour=4),
        )


def test_c2a_staged_missing_hold_default_refuses_with_setpoint_key() -> None:
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"]["C2A_staged"].pop("default_hold_T_C")
    manager = CampaignManager(setpoints)

    with pytest.raises(
        ValueError,
        match=r"Missing numeric campaign setpoint: C2A_staged\.default_hold_T_C",
    ):
        manager.get_temp_target(
            CampaignPhase.C2A_STAGED,
            7,
            MeltState(campaign=CampaignPhase.C2A_STAGED, campaign_hour=7),
        )


def test_c2a_staged_missing_stage_ramp_refuses_with_setpoint_key() -> None:
    setpoints = deepcopy(_setpoints())
    _stage(setpoints, "alkali_early_fe").pop("ramp_rate_C_per_hr")
    manager = CampaignManager(setpoints)

    with pytest.raises(
        ValueError,
        match=r"Missing numeric campaign setpoint: C2A_staged\.stages\.alkali_early_fe\.ramp_rate_C_per_hr",
    ):
        manager.get_temp_target(
            CampaignPhase.C2A_STAGED,
            0,
            MeltState(campaign=CampaignPhase.C2A_STAGED, campaign_hour=0),
        )


@pytest.mark.parametrize("config_key", ["C0", "C2B"])
def test_configured_temperature_ramp_refuses_nonfinite_values(config_key):
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"][config_key]["dT_dt_C_per_hr"] = float("nan")
    phase = CampaignPhase[config_key]

    with pytest.raises(ValueError, match="Invalid campaign thermal"):
        CampaignManager(setpoints).get_temp_target(
            phase,
            0,
            MeltState(campaign=phase, temperature_C=25.0),
        )


def test_melt_pressure_validator_refuses_partial_pressure_above_total():
    melt = MeltState()
    melt.p_total_mbar = 1.0
    melt.pO2_mbar = 1.0 + 5e-10
    melt.validate_melt_pressures()

    melt.pO2_mbar = 1.1
    with pytest.raises(ValueError, match="melt_pressure_partial_exceeds_total"):
        melt.validate_melt_pressures()


def test_configure_campaign_refuses_pO2_default_above_total():
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"]["C2B"]["pO2_mbar_default"] = 2.0
    setpoints["campaigns"]["C2B"]["p_total_mbar_default"] = 1.0

    with pytest.raises(ValueError, match="melt_pressure_partial_exceeds_total"):
        CampaignManager(setpoints).configure_campaign(MeltState(), CampaignPhase.C2B)


def test_present_but_nonnumeric_campaign_pressure_default_raises():
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"]["C2B"]["pO2_mbar_default"] = "bad"

    with pytest.raises(ValueError, match="Invalid numeric campaign setpoint"):
        CampaignManager(setpoints).configure_campaign(MeltState(), CampaignPhase.C2B)


def test_mre_baseline_pressure_defaults_are_yaml_sourced_and_decoupled():
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"]["C5"]["pO2_mbar_default"] = 12.0
    setpoints["campaigns"]["C5"]["p_total_mbar_default"] = 12.0

    melt = MeltState()
    CampaignManager(setpoints).configure_campaign(melt, CampaignPhase.MRE_BASELINE)

    assert melt.pO2_mbar == pytest.approx(50.0)
    assert melt.p_total_mbar == pytest.approx(50.0)

    setpoints["campaigns"]["mre_baseline"]["pO2_mbar_default"] = 40.0
    setpoints["campaigns"]["mre_baseline"]["p_total_mbar_default"] = 41.0

    melt = MeltState()
    CampaignManager(setpoints).configure_campaign(melt, CampaignPhase.MRE_BASELINE)

    assert melt.pO2_mbar == pytest.approx(40.0)
    assert melt.p_total_mbar == pytest.approx(41.0)


def test_c2a_staged_gas_cover_switch_is_stage_atomic():
    setpoints = deepcopy(_setpoints())
    _stage(setpoints, "alkali_early_fe").update({
        "gas_cover_mode": "po2_hold",
        "pO2_mbar": 1.0,
        "p_total_mbar": 1.0,
    })
    _stage(setpoints, "sio_window").update({
        "gas_cover_mode": "pn2_sweep",
        "pO2_mbar": 1.0e-6,
        "p_total_mbar": 10.000001,
    })
    manager = CampaignManager(setpoints)
    melt = MeltState(campaign=CampaignPhase.C2A_STAGED)

    manager.configure_campaign(melt, CampaignPhase.C2A_STAGED)
    first = dict(manager.last_c2a_staged_gas_control or {})
    assert melt.atmosphere is Atmosphere.CONTROLLED_O2
    assert first["stage_name"] == "alkali_early_fe"
    assert first["gas_cover_mode"] == "po2_hold"
    assert first["pO2_mbar"] == pytest.approx(1.0)
    assert first["p_total_mbar"] == pytest.approx(1.0)
    assert melt.background_gas_species == ""
    assert melt.background_gas_mole_fraction == pytest.approx(0.0)

    melt.campaign_hour = 4
    manager.apply_c2a_staged_gas_controls(melt)
    second = dict(manager.last_c2a_staged_gas_control or {})
    assert melt.atmosphere is Atmosphere.PN2_SWEEP
    assert second["stage_name"] == "sio_window"
    assert second["gas_cover_mode"] == "pn2_sweep"
    assert second["pO2_mbar"] == pytest.approx(1.0e-6)
    assert second["p_total_mbar"] == pytest.approx(10.000001)
    assert second["pN2_mbar"] == pytest.approx(10.0)
    assert melt.background_gas_species == "N2"
    assert melt.background_gas_mole_fraction == pytest.approx(1.0)


def test_c2a_staged_pn2_sweep_trace_po2_is_not_silent_or_phantom_o2():
    setpoints = deepcopy(_setpoints())
    _stage(setpoints, "alkali_early_fe").update({
        "gas_cover_mode": "pn2_sweep",
        "pO2_mbar": 1.0e-6,
        "p_total_mbar": 10.000001,
    })
    manager = CampaignManager(setpoints)
    melt = MeltState(campaign=CampaignPhase.C2A_STAGED)

    manager.configure_campaign(melt, CampaignPhase.C2A_STAGED)
    gas = dict(manager.last_c2a_staged_gas_control or {})

    assert melt.atmosphere is Atmosphere.PN2_SWEEP
    assert melt.pO2_mbar == pytest.approx(1.0e-6)
    assert melt.p_total_mbar == pytest.approx(10.000001)
    assert gas["pN2_mbar"] == pytest.approx(10.0)
    assert gas["pn2_band_action"] == ""


# SC-67 adjudication (t-185 x wave-06-pressure fold): an out-of-band configured
# p_total is a strandable operating point with a COMPUTABLE non-empty feasible
# band, so it adjusts to the nearest band edge with loud provenance
# (pn2_band_action + requested_p_total_mbar) instead of refusing; the typed
# refusal is reserved for the genuinely empty/invalid band (test below).
@pytest.mark.parametrize(
    ("p_total_mbar", "expected_pN2", "expected_action"),
    [(1.25, 5.0, "clamped_low"), (20.25, 15.0, "clamped_high")],
)
def test_c2a_staged_pn2_sweep_outside_operating_band_adjusts_with_provenance(
    p_total_mbar,
    expected_pN2,
    expected_action,
):
    setpoints = deepcopy(_setpoints())
    _stage(setpoints, "alkali_early_fe").update({
        "gas_cover_mode": "pn2_sweep",
        "pO2_mbar": 0.25,
        "p_total_mbar": p_total_mbar,
    })
    manager = CampaignManager(setpoints)
    melt = MeltState(campaign=CampaignPhase.C2A_STAGED)

    manager.configure_campaign(melt, CampaignPhase.C2A_STAGED)
    gas = dict(manager.last_c2a_staged_gas_control or {})

    assert melt.atmosphere is Atmosphere.PN2_SWEEP
    assert gas["pN2_mbar"] == pytest.approx(expected_pN2)
    assert gas["p_total_mbar"] == pytest.approx(0.25 + expected_pN2)
    assert gas["requested_p_total_mbar"] == pytest.approx(p_total_mbar)
    assert gas["pn2_band_action"] == expected_action


def test_c2a_staged_pn2_sweep_recovers_when_total_does_not_exceed_po2():
    setpoints = deepcopy(_setpoints())
    _stage(setpoints, "alkali_early_fe").update({
        "gas_cover_mode": "pn2_sweep",
        "pO2_mbar": 2.0,
        "p_total_mbar": 1.0,
    })

    manager = CampaignManager(setpoints)
    melt = MeltState(campaign=CampaignPhase.C2A_STAGED)
    manager.configure_campaign(melt, CampaignPhase.C2A_STAGED)
    gas = dict(manager.last_c2a_staged_gas_control or {})

    assert gas["requested_p_total_mbar"] == pytest.approx(1.0)
    assert gas["pN2_mbar"] == pytest.approx(5.0)
    assert gas["p_total_mbar"] == pytest.approx(7.0)
    assert gas["pn2_band_action"] == "clamped_low"
    assert melt.p_total_mbar == pytest.approx(7.0)


def test_c2a_staged_pn2_sweep_refuses_invalid_empty_clamp_band(monkeypatch):
    setpoints = deepcopy(_setpoints())
    _stage(setpoints, "alkali_early_fe").update({
        "gas_cover_mode": "pn2_sweep",
        "pO2_mbar": 2.0,
        "p_total_mbar": 1.0,
    })
    monkeypatch.setattr(campaigns_module, "C2A_STAGED_PN2_SWEEP_MIN_MBAR", 15.0)
    monkeypatch.setattr(campaigns_module, "C2A_STAGED_PN2_SWEEP_MAX_MBAR", 5.0)

    # Empty feasible band = the SC-67 boundary case: typed refusal (feeds the
    # runner failure envelope), not adjustment.
    with pytest.raises(
        CampaignPressureSetpointRefusal,
        match="operating band is empty or invalid",
    ):
        CampaignManager(setpoints).configure_campaign(
            MeltState(),
            CampaignPhase.C2A_STAGED,
        )


@pytest.mark.parametrize(
    ("stage_patch", "message"),
    [
        ({"gas_cover_mode": "bad"}, "C2A_staged.stages.alkali_early_fe.gas_cover_mode"),
        (
            {"gas_cover_mode": "po2_hold", "pO2_mbar": 0.0, "p_total_mbar": 1.0},
            "C2A_staged.stages.alkali_early_fe.pO2_mbar",
        ),
        (
            {"gas_cover_mode": "po2_hold", "pO2_mbar": 2.0, "p_total_mbar": 1.0},
            "C2A_staged.stages.alkali_early_fe.p_total_mbar",
        ),
    ],
)
def test_c2a_staged_invalid_gas_schedule_fails_loud(stage_patch, message):
    setpoints = deepcopy(_setpoints())
    _stage(setpoints, "alkali_early_fe").update(stage_patch)

    with pytest.raises(ValueError, match=message):
        CampaignManager(setpoints).configure_campaign(
            MeltState(),
            CampaignPhase.C2A_STAGED,
        )


def test_c2a_staged_stale_max_hold_is_recomputed_with_info_note():
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"]["C2A_staged"]["max_hold_hr"] = 999
    manager = CampaignManager(setpoints)

    applied = manager._configured_staged_max_hold_hr(CampaignPhase.C2A_STAGED)
    expected = sum(
        max(1, int(float(stage.get("duration_h", 1.0))))
        for stage in setpoints["campaigns"]["C2A_staged"]["stages"]
    )

    assert applied == pytest.approx(expected)
    assert manager.last_c2a_staged_max_hold_adjustment == {
        "severity": "info",
        "code": "c2a_staged_max_hold_recomputed",
        "field": "C2A_staged.max_hold_hr",
        "requested_max_hold_hr": 999.0,
        "applied_max_hold_hr": expected,
        "derivation": "sum(max(1, int(stage.duration_h)))",
    }

    melt = MeltState(campaign=CampaignPhase.C2A_STAGED)
    manager.apply_c2a_staged_gas_controls(melt)
    assert manager.last_c2a_staged_gas_control["max_hold_adjustment"] == (
        manager.last_c2a_staged_max_hold_adjustment
    )


def test_c2a_staged_max_hold_adjustment_reaches_snapshot_and_runner_diagnostic():
    run = PyrolysisRun(
        feedstock_id="mars_basalt",
        campaign="C2A_staged",
        hours=1,
        additives_kg={"C": 30.0},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        setpoints_patch={
            "campaigns": {"C2A_staged": {"max_hold_hr": 999}},
        },
        run_metadata_overrides={
            "started_at_utc": "2026-07-11T00:00:00Z",
            "kernel_commit_sha": "c2a-max-hold-provenance-test",
        },
    )

    execution = RunExecutor().execute(run._session_config())
    snapshot_adjustment = execution.snapshots[0].c2a_staged_gas[
        "max_hold_adjustment"
    ]

    assert snapshot_adjustment["code"] == "c2a_staged_max_hold_recomputed"
    assert snapshot_adjustment["requested_max_hold_hr"] == pytest.approx(999.0)
    assert snapshot_adjustment["applied_max_hold_hr"] == 9
    assert (
        execution.per_hour[0]["c2a_staged_gas"]["max_hold_adjustment"]
        == snapshot_adjustment
    )
    runner_adjustment = run.run()["per_hour_summary"][0]["c2a_staged_gas"][
        "max_hold_adjustment"
    ]
    assert runner_adjustment == snapshot_adjustment


@pytest.mark.parametrize("stages", [[], None, ["invalid-stage"]])
def test_c2a_staged_max_hold_refuses_genuinely_invalid_stage_lists(stages):
    setpoints = deepcopy(_setpoints())
    setpoints["campaigns"]["C2A_staged"]["stages"] = stages

    with pytest.raises(ValueError, match=r"C2A_staged\.stages"):
        CampaignManager(setpoints)._configured_staged_max_hold_hr(
            CampaignPhase.C2A_STAGED
        )


@pytest.mark.parametrize("stages_value", [None, [], "drop_key"])
def test_c2a_staged_without_stage_schedule_noops_gas_controls(stages_value):
    # A minimal/legacy C2A_staged config (no per-stage schedule) must configure
    # without raising and leave gas cover untouched — per-stage gas control is a
    # no-op when there is no schedule, preserving pre-schedule behavior.
    setpoints = deepcopy(_setpoints())
    c2a = setpoints["campaigns"]["C2A_staged"]
    if stages_value == "drop_key":
        c2a.pop("stages", None)
    else:
        c2a["stages"] = stages_value

    mgr = CampaignManager(setpoints)
    melt = MeltState()
    mgr.configure_campaign(melt, CampaignPhase.C2A_STAGED)

    assert mgr.last_c2a_staged_gas_control is None
    # Idempotent re-application also no-ops rather than raising.
    mgr.apply_c2a_staged_gas_controls(melt, CampaignPhase.C2A_STAGED)
    assert mgr.last_c2a_staged_gas_control is None


def test_configure_campaign_reset_does_not_clobber_lab_schedule_background():
    setpoints = _setpoints()
    manager = CampaignManager(setpoints)
    manager.overrides["C3_NA"] = {
        "lab_schedule": _n2_lab_schedule(),
        "lab_schedule_pO2_setpoint_mbar": 1.0,
    }
    melt = MeltState(campaign=CampaignPhase.C3_NA)
    melt.background_gas_species = "Ar"
    melt.background_gas_mole_fraction = 0.25

    manager.configure_campaign(melt, CampaignPhase.C3_NA)

    assert melt.atmosphere is Atmosphere.CONTROLLED_O2
    assert melt.pO2_mbar == pytest.approx(1.0)
    assert melt.p_total_mbar == pytest.approx(10.0)
    assert melt.background_gas_species == "N2"
    assert melt.background_gas_mole_fraction == pytest.approx(0.8)


def test_zero_o2_argon_lab_schedule_does_not_synthesize_n2():
    from simulator.overhead import OverheadGasModel
    from simulator.state import CondensationTrain, EvaporationFlux

    schedule = deepcopy(_n2_lab_schedule())
    schedule["gas_boundary"]["background_gas"]["species"] = "Ar"
    schedule["gas_boundary"]["background_gas"]["mole_fraction"] = 0.8
    manager = CampaignManager(_setpoints())
    manager.overrides["C2A"] = {"lab_schedule": schedule}
    melt = MeltState()

    manager.configure_campaign(melt, CampaignPhase.C2A)

    assert melt.pO2_mbar == pytest.approx(0.0)
    assert melt.p_total_mbar == pytest.approx(10.0)
    assert melt.atmosphere is Atmosphere.CONTROLLED_O2
    assert melt.background_gas_species == "Ar"

    gas = OverheadGasModel({
        "enabled": True,
        # Explicit test geometry, not a product default: finite headspace must
        # refuse when neither equipment nor the caller supplies a volume.
        "volume_m3": 1.0,
    }).update(EvaporationFlux(), melt, CondensationTrain.create_default())

    assert gas.composition.get("Ar", 0.0) == pytest.approx(8.0)
    assert gas.composition.get("N2", 0.0) == pytest.approx(0.0)
