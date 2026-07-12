"""Unit tests for the synchronous SimSession command core."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import simulator.session as session_module
from simulator.backends import BackendSelectionPolicy
from simulator.campaigns import CampaignManager
from simulator.feedstock_guard import is_blocked_feedstock
from simulator.session import (
    DecisionPolicy,
    SimSession,
    SimSessionConfig,
    drive_auto_apply,
)
from simulator.state import CampaignPhase, DecisionPoint, DecisionType, HourSnapshot


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
        "campaign": "C0",
        "backend_name": "stub",
        "backend_policy": BackendSelectionPolicy.RUNNER_STRICT,
    }
    values.update(overrides)
    return SimSessionConfig(**values)


def test_session_passes_stage0_subprocess_inputs_to_resolver(monkeypatch):
    calls: list[dict] = []

    def record_resolve(*_args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("stop after resolver inputs")

    monkeypatch.setattr(session_module, "resolve_backend", record_resolve)

    with pytest.raises(RuntimeError, match="stop after resolver inputs"):
        SimSession().start(
            _config(
                backend_name="alphamelts",
                feedstock_id="spinel-feed",
                feedstocks={"spinel-feed": {"spinel_rich": True}},
            )
        )

    assert calls
    assert calls[0]["feedstock_id"] == "spinel-feed"
    assert calls[0]["feedstocks"] == {"spinel-feed": {"spinel_rich": True}}
    assert calls[0]["stage0_subprocess_required"] is True


def test_session_rejects_metallic_real_backend_before_resolver(monkeypatch):
    def fail_resolve(*_args, **_kwargs):
        raise AssertionError("backend resolver must not run for non-silicate feedstock")

    monkeypatch.setattr(session_module, "resolve_backend", fail_resolve)

    with pytest.raises(
        RuntimeError,
        match=(
            "real_backend_out_of_domain: non_silicate_feedstock: "
            "feedstock 'm_type_metallic_phase'"
        ),
    ):
        SimSession().start(
            _config(
                backend_name="alphamelts",
                feedstock_id="m_type_metallic_phase",
            )
        )


class _FakeSim:
    def __init__(
        self,
        *,
        summaries: list[dict] | None = None,
        decision_after_step: DecisionPoint | None = None,
    ) -> None:
        self.melt = SimpleNamespace(
            hour=0,
            campaign=CampaignPhase.C0,
            pO2_mbar=0.0,
            stir_factor=1.0,
        )
        self.campaign_mgr = SimpleNamespace(c4_max_temp_C=1670.0, overrides={})
        self.c4_max_temp_C = 1670.0
        self._last_backend_error = ""
        self._last_campaign_summary = None
        self.pending_decision = None
        self.paused_for_decision = False
        self.applied_decisions: list[tuple[DecisionType, str]] = []
        self._summaries = list(summaries or [])
        self._decision_after_step = decision_after_step

    def step(self) -> HourSnapshot:
        self.melt.hour += 1
        if self._summaries:
            self._last_campaign_summary = self._summaries.pop(0)
        if self._decision_after_step is not None:
            self.pending_decision = self._decision_after_step
            self.paused_for_decision = True
            self._decision_after_step = None
        return self._make_snapshot()

    def apply_decision(self, decision_type: DecisionType, choice: str) -> None:
        self.applied_decisions.append((decision_type, choice))
        self.pending_decision = None
        self.paused_for_decision = False

    def is_complete(self) -> bool:
        return False

    def product_ledger(self) -> dict[str, float]:
        return {}

    def _make_snapshot(self) -> HourSnapshot:
        return HourSnapshot(
            hour=self.melt.hour,
            campaign=self.melt.campaign,
            temperature_C=25.0 + self.melt.hour,
        )


def _fake_session(fake: _FakeSim) -> SimSession:
    session = SimSession()
    session._sim = fake
    return session


def test_start_queries_and_result_document_factory():
    session = SimSession().start(
        _config(
            result_document_factory=lambda active: {
                "hour": active.snapshot().hour,
                "campaign": active.snapshot().campaign.name,
            }
        )
    )

    assert session.pending_decision() is None
    assert not session.is_complete()
    assert session.snapshot().campaign == CampaignPhase.C0
    assert session.result_document() == {"hour": 0, "campaign": "C0"}


def test_start_refuses_status_blocked_feedstock_selection():
    feedstocks = _load_yaml("feedstocks.yaml")
    blocked_key, blocked_entry = next(
        (key, entry)
        for key, entry in feedstocks.items()
        if is_blocked_feedstock(entry)
    )

    with pytest.raises(RuntimeError) as excinfo:
        SimSession().start(_config(feedstock_id=blocked_key))

    message = str(excinfo.value)
    assert "BlockedFeedstockError" in message
    assert blocked_key in message
    assert blocked_entry["status"] in message
    assert blocked_entry["blocked_reason"] in message


def test_adjust_handles_only_session_parameters_with_live_override_effects():
    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator

    session.adjust("stir_factor", 1.25)
    session.adjust("pO2_mbar", 2.5)
    session.adjust("c4_max_temp", 1660.0)
    session.adjust("campaign_override", 1.5, campaign="C2A", field="stir_factor")
    session.adjust("campaign_override", 0.75, campaign="C2A", field="pO2_mbar")

    assert sim.melt.stir_factor == pytest.approx(1.5)
    assert sim.melt.pO2_mbar == pytest.approx(0.75)
    assert sim.c4_max_temp_C == pytest.approx(1660.0)
    assert sim.campaign_mgr.c4_max_temp_C == pytest.approx(1660.0)
    assert sim.campaign_mgr.overrides["C2A"]["stir_factor"] == pytest.approx(1.5)
    assert sim.campaign_mgr.overrides["C2A"]["pO2_mbar"] == pytest.approx(0.75)
    with pytest.raises(ValueError, match="unsupported"):
        session.adjust("speed", 0.0)


def test_session_adjust_rejects_nonfinite_numeric_controls():
    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator

    with pytest.raises(ValueError, match="must be finite"):
        session.adjust(
            "campaign_override",
            float("nan"),
            campaign="C2A",
            field="hold_temp_C",
        )
    with pytest.raises(ValueError, match="must be finite"):
        session.adjust("pO2_mbar", float("inf"))

    assert "hold_temp_C" not in sim.campaign_mgr.overrides.get("C2A", {})


def test_session_adjust_clamps_absurd_stir_factor_to_physical_ceiling():
    """Operator-boundary clamp: a wildly out-of-range ``stir_factor``
    override (e.g. an auto-tuner that proposes ``100`` because it
    monotonically improves yield) MUST NOT slosh the melt right out of
    its pot. The canonical ``clamp_stir_factor`` ceiling is the
    "melt-flying-out-of-the-pot" upper bound (``MAX_STIR_FACTOR = 10``,
    per ``simulator/state.py``); session.adjust + campaign_override
    both route through it. 0.5.2 Phase B P1."""
    from simulator.state import MAX_STIR_FACTOR

    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator

    session.adjust("stir_factor", 100.0)
    assert sim.melt.stir_factor == pytest.approx(MAX_STIR_FACTOR)

    session.adjust(
        "campaign_override", 250.0,
        campaign="C2A", field="stir_factor",
    )
    assert sim.melt.stir_factor == pytest.approx(MAX_STIR_FACTOR)
    # codex autoreview-r2 P3: overrides dict must also carry the
    # CLAMPED value (was pre-coerced to raw float before this fix,
    # leaving re-entry paths inconsistent with the operator contract).
    assert sim.campaign_mgr.overrides["C2A"]["stir_factor"] == pytest.approx(
        MAX_STIR_FACTOR
    )

    # Sub-laminar (legitimate "halve evap" operator control) passes through
    # unchanged — only the upper ceiling slosh-guard fires.
    session.adjust("stir_factor", 0.5)
    assert sim.melt.stir_factor == pytest.approx(0.5)

    # Negative values clamp to the fail-closed 0.0 (halt-evap signal).
    session.adjust("stir_factor", -1.0)
    assert sim.melt.stir_factor == pytest.approx(0.0)

    # Bool / string corrupt-input on the campaign_override path collapses
    # to 0.0 (fail-closed) instead of raising or lying via float coercion.
    session.adjust(
        "campaign_override", True,
        campaign="C2A", field="stir_factor",
    )
    assert sim.campaign_mgr.overrides["C2A"]["stir_factor"] == pytest.approx(0.0)
    session.adjust(
        "campaign_override", "bogus",
        campaign="C2A", field="stir_factor",
    )
    assert sim.campaign_mgr.overrides["C2A"]["stir_factor"] == pytest.approx(0.0)


def test_session_adjust_campaign_override_po2_switches_atmosphere_to_controlled_o2():
    """0.5.4 W5 (post-push P2, codex review + codex challenge
    convergent 2026-05-28): the campaign-override write path for
    ``field="pO2_mbar"`` must mirror the direct-adjust
    ``"pO2_mbar"`` path and switch ``melt.atmosphere`` to
    ``CONTROLLED_O2`` when the operator commands a positive pO2 on
    the active campaign. Pre-W5 the override wrote the setpoint but
    left the atmosphere in PN2_SWEEP, so the commanded-pO2 floor
    didn't fire under finite-headspace ON (only triggers in
    ``_O2_CONTROLLED_ATMOSPHERES``). This pins the convergent
    fix-pattern shared with Phase A wall-sweep + Phase C direct
    adjust."""
    from simulator.state import Atmosphere

    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator

    # Pre-condition: PN2_SWEEP is the default atmosphere on C2A.
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim.melt.pO2_mbar = 0.0

    # Positive pO2 via campaign_override → switch atmosphere
    session.adjust(
        "campaign_override", 1.0,
        campaign="C2A", field="pO2_mbar",
    )
    assert sim.melt.pO2_mbar == pytest.approx(1.0)
    assert sim.melt.atmosphere == Atmosphere.CONTROLLED_O2, (
        "campaign_override pO2_mbar must switch atmosphere to "
        "CONTROLLED_O2 so the commanded-pO2 floor + 1/sqrt(pO2) "
        "Ellingham SiO suppression go live (post-push P2)"
    )
    # Overrides dict also carries the value (re-entry consistency).
    assert sim.campaign_mgr.overrides["C2A"]["pO2_mbar"] == pytest.approx(1.0)


def test_session_adjust_campaign_override_po2_zero_leaves_atmosphere_alone():
    """W5 complement: pO2=0 is the operator CLEARING the setpoint, not
    requesting controlled-O2. Atmosphere must stay where it is — the
    direct-adjust path documents this contract; campaign_override
    must mirror it. A reset-to-zero is not a covert mode change."""
    from simulator.state import Atmosphere

    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator

    # Set a non-default atmosphere first (any non-CONTROLLED_O2 mode)
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim.melt.pO2_mbar = 1.0

    # Clearing the setpoint via override
    session.adjust(
        "campaign_override", 0.0,
        campaign="C2A", field="pO2_mbar",
    )
    assert sim.melt.pO2_mbar == pytest.approx(0.0)
    # Atmosphere NOT switched — operator only cleared the setpoint.
    assert sim.melt.atmosphere == Atmosphere.PN2_SWEEP


def test_session_adjust_campaign_override_po2_inactive_campaign_skips_live_update():
    """W5 invariant: the atmosphere-switch fix is gated on the
    override targeting the ACTIVE campaign (``sim.melt.campaign.name
    == campaign_name``). An override targeting a different campaign
    must NOT touch ``melt.atmosphere`` or ``melt.pO2_mbar`` — only
    write to the overrides dict for the future campaign transition.
    Mirrors the existing live-update gate logic; documented so a
    future refactor doesn't accidentally apply a future-campaign
    setpoint live."""
    from simulator.state import Atmosphere

    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim.melt.pO2_mbar = 0.0
    before_atm = sim.melt.atmosphere
    before_pO2 = sim.melt.pO2_mbar

    # Override targeting C2B (NOT the active C2A)
    session.adjust(
        "campaign_override", 1.0,
        campaign="C2B", field="pO2_mbar",
    )

    # Active-campaign melt state untouched.
    assert sim.melt.atmosphere == before_atm
    assert sim.melt.pO2_mbar == pytest.approx(before_pO2)
    # Future-campaign overrides dict still gets the value.
    assert sim.campaign_mgr.overrides["C2B"]["pO2_mbar"] == pytest.approx(1.0)


def test_campaign_transition_applies_stored_po2_override_with_atmosphere_switch():
    """0.5.4 milestone-review P1 fix (codex /challenge 2026-05-28):
    when a future-campaign override is STORED while a different
    campaign is active, the ``configure_campaign()`` application
    path at campaign transition time MUST mirror the active-path
    atmosphere switch (which is gated on positive pO2).

    Pre-fix: storing ``campaign_override pO2_mbar=1.0`` for C2A
    while C0 was active wrote the dict correctly, but when C2A
    later became active via ``configure_campaign()`` the override
    applied as a bare ``melt.pO2_mbar`` write — leaving
    ``melt.atmosphere = PN2_SWEEP`` (the default for C2A). The
    commanded-pO2 floor never fired because PN2_SWEEP isn't in
    ``_O2_CONTROLLED_ATMOSPHERES``.

    Now: after configure_campaign(C2A), both the pO2 AND the
    atmosphere reflect the operator's intent."""
    from simulator.state import Atmosphere, CampaignPhase, MeltState

    session = SimSession().start(_config(campaign="C0"))
    sim = session.simulator

    # Store an override for the inactive C2A campaign.
    session.adjust(
        "campaign_override", 1.5,
        campaign="C2A", field="pO2_mbar",
    )
    # Active C0 untouched (verified in the prior test).
    # Stored in overrides dict, ready for application.
    assert sim.campaign_mgr.overrides["C2A"]["pO2_mbar"] == pytest.approx(1.5)

    # Now transition to C2A via configure_campaign — the stored
    # override should be applied AND the atmosphere should switch
    # to CONTROLLED_O2.
    sim.melt.campaign = CampaignPhase.C2A
    sim.campaign_mgr.configure_campaign(sim.melt, CampaignPhase.C2A)

    # Override pO2 applied.
    assert sim.melt.pO2_mbar == pytest.approx(1.5)
    # Atmosphere switched (this is the P1 fix).
    assert sim.melt.atmosphere == Atmosphere.CONTROLLED_O2, (
        "campaigns.py:149 configure_campaign() must switch atmosphere "
        "to CONTROLLED_O2 when an override pO2 > 0 is applied — "
        "otherwise the commanded-pO2 floor stays disabled because "
        "the C2A default atmosphere PN2_SWEEP isn't in "
        "_O2_CONTROLLED_ATMOSPHERES (milestone-review P1)"
    )


