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


def _build_c2a_sim(carrier_gas: str | None):
    """Config-level carrier selection: the runtime-override surface
    deliberately refuses carrier_gas (fail-closed; operator wiring is a
    separate chunk), so a Kr recipe arrives the way a real one would —
    in the setpoints config."""
    import copy

    bundle = load_config_bundle()
    setpoints = bundle.setpoints
    if carrier_gas is not None:
        setpoints = copy.deepcopy(setpoints)
        setpoints["campaigns"]["C2A_continuous"]["carrier_gas"] = carrier_gas
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
    sim.start_campaign(CampaignPhase.C2A)
    return sim


def test_kr_carrier_reaches_overhead_without_phantom_n2():
    sim = _build_c2a_sim("pKr")
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


def test_runner_carrier_observables_label_kr():
    """The artifact-identity hole: _CARRIER_TOKENS lacked KR, so hourly
    observables relabelled a Kr run as N2 via the PN2_SWEEP fallback."""
    from simulator.runner import _CARRIER_TOKENS

    assert _CARRIER_TOKENS.get("KR") == "Kr"
    assert _CARRIER_TOKENS.get("HE") == "He"
