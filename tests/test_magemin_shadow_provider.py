"""Tests for the MAGEMin kernel-shadow provider.

See ``engines/magemin/README.md``, ``engines/magemin/parity.py`` and
goal #9 ``MAGEMIN-SHADOW-PARITY`` for the contract these tests defend.

These tests cover:

- :class:`MAGEMinDomainGate.validate` accepts basalt, rejects regolith
  laced with halides + native Fe.
- :class:`MAGEMinParityComparator.compare` returns ``agreement=True``
  for identical synthetic results.
- :class:`MAGEMinParityComparator.compare` returns ``agreement=False``
  with a warning for a synthetic 100 K liquidus delta.
- :class:`MAGEMinShadowProvider.capability_profile` declares shadow
  intent surface plus the gate fallback authority.
- :class:`MAGEMinShadowProvider.dispatch` is now wired through the
  kernel; the writer-purity contract (transition=None always) is
  enforced by tests at the dispatch level.

Cross-engine parity tests (the planner running authoritative +
MAGEMin shadow alongside, with parity warnings appearing in the
shadow trace) live under
``tests/chemistry/test_magemin_shadow.py``.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from engines.magemin import (
    MAGEMinDomainGate,
    MAGEMinParityComparator,
    MAGEMinShadowDiagnostics,
    MAGEMinShadowProvider,
    ParityReport,
)
import engines.magemin.provider as provider_module
from simulator.chemistry.kernel.capabilities import ChemistryIntent


# ----------------------------------------------------------------------
# Domain gate
# ----------------------------------------------------------------------


def _basalt_wt_pct() -> dict:
    """A nominal lunar/Mars basalt analog within MAGEMin's calibration."""
    return {
        'SiO2': 49.0,
        'TiO2': 1.5,
        'Al2O3': 14.0,
        'FeO': 10.0,
        'Fe2O3': 1.0,
        'MgO': 9.0,
        'CaO': 11.0,
        'Na2O': 2.5,
        'K2O': 0.8,
        'Cr2O3': 0.2,
        'MnO': 0.2,
        'P2O5': 0.3,
        'NiO': 0.02,
        'CoO': 0.01,
    }


def _hostile_regolith_wt_pct() -> dict:
    """Regolith with native Fe + halides -- must fail the domain gate."""
    return {
        'SiO2': 45.0,
        'Al2O3': 12.0,
        'MgO': 8.0,
        'CaO': 10.0,
        'FeO': 8.0,
        # Forbidden species: native Fe, chloride, perchlorate.
        'Fe': 6.0,
        'NaCl': 1.5,
        'Mg(ClO4)2': 0.8,
        # And a known sulfide.
        'FeS': 2.0,
    }


def test_domain_gate_accepts_basalt():
    valid, warnings = MAGEMinDomainGate.validate(_basalt_wt_pct())
    assert valid is True
    assert warnings == []


def test_domain_gate_rejects_halides_and_native_metals():
    valid, warnings = MAGEMinDomainGate.validate(_hostile_regolith_wt_pct())
    assert valid is False
    assert warnings, 'expected at least one warning for hostile regolith'
    joined = ' '.join(warnings)
    assert 'non-oxide species present' in joined
    assert 'Fe' in joined  # native Fe flagged
    assert 'NaCl' in joined  # halide flagged


def test_domain_gate_rejects_empty_composition():
    valid, warnings = MAGEMinDomainGate.validate({})
    assert valid is False
    assert any('empty composition' in w for w in warnings)


def test_domain_gate_does_not_raise_on_unparseable_values():
    # Non-numeric values are skipped (not raised). Result is empty -> invalid.
    valid, warnings = MAGEMinDomainGate.validate({'SiO2': 'bad'})
    assert valid is False
    assert warnings, 'expected warning for unparseable input'


def test_domain_gate_flags_sio2_outside_range():
    composition = _basalt_wt_pct()
    composition['SiO2'] = 88.0
    composition['Al2O3'] = 3.0
    composition['FeO'] = 2.0
    composition['MgO'] = 1.0
    valid, warnings = MAGEMinDomainGate.validate(composition)
    assert valid is False
    assert any('SiO2' in w and 'outside' in w for w in warnings)


# ----------------------------------------------------------------------
# Parity comparator
# ----------------------------------------------------------------------