def test_campaign_transition_zero_po2_override_leaves_atmosphere_at_default():
    """W5 P1 complement: ``pO2_mbar = 0`` is the operator CLEARING
    the setpoint, NOT requesting controlled-O2. The transition-time
    application must leave the default atmosphere alone in that
    case (same semantics as the active-path direct adjust)."""
    from simulator.state import Atmosphere, CampaignPhase

    session = SimSession().start(_config(campaign="C0"))
    sim = session.simulator

    # Store a clear-the-setpoint override.
    session.adjust(
        "campaign_override", 0.0,
        campaign="C2A", field="pO2_mbar",
    )
    assert sim.campaign_mgr.overrides["C2A"]["pO2_mbar"] == pytest.approx(0.0)

    # Transition to C2A.
    sim.melt.campaign = CampaignPhase.C2A
    sim.campaign_mgr.configure_campaign(sim.melt, CampaignPhase.C2A)

    # pO2 set to 0 by override.
    assert sim.melt.pO2_mbar == pytest.approx(0.0)
    # Atmosphere stays at C2A's documented default (PN2_SWEEP) —
    # operator only cleared the setpoint, not requested CONTROLLED_O2.
    assert sim.melt.atmosphere == Atmosphere.PN2_SWEEP


def test_session_adjust_stir_state_writes_canonical_2_axis_state():
    """0.5.3 Phase B: ``session.adjust("stir_state", {axial, radial})``
    is the canonical 2-axis writer. Drives both axes through
    ``clamp_stir_state``, replacing the whole ``melt.stir_state``
    dataclass. Operator intent: "set the stirring state to this".
    Legacy ``session.adjust("stir_factor", ...)`` writes axial only
    (backward-compat for pre-Phase-B web UI / auto-tuner callers)."""
    from simulator.state import MAX_STIR_FACTOR, StirState

    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator

    # Full 2-axis dict
    session.adjust("stir_state", {"axial": 4.0, "radial": 6.0})
    assert sim.melt.stir_state == StirState(axial=4.0, radial=6.0)
    assert sim.melt.stir_factor == 4.0  # legacy property still reads axial

    # Partial dict — missing axis defaults to 1.0 (laminar baseline)
    session.adjust("stir_state", {"radial": 8.0})
    assert sim.melt.stir_state.axial == 1.0
    assert sim.melt.stir_state.radial == 8.0

    # Scalar through stir_state path → axial only (same semantics as
    # the legacy stir_factor path, but the user used the new field)
    session.adjust("stir_state", 5.0)
    assert sim.melt.stir_state.axial == 5.0
    assert sim.melt.stir_state.radial == 1.0

    # Per-axis clamping: both axes honour MAX_STIR_FACTOR independently
    session.adjust("stir_state", {"axial": 100.0, "radial": 250.0})
    assert sim.melt.stir_state.axial == MAX_STIR_FACTOR
    assert sim.melt.stir_state.radial == MAX_STIR_FACTOR

    # bool / non-finite on a single axis fails closed on that axis only
    session.adjust("stir_state", {"axial": float("nan"), "radial": 4.0})
    assert sim.melt.stir_state.axial == 0.0
    assert sim.melt.stir_state.radial == 4.0
    session.adjust("stir_state", {"axial": True, "radial": False})
    # Whole-dict bool would still pass through the per-axis branch:
    # bool keys are individually rejected per-axis (mirrors
    # clamp_stir_factor's bool defensive rejection).
    assert sim.melt.stir_state.axial == 0.0
    assert sim.melt.stir_state.radial == 0.0


