"""Tests for the MAGEMin kernel-shadow provider scaffold.

See ``engines/magemin/README.md`` and
``docs-private/codex-goal-queue-2026-05-14.md`` (\\goal
MAGEMIN-SHADOW-PARITY) for the contract these tests defend.

These tests cover the scaffold ONLY:

- :class:`MAGEMinDomainGate.validate` accepts basalt, rejects regolith
  laced with halides + native Fe.
- :class:`MAGEMinParityComparator.compare` returns ``agreement=True``
  for identical synthetic results.
- :class:`MAGEMinParityComparator.compare` returns ``agreement=False``
  with a warning for a synthetic 100 K liquidus delta.
- :class:`MAGEMinShadowProvider.dispatch` raises ``NotImplementedError``
  with a kernel-pointer message.

Kernel-shape tests (account view filtering, intent authority enforcement,
atom-balance gating) live under ``\\goal CHEMISTRY-KERNEL-CARVE-OUT`` and
are deliberately out of scope here.
"""

from __future__ import annotations

import pytest

from engines.magemin import (
    MAGEMinDomainGate,
    MAGEMinParityComparator,
    MAGEMinShadowProvider,
    ParityReport,
)


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
    """Regolith with native Fe + halides — must fail the domain gate."""
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
    # The combined warnings must call out the forbidden species explicitly.
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
    # Push SiO2 above the 80 wt% upper bound (rhyolite+).
    composition['SiO2'] = 88.0
    composition['Al2O3'] = 3.0  # rebalance roughly; remaining oxides still sum > 95.
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
        liquidus_T_K=1350.0,  # 100 K below authoritative — twice tolerance.
        phase_modes_wt_pct={'liquid': 75.0, 'olivine': 15.0, 'cpx': 10.0},
    )
    report = comp.compare(auth, shadow)
    assert report.agreement is False
    assert report.liquidus_T_delta_K == pytest.approx(100.0)
    assert report.warnings, 'expected at least one warning'
    assert any('liquidus delta' in w for w in report.warnings)
    # The comparator must not silently average — both numbers stay visible.
    joined = ' '.join(report.warnings)
    assert '+100' in joined.replace(' ', '') or '100.0' in joined


def test_parity_50K_liquidus_at_tolerance_agrees():
    # The tolerance is inclusive: a delta exactly at the boundary is fine.
    comp = MAGEMinParityComparator()
    auth = _synthetic_result(
        liquidus_T_K=1450.0,
        phase_modes_wt_pct={'liquid': 100.0},
    )
    shadow = _synthetic_result(
        liquidus_T_K=1400.0,  # exactly 50 K below — at tolerance.
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
        # 5 wt% disagreement on liquid and olivine — above 2 wt% tolerance.
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
        # spinel appears only in shadow at 10 wt%, well above tolerance.
    )
    report = comp.compare(auth, shadow)
    assert report.agreement is False
    assert 'spinel' in report.phases_only_in_shadow
    assert report.phases_only_in_authoritative == ()


def test_parity_does_not_treat_equilibration_temperature_as_liquidus():
    # `temperature_C` on an EquilibriumResult is the temperature the melt
    # was equilibrated AT -- not its liquidus. The comparator must not
    # fall back to it: two results equilibrated at the same T would
    # otherwise report liquidus_T_delta_K = 0 / agreement = True, a
    # silent false positive. With no real liquidus on either side the
    # conservative "cannot evaluate parity" branch must fire instead.
    comp = MAGEMinParityComparator()
    auth = {'temperature_C': 1600.0}
    shadow = {'temperature_C': 1600.0}

    report = comp.compare(auth, shadow)

    assert report.agreement is False
    assert report.liquidus_T_delta_K is None
    assert any('cannot evaluate parity' in w for w in report.warnings)

    # An explicit liquidus field IS still honored.
    auth_real = {'temperature_C': 1600.0, 'liquidus_T_C': 1350.0}
    shadow_real = {'temperature_C': 1600.0, 'liquidus_T_C': 1340.0}
    real_report = comp.compare(auth_real, shadow_real)
    assert real_report.liquidus_T_delta_K == pytest.approx(10.0)
    assert real_report.agreement is True


# ----------------------------------------------------------------------
# Provider dispatch
# ----------------------------------------------------------------------


def test_provider_intent_surface():
    provider = MAGEMinShadowProvider()
    intents = provider.intents()
    assert intents == frozenset({'SILICATE_LIQUIDUS', 'SILICATE_EQUILIBRIUM'})


def test_provider_is_never_authoritative():
    provider = MAGEMinShadowProvider()
    for intent in ('SILICATE_LIQUIDUS', 'SILICATE_EQUILIBRIUM',
                   'VAPOR_PRESSURE', 'EVAPORATION_FLUX'):
        assert provider.is_authoritative_for(intent) is False


def test_provider_does_not_emit_ledger_transition():
    provider = MAGEMinShadowProvider()
    assert provider.emits_ledger_transition() is False


def test_provider_capability_profile_advertises_shadow_only():
    profile = MAGEMinShadowProvider().capability_profile()
    assert profile['engine'] == 'magemin'
    assert profile['authoritative_intents'] == frozenset()
    assert profile['shadow_intents'] == frozenset(
        {'SILICATE_LIQUIDUS', 'SILICATE_EQUILIBRIUM'}
    )
    assert profile['pressure_unit'] == 'GPa'


def test_dispatch_raises_not_implemented_with_clear_message():
    provider = MAGEMinShadowProvider()
    with pytest.raises(NotImplementedError) as exc_info:
        provider.dispatch(object())
    message = str(exc_info.value)
    assert 'chemistry kernel' in message.lower()
    assert 'CHEMISTRY-KERNEL-CARVE-OUT' in message
    assert 'MAGEMIN-SHADOW-PARITY' in message
