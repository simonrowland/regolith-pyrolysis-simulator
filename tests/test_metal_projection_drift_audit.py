"""W8 / M2 historical-audit closure (2026-05-28): per-species drift
between ``process.metal_phase`` (canonical AtomLedger account) and
the ``train.stages[*].collected_kg`` UI projection.

Diagnostic only — the global ≤5e-12 % closure on
``HourSnapshot.mass_balance_error_pct`` remains the hard gate; this
audit gives earlier-warning visibility when ledger ↔ UI projection
drift opens up on individual species.

The audit helper reads
``self.atom_ledger.kg_by_account('process.metal_phase')`` and compares
to the sum of ``train.stages[*].collected_kg`` for each species in
the ledger. To exercise the helper in isolation we drive the read
side via a fake-ledger pattern; the snapshot wiring is then verified
via a stub-sim end-to-end.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Dict, Iterable

import pytest

from simulator.core import PyrolysisSimulator
from simulator.extraction import ExtractionMixin
from simulator.melt_backend.base import StubBackend
from simulator.state import (
    CampaignPhase,
    CondensationStage,
    CondensationTrain,
    MOLAR_MASS,
)


def _make_audit_target(
    *,
    metal_phase_kg: Dict[str, float],
    train_kg_by_stage: Iterable[Dict[str, float]],
) -> SimpleNamespace:
    """Minimal duck-typed surface for ``_audit_metal_projection_drift``.

    The method only touches ``self.atom_ledger.kg_by_account`` and
    ``self.train.stages[*].collected_kg``; everything else on
    ``ExtractionMixin`` is irrelevant. Build a SimpleNamespace that
    exposes just those two surfaces with the requested test
    fixture, then bind the method via ``__get__`` so ``self`` is the
    namespace.
    """
    stages = []
    for idx, kg_map in enumerate(train_kg_by_stage):
        stages.append(
            CondensationStage(
                idx, f'Test stage {idx}', (1100.0, 1400.0), [],
                collected_kg=dict(kg_map),
            )
        )
    train = CondensationTrain(stages=stages)

    def _fake_kg_by_account(account):
        if account == 'process.metal_phase':
            return dict(metal_phase_kg)
        return {}

    ns = SimpleNamespace(
        atom_ledger=SimpleNamespace(kg_by_account=_fake_kg_by_account),
        train=train,
    )
    # Bind the audit + projection-sum helpers + tolerance so ``self``
    # is ``ns``. ExtractionMixin is a duck-typed mixin; the audit only
    # touches these surfaces. _LEDGER_KG_TOL is a class-level attr,
    # propagate to the namespace for the > tol comparison.
    ns._audit_metal_projection_drift = (
        ExtractionMixin._audit_metal_projection_drift.__get__(ns)
    )
    ns._condensed_species_projected_kg = (
        ExtractionMixin._condensed_species_projected_kg.__get__(ns)
    )
    ns._LEDGER_KG_TOL = ExtractionMixin._LEDGER_KG_TOL
    return ns


# ---------------------------------------------------------------------------
# 1. Empty / in-sync / divergent baselines
# ---------------------------------------------------------------------------

def test_audit_empty_metal_phase_returns_empty_dict():
    """Pre-extraction sims (no metal credited) → empty audit dict.
    Presence of a key is the honesty signal: an empty dict means
    "all metals in sync (or absent)"."""
    target = _make_audit_target(
        metal_phase_kg={},
        train_kg_by_stage=[{}, {}, {}],
    )
    assert target._audit_metal_projection_drift() == {}


def test_audit_in_sync_ledger_and_projection_returns_empty_dict():
    """Steady state: ledger Fe=0.05, projection Fe=0.05 → no drift
    surfaced. Multi-stage projection sums correctly."""
    target = _make_audit_target(
        metal_phase_kg={'Fe': 0.05},
        train_kg_by_stage=[
            {'Fe': 0.02},
            {'Fe': 0.03},
            {},
        ],
    )
    assert target._audit_metal_projection_drift() == {}


def test_audit_normal_direction_drift_ledger_above_projection():
    """Normal-direction drift: ledger has 0.05, projection has 0.02,
    drift = +0.03. Surfaced with sign convention
    ``ledger - projection > 0``."""
    target = _make_audit_target(
        metal_phase_kg={'Fe': 0.05},
        train_kg_by_stage=[{'Fe': 0.02}, {}, {}],
    )
    audit = target._audit_metal_projection_drift()
    assert audit == {'Fe': pytest.approx(0.03)}


# ---------------------------------------------------------------------------
# 2. Tolerance / noise floor
# ---------------------------------------------------------------------------

def test_audit_below_tolerance_drift_suppressed():
    """Drifts below ``_LEDGER_KG_TOL = 1e-9 kg`` (per-species FP
    noise floor) are NOT surfaced. Operator only sees real signal."""
    target = _make_audit_target(
        metal_phase_kg={'Fe': 0.05 + 1e-12},  # negligible delta
        train_kg_by_stage=[{'Fe': 0.05}, {}],
    )
    assert 'Fe' not in target._audit_metal_projection_drift()


def test_audit_at_tolerance_boundary_suppressed():
    """A drift exactly at ``_LEDGER_KG_TOL`` boundary is suppressed —
    the audit uses strict ``> _LEDGER_KG_TOL`` so the boundary case
    stays silent, matching the documented threshold semantics."""
    target = _make_audit_target(
        metal_phase_kg={'Fe': 0.05 + ExtractionMixin._LEDGER_KG_TOL},
        train_kg_by_stage=[{'Fe': 0.05}, {}],
    )
    assert 'Fe' not in target._audit_metal_projection_drift()


def test_audit_just_above_tolerance_surfaces():
    """Just above ``_LEDGER_KG_TOL`` (1.5 × tol) → surface. The
    audit catches noise-above-floor signal honestly."""
    target = _make_audit_target(
        metal_phase_kg={'Fe': 0.05 + 1.5 * ExtractionMixin._LEDGER_KG_TOL},
        train_kg_by_stage=[{'Fe': 0.05}, {}],
    )
    assert 'Fe' in target._audit_metal_projection_drift()


# ---------------------------------------------------------------------------
# 3. Multi-species independence
# ---------------------------------------------------------------------------

def test_audit_multiple_species_drift_independently():
    """Two metals out of sync simultaneously → both surface with
    their own drift values. Operators see exactly which species
    are off."""
    target = _make_audit_target(
        metal_phase_kg={'Fe': 0.03, 'Na': 0.007, 'Si': 0.001},
        train_kg_by_stage=[
            {},                                  # Fe lagging
            {'Si': 0.001},                       # Si in sync
            {},                                  # Na lagging
        ],
    )
    audit = target._audit_metal_projection_drift()
    assert audit.get('Fe') == pytest.approx(0.03)
    assert audit.get('Na') == pytest.approx(0.007)
    assert 'Si' not in audit  # in sync


def test_audit_in_sync_species_alongside_drifting_species():
    """One species in sync + one drifting → only the drifting
    species appears. Confirms the audit doesn't smear noise."""
    target = _make_audit_target(
        metal_phase_kg={'Fe': 0.05, 'Cr': 0.01},
        train_kg_by_stage=[
            {'Fe': 0.05, 'Cr': 0.005},   # Cr lagging by 0.005
            {},
        ],
    )
    audit = target._audit_metal_projection_drift()
    assert 'Fe' not in audit
    assert audit.get('Cr') == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# 4. Defensive: NaN / negative inputs are handled