def test_session_adjust_legacy_stir_factor_touches_axial_only():
    """0.5.3 Phase B: the legacy ``session.adjust("stir_factor", x)``
    path must NOT silently inflate the radial axis. Pre-0.5.3 the
    scalar drove BOTH consumers (evap + condensation); 0.5.3 splits
    them, and the operator-intent reading is that a pre-Phase-B
    caller using the legacy scalar API only meant to dial the
    melt-side (axial) consumer. Radial stays at its current value
    (the Phase B default ``1.0``, laminar Sh baseline)."""
    from simulator.state import StirState

    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator

    # Pin starting state
    assert sim.melt.stir_state == StirState(axial=6.0, radial=1.0)

    session.adjust("stir_factor", 8.0)
    assert sim.melt.stir_state.axial == 8.0
    assert sim.melt.stir_state.radial == 1.0  # untouched

    # Dial radial via the new 2-axis path, then check the legacy
    # path still only writes axial
    session.adjust("stir_state", {"axial": 8.0, "radial": 6.0})
    assert sim.melt.stir_state.radial == 6.0
    # Legacy stir_factor write must NOT clobber radial back to 1.0
    session.adjust("stir_factor", 4.0)
    assert sim.melt.stir_state.axial == 4.0
    assert sim.melt.stir_state.radial == 6.0


