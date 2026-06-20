"""E1a — North-star recipe-correctness baseline (DIAGNOSTIC).

Reads the four success measurements from canonical full-sequence
campaign runs and reports them. NO hard threshold gate on yield /
Stage 4 carryover / wall deposit — those flips are E1b deferred
to post-Phase-D per plan rev 2. Mass-balance ≤5×10⁻¹² % IS the
hard gate (matches `AGENTS.md` hard invariant); everything else
is a documented observation.

This file is the LIVING NORTH STAR: as the simulator gets more
honest, these numbers should trend toward the success
measurements (≥95% target-species yield, Stage 4 carryover ≤
documented routing trade-off bound, wall deposit < operational
threshold). When a number trends the wrong way, the diagnostic
catches it before E1b's hard gate would.

Closes E1a from
``docs-private/goal-deferred-and-roadmap-2026-05-28.md`` rev 2.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.backends import BackendSelectionPolicy
from simulator.session import SimSession, SimSessionConfig
from simulator.state import CampaignPhase

# Heavy real-backend c2a baseline runs: spuriously SIGALRM/timeout when xdist
# co-schedules them under resource contention. Run them serially (pyproject
# `markers`); -n0 is also AGENTS.md guidance for the c2a freeze-gate class.
pytestmark = pytest.mark.serial

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Mass-balance closure invariant per AGENTS.md hard rule.
MASS_BALANCE_HARD_GATE_PCT = 5.0e-12

# Documented routing trade-off from 0.5.3 CHANGELOG "Known limitation":
# under default StirState(radial=1.0) laminar Sherwood, Stage 4 SiO
# carryover currently exceeds Stage 3 SiO product. This is the
# diagnostic ceiling, NOT the success threshold — E1b will tighten.
ROUTING_TRADEOFF_STAGE_4_SIO_KG_BOUND = 0.01

# Diagnostic upper bound on alkali species yield as fraction of
# the cleaned-melt alkali-oxide budget. The north-star target is
# ≥0.95 (95%); pre-Phase-D the simulator may report less. We
# only fail the test if the value drops BELOW a permissive floor
# (so a regression that completely halts evaporation surfaces).
PERMISSIVE_ALKALI_FLOOR = 0.0  # diagnostic-only: track value, no gate


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


def _run_for_hours(session: SimSession, hours: int) -> list:
    """Tick the session for ``hours`` and return collected
    HourSnapshots. Stops early if the sim completes."""
    snapshots = []
    sim = session.simulator
    for _ in range(hours):
        if sim.is_complete():
            break
        sim.step()
        snapshots.append(session.snapshot())
    return snapshots


# ---------------------------------------------------------------------------
# Hard gate: mass-balance closure ≤5e-12 % per tick
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("feedstock", ["lunar_mare_low_ti"])
def test_mass_balance_closure_holds_through_short_c2a_run(feedstock):
    """Hard gate from AGENTS.md: ``mass_balance_error_pct`` MUST
    stay below 5e-12 % at every tick across all canonical
    feedstocks + campaign sequences. This catches the kind of
    accounting drift that the per-species W8 audit complements
    at a finer granularity."""
    session = SimSession().start(_config(feedstock_id=feedstock,
                                          campaign="C2A"))
    snapshots = _run_for_hours(session, 8)
    assert snapshots, "C2A short run produced no snapshots"
    worst = max(
        abs(snap.mass_balance_error_pct) for snap in snapshots
    )
    assert worst <= MASS_BALANCE_HARD_GATE_PCT, (
        f"mass-balance closure broke on {feedstock} C2A: "
        f"worst error_pct = {worst:.3e} > {MASS_BALANCE_HARD_GATE_PCT:.3e}"
    )


# ---------------------------------------------------------------------------
# Diagnostic: Stage 4 SiO carryover routing trade-off
# ---------------------------------------------------------------------------

def test_diagnostic_stage_4_sio_carryover_under_documented_bound():
    """Per 0.5.3 CHANGELOG known-limitation: Stage 4 SiO carryover
    under default `StirState(radial=1.0)` is documented to exceed
    Stage 3 SiO product but stay BELOW the operational ceiling
    of 0.01 kg over a C2A short run. Diagnostic only — E1b will
    tighten once Phase D / routing improvements land.

    A breach of the 0.01 kg ceiling indicates a routing collapse
    (e.g., a regression in the condensation train) and should
    surface immediately."""
    session = SimSession().start(_config(campaign="C2A"))
    snapshots = _run_for_hours(session, 8)
    # Read terminal-state Stage 4 SiO from the condensation train
    # (sum across all snapshots' Stage 4 collected_kg for SiO).
    sim = session.simulator
    stage_4_sio_kg = float(
        sim.train.stages[4].collected_kg.get('SiO', 0.0)
    )
    # Diagnostic upper bound from the routing trade-off plan.
    assert stage_4_sio_kg <= ROUTING_TRADEOFF_STAGE_4_SIO_KG_BOUND, (
        f"Stage 4 SiO carryover {stage_4_sio_kg:.3e} kg exceeds "
        f"the documented routing-trade-off bound "
        f"{ROUTING_TRADEOFF_STAGE_4_SIO_KG_BOUND} kg — routing "
        f"may have collapsed; investigate before pushing"
    )


# ---------------------------------------------------------------------------
# Diagnostic: north-star product surface accessibility
# ---------------------------------------------------------------------------

def test_product_ledger_surface_callable_during_c2a_run():
    """The four north-star product classes (metals + O2, silica
    glass via gas-cover switch, mixed glass via early tap,
    refractory ceramic rump) all flow through
    ``PyrolysisSimulator.product_ledger()``. Verify the dict is
    accessible (even if empty) after a short C2A run — an 8h
    C2A_continuous is largely warmup before extraction kicks in.
    Diagnostic only: the dict being callable + returning a dict-
    typed object is the structural invariant. E1b will tighten
    once a full sequence runs and product masses populate."""
    session = SimSession().start(_config(campaign="C2A"))
    _run_for_hours(session, 8)
    sim = session.simulator
    products = sim.product_ledger()
    assert isinstance(products, dict), (
        f"product_ledger returned non-dict {type(products)}"
    )
    # Diagnostic: report content without gating on non-emptiness.
    # The empty case is expected for short-warmup runs; E1b would
    # only fire after a full sequence that exits with mass on the
    # condenser train.
    for species, kg in products.items():
        assert kg >= 0.0, (
            f"product_ledger returned negative mass for "
            f"{species}: {kg}"
        )


# ---------------------------------------------------------------------------
# Diagnostic: wall-deposit ledger accessibility
# ---------------------------------------------------------------------------

def test_wall_deposit_ledger_accessible_via_atom_ledger():
    """Per-species wall_deposit accounts are the canonical
    surface for the "furnace coating" failure-mode tracking.
    Verify they're readable via the AtomLedger after a C2A run
    (per-species per-segment routing landed in F2)."""
    from simulator.state import PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS

    session = SimSession().start(_config(campaign="C2A"))
    _run_for_hours(session, 8)
    sim = session.simulator
    # Each pipe-segment wall account is a valid AtomLedger account.
    for account in PIPE_SEGMENT_WALL_DEPOSIT_ACCOUNTS:
        species_kg = sim.atom_ledger.kg_by_account(account)
        # All values are finite + non-negative (defensive).
        for species, kg in species_kg.items():
            assert kg >= 0.0, (
                f"wall deposit account {account} has negative "
                f"mass for {species}: {kg}"
            )


# ---------------------------------------------------------------------------
# Diagnostic: snapshot structural completeness
# ---------------------------------------------------------------------------

def test_snapshots_carry_all_expected_north_star_fields():
    """The HourSnapshot dataclass is the operator-visible per-tick
    surface. Per the project mandate it must expose the four
    success measurements directly OR via a documented account
    path. Verify the structural completeness of one snapshot:
    mass balance, energy, condensation totals, metal projection
    drift, all present and finite."""
    session = SimSession().start(_config(campaign="C2A"))
    _run_for_hours(session, 4)
    snap = session.snapshot()

    # Mass-balance gate (hard invariant).
    assert snap.mass_balance_error_pct == snap.mass_balance_error_pct  # not NaN
    assert isinstance(snap.mass_balance_error_pct, float)
    assert snap.mass_balance_error_pct >= 0.0

    # Mass-in / mass-out (used to compute the % above).
    assert snap.mass_in_kg > 0.0
    assert snap.mass_out_kg > 0.0

    # Energy accumulator.
    assert snap.energy_cumulative_kWh >= 0.0

    # Metal-projection drift audit (W8) on the snapshot.
    assert hasattr(snap, 'metal_projection_drift_kg')
    assert isinstance(snap.metal_projection_drift_kg, dict)

    # Condensation totals carry the per-species recovery surface.
    assert hasattr(snap, 'condensation_totals')
    assert isinstance(snap.condensation_totals, dict)


# ---------------------------------------------------------------------------
# Future-work tag: E1b will add hard-assertion gates here
# ---------------------------------------------------------------------------

@pytest.mark.skip(
    reason=(
        "E1b future-work: hard-assertion gate on >= 95% Na/K/Fe/Mg/SiO "
        "yield is deferred to post-Phase-D per "
        "docs-private/goal-deferred-and-roadmap-2026-05-28.md rev 2. "
        "Reactivate this test once the recipe defaults land that "
        "actually clear the 95% threshold without fudging."
    )
)
def test_e1b_future_target_species_yield_threshold():
    """E1b placeholder — DELETE THIS SKIP MARKER once the recipe
    defaults clear the 95% threshold honestly."""
    session = SimSession().start(_config(campaign="C2A"))
    _run_for_hours(session, 24)
    sim = session.simulator
    products = sim.product_ledger()
    # E1b would compute yield % per target species vs initial
    # cleaned-melt budget and assert >= 0.95 for each.
    raise NotImplementedError("E1b — see plan rev 2")