def _synthetic_result(
    *,
    liquidus_T_K: float,
    phase_modes_wt_pct: dict,
) -> dict:
    return {
        'liquidus_T_K': liquidus_T_K,
        'phase_modes_wt_pct': dict(phase_modes_wt_pct),
        'phases_present': sorted(phase_modes_wt_pct),
    }


def test_parity_identical_results_agree():
    comp = MAGEMinParityComparator()
    auth = _synthetic_result(
        liquidus_T_K=1450.0,
        phase_modes_wt_pct={'liquid': 75.0, 'olivine': 15.0, 'cpx': 10.0},
    )
    shadow = _synthetic_result(
        liquidus_T_K=1450.0,
        phase_modes_wt_pct={'liquid': 75.0, 'olivine': 15.0, 'cpx': 10.0},
    )
    report = comp.compare(auth, shadow)
    assert isinstance(report, ParityReport)
    assert report.agreement is True
    assert report.warnings == []
    assert report.liquidus_T_delta_K == 0.0
    assert report.mode_pct_max_delta == 0.0
    assert report.phases_only_in_authoritative == ()
    assert report.phases_only_in_shadow == ()


def test_parity_100K_liquidus_delta_disagrees():
    comp = MAGEMinParityComparator()
    auth = _synthetic_result(
        liquidus_T_K=1450.0,
        phase_modes_wt_pct={'liquid': 75.0, 'olivine': 15.0, 'cpx': 10.0},
    )
    shadow = _synthetic_result(
        liquidus_T_K=1350.0,
        phase_modes_wt_pct={'liquid': 75.0, 'olivine': 15.0, 'cpx': 10.0},
    )
    report = comp.compare(auth, shadow)
    assert report.agreement is False
    assert report.liquidus_T_delta_K == pytest.approx(100.0)
    assert report.warnings, 'expected at least one warning'
    assert any('liquidus delta' in w for w in report.warnings)
    joined = ' '.join(report.warnings)
    assert '+100' in joined.replace(' ', '') or '100.0' in joined


def test_parity_50K_liquidus_at_tolerance_agrees():
    comp = MAGEMinParityComparator()
    auth = _synthetic_result(
        liquidus_T_K=1450.0,
        phase_modes_wt_pct={'liquid': 100.0},
    )
    shadow = _synthetic_result(
        liquidus_T_K=1400.0,
        phase_modes_wt_pct={'liquid': 100.0},
    )
    report = comp.compare(auth, shadow)
    assert report.agreement is True
    assert report.liquidus_T_delta_K == pytest.approx(50.0)
    assert report.warnings == []


def test_parity_modal_disagreement_above_tolerance():
    comp = MAGEMinParityComparator()
    auth = _synthetic_result(
        liquidus_T_K=1450.0,
        phase_modes_wt_pct={'liquid': 75.0, 'olivine': 15.0, 'cpx': 10.0},
    )
    shadow = _synthetic_result(
        liquidus_T_K=1450.0,
        phase_modes_wt_pct={'liquid': 70.0, 'olivine': 20.0, 'cpx': 10.0},
    )
    report = comp.compare(auth, shadow)
    assert report.agreement is False
    assert report.mode_pct_max_delta == pytest.approx(5.0)
    assert any('modal disagreement' in w for w in report.warnings)


def test_parity_phase_only_in_shadow_flagged():
    comp = MAGEMinParityComparator()
    auth = _synthetic_result(
        liquidus_T_K=1450.0,
        phase_modes_wt_pct={'liquid': 85.0, 'olivine': 15.0},
    )
    shadow = _synthetic_result(
        liquidus_T_K=1450.0,
        phase_modes_wt_pct={'liquid': 75.0, 'olivine': 15.0, 'spinel': 10.0},
    )
    report = comp.compare(auth, shadow)
    assert report.agreement is False
    assert 'spinel' in report.phases_only_in_shadow
    assert report.phases_only_in_authoritative == ()


def test_parity_does_not_treat_equilibration_temperature_as_liquidus():
    comp = MAGEMinParityComparator()
    auth = {'temperature_C': 1600.0}
    shadow = {'temperature_C': 1600.0}

    report = comp.compare(auth, shadow)

    assert report.agreement is False
    assert report.liquidus_T_delta_K is None
    assert any('cannot evaluate parity' in w for w in report.warnings)

    auth_real = {'temperature_C': 1600.0, 'liquidus_T_C': 1350.0}
    shadow_real = {'temperature_C': 1600.0, 'liquidus_T_C': 1340.0}
    real_report = comp.compare(auth_real, shadow_real)
    assert real_report.liquidus_T_delta_K == pytest.approx(10.0)
    assert real_report.agreement is True