def test_session_adjust_campaign_override_stir_state_field():
    """0.5.3 Phase B: ``campaign_override`` accepts ``field="stir_state"``
    with a dict / StirState / scalar value. Stored as a clamped
    StirState in the overrides dict so any re-entry path
    (CampaignManager._apply_overrides) sees the 2-axis value rather
    than a scalar that would silently take the legacy mis-route."""
    from simulator.state import StirState

    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator

    session.adjust(
        "campaign_override", {"axial": 4.0, "radial": 8.0},
        campaign="C2A", field="stir_state",
    )
    override = sim.campaign_mgr.overrides["C2A"]["stir_state"]
    assert isinstance(override, StirState)
    assert override.axial == 4.0 and override.radial == 8.0
    # Active-campaign live update
    assert sim.melt.stir_state == StirState(axial=4.0, radial=8.0)

    # Above-ceiling values clamp on each axis
    session.adjust(
        "campaign_override", {"axial": 250.0, "radial": 99.0},
        campaign="C2A", field="stir_state",
    )
    from simulator.state import MAX_STIR_FACTOR
    override2 = sim.campaign_mgr.overrides["C2A"]["stir_state"]
    assert override2.axial == MAX_STIR_FACTOR
    assert override2.radial == MAX_STIR_FACTOR