# ---------------------------------------------------------------------------

def test_audit_skips_nan_ledger_entry():
    """A NaN kg in the ledger (corrupt upstream computation) MUST NOT
    propagate into the audit dict. The audit is a diagnostic;
    surfacing NaN would poison downstream display + alerts."""
    target = _make_audit_target(
        metal_phase_kg={'Cr': float('nan'), 'Fe': 0.02},
        train_kg_by_stage=[{}, {}],
    )
    audit = target._audit_metal_projection_drift()
    assert 'Cr' not in audit
    # Legitimate Fe drift still surfaces.
    assert audit.get('Fe') == pytest.approx(0.02)
    # Every value in the audit is finite.
    for kg in audit.values():
        assert math.isfinite(kg)


def test_audit_skips_non_coercible_ledger_entry():
    """A non-coercible kg (e.g., a string accidentally landed in the
    ledger via a bad commit) gets skipped via the try/except float()
    coercion guard. Legitimate entries still surface."""
    target = _make_audit_target(
        metal_phase_kg={'Bad': 'not a number', 'Fe': 0.02},
        train_kg_by_stage=[{}],
    )
    audit = target._audit_metal_projection_drift()
    assert 'Bad' not in audit
    assert audit.get('Fe') == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# 5. End-to-end snapshot integration on a real simulator
# ---------------------------------------------------------------------------

def _basic_sim() -> PyrolysisSimulator:
    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"silica": {"label": "Silica", "composition_wt_pct": {"SiO2": 100.0}}},
        {
            "metals": {
                "Fe": {"parent_oxide": "FeO"},
                "Si": {"parent_oxide": "SiO2"},
            },
            "oxide_vapors": {},
        },
    )
    sim.load_batch("silica", mass_kg=100.0)
    return sim


def test_snapshot_carries_metal_projection_drift_kg_field():
    """End-to-end wiring: ``_make_snapshot`` invokes
    ``_audit_metal_projection_drift`` and stores the result in
    ``HourSnapshot.metal_projection_drift_kg``. On a fresh sim with
    no metals in the ledger, the field is an empty dict."""
    sim = _basic_sim()
    sim.melt.campaign = CampaignPhase.IDLE
    snapshot = sim._make_snapshot()
    assert hasattr(snapshot, 'metal_projection_drift_kg')
    assert snapshot.metal_projection_drift_kg == {}


def test_snapshot_drift_surfaces_after_manual_balance_state_change():
    """When the test directly sets the ledger metal-phase balance
    (bypassing the normal commit_batch path), the audit picks up
    the drift relative to the empty projection. End-to-end check
    of the snapshot field carrying real audit data."""
    sim = _basic_sim()
    # Directly seed process.metal_phase with mol counts equivalent to
    # 0.05 kg Fe. The ledger stores mol; kg_by_account projects back.
    fe_mol = 0.05 / (MOLAR_MASS['Fe'] / 1000.0)
    sim.atom_ledger._balances['process.metal_phase'] = {'Fe': fe_mol}

    snapshot = sim._make_snapshot()
    assert snapshot.metal_projection_drift_kg.get('Fe') == pytest.approx(
        0.05, abs=1e-9
    )
