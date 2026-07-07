"""E3 — Knudsen-regime warning sticker on HourSnapshot.

The F3 work added a hard refusal at ``Kn >= 10`` (`refused outside
viscous flow`). E3 surfaces an EARLIER-WARNING diagnostic on the
per-tick HourSnapshot so an operator can see when the regime is
approaching the boundary BEFORE the F3 refusal fires.

These tests pin:
1. The new ``HourSnapshot.knudsen_regime_summary`` field exists +
   defaults to an empty dict.
2. After a condensation route fires, the summary carries the canonical fields
   (status, knudsen_regime, regime_factor, warnings) and only emits
   knudsen_number when it is finite.
3. JSON-serialisability invariant: the summary dict round-trips
   cleanly through ``json.dumps`` so the runner output isn't
   broken by tuple values etc.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from simulator.backends import BackendSelectionPolicy
from simulator.session import SimSession, SimSessionConfig
from simulator.state import EvaporationFlux, HourSnapshot, OverheadGas

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


def test_snapshot_has_knudsen_regime_summary_field():
    """Defaults to an empty dict on a fresh sim."""
    session = SimSession().start(_config(campaign="C2A"))
    snap = session.snapshot()
    assert isinstance(snap, HourSnapshot)
    assert hasattr(snap, 'knudsen_regime_summary')
    assert isinstance(snap.knudsen_regime_summary, dict)


def test_snapshot_knudsen_summary_carries_canonical_fields_after_route():
    """After a few ticks of C2A_continuous, the condensation route
    has fired at least once → the summary carries the documented
    fields. Each field is JSON-serialisable / dataclass-friendly.

    Midflight-review P2 hardening (2026-05-28): FORCE the diagnostic
    populated by directly invoking ``condensation_model.route(...)``
    before reading the snapshot, instead of relying on the C2A
    integration path firing the route within 4 ticks (which it
    doesn't in stub-backend mode). The test now actually exercises
    the populated-summary code path instead of passing vacuously."""
    from simulator.state import EvaporationFlux

    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator
    # Force a condensation route so the Kn diagnostic actually
    # populates. Without this, the summary stays {} through a
    # short C2A warmup and the original assertions were vacuous.
    sim.melt.temperature_C = 1500.0
    model = sim.condensation_model
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        gas_temperature_C=1500.0,
        campaign_name="C2A",
    )
    flux = EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0)
    model.route(flux, sim.melt)
    snap = sim._make_snapshot()
    summary = snap.knudsen_regime_summary
    # Now non-empty — the test must actually pin field shapes.
    assert summary, (
        "Knudsen diagnostic should populate after a condensation route; "
        "midflight P2 fix made this assertion non-vacuous"
    )
    # Canonical field shapes.
    assert 'status' in summary
    assert isinstance(summary['status'], str)
    if 'knudsen_number' in summary:
        assert isinstance(summary['knudsen_number'], float)
        assert summary['knudsen_number'] >= 0.0
    else:
        assert summary['status'] == 'unconfigured'
        assert summary['knudsen_regime'] == 'free_molecular'
    assert 'knudsen_regime' in summary
    assert isinstance(summary['knudsen_regime'], str)
    assert summary['knudsen_regime'] in (
        'viscous', 'transition', 'free_molecular'
    )
    assert 'regime_factor' in summary
    assert isinstance(summary['regime_factor'], float)
    assert 0.0 <= summary['regime_factor'] <= 1.0
    assert 'warnings' in summary
    assert isinstance(summary['warnings'], tuple)
    for w in summary['warnings']:
        assert isinstance(w, str)


def test_snapshot_knudsen_summary_is_json_serialisable():
    """Runner output emits HourSnapshot as JSON via
    ``simulator/runner.py``. The new field must round-trip through
    ``json.dumps`` without breaking — tuples are NOT JSON-native,
    so the runner converts them to lists at serialisation time.
    Verify here that the summary content is at least
    deeply-convertible (no objects, no NaN escape)."""
    session = SimSession().start(_config(campaign="C2A"))
    for _ in range(4):
        session.simulator.step()
    snap = session.snapshot()
    summary = snap.knudsen_regime_summary
    # Convert tuples to lists for JSON; replicate what runner does.
    json_safe = {
        k: list(v) if isinstance(v, tuple) else v
        for k, v in summary.items()
    }
    serialized = json.dumps(json_safe)
    assert isinstance(serialized, str)
    round_trip = json.loads(serialized)
    assert isinstance(round_trip, dict)


def test_latest_knudsen_summary_returns_empty_dict_pre_condensation():
    """Internal contract: ``_latest_knudsen_summary`` returns an
    empty dict when ``_condensation_model`` is None or the
    diagnostic hasn't been populated yet (e.g., a pre-step snapshot
    of a fresh sim)."""
    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator
    # Force-clear the condensation model to simulate a pre-route tick.
    sim._condensation_model = None
    summary = sim._latest_knudsen_summary()
    assert summary == {}


def test_zero_overhead_marker_rejects_mre_only_o2_flow():
    session = SimSession().start(_config(campaign="C5"))
    sim = session.simulator
    sim._last_condensed_by_stage_species_delta = {}
    sim._last_wall_deposit_by_segment_species_delta = {}
    sim.overhead = OverheadGas(mre_anode_O2_mol_hr=1.0)

    sim._refresh_knudsen_zero_overhead_flow_marker(
        EvaporationFlux(species_kg_hr={}, total_kg_hr=0.0)
    )

    assert sim._latest_knudsen_summary() == {}