def test_advance_is_policy_free_and_surfaces_decision_without_applying():
    assert DecisionPolicy.AUTO_APPLY is not DecisionPolicy.OPERATOR
    decision = DecisionPoint(
        DecisionType.PATH_AB,
        options=["A", "B"],
        recommendation="B",
        context="choose route",
    )
    fake = _FakeSim(decision_after_step=decision)
    session = _fake_session(fake)

    result = session.advance()

    assert result.decision_event == {
        "type": "PATH_AB",
        "options": ["A", "B"],
        "recommendation": "B",
        "context": "choose route",
    }
    assert fake.pending_decision is decision
    assert fake.applied_decisions == []


def test_advance_refuses_complete_session_without_stepping():
    fake = _FakeSim()
    fake.is_complete = lambda: True
    session = _fake_session(fake)

    with pytest.raises(RuntimeError, match="complete session"):
        session.advance()

    assert fake.melt.hour == 0


def test_projection_failure_poisons_committed_session(monkeypatch):
    fake = _FakeSim()
    session = _fake_session(fake)
    monkeypatch.setattr(
        session,
        "_build_per_hour_summary",
        lambda *_args: (_ for _ in ()).throw(ValueError("projection broke")),
    )

    with pytest.raises(RuntimeError, match="projection failed"):
        session.advance()
    assert fake.melt.hour == 1

    with pytest.raises(RuntimeError, match="session is poisoned"):
        session.advance()
    assert fake.melt.hour == 1


