"""End-to-end Kr carrier honesty (2026-07-25 Kr carrier-coverage audit).

The audit found Kr accepted at the input gate but silently relabelled N2
downstream: the overhead sweep branch hard-wrote a phantom 'N2' partial,
C2A appliers stamped background_gas_species='N2' regardless of config,
and the runner's carrier observables fell through a PN2_SWEEP elif to
'N2'. These tests drive a REAL short C2A run with carrier pKr and assert
the plumbing end to end — plus the golden-neutrality contract that a
default (pN2) run is byte-indistinguishable from before the fix.
"""

from __future__ import annotations

import pytest

from simulator.backends import SimulatorBuildConfig, build_simulator
from simulator.config import load_config_bundle
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import CampaignPhase


def _build_c2a_sim(
    carrier_gas: str | None,
    *,
    finite_headspace: bool = True,
    campaign: CampaignPhase = CampaignPhase.C2A,
    runtime_carrier_gas: str | None = None,
):
    """Build a short C2A run with config-level or runtime carrier selection."""
    import copy

    bundle = load_config_bundle()
    setpoints = bundle.setpoints
    if carrier_gas is not None or not finite_headspace:
        setpoints = copy.deepcopy(setpoints)
    if carrier_gas is not None:
        setpoints["campaigns"]["C2A_continuous"]["carrier_gas"] = carrier_gas
    if not finite_headspace:
        setpoints.setdefault("overhead_headspace", {})["enabled"] = False
    backend = InternalAnalyticalBackend()
    assert backend.initialize({})
    sim = build_simulator(
        SimulatorBuildConfig(
            backend=backend,
            setpoints=setpoints,
            feedstocks=bundle.feedstocks,
            vapor_pressures=bundle.vapor_pressures,
            materials=bundle.materials,
        )
    )
    sim.load_batch("lunar_mare_low_ti", 1000.0)
    if runtime_carrier_gas is not None:
        override_key = (
            "C2A_staged" if campaign == CampaignPhase.C2A_STAGED else "C2A"
        )
        sim.campaign_mgr.overrides[override_key] = {
            "carrier_gas": runtime_carrier_gas,
        }
    sim.start_campaign(campaign)
    return sim


# Both overhead sweep branches carry the phantom-N2 fix; NOT-FIXED lens
# 2026-07-25 verified the legacy branch clean by probe but flagged it
# unguarded — parametrize so a revert of EITHER branch goes red.
@pytest.mark.parametrize("finite_headspace", [True, False])
def test_kr_carrier_reaches_overhead_without_phantom_n2(finite_headspace):
    sim = _build_c2a_sim("pKr", finite_headspace=finite_headspace)
    sim.step()

    assert sim.melt.background_gas_species == "Kr"
    composition = dict(sim.record.snapshots[-1].overhead.composition)
    assert composition.get("Kr", 0.0) > 0.0, composition
    # The double-count hole: pre-fix, the sweep branch wrote a
    # full-magnitude phantom N2 partial alongside the real Kr one.
    assert composition.get("N2", 0.0) == pytest.approx(0.0), composition


def test_default_n2_carrier_unchanged_by_kr_plumbing():
    """Golden-neutrality contract: default pN2 behaves exactly as before."""
    sim = _build_c2a_sim(None)
    sim.step()

    assert sim.melt.background_gas_species == "N2"
    composition = dict(sim.record.snapshots[-1].overhead.composition)
    assert composition.get("N2", 0.0) > 0.0
    assert composition.get("Kr", 0.0) == pytest.approx(0.0)


def test_runtime_carrier_override_is_canonicalized_for_both_c2a_variants():
    from simulator.campaigns import CampaignManager

    for campaign in ("C2A", "C2A_staged"):
        canonical = CampaignManager._canonicalize_runtime_carrier_overrides(
            {campaign: {"carrier_gas": "pKr"}}
        )
        CampaignManager.validate_runtime_campaign_overrides(canonical)
        assert canonical[campaign]["carrier_gas"] == "Kr"
        assert "carrier_gas" in CampaignManager.known_override_fields(campaign)


def test_runtime_unknown_carrier_still_fails_closed_without_input_mutation():
    from simulator.campaigns import CampaignManager

    overrides = {"C2A": {"carrier_gas": "pXenon"}}
    before = {campaign: dict(fields) for campaign, fields in overrides.items()}
    with pytest.raises(ValueError, match="supported carrier gases:.*Kr/pKr"):
        CampaignManager.validate_runtime_campaign_overrides(overrides)
    assert overrides == before


@pytest.mark.parametrize(
    ("campaign", "override_name"),
    [
        (CampaignPhase.C2A, "C2A"),
        (CampaignPhase.C2A_STAGED, "C2A_staged"),
    ],
)
def test_runtime_kr_override_reaches_melt_overhead_and_hourly_summary(
    campaign,
    override_name,
):
    from simulator.runner import _carrier_pressure_observables

    sim = _build_c2a_sim(
        None,
        campaign=campaign,
        runtime_carrier_gas="pKr",
    )
    sim.step()

    assert sim.campaign_mgr.overrides[override_name]["carrier_gas"] == "pKr"
    assert sim.melt.background_gas_species == "Kr"
    composition = dict(sim.record.snapshots[-1].overhead.composition)
    assert composition.get("Kr", 0.0) > 0.0, composition
    assert composition.get("N2", 0.0) == pytest.approx(0.0), composition
    hourly = _carrier_pressure_observables(sim, sim.record.snapshots[-1])
    assert hourly["carrier_identity"] == "Kr"
    assert hourly["p_carrier_bar"] > 0.0


def test_runner_carrier_observables_label_kr():
    """The artifact-identity hole: _CARRIER_TOKENS lacked KR, so hourly
    observables relabelled a Kr run as N2 via the PN2_SWEEP fallback."""
    from simulator.runner import _CARRIER_TOKENS

    assert _CARRIER_TOKENS.get("KR") == "Kr"
    assert _CARRIER_TOKENS.get("HE") == "He"