# ----------------------------------------------------------------------
# Provider capability profile (post-promotion to kernel ABC)
# ----------------------------------------------------------------------


def test_provider_capability_profile_declares_silicate_intent_set():
    profile = MAGEMinShadowProvider().capability_profile()
    assert profile.intents == frozenset({
        ChemistryIntent.SILICATE_LIQUIDUS,
        ChemistryIntent.SILICATE_EQUILIBRIUM,
        ChemistryIntent.GATE_LIQUID_FRACTION,
    })


def test_provider_capability_profile_authority_limited_to_gate_intent():
    """MAGEMin stays shadow-only for full silicate-state intents."""
    profile = MAGEMinShadowProvider().capability_profile()
    assert profile.is_authoritative_for == frozenset({
        ChemistryIntent.GATE_LIQUID_FRACTION,
    })
    for intent in (
        ChemistryIntent.SILICATE_LIQUIDUS,
        ChemistryIntent.SILICATE_EQUILIBRIUM,
    ):
        assert not profile.is_authoritative(intent)
    assert profile.is_authoritative(ChemistryIntent.GATE_LIQUID_FRACTION)


def test_provider_declares_only_cleaned_melt_account():
    profile = MAGEMinShadowProvider().capability_profile()
    assert profile.declared_accounts == frozenset({'process.cleaned_melt'})


def test_provider_does_not_declare_unrelated_intents():
    """Defence in depth: only silicate shadows plus gate fallback dispatch."""
    profile = MAGEMinShadowProvider().capability_profile()
    for intent in ChemistryIntent:
        if intent in (
            ChemistryIntent.SILICATE_LIQUIDUS,
            ChemistryIntent.SILICATE_EQUILIBRIUM,
            ChemistryIntent.GATE_LIQUID_FRACTION,
        ):
            assert profile.can_dispatch(intent)
        else:
            assert not profile.can_dispatch(intent)


# ----------------------------------------------------------------------
# Writer-purity: provider module must NOT import LedgerTransitionProposal
# ----------------------------------------------------------------------


def test_no_ledger_transition_import():
    """AST walk -- goal #9 forbids any LedgerTransitionProposal reference.

    A shadow provider that imported the proposal DTO would be one
    refactor away from accidentally constructing one. The kernel's
    writer-purity invariant would catch the resulting commit attempt
    at runtime, but blocking the import at the module-source level
    closes the door before it can be opened.
    """
    source_path = Path(inspect.getsourcefile(provider_module))
    tree = ast.parse(source_path.read_text())

    bad_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.name
                if 'LedgerTransition' in name:
                    bad_names.add(name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if 'LedgerTransition' in alias.name:
                    bad_names.add(alias.name)
        elif isinstance(node, ast.Attribute):
            # Catches ``module.LedgerTransitionProposal`` qualified refs.
            if 'LedgerTransition' in node.attr:
                bad_names.add(node.attr)
        elif isinstance(node, ast.Name):
            if 'LedgerTransition' in node.id:
                bad_names.add(node.id)
    assert not bad_names, (
        f'provider module references {sorted(bad_names)}; '
        'MAGEMin shadow provider must NOT import LedgerTransition*'
    )


def test_provider_diagnostic_shape_matches_alphamelts_keys():
    """Parity comparator looks up the same keys on both engines.

    Goal #9 binds the shadow trace to record an apples-to-apples
    comparison. The parity comparator (engines/magemin/parity.py)
    pulls ``liquidus_T_K`` / ``liquidus_T_C`` / ``phase_modes_wt_pct``
    from each side; the diagnostic projections on both providers MUST
    expose the same keys.
    """
    diag = MAGEMinShadowDiagnostics(
        liquidus_T_K=1700.0,
        liquidus_T_C=1426.85,
        phases_present=('liquid',),
        phase_modes_wt_pct={'liquid': 100.0},
    ).as_diagnostic()
    for key in (
        'liquidus_T_K',
        'liquidus_T_C',
        'phases_present',
        'phase_modes_wt_pct',
        'liquid_composition_wt_pct',
        'mode',
        'engine_version',
        'backend_status',
        'backend_warnings',
    ):
        assert key in diag, f'diagnostic missing key {key!r}'