def test_paused_core_poll_preserves_last_hour_diagnostics():
    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator
    sim._last_extraction_completeness_diagnostic = {"marker": "retained"}
    sim.paused_for_decision = True
    before_hour = sim.melt.hour

    snapshot = sim.step()

    assert snapshot.hour == before_hour
    assert sim.melt.hour == before_hour
    assert sim._last_extraction_completeness_diagnostic == {
        "marker": "retained"
    }

@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_session_refuses_invalid_mre_voltage_before_start(value):
    with pytest.raises(ValueError, match="mre_max_voltage_V"):
        _config(
            c5_enabled=True,
            mre_target_species="SiO2",
            mre_max_voltage_V=value,
        )


def test_session_refuses_empty_enabled_mre_policy():
    with pytest.raises(ValueError, match="c5_enabled requires"):
        _config(c5_enabled=True, mre_target_species="", mre_max_voltage_V=0.0)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_session_rejects_invalid_po2_before_mutation(value):
    session = SimSession().start(_config(campaign="C2A"))
    before = (
        session.simulator.melt.pO2_mbar,
        session.simulator.melt.p_total_mbar,
        session.simulator.melt.atmosphere,
    )

    with pytest.raises(ValueError, match="pO2_mbar"):
        session.adjust("pO2_mbar", value)

    after = (
        session.simulator.melt.pO2_mbar,
        session.simulator.melt.p_total_mbar,
        session.simulator.melt.atmosphere,
    )
    assert after == before


