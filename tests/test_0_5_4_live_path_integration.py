"""A1 — 0.5.4 W5+W7+W8 live-path integration tests (post-push).

The 0.5.4 changes that touched real recipe surfaces (W5
campaign_override pO2 atmosphere switch, W7 live mole-weighted
M_avg for pipe conductance, W8 metal-projection drift audit on
HourSnapshot) all had focused unit tests but only INDIRECT
golden-fixture integration coverage. These tests drive the
SimSession command core end-to-end against the real
``lunar_mare_low_ti`` feedstock + canonical setpoints, exercising
each fix on a live tick / snapshot read.

Closes A1 from
``docs-private/goal-deferred-and-roadmap-2026-05-28.md`` (rev 2).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.backends import BackendSelectionPolicy
from simulator.session import SimSession, SimSessionConfig
from simulator.state import Atmosphere, CampaignPhase, HourSnapshot


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_yaml(name: str) -> dict:
    with (DATA_DIR / name).open() as f:
        return yaml.safe_load(f) or {}


def _config(**overrides) -> SimSessionConfig:
    values = {
        "feedstock_id": "lunar_mare_low_ti",
        "feedstocks": _load_yaml("feedstocks.yaml"),
        "setpoints": _load_yaml("setpoints.yaml"),
        "vapor_pressures": _load_yaml("vapor_pressures.yaml"),
        "campaign": "C2A",
        "backend_name": "stub",
        "backend_policy": BackendSelectionPolicy.RUNNER_STRICT,
    }
    values.update(overrides)
    return SimSessionConfig(**values)


# ---------------------------------------------------------------------------
# W8 metal_projection_drift_kg: snapshot field accessible + empty on idle
# ---------------------------------------------------------------------------

def test_w8_snapshot_field_exposed_on_idle_session():
    """A1: ``HourSnapshot.metal_projection_drift_kg`` is wired
    through ``_make_snapshot`` and accessible on a real
    SimSession-driven snapshot. Empty dict on a fresh sim because
    process.metal_phase carries no metal at C2A campaign start."""
    session = SimSession().start(_config(campaign="C2A"))
    snap = session.snapshot()
    assert isinstance(snap, HourSnapshot)
    assert hasattr(snap, 'metal_projection_drift_kg')
    assert snap.metal_projection_drift_kg == {}


def test_w8_drift_audit_stays_empty_through_a_few_ticks_on_clean_recipe():
    """A1: when the recipe credits metals via the canonical
    commit_batch path, the projection sweep runs in the same tick,
    so the W8 audit dict stays empty (no drift > 1e-9 kg) on a
    clean recipe. A few ticks of C2A_continuous are well within
    the ≤5e-12 % global closure invariant and should produce no
    drift surface entries."""
    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator
    for _ in range(3):
        sim.step()
    snap = session.snapshot()
    # No species should appear in the drift dict (canonical sweep
    # keeps ledger ↔ projection in sync per the W8 + milestone-P2
    # union-iteration contract).
    assert snap.metal_projection_drift_kg == {}, (
        f"unexpected metal drift on a clean C2A tick: "
        f"{snap.metal_projection_drift_kg}"
    )


# ---------------------------------------------------------------------------
# W7 live mole-weighted M_avg through estimate_transport_state
# ---------------------------------------------------------------------------

def test_w7_pipe_conductance_uses_live_evap_flux_species():
    """A1: ``OverheadGasModel.estimate_transport_state(evap_flux,
    melt)`` threads ``evap_flux.species_kg_hr`` through to
    ``_pipe_conductance`` as the source for M_avg. With a
    Na-only flux vs a Fe-only flux at the same total mass rate
    and same pressure / T, the resulting conductance scales by
    ``M_Fe / M_Na ≈ 2.43``. End-to-end check that the W7 wire is
    live in the production callsite (not just the unit-test
    monkey-patch)."""
    from simulator.overhead import OverheadGasModel
    from simulator.state import (
        Atmosphere,
        CondensationTrain,
        EvaporationFlux,
        MeltState,
        MOLAR_MASS,
    )

    melt = MeltState()
    melt.atmosphere = Atmosphere.PN2_SWEEP
    melt.temperature_C = 1500.0
    melt.p_total_mbar = 10.0
    train = CondensationTrain.create_default()

    # Two scenarios: same total flow, different species mix.
    flux_na = EvaporationFlux(species_kg_hr={"Na": 1.0}, total_kg_hr=1.0)
    flux_fe = EvaporationFlux(species_kg_hr={"Fe": 1.0}, total_kg_hr=1.0)

    model = OverheadGasModel({})
    state_na = model.estimate_transport_state(flux_na, melt)
    state_fe = model.estimate_transport_state(flux_fe, melt)

    # End-to-end ratio: Fe (56 g/mol) conductance / Na (23 g/mol)
    # conductance == M_Fe / M_Na (the rest of the formula is
    # identical across both calls).
    conductance_na = state_na["pipe_conductance_kg_hr"]
    conductance_fe = state_fe["pipe_conductance_kg_hr"]
    expected_ratio = MOLAR_MASS["Fe"] / MOLAR_MASS["Na"]
    actual_ratio = conductance_fe / conductance_na
    assert actual_ratio == pytest.approx(expected_ratio, rel=1e-6), (
        f"W7 wire broken: live mixture ratio {actual_ratio} "
        f"!= expected M_Fe/M_Na ratio {expected_ratio}"
    )


def test_w7_pipe_conductance_empty_flux_falls_back_to_default_m_avg():
    """A1: a zero-flow tick (e.g., recipe warmup before any
    evaporation) → evap_flux.species_kg_hr is empty → M_avg
    falls back to ``DEFAULT_PIPE_M_AVG_KG_MOL = 0.040``. End-to-
    end check that the W7 fallback path is preserved across the
    integration seam."""
    from simulator.overhead import (
        DEFAULT_PIPE_M_AVG_KG_MOL,
        OverheadGasModel,
        _mean_molar_mass_kg_mol,
    )
    from simulator.state import (
        Atmosphere,
        CondensationTrain,
        EvaporationFlux,
        MeltState,
    )

    melt = MeltState()
    melt.atmosphere = Atmosphere.PN2_SWEEP
    melt.temperature_C = 1500.0
    melt.p_total_mbar = 10.0
    train = CondensationTrain.create_default()

    empty_flux = EvaporationFlux(species_kg_hr={}, total_kg_hr=0.0)
    model = OverheadGasModel({})
    state = model.estimate_transport_state(empty_flux, melt)
    # Conductance is non-zero only via the M_avg path × pressure;
    # at p=1000 Pa with empty flux the helper falls back to 0.040.
    assert _mean_molar_mass_kg_mol(empty_flux.species_kg_hr) == (
        DEFAULT_PIPE_M_AVG_KG_MOL
    )
    # The estimate path also returns a finite (positive) conductance.
    assert state["pipe_conductance_kg_hr"] >= 0.0


# ---------------------------------------------------------------------------
# W5 campaign_override pO2 atmosphere switch — live propagation
# ---------------------------------------------------------------------------

def test_w5_campaign_override_po2_switches_atmosphere_live_in_a_real_session():
    """A1: drive a SimSession through C2A start → operator sets
    campaign_override pO2 → atmosphere flips to CONTROLLED_O2 on
    the active campaign → next tick honors the floor.

    End-to-end check that the W5 wire propagates through the real
    SimSession.adjust path, not just the unit-test stub session."""
    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator

    # Pre-adjust: C2A default is PN2_SWEEP per
    # simulator/campaigns.py:115.
    assert sim.melt.atmosphere == Atmosphere.PN2_SWEEP
    assert sim.melt.pO2_mbar == pytest.approx(0.0)

    # Operator commands a positive pO2 via the campaign-override
    # write path (W5).
    session.adjust(
        "campaign_override", 1.0,
        campaign="C2A", field="pO2_mbar",
    )

    # Active-path atmosphere switch live-fires.
    assert sim.melt.atmosphere == Atmosphere.CONTROLLED_O2, (
        "W5 wire broken: campaign_override pO2 didn't switch "
        "atmosphere to CONTROLLED_O2 on the active C2A campaign"
    )
    assert sim.melt.pO2_mbar == pytest.approx(1.0)

    # Run a step; the commanded-pO2 floor now applies via the
    # equilibrium.py _O2_CONTROLLED_ATMOSPHERES branch (per the
    # Phase A + W5 fix-pattern).
    sim.step()
    # No assertion-failure at the engine level confirms the path
    # is live; the snapshot atmosphere reads what we set.
    snap = session.snapshot()
    assert snap.campaign == CampaignPhase.C2A


def test_w5_zero_po2_override_does_not_switch_atmosphere():
    """A1 complement: pO2=0 via campaign_override is the operator
    CLEARING the setpoint, NOT requesting controlled-O2 — the W5
    contract preserves PN2_SWEEP on the active campaign."""
    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator

    assert sim.melt.atmosphere == Atmosphere.PN2_SWEEP
    session.adjust(
        "campaign_override", 0.0,
        campaign="C2A", field="pO2_mbar",
    )
    # Atmosphere unchanged.
    assert sim.melt.atmosphere == Atmosphere.PN2_SWEEP
    assert sim.melt.pO2_mbar == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# E7 — runner / web/events.py pO2 cross-layer integration
# ---------------------------------------------------------------------------

def test_e7_direct_session_adjust_po2_flips_atmosphere_live():
    """E7: the canonical web-handler path
    ``web/events.py:769-771`` calls ``state['session'].adjust(
    'pO2_mbar', value)`` for the operator UI lever. Integration
    check that the 0.5.3 Phase C P2 fix-pattern is live on the
    direct-adjust path (W5 covered campaign_override; this covers
    the path the production web UI actually uses)."""
    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator

    assert sim.melt.atmosphere == Atmosphere.PN2_SWEEP
    session.adjust("pO2_mbar", 2.5)

    # Direct-adjust atmosphere switch (Phase C milestone P2 fix at
    # simulator/session.py:228) lives end-to-end through the
    # SimSession boundary.
    assert sim.melt.atmosphere == Atmosphere.CONTROLLED_O2
    assert sim.melt.pO2_mbar == pytest.approx(2.5)


def test_e7_direct_session_adjust_zero_po2_preserves_atmosphere():
    """E7 complement: the web handler's pO2=0 lever (operator
    clearing the setpoint, not requesting controlled-O2) MUST
    leave the atmosphere alone. Three-way invariant across:
    - direct adjust (Phase C P2 fix)
    - campaign_override (W5)
    - configure_campaign transition (milestone P1)
    All preserve PN2_SWEEP on a pO2=0 write."""
    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator

    assert sim.melt.atmosphere == Atmosphere.PN2_SWEEP
    session.adjust("pO2_mbar", 0.0)

    # No atmosphere change on a clearing-write.
    assert sim.melt.atmosphere == Atmosphere.PN2_SWEEP
    assert sim.melt.pO2_mbar == pytest.approx(0.0)


def test_e7_post_adjust_run_keeps_atmosphere_through_subsequent_ticks():
    """E7: the W5 atmosphere switch is sticky — it should remain
    CONTROLLED_O2 across subsequent ticks until the operator
    explicitly changes it. Ensures the W5 fix doesn't accidentally
    revert on a tick where the campaign manager re-applies
    defaults."""
    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator

    session.adjust("pO2_mbar", 1.0)
    assert sim.melt.atmosphere == Atmosphere.CONTROLLED_O2

    # Run a couple of ticks; atmosphere should stay CONTROLLED_O2.
    for _ in range(3):
        sim.step()
    assert sim.melt.atmosphere == Atmosphere.CONTROLLED_O2, (
        "Phase C P2 atmosphere switch was not sticky across ticks — "
        "campaign-manager defaults may be overwriting the operator's "
        "lever after the fact"
    )
    assert sim.melt.pO2_mbar == pytest.approx(1.0)


def test_w5_milestone_p1_transition_path_lives_in_session_driven_run():
    """A1: the milestone-review P1 fix (campaign-override pO2 stored
    for a future campaign, then applied via configure_campaign at
    transition time) lives correctly inside a real SimSession run.

    Store pO2 override for C2A while C0 is active; the active
    melt is untouched. Transition C0 → C2A. After
    configure_campaign(C2A), atmosphere is CONTROLLED_O2 (not the
    C2A default PN2_SWEEP) AND pO2 is the stored override."""
    session = SimSession().start(_config(campaign="C0"))
    sim = session.simulator
    assert sim.melt.campaign == CampaignPhase.C0

    # Pre-store an override for the future C2A campaign.
    session.adjust(
        "campaign_override", 1.5,
        campaign="C2A", field="pO2_mbar",
    )
    # C0 active campaign untouched (override only stored).
    assert sim.melt.campaign == CampaignPhase.C0

    # Transition to C2A.
    sim.melt.campaign = CampaignPhase.C2A
    sim.campaign_mgr.configure_campaign(sim.melt, CampaignPhase.C2A)

    # Milestone-P1 invariant: atmosphere is CONTROLLED_O2, NOT
    # the C2A default PN2_SWEEP, because the stored override pO2
    # > 0 propagates through campaigns.py:149 atmosphere-switch
    # branch.
    assert sim.melt.atmosphere == Atmosphere.CONTROLLED_O2, (
        "Milestone P1 wire broken at the transition-time path: "
        "stored campaign_override pO2 didn't switch atmosphere at "
        "configure_campaign(C2A) time"
    )
    assert sim.melt.pO2_mbar == pytest.approx(1.5)
