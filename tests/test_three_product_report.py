"""Tests for E6a north-star product-class classifier.

Pins:
1. The expanded output shape preserves the original 5 canonical
   buckets (metals+O2 / silica / mixed glass / rump / unclassified)
   and adds product-specific convenience views.
2. Metal/O2 sums match the canonical METAL_PRODUCT_SPECIES list.
3. Stage 3 capture reads SiO + SiO2 collected_kg.
4. Defensive: missing methods / empty product_ledger don't raise.
5. End-to-end on real sim — full classification round-trips
   product_ledger() without losing or duplicating mass.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.accounting.queries import TERMINAL_RUMP_CLASS_TOLERANCE_PCT
from simulator.backends import BackendSelectionPolicy
from simulator.session import SimSession, SimSessionConfig
from simulator.three_product_report import (
    METAL_PRODUCT_SPECIES,
    O2_PRODUCT_SPECIES,
    PURE_SILICA_GLASS_SPECIES,
    classify_products,
)


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(name: str) -> dict:
    with (DATA_DIR / name).open() as f:
        return yaml.safe_load(f) or {}


def _config(**overrides) -> SimSessionConfig:
    values = {
        "feedstock_id": "lunar_mare_low_ti",
        "feedstocks": _load("feedstocks.yaml"),
        "setpoints": _load("setpoints.yaml"),
        "vapor_pressures": _load("vapor_pressures.yaml"),
        "campaign": "C2A",
        "backend_name": "stub",
        "backend_policy": BackendSelectionPolicy.RUNNER_STRICT,
    }
    values.update(overrides)
    return SimSessionConfig(**values)


# ---------------------------------------------------------------------------
# 1. Output shape
# ---------------------------------------------------------------------------

def test_classifier_returns_documented_5_bucket_shape():
    """The classifier MUST return the documented expanded dict while
    preserving the original 5 canonical buckets for downstream
    consumers (E6b runner CLI, web UI, log scrapers)."""
    session = SimSession().start(_config(campaign="C2A"))
    result = classify_products(session.simulator)
    canonical_buckets = {
        'metals_plus_O2',
        'pure_silica_glass',
        'industrial_mixed_glass',
        'refractory_ceramic_rump',
        'unclassified',
    }
    additive_buckets = {
        'ingots_metals',
        'oxygen',
        'glass',
        'captured_volatiles',
        'process_inventory_spent_reductant',
    }
    assert canonical_buckets <= result.keys()
    assert set(result.keys()) == canonical_buckets | additive_buckets
    # Each bucket carries a ``class_total_kg`` (or for unclassified,
    # ``total_kg``) so the operator can sum across classes.
    for bucket in (
        'metals_plus_O2', 'pure_silica_glass',
        'industrial_mixed_glass', 'refractory_ceramic_rump',
        'ingots_metals', 'oxygen', 'glass', 'captured_volatiles',
    ):
        assert 'class_total_kg' in result[bucket]
        assert isinstance(result[bucket]['class_total_kg'], float)
        assert result[bucket]['class_total_kg'] >= 0.0


# ---------------------------------------------------------------------------
# 2. Metals + O2 species coverage
# ---------------------------------------------------------------------------

def test_metals_plus_o2_uses_canonical_species_list():
    """The metals product class iterates METAL_PRODUCT_SPECIES
    (canonical list). O2 is read directly. Sanity check that the
    canonical lists match what the docstring + CLAUDE.md describe."""
    assert 'Na' in METAL_PRODUCT_SPECIES
    assert 'Fe' in METAL_PRODUCT_SPECIES
    assert 'Si' in METAL_PRODUCT_SPECIES
    assert 'Al' in METAL_PRODUCT_SPECIES
    # SiO is NOT here — it's a gas-phase silicate oxide, mapped to
    # the silica-glass class.
    assert 'SiO' not in METAL_PRODUCT_SPECIES
    assert 'SiO2' not in METAL_PRODUCT_SPECIES
    # O2 is its own surface.
    assert O2_PRODUCT_SPECIES == ('O2',)


def test_metals_total_excludes_o2():
    """``metals_total_kg`` carries the metal-only sum; ``O2_kg`` is
    separate. ``class_total_kg`` = metals + O2 (the full class 1
    bookkeeping per CLAUDE.md § 5)."""
    sim = SimpleNamespace(
        product_ledger=lambda: {
            'Fe': 5.0, 'Na': 1.0, 'O2': 2.0,
            'SomeOxide': 0.5,  # → unclassified
        },
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    assert result['metals_plus_O2']['metals_total_kg'] == 6.0
    assert result['metals_plus_O2']['O2_kg'] == 2.0
    assert result['metals_plus_O2']['class_total_kg'] == 8.0


# ---------------------------------------------------------------------------
# 3. Pure silica glass: Stage 3 capture
# ---------------------------------------------------------------------------

def test_silica_glass_reads_stage_3_collected_kg():
    """The Stage 3 fused-silica baffles surface is
    ``train.stages[3].collected_kg``. The classifier sums SiO + SiO2
    entries there."""
    stage_3 = SimpleNamespace(collected_kg={'SiO': 3.0, 'SiO2': 1.0})
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[None, None, None, stage_3]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    assert result['pure_silica_glass']['stage_3_capture_kg'] == 4.0
    assert result['pure_silica_glass']['stage_3_kg_by_species'] == {
        'SiO': 3.0, 'SiO2': 1.0,
    }


def test_silica_glass_zero_when_stage_3_missing():
    """No stage 3 in the train → zero capture, no crash."""
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    assert result['pure_silica_glass']['stage_3_capture_kg'] == 0.0
    assert result['pure_silica_glass']['stage_3_kg_by_species'] == {}


def test_captured_volatiles_include_condensation_train_account():
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=SimpleNamespace(
            kg_by_account=lambda acct: {
                'terminal.offgas': {'H2O': 1.0, 'O2': 2.0},
                'process.condensation_train': {'Na': 3.0, 'K': 4.0},
            }.get(acct, {})
        ),
    )

    result = classify_products(sim)

    assert result['captured_volatiles']['kg_by_species'] == {
        'H2O': 1.0,
        'K': 4.0,
        'Na': 3.0,
    }
    assert result['captured_volatiles']['class_total_kg'] == pytest.approx(8.0)


def test_spent_reductant_residue_surfaces_as_process_inventory_bucket():
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=SimpleNamespace(
            kg_by_account=lambda acct: {
                'process.cleaned_melt': {'CaO': 1.0},
                'process.spent_reductant_residue': {'Na2O': 2.5},
            }.get(acct, {})
        ),
        _terminal_rump_by_species=lambda: {'CaO': 1.0},
    )

    result = classify_products(sim)

    assert result['process_inventory_spent_reductant'] == {
        'kg_by_species': {'Na2O': 2.5},
        'class_total_kg': 2.5,
        'account': 'process.spent_reductant_residue',
        'disposition': 'process_inventory_spent_reductant',
    }
    assert result['refractory_ceramic_rump']['rump_kg_by_species'] == {'CaO': 1.0}
    assert 'Na2O' not in result['refractory_ceramic_rump']['rump_kg_by_species']
    assert 'Na2O' not in result['unclassified']['kg_by_species']


# ---------------------------------------------------------------------------
# 4. Refractory ceramic rump
# ---------------------------------------------------------------------------

def test_rump_reads_terminal_rump_method():
    """The classifier prefers ``_terminal_rump_by_species`` when
    available (the canonical surface). Empty dict / missing method
    is the no-rump degenerate."""
    sim_with_rump = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
        _terminal_rump_by_species=lambda: {'CaO': 5.0, 'REE_oxides': 0.5},
    )
    result = classify_products(sim_with_rump)
    assert result['refractory_ceramic_rump']['rump_total_kg'] == 5.5
    assert (
        result['refractory_ceramic_rump']['rump_kg_by_species']
        == {'CaO': 5.0, 'REE_oxides': 0.5}
    )


def test_rump_surfaces_nonzero_other_bucket_and_mass_closes():
    """The terminal rump report must not hide the accounting ``other`` bucket."""
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
        _terminal_rump_by_species=lambda: {
            'CaO': 4.0,
            'SiO2': 1.0,
            'Fe': 0.5,
            'NaCl': 0.25,
        },
        _terminal_rump_by_class=lambda: {
            'refractory_oxides': 4.0,
            'silicate_residual': 1.0,
            'unextracted_metals': 0.5,
            'other': 0.25,
        },
    )

    result = classify_products(sim)
    bucket = result['refractory_ceramic_rump']

    assert bucket['rump_total_kg'] == pytest.approx(5.75)
    assert bucket['rump_refractory_oxides_kg'] == pytest.approx(4.0)
    assert bucket['rump_silicate_residual_kg'] == pytest.approx(1.0)
    assert bucket['rump_unextracted_metals_kg'] == pytest.approx(0.5)
    assert bucket['rump_other_kg'] == pytest.approx(0.25)

    surfaced_total_kg = (
        bucket['rump_refractory_oxides_kg']
        + bucket['rump_silicate_residual_kg']
        + bucket['rump_unextracted_metals_kg']
        + bucket['rump_other_kg']
    )
    error_pct = abs(surfaced_total_kg - bucket['rump_total_kg']) / (
        bucket['rump_total_kg']
    ) * 100.0
    assert error_pct <= TERMINAL_RUMP_CLASS_TOLERANCE_PCT

    assert bucket['rump_refractory_oxides_kg'] > 0.0
    assert bucket['rump_silicate_residual_kg'] > 0.0
    assert bucket['rump_unextracted_metals_kg'] > 0.0
    assert bucket['rump_other_kg'] > 0.0
    assert (
        bucket['rump_refractory_oxides_kg']
        != bucket['rump_unextracted_metals_kg']
    )
    assert bucket['rump_unextracted_metals_kg'] != bucket['rump_other_kg']


def test_rump_zero_when_method_missing():
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    assert result['refractory_ceramic_rump']['rump_total_kg'] == 0.0


# ---------------------------------------------------------------------------
# 5. Industrial mixed glass: early-tap detection
# ---------------------------------------------------------------------------

def test_mixed_glass_zero_by_default_even_with_cleaned_melt():
    """Per evening-4commits review P2 #2: ``cleaned_melt`` at any
    mid-run tick is NOT a mixed-glass product — it's the melt
    sitting in the crucible waiting for the next campaign. The
    classifier MUST default to zero for this bucket unless the
    operator explicitly declares early-tap intent."""
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=SimpleNamespace(
            kg_by_account=lambda acct: (
                {'SiO2': 100.0, 'Al2O3': 20.0}
                if acct == 'process.cleaned_melt' else {}
            ),
        ),
    )
    result = classify_products(sim)
    # Default behaviour: mixed-glass bucket zeroed — operator hasn't
    # declared early-tap intent.
    assert (
        result['industrial_mixed_glass']['mixed_melt_residual_kg']
        == 0.0
    )
    assert (
        result['industrial_mixed_glass']['early_tap_mode'] is False
    )


def test_mixed_glass_counts_cleaned_melt_only_in_early_tap_mode():
    """With ``early_tap_mode=True``, the cleaned_melt residual IS
    the mixed-glass product (operator has chosen to tap before
    C5/C6). 100 + 20 = 120 kg residual melt → 120 kg mixed-glass
    when the explicit intent flag is on."""
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=SimpleNamespace(
            kg_by_account=lambda acct: (
                {'SiO2': 100.0, 'Al2O3': 20.0}
                if acct == 'process.cleaned_melt' else {}
            ),
        ),
    )
    result = classify_products(sim, early_tap_mode=True)
    assert (
        result['industrial_mixed_glass']['mixed_melt_residual_kg']
        == 120.0
    )
    assert (
        result['industrial_mixed_glass']['early_tap_mode'] is True
    )


# ---------------------------------------------------------------------------
# 6. Defensive: empty / degenerate inputs
# ---------------------------------------------------------------------------

def test_classifier_handles_empty_product_ledger():
    """A fresh sim with no products at all returns the empty-but-
    well-formed shape — no exceptions."""
    sim = SimpleNamespace(
        product_ledger=lambda: {},
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    assert result['metals_plus_O2']['class_total_kg'] == 0.0
    assert result['pure_silica_glass']['class_total_kg'] == 0.0
    assert result['refractory_ceramic_rump']['class_total_kg'] == 0.0
    assert result['unclassified']['total_kg'] == 0.0


def test_classifier_handles_missing_product_ledger_method():
    """A sim-like object with no ``product_ledger`` method falls
    back to empty dict."""
    sim = SimpleNamespace(
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    # Empty everything.
    for bucket in (
        'metals_plus_O2', 'pure_silica_glass',
        'industrial_mixed_glass', 'refractory_ceramic_rump',
    ):
        assert result[bucket]['class_total_kg'] == 0.0


def test_classifier_handles_non_coercible_product_kg():
    """Defensive: a product entry with a non-coercible kg gets
    skipped, not raised."""
    sim = SimpleNamespace(
        product_ledger=lambda: {
            'Fe': 5.0,
            'BadEntry': 'not a number',
            'AnotherBad': None,
        },
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    # Fe surfaces; bad entries skipped.
    assert result['metals_plus_O2']['metals_kg']['Fe'] == 5.0
    assert 'BadEntry' not in result['unclassified']['kg_by_species']


# ---------------------------------------------------------------------------
# 7. End-to-end: real sim integration
# ---------------------------------------------------------------------------

def test_classifier_end_to_end_on_short_c2a_run():
    """Drive a SimSession through a short C2A run; classify the
    result. The output is well-formed; we don't assert specific
    masses (those depend on the recipe + are E1b territory)."""
    session = SimSession().start(_config(campaign="C2A"))
    sim = session.simulator
    for _ in range(4):
        sim.step()
    result = classify_products(sim)
    # All buckets present + finite.
    assert all(
        isinstance(result[bucket]['class_total_kg'], float)
        and result[bucket]['class_total_kg'] >= 0.0
        for bucket in (
            'metals_plus_O2', 'pure_silica_glass',
            'industrial_mixed_glass', 'refractory_ceramic_rump',
        )
    )


def test_classifier_unclassified_bin_catches_unknown_species():
    """Future-proofing: if a new species lands in product_ledger
    that the canonical lists don't cover, it MUST surface in the
    'unclassified' bin so operators see the mapping gap."""
    sim = SimpleNamespace(
        product_ledger=lambda: {
            'NewExoticHalide': 2.5,  # not in any canonical list
            'Fe': 1.0,
        },
        train=SimpleNamespace(stages=[]),
        atom_ledger=None,
    )
    result = classify_products(sim)
    assert result['unclassified']['kg_by_species'] == {
        'NewExoticHalide': 2.5,
    }
    assert result['unclassified']['total_kg'] == 2.5
    # Fe still maps correctly to metals.
    assert result['metals_plus_O2']['metals_kg']['Fe'] == 1.0