@pytest.mark.parametrize("campaign_name", ["C2A", "C2B"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_session_rejects_invalid_campaign_po2_before_mutation(campaign_name, value):
    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator
    before_melt = (
        sim.melt.pO2_mbar,
        sim.melt.p_total_mbar,
        sim.melt.atmosphere,
    )
    before_overrides = dict(sim.campaign_mgr.overrides)

    with pytest.raises(ValueError, match="pO2_mbar"):
        session.adjust(
            "campaign_override",
            value,
            campaign=campaign_name,
            field="pO2_mbar",
        )

    after_melt = (
        sim.melt.pO2_mbar,
        sim.melt.p_total_mbar,
        sim.melt.atmosphere,
    )
    assert after_melt == before_melt
    assert dict(sim.campaign_mgr.overrides) == before_overrides


def test_auto_apply_driver_applies_recommendation_before_advancing():
    decision = DecisionPoint(
        DecisionType.PATH_AB,
        options=["A", "B"],
        recommendation="B",
        context="choose route",
    )
    fake = _FakeSim()
    fake.pending_decision = decision
    fake.paused_for_decision = True
    session = _fake_session(fake)
    operator_decisions: list[dict] = []

    results = list(drive_auto_apply(session, 1, operator_decisions=operator_decisions))

    assert len(results) == 1
    assert fake.applied_decisions == [(DecisionType.PATH_AB, "B")]
    assert operator_decisions[0]["choice"] == "B"
    assert operator_decisions[0]["recommendation"] == "B"


def test_consecutive_campaign_summaries_are_captured_and_cleared_in_order():
    fake = _FakeSim(
        summaries=[
            {"campaign": "C0", "hour": 1},
            {"campaign": "C0B", "hour": 2},
        ]
    )
    session = _fake_session(fake)

    first = session.advance()
    second = session.advance()

    assert [first.campaign_summary, second.campaign_summary] == [
        {"campaign": "C0", "hour": 1},
        {"campaign": "C0B", "hour": 2},
    ]
    assert fake._last_campaign_summary is None


def test_mre_baseline_track_start_tags_track_without_jumping_campaign():
    session = SimSession().start(_config(campaign="C0", track="mre_baseline"))

    assert session.simulator.record.track == "mre_baseline"
    assert session.simulator.melt.campaign == CampaignPhase.C0
    assert session.simulator.melt.campaign != CampaignPhase.MRE_BASELINE


def test_session_config_rejects_unknown_track():
    with pytest.raises(ValueError, match="unknown track"):
        _config(track="mre_basline")


def test_c0b_hard_cap_ignores_sub_soft_temperature():
    manager = CampaignManager(_load_yaml("setpoints.yaml"))
    melt = SimpleNamespace(
        campaign=CampaignPhase.C0B,
        campaign_hour=2.0,
        temperature_C=1199.0,
    )

    assert manager.check_endpoint(
        melt,
        SimpleNamespace(total_kg_hr=0.0),
        SimpleNamespace(),
        SimpleNamespace(),
    )


def test_core_apply_decision_rejects_pending_type_mismatch():
    session = SimSession().start(_config())
    sim = session.simulator
    pending = DecisionPoint(
        DecisionType.PATH_AB,
        options=["A", "A_staged", "B"],
        recommendation="A_staged",
        context="choose path",
    )
    sim.pending_decision = pending
    sim.paused_for_decision = True

    with pytest.raises(ValueError, match="pending decision is PATH_AB"):
        sim.apply_decision(DecisionType.BRANCH_ONE_TWO, "two")

    assert sim.pending_decision is pending
    assert sim.paused_for_decision is True
    assert sim.record.decisions[-1:] == []


def test_core_apply_decision_handles_c7_proceed():
    session = SimSession().start(_config(campaign="C6"))
    sim = session.simulator
    sim.pending_decision = DecisionPoint(
        DecisionType.C7_PROCEED,
        options=["yes", "no"],
        recommendation="yes",
        context="run C7",
    )
    sim.paused_for_decision = True

    sim.apply_decision(DecisionType.C7_PROCEED, "yes")

    assert sim.melt.campaign == CampaignPhase.C7_CA_ALUMINOTHERMIC
    assert sim.pending_decision is None
    assert sim.paused_for_decision is False


def test_pause_resume_are_result_neutral_pacing_flags():
    paused = SimSession().start(_config())
    unpaused = SimSession().start(_config())
    paused_results = []
    unpaused_results = []

    for _ in range(3):
        paused.pause()
        paused.resume()
        paused_results.append(paused.advance().per_hour_summary)
        unpaused_results.append(unpaused.advance().per_hour_summary)

    assert paused_results == unpaused_results
