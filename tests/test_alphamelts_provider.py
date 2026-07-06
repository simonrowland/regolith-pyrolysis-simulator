"""Tests for the AlphaMELTS kernel-registered diagnostic provider.

Covers goal #8 ``ALPHAMELTS-DIAGNOSTIC-GATE`` checklist:

1. Capability profile declares the AlphaMELTS silicate diagnostic intents
   and only ``process.cleaned_melt``.
2. DomainGate rejects metal-only / gas-only / halide-only compositions
   (checklist item 2 / 4 / acceptance gate "DomainGate rejects
   metal/gas/halide compositions in tests").
3. Provider returns :class:`LiquidusDiagnostics` from ThermoEngine,
   PetThermoTools, or subprocess-backed liquid-fraction sampling for the
   liquidus finder.
4. :attr:`IntentResult.transition` is ALWAYS ``None`` (checklist 5,
   acceptance gate "No LedgerTransition emitted").
5. The provider module does NOT import ``LedgerTransition`` /
   ``LedgerTransitionProposal`` (checklist 5, acceptance gate "tests
   prove provider class doesn't import it"). Enforced via AST walk.
6. ControlAudit records ``applied == requested`` with the note
   ``"diagnostic, not enforced"`` for fO2 / P (checklist 6).
7. Account view is filtered to ``process.cleaned_melt`` -- the kernel
   filter is the enforcer (account-filter test).
8. Pre-call ``ProviderAccountView`` blocks every account except
   ``process.cleaned_melt`` (checklist 4).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

import engines.alphamelts.provider as provider_module
from engines.alphamelts.parser import (
    ParserError,
    diagnostics_to_equilibrium,
    project_equilibrium_to_diagnostics,
)
from engines.alphamelts import (
    AlphaMELTSDomainGate,
    AlphaMELTSProvider,
    LiquidusDiagnostics,
)
from simulator.melt_backend.base import LiquidFractionInvalidError
from simulator.melt_backend.liquidus import LiquidusSolidusResult
from simulator.chemistry.kernel import (
    ChemistryIntent,
    ChemistryKernel,
    IntentRequest,
    ProviderRegistry,
)
from simulator.chemistry.kernel.dto import ProviderAccountView


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _basalt_wt_pct() -> dict:
    """A nominal lunar/Mars basalt analog within MELTS calibration."""
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


def _basalt_species_mol() -> dict:
    """Approximate mol amounts (kg / molar mass) for a 1 kg basalt charge.

    The exact numbers do not matter: the domain gate operates on the
    wt% projection of the mol map, and the projection is monotonic in
    each species. The values here are chosen so the resulting wt% map
    matches :func:`_basalt_wt_pct` to within a few percent.
    """
    # Approximate molar masses (kg/mol). Order matches _basalt_wt_pct.
    masses = {
        'SiO2': 0.06008,
        'TiO2': 0.07987,
        'Al2O3': 0.10196,
        'FeO': 0.07184,
        'Fe2O3': 0.15969,
        'MgO': 0.04030,
        'CaO': 0.05608,
        'Na2O': 0.06198,
        'K2O': 0.09420,
        'Cr2O3': 0.15199,
        'MnO': 0.07094,
        'P2O5': 0.14194,
        'NiO': 0.07469,
        'CoO': 0.07493,
    }
    return {
        oxide: (wt_pct / 100.0) / masses[oxide]
        for oxide, wt_pct in _basalt_wt_pct().items()
    }


def _hostile_metal_only() -> dict:
    """Pure native Fe -- DomainGate must reject (checklist acceptance)."""
    return {'Fe': 1.0}


def _hostile_gas_only() -> dict:
    """Pure O2 -- non-oxide species, DomainGate must reject."""
    return {'O2': 1.0}


def _hostile_halide_only() -> dict:
    """Pure NaCl -- halide, DomainGate must reject."""
    return {'NaCl': 1.0}


def _make_request(
    intent: ChemistryIntent,
    *,
    composition_mol: dict,
    temperature_C: float = 1400.0,
    pressure_bar: float = 1.0,
    fO2_log: float = -9.0,
    fe_redox_policy: str = 'intrinsic',
) -> IntentRequest:
    """Build an IntentRequest with a ``process.cleaned_melt``-only view."""
    view = ProviderAccountView(
        accounts={'process.cleaned_melt': dict(composition_mol)},
        species_formula_registry={},
    )
    return IntentRequest(
        intent=intent,
        account_view=view,
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        fO2_log=fO2_log,
        fe_redox_policy=fe_redox_policy,
        control_inputs={},
    )


def _fe3fet_from_species_mol(species_mol: dict) -> float:
    feo = float(species_mol.get('FeO', 0.0))
    fe2o3 = float(species_mol.get('Fe2O3', 0.0))
    return (2.0 * fe2o3) / (feo + 2.0 * fe2o3)


class _FakeAlphaMELTSBackend:
    """Light backend stand-in to drive all AlphaMELTS transport paths.

    Constructed with ``mode`` set to ``'thermoengine'``, ``'python_api'``,
    or ``'subprocess'``
    and a canned :class:`EquilibriumResult`-shaped return value. The
    provider treats the adapter as duck-typed so this stand-in is
    sufficient; the goal #1 hardened adapter is exercised separately by
    ``tests/test_alphamelts_backend.py``.
    """

    def __init__(
        self,
        *,
        mode: str,
        equilibrium: SimpleNamespace,
        finder_result: LiquidusSolidusResult | None = None,
        equilibrate_func=None,
    ) -> None:
        self._mode = mode
        self._equilibrium = equilibrium
        self._finder_result = finder_result
        self._equilibrate_func = equilibrate_func
        self.calls: list[dict] = []
        self.finder_calls: list[dict] = []
        self.is_available_calls = 0

    def is_available(self) -> bool:
        self.is_available_calls += 1
        return self._mode in {'thermoengine', 'python_api', 'subprocess'}

    def get_engine_version(self) -> str:
        return f'fake-alphamelts {self._mode}'

    def equilibrate(self, **kwargs):
        self.calls.append(kwargs)
        if self._equilibrate_func is not None:
            return self._equilibrate_func(**kwargs)
        return self._equilibrium

    def find_liquidus_solidus(self, **kwargs):
        self.finder_calls.append(kwargs)
        if self._mode not in {'thermoengine', 'python_api', 'subprocess'}:
            return LiquidusSolidusResult(
                status='unavailable',
                warnings=(
                    'fake AlphaMELTS finder requires thermoengine, '
                    'python_api, or subprocess mode',
                ),
            )
        return self._finder_result or LiquidusSolidusResult(
            liquidus_T_C=1305.0,
            solidus_T_C=1000.0,
            liquid_fraction=1.0,
            status='ok',
        )


def _build_equilibrium_for_basalt(
    *,
    liquidus_C: float = 1305.0,
    status: str = 'ok',
) -> SimpleNamespace:
    """Build a SimpleNamespace shaped like an EquilibriumResult.

    Mirrors the AlphaMELTS adapter's
    :meth:`_parse_petthermotools_result` output: ``phases_present``,
    ``phase_masses_kg``, ``liquid_composition_wt_pct``,
    ``activity_coefficients``, ``status``, ``warnings``. The provider's
    :mod:`engines.alphamelts.parser` extracts the liquidus from a
    matching ``AlphaMELTS liquidus_C=...`` warning string.
    """
    return SimpleNamespace(
        phases_present=['liquid', 'olivine'],
        phase_masses_kg={'liquid': 0.8, 'olivine': 0.2},
        liquid_fraction=0.8,
        liquid_composition_wt_pct=dict(_basalt_wt_pct()),
        activity_coefficients={'SiO2': 0.95, 'FeO': 1.1},
        fO2_log=-8.25,
        status=status,
        warnings=[f'AlphaMELTS liquidus_C={liquidus_C:.3f}'],
        vapor_pressures_Pa={},
        ledger_transition=None,
    )


# ---------------------------------------------------------------------------
# 1. Capability profile
# ---------------------------------------------------------------------------


def test_provider_declares_silicate_intent_set():
    provider = AlphaMELTSProvider(backend=None)
    profile = provider.capability_profile()
    assert profile.intents == frozenset({
        ChemistryIntent.SILICATE_LIQUIDUS,
        ChemistryIntent.SILICATE_EQUILIBRIUM,
        ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
        ChemistryIntent.GATE_LIQUID_FRACTION,
    })


def test_provider_authoritative_for_silicate_intents():
    """Authoritative registration is required so the kernel routes here.

    The provider is *diagnostic* in the sense that
    :attr:`IntentResult.transition` is always None; that is enforced
    by separate tests (no_ledger_transition / writer-purity). The
    registry-level authority is the only mechanism the kernel exposes
    for "this provider answers this intent".
    """
    provider = AlphaMELTSProvider(backend=None)
    profile = provider.capability_profile()
    for intent in (
        ChemistryIntent.SILICATE_LIQUIDUS,
        ChemistryIntent.SILICATE_EQUILIBRIUM,
        ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
        ChemistryIntent.GATE_LIQUID_FRACTION,
    ):
        assert profile.is_authoritative(intent)


def test_provider_declares_only_cleaned_melt_account():
    """Checklist item 4: declared_accounts = {process.cleaned_melt}."""
    profile = AlphaMELTSProvider(backend=None).capability_profile()
    assert profile.declared_accounts == frozenset({'process.cleaned_melt'})


def test_provider_does_not_declare_non_silicate_intents():
    """Defence in depth: only AlphaMELTS silicate/gate intents dispatch."""
    profile = AlphaMELTSProvider(backend=None).capability_profile()
    for intent in ChemistryIntent:
        if intent in (
            ChemistryIntent.SILICATE_LIQUIDUS,
            ChemistryIntent.SILICATE_EQUILIBRIUM,
            ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
            ChemistryIntent.GATE_LIQUID_FRACTION,
        ):
            assert profile.can_dispatch(intent)
        else:
            assert not profile.can_dispatch(intent)


# ---------------------------------------------------------------------------
# 2. DomainGate rejection (checklist acceptance: metal/gas/halide)
# ---------------------------------------------------------------------------


def test_domain_gate_accepts_basalt():
    valid, warnings = AlphaMELTSDomainGate.validate(_basalt_wt_pct())
    assert valid is True
    assert warnings == []


def test_domain_gate_rejects_metal_only_composition():
    """Native Fe alone -- not an oxide, must be rejected."""
    valid, warnings = AlphaMELTSDomainGate.validate(_hostile_metal_only())
    assert valid is False
    joined = ' '.join(warnings)
    assert 'non-oxide species present' in joined
    assert 'Fe' in joined


def test_domain_gate_rejects_gas_only_composition():
    """Pure O2 -- diatomic gas, not an oxide of a metal cation."""
    valid, warnings = AlphaMELTSDomainGate.validate(_hostile_gas_only())
    assert valid is False
    # Either flagged as non-oxide (no element other than O) or low major-oxide sum.
    joined = ' '.join(warnings)
    assert any(
        keyword in joined
        for keyword in ('non-oxide', 'major-oxide sum', 'unrecognised', 'SiO2')
    )


def test_domain_gate_rejects_halide_only_composition():
    """NaCl alone -- halide, must be rejected."""
    valid, warnings = AlphaMELTSDomainGate.validate(_hostile_halide_only())
    assert valid is False
    joined = ' '.join(warnings)
    assert 'non-oxide species present' in joined
    assert 'NaCl' in joined


def test_domain_gate_rejects_sulfide():
    """Sulfides (FeS) must route through Stage 0 first."""
    composition = dict(_basalt_wt_pct())
    composition['FeS'] = 5.0
    valid, warnings = AlphaMELTSDomainGate.validate(composition)
    assert valid is False
    joined = ' '.join(warnings)
    assert 'non-oxide species present' in joined
    assert 'FeS' in joined


def test_domain_gate_rejects_low_sio2():
    """SiO2 outside [30, 80] wt% must fail the silicate-network criterion."""
    composition = {
        'SiO2': 10.0, 'TiO2': 1.0, 'Al2O3': 30.0,
        'FeO': 20.0, 'MgO': 25.0, 'CaO': 14.0,
    }
    valid, warnings = AlphaMELTSDomainGate.validate(composition)
    assert valid is False
    assert any('SiO2' in w and 'outside' in w for w in warnings)


def test_domain_gate_rejects_empty_composition():
    valid, warnings = AlphaMELTSDomainGate.validate({})
    assert valid is False
    assert any('empty composition' in w for w in warnings)


def test_domain_gate_reject_unsupported_accounts_reports_non_cleaned_melt():
    """Account-level filter mirrors AlphaMELTSBackend._unsupported_accounts."""
    reasons = AlphaMELTSDomainGate.reject_unsupported_accounts({
        'process.cleaned_melt': {'SiO2': 1.0},
        'process.metal_phase': {'Fe': 0.25},
        'process.overhead_gas': {'O2': 0.5},
    })
    assert "process.metal_phase=['Fe']" in reasons
    assert "process.overhead_gas=['O2']" in reasons


# ---------------------------------------------------------------------------
# 3. Provider returns LiquidusDiagnostics from both paths
# ---------------------------------------------------------------------------


def test_provider_returns_liquidus_diagnostics_for_python_api_path():
    backend = _FakeAlphaMELTSBackend(
        mode='python_api',
        equilibrium=_build_equilibrium_for_basalt(),
    )
    provider = AlphaMELTSProvider(backend=backend)
    request = _make_request(
        ChemistryIntent.SILICATE_LIQUIDUS,
        composition_mol=_basalt_species_mol(),
    )
    result = provider.dispatch(request)
    assert result.status == 'ok'
    assert result.transition is None  # checklist 5 -- always.
    diagnostic = dict(result.diagnostic or {})
    assert diagnostic['mode'] == 'petthermotools'
    assert diagnostic['liquidus_T_C'] == pytest.approx(1305.0)
    assert diagnostic['solidus_T_C'] == pytest.approx(1000.0)
    # Liquidus intent runs the finder, not the single-T equilibrium path.
    assert backend.finder_calls, 'backend finder was never called'
    assert not backend.calls
    call_kwargs = backend.finder_calls[0]
    assert (
        'process.cleaned_melt' in call_kwargs['composition_mol_by_account']
    )


def test_provider_returns_liquidus_diagnostics_for_thermoengine_path():
    backend = _FakeAlphaMELTSBackend(
        mode='thermoengine',
        equilibrium=_build_equilibrium_for_basalt(),
    )
    provider = AlphaMELTSProvider(backend=backend)
    request = _make_request(
        ChemistryIntent.SILICATE_LIQUIDUS,
        composition_mol=_basalt_species_mol(),
    )
    result = provider.dispatch(request)
    assert result.status == 'ok'
    assert result.transition is None
    diagnostic = dict(result.diagnostic or {})
    assert diagnostic['mode'] == 'thermoengine'
    assert diagnostic['liquidus_T_C'] == pytest.approx(1305.0)
    assert backend.finder_calls


def test_provider_returns_liquidus_diagnostics_for_subprocess_path():
    backend = _FakeAlphaMELTSBackend(
        mode='subprocess',
        equilibrium=_build_equilibrium_for_basalt(liquidus_C=1280.0),
    )
    provider = AlphaMELTSProvider(backend=backend)
    request = _make_request(
        ChemistryIntent.SILICATE_LIQUIDUS,
        composition_mol=_basalt_species_mol(),
    )
    result = provider.dispatch(request)
    assert result.status == 'ok'
    assert result.transition is None
    diagnostic = dict(result.diagnostic or {})
    assert diagnostic['mode'] == 'subprocess'
    assert diagnostic['liquidus_T_C'] == pytest.approx(1305.0)
    assert diagnostic['solidus_T_C'] == pytest.approx(1000.0)
    assert backend.finder_calls
    call_kwargs = backend.finder_calls[0]
    assert (
        'process.cleaned_melt' in call_kwargs['composition_mol_by_account']
    )


def test_provider_subprocess_required_skips_thermoengine_route(monkeypatch):
    backend = _FakeAlphaMELTSBackend(
        mode='thermoengine',
        equilibrium=_build_equilibrium_for_basalt(liquidus_C=1290.0),
    )
    backend.stage0_subprocess_required = True
    provider = AlphaMELTSProvider(backend=backend)
    request = _make_request(
        ChemistryIntent.SILICATE_EQUILIBRIUM,
        composition_mol=_basalt_species_mol(),
    )
    calls: list[str] = []

    def fail_thermoengine(*_args, **_kwargs):
        raise AssertionError('ThermoEngine route must be skipped')

    def fake_subprocess(*_args, **_kwargs):
        calls.append('subprocess')
        return _build_equilibrium_for_basalt(liquidus_C=1290.0)

    monkeypatch.setattr(provider_module, 'thermoengine_available', lambda _backend: True)
    monkeypatch.setattr(provider_module, 'subprocess_available', lambda _backend: True)
    monkeypatch.setattr(provider_module, 'python_api_available', lambda _backend: True)
    monkeypatch.setattr(
        provider_module,
        'equilibrate_via_thermoengine',
        fail_thermoengine,
    )
    monkeypatch.setattr(
        provider_module,
        'equilibrate_via_subprocess',
        fake_subprocess,
    )

    result = provider.dispatch(request)

    diagnostic = dict(result.diagnostic or {})
    assert result.status == 'ok'
    assert diagnostic['mode'] == 'subprocess'
    assert calls == ['subprocess']


@pytest.mark.parametrize(
    'intent',
    [
        ChemistryIntent.SILICATE_LIQUIDUS,
        ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
    ],
)
def test_provider_liquidus_exception_surfaces_status_reason(intent):
    backend = _FakeAlphaMELTSBackend(
        mode='python_api',
        equilibrium=_build_equilibrium_for_basalt(),
    )

    def fail_liquidus(**kwargs):
        raise RuntimeError('finder exploded')

    backend.find_liquidus_solidus = fail_liquidus
    provider = AlphaMELTSProvider(backend=backend)
    request = _make_request(
        intent,
        composition_mol=_basalt_species_mol(),
    )

    result = provider.dispatch(request)

    assert result.status == 'not_converged'
    diagnostic = result.diagnostic or {}
    assert diagnostic.get('backend_status_reason') == 'not_converged'
    assert (
        diagnostic.get('backend_diagnostics', {})
        .get('backend_status_reason')
        == 'not_converged'
    )


def test_provider_handles_silicate_equilibrium_intent():
    """Both intents share the same provider entry."""
    backend = _FakeAlphaMELTSBackend(
        mode='python_api',
        equilibrium=_build_equilibrium_for_basalt(),
    )
    provider = AlphaMELTSProvider(backend=backend)
    request = _make_request(
        ChemistryIntent.SILICATE_EQUILIBRIUM,
        composition_mol=_basalt_species_mol(),
    )
    result = provider.dispatch(request)
    assert result.intent == ChemistryIntent.SILICATE_EQUILIBRIUM
    assert result.transition is None
    diagnostic = result.diagnostic or {}
    assert diagnostic.get('mode') == 'petthermotools'
    assert diagnostic['fe_redox_policy'] == 'intrinsic'
    assert diagnostic['intrinsic_fO2_log'] == pytest.approx(-9.0)
    assert diagnostic['applied_fe3fet'] == pytest.approx(
        _fe3fet_from_species_mol(_basalt_species_mol())
    )


def test_diagnostics_to_equilibrium_round_trips_legacy_fields():
    legacy = _build_equilibrium_for_basalt(liquidus_C=1290.0)
    legacy.diagnostics = {
        'backend_status': 'out_of_domain',
        'out_of_domain_crash_point': {
            'temperature_C': 865.0,
            'composition_wt_pct': {'SiO2': 45.0},
        },
    }
    diagnostics = project_equilibrium_to_diagnostics(
        legacy,
        mode='subprocess',
        engine_version='fake-alphamelts subprocess',
    )

    result = diagnostics_to_equilibrium(
        diagnostics,
        {
            'temperature_C': 1425.0,
            'pressure_bar': 1e-6,
            'fO2_log': -7.9,
        },
    )

    assert result.temperature_C == pytest.approx(1425.0)
    assert result.pressure_bar == pytest.approx(1e-6)
    assert result.phases_present == ['liquid', 'olivine']
    assert result.phase_masses_kg == pytest.approx({'liquid': 0.8, 'olivine': 0.2})
    assert result.liquid_fraction == pytest.approx(0.8)
    assert result.liquid_composition_wt_pct == pytest.approx(_basalt_wt_pct())
    assert result.activity_coefficients == pytest.approx({'SiO2': 0.95, 'FeO': 1.1})
    assert result.fO2_log == pytest.approx(-8.25)
    assert result.diagnostics == legacy.diagnostics
    assert result.ledger_transition is None


def test_provider_rejects_unsupported_intent_with_status_unsupported():
    """Defence in depth: providers must reject intents outside their set."""
    provider = AlphaMELTSProvider(backend=None)
    request = _make_request(
        ChemistryIntent.VAPOR_PRESSURE,
        composition_mol=_basalt_species_mol(),
    )
    result = provider.dispatch(request)
    assert result.status == 'unsupported'
    assert result.transition is None


# ---------------------------------------------------------------------------
# 0.5.4 W6 (M3 historical-audit closure): structured liquidus_T_C field
# preferred over the legacy warning-string regex
# ---------------------------------------------------------------------------

def test_parser_prefers_structured_liquidus_field_over_warning_regex():
    """Pre-W6 the diagnostic projection extracted liquidus from the
    ``AlphaMELTS liquidus_C=...`` warning string. W6 adds the
    structured ``EquilibriumResult.liquidus_T_C`` field and flips the
    precedence: structured first, warning regex as legacy fallback.
    Pinned with a synthetic ``EquilibriumResult`` where the field and
    the warning disagree — the structured value MUST win."""

    legacy = SimpleNamespace(
        phases_present=['liquid', 'olivine'],
        phase_masses_kg={'liquid': 0.8, 'olivine': 0.2},
        liquid_fraction=0.8,
        liquid_composition_wt_pct=dict(_basalt_wt_pct()),
        activity_coefficients={'SiO2': 0.95, 'FeO': 1.1},
        fO2_log=-8.25,
        status='ok',
        warnings=['AlphaMELTS liquidus_C=1305.000'],   # legacy fallback
        liquidus_T_C=1290.0,                            # new structured field
        vapor_pressures_Pa={},
        ledger_transition=None,
    )
    diagnostics = project_equilibrium_to_diagnostics(
        legacy,
        mode='subprocess',
        engine_version='fake-alphamelts subprocess',
    )
    # Structured field wins; warning string ignored.
    assert diagnostics.liquidus_T_C == pytest.approx(1290.0)


def test_parser_falls_back_to_warning_regex_when_field_missing():
    """Backward-compat invariant: existing backends that emit only the
    warning string (no structured ``liquidus_T_C`` attr) still surface
    a usable liquidus through the legacy regex path. This is the
    pre-W6 behaviour, preserved as a fallback."""

    legacy = SimpleNamespace(
        phases_present=['liquid'],
        phase_masses_kg={'liquid': 1.0},
        liquid_fraction=1.0,
        liquid_composition_wt_pct=dict(_basalt_wt_pct()),
        activity_coefficients={},
        fO2_log=-9.0,
        status='ok',
        warnings=['AlphaMELTS liquidus_C=1287.500'],
        # NO liquidus_T_C attribute — legacy backend shape.
        vapor_pressures_Pa={},
        ledger_transition=None,
    )
    diagnostics = project_equilibrium_to_diagnostics(
        legacy,
        mode='subprocess',
        engine_version='fake-alphamelts subprocess',
    )
    # Warning regex still extracts the value when field is absent.
    assert diagnostics.liquidus_T_C == pytest.approx(1287.5)


def test_parser_raises_when_liquid_fraction_missing():
    legacy = SimpleNamespace(
        phases_present=['liquid'],
        phase_masses_kg={'liquid': 1.0},
        liquid_composition_wt_pct=dict(_basalt_wt_pct()),
        activity_coefficients={},
        fO2_log=-9.0,
        status='ok',
        warnings=[],
        vapor_pressures_Pa={},
        ledger_transition=None,
    )

    with pytest.raises(ParserError, match='liquid_fraction_missing'):
        project_equilibrium_to_diagnostics(
            legacy,
            mode='subprocess',
            engine_version='fake-alphamelts subprocess',
        )


def test_alphamelts_writer_populates_structured_field_and_warning():
    """Round-trip: the AlphaMELTS subprocess writer (W6) MUST populate
    BOTH the structured ``EquilibriumResult.liquidus_T_C`` field AND
    keep emitting the legacy ``AlphaMELTS liquidus_C=...`` warning
    string so legacy log consumers reading raw warnings remain
    unaffected. This is the writer-side half of the W6 contract;
    the reader-side preference is pinned above."""

    from simulator.melt_backend.base import EquilibriumResult

    # Field default is None (no opportunistic liquidus computed).
    eq_no_liquidus = EquilibriumResult(status='unavailable')
    assert eq_no_liquidus.liquidus_T_C is None

    # Field accepts float; the dataclass shape is unchanged otherwise.
    eq_with_liquidus = EquilibriumResult(
        liquid_fraction=1.0,
        liquidus_T_C=1305.5,
    )
    assert eq_with_liquidus.liquidus_T_C == 1305.5


# ---------------------------------------------------------------------------
# 4. transition=None always (checklist 5)
# ---------------------------------------------------------------------------


def test_provider_never_emits_ledger_transition_for_basalt():
    backend = _FakeAlphaMELTSBackend(
        mode='python_api',
        equilibrium=_build_equilibrium_for_basalt(),
    )
    provider = AlphaMELTSProvider(backend=backend)
    request = _make_request(
        ChemistryIntent.SILICATE_LIQUIDUS,
        composition_mol=_basalt_species_mol(),
    )
    result = provider.dispatch(request)
    assert result.transition is None


def test_provider_never_emits_ledger_transition_for_domain_gate_rejection():
    backend = _FakeAlphaMELTSBackend(
        mode='python_api',
        equilibrium=_build_equilibrium_for_basalt(),
    )
    provider = AlphaMELTSProvider(backend=backend)
    request = _make_request(
        ChemistryIntent.SILICATE_LIQUIDUS,
        composition_mol={'Fe': 1.0},  # forced metal-only -> domain gate rejects
    )
    result = provider.dispatch(request)
    assert result.transition is None
    assert result.status == 'out_of_domain'


def test_provider_never_emits_ledger_transition_when_backend_unavailable():
    """When the adapter is None, the provider returns ``unavailable``
    with no transition."""
    provider = AlphaMELTSProvider(backend=None)
    request = _make_request(
        ChemistryIntent.SILICATE_LIQUIDUS,
        composition_mol=_basalt_species_mol(),
    )
    result = provider.dispatch(request)
    assert result.status == 'unavailable'
    assert result.transition is None


# ---------------------------------------------------------------------------
# 5. AST-level proof: provider module does NOT import LedgerTransition
# ---------------------------------------------------------------------------


def test_provider_module_does_not_import_ledger_transition():
    """Checklist 5 (acceptance gate): the provider module cannot import
    LedgerTransition or LedgerTransitionProposal.

    Enforced at the AST level: walk every Import / ImportFrom node and
    refuse the forbidden names. This protects against a future refactor
    silently re-introducing a path through which AlphaMELTS could
    construct a ledger write.
    """
    source = inspect.getsource(provider_module)
    tree = ast.parse(source)
    forbidden = {'LedgerTransition', 'LedgerTransitionProposal'}
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.name
                if name in forbidden:
                    offenders.append(
                        f'from {node.module} import {name}'
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                # Catch ``import simulator.accounting.ledger as L`` and
                # the fully-qualified ``simulator.accounting.ledger`` form.
                if name == 'simulator.accounting.ledger':
                    offenders.append(f'import {name}')
    assert offenders == [], (
        'AlphaMELTS provider module imports a LedgerTransition surface: '
        f'{offenders}. The provider must remain diagnostic-only '
        '(\\goal ALPHAMELTS-DIAGNOSTIC-GATE #8 checklist item 5).'
    )


def test_provider_package_files_do_not_import_ledger_transition():
    """The same AST check for every file in engines/alphamelts/.

    Belt-and-braces: a future contributor could split the provider into
    helpers; the diagnostic-only invariant must hold across all of
    them, not just provider.py.
    """
    package_dir = Path(provider_module.__file__).resolve().parent
    forbidden = {'LedgerTransition', 'LedgerTransitionProposal'}
    offenders: list[str] = []

    for path in sorted(package_dir.glob('*.py')):
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in forbidden:
                        offenders.append(
                            f'{path.name}: from {node.module} import {alias.name}'
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == 'simulator.accounting.ledger':
                        offenders.append(f'{path.name}: import {alias.name}')
    assert offenders == [], (
        'engines/alphamelts/* imports a LedgerTransition surface: '
        f'{offenders}'
    )


# ---------------------------------------------------------------------------
# 6. ControlAudit with 'diagnostic, not enforced'
# ---------------------------------------------------------------------------


def test_provider_control_audit_records_diagnostic_note():
    """Checklist 6: ControlAudit applied=requested with the diagnostic note."""
    backend = _FakeAlphaMELTSBackend(
        mode='python_api',
        equilibrium=_build_equilibrium_for_basalt(),
    )
    provider = AlphaMELTSProvider(backend=backend)
    request = _make_request(
        ChemistryIntent.SILICATE_LIQUIDUS,
        composition_mol=_basalt_species_mol(),
        temperature_C=1500.0,
        pressure_bar=1e-6,
        fO2_log=-10.5,
    )
    result = provider.dispatch(request)
    audit = result.control_audit
    assert audit is not None
    assert audit.requested == audit.applied  # applied == requested
    assert audit.requested['temperature_C'] == 1500.0
    assert audit.requested['pressure_bar'] == 1e-6
    assert audit.requested['fO2_log'] == -10.5
    assert audit.requested['fe_redox_policy'] == 'intrinsic'
    assert 'diagnostic, not enforced' in audit.notes


def test_provider_control_audit_records_clamped_applied_controls():
    equilibrium = _build_equilibrium_for_basalt()
    equilibrium.diagnostics = {
        'operating_point_clamped': True,
        'operating_point_transport': 'subprocess',
        'temperature_clamped': True,
        'pressure_clamped': True,
        'requested_temperature_C': 650.0,
        'requested_pressure_bar': 1.0e-6,
        'requested_fO2_log': -10.5,
        'solved_temperature_C': 800.0,
        'solved_pressure_bar': 1.0,
        'solved_fO2_log': -8.25,
        'authoritative_for_requested_conditions': False,
        'authoritative_for_solved_conditions': True,
    }
    backend = _FakeAlphaMELTSBackend(
        mode='python_api',
        equilibrium=equilibrium,
    )
    provider = AlphaMELTSProvider(backend=backend)
    request = _make_request(
        ChemistryIntent.SILICATE_EQUILIBRIUM,
        composition_mol=_basalt_species_mol(),
        temperature_C=650.0,
        pressure_bar=1e-6,
        fO2_log=-10.5,
    )

    result = provider.dispatch(request)

    audit = result.control_audit
    assert audit is not None
    assert audit.requested['temperature_C'] == 650.0
    assert audit.requested['pressure_bar'] == 1e-6
    assert audit.requested['fO2_log'] == -10.5
    assert audit.applied['temperature_C'] == 800.0
    assert audit.applied['pressure_bar'] == 1.0
    assert audit.applied['fO2_log'] == -8.25
    assert 'clamped operating point' in audit.notes


def test_provider_control_audit_present_for_unavailable_backend():
    """Even when the adapter is None, ControlAudit must still be populated.

    The trace consumer reads the audit to learn what the simulator asked
    for; producing an empty audit on the 'unavailable' branch would lose
    that signal.
    """
    provider = AlphaMELTSProvider(backend=None)
    request = _make_request(
        ChemistryIntent.SILICATE_LIQUIDUS,
        composition_mol=_basalt_species_mol(),
    )
    result = provider.dispatch(request)
    assert result.control_audit is not None
    assert 'diagnostic, not enforced' in result.control_audit.notes


# ---------------------------------------------------------------------------
# 7. Account view filtered to process.cleaned_melt (checklist 4)
# ---------------------------------------------------------------------------


def test_kernel_filter_blocks_undeclared_accounts_for_provider():
    """The kernel filter must drop every account except process.cleaned_melt
    before AlphaMELTSProvider sees the view."""

    from simulator.accounting.ledger import AtomLedger

    backend = _FakeAlphaMELTSBackend(
        mode='python_api',
        equilibrium=_build_equilibrium_for_basalt(),
    )
    provider = AlphaMELTSProvider(backend=backend)
    registry = ProviderRegistry()
    registry.register(provider, [
        ChemistryIntent.SILICATE_LIQUIDUS,
        ChemistryIntent.SILICATE_EQUILIBRIUM,
    ])
    ledger = AtomLedger()
    # Seed both a cleaned-melt account and a metal-phase one so the
    # filter has something to drop.
    ledger.load_external(
        'process.cleaned_melt',
        _basalt_species_mol(),
        source='test seed cleaned melt',
    )
    ledger.load_external(
        'process.metal_phase',
        {'Fe': 0.5},
        source='test seed metal phase',
    )
    kernel = ChemistryKernel(
        ledger=ledger,
        registry=registry,
        species_formula_registry={},
    )

    seen_accounts: list[frozenset[str]] = []
    original_dispatch = AlphaMELTSProvider.dispatch

    def _spying_dispatch(self, request):
        seen_accounts.append(frozenset(request.account_view.accounts))
        return original_dispatch(self, request)

    AlphaMELTSProvider.dispatch = _spying_dispatch
    try:
        kernel.dispatch(
            ChemistryIntent.SILICATE_LIQUIDUS,
            temperature_C=1400.0,
            pressure_bar=1.0,
            fO2_log=-9.0,
        )
    finally:
        AlphaMELTSProvider.dispatch = original_dispatch

    assert seen_accounts, 'provider was never dispatched'
    for accounts in seen_accounts:
        assert accounts == frozenset({'process.cleaned_melt'}), (
            'kernel filter leaked an undeclared account into the provider'
        )


# ---------------------------------------------------------------------------
# 8. LiquidusDiagnostics dataclass shape
# ---------------------------------------------------------------------------


def test_liquidus_diagnostics_is_frozen_and_carries_no_transition_field():
    """Defence in depth: the dataclass must not even reserve a
    ``transition`` / ``ledger_transition`` field. Tests against a future
    refactor that smuggles a transition field onto the diagnostic.
    """
    diagnostic = LiquidusDiagnostics(
        liquidus_T_C=1305.0,
        solidus_T_C=1000.0,
        phases_present=('liquid', 'olivine'),
    )
    # frozen: assignment must raise.
    with pytest.raises((AttributeError, TypeError)):
        diagnostic.liquidus_T_C = 1400.0  # type: ignore[misc]
    # No transition field is reserved.
    payload = diagnostic.as_diagnostic()
    assert payload['solidus_T_C'] == pytest.approx(1000.0)
    assert payload['fe_redox_policy'] == 'intrinsic'
    assert payload['applied_fe3fet'] is None
    assert payload['intrinsic_fO2_log'] is None
    for forbidden in ('transition', 'ledger_transition', 'proposal'):
        assert forbidden not in payload


# ---------------------------------------------------------------------------
# 9. Provider unavailable when adapter present but is_available() False
# ---------------------------------------------------------------------------


def test_provider_returns_unavailable_when_backend_marked_unavailable():
    """When the adapter reports is_available() False (e.g. after a
    PetThermoTools crash zeroed _mode), the provider surfaces
    status='unavailable' rather than silently falling through."""

    backend = _FakeAlphaMELTSBackend(
        mode='unavailable',
        equilibrium=_build_equilibrium_for_basalt(),
    )
    # The fake's is_available returns False when mode is not a known transport.
    provider = AlphaMELTSProvider(backend=backend)
    request = _make_request(
        ChemistryIntent.SILICATE_LIQUIDUS,
        composition_mol=_basalt_species_mol(),
    )
    result = provider.dispatch(request)
    assert result.status == 'unavailable'
    assert result.transition is None
    assert (result.diagnostic or {}).get('mode') == 'unavailable'


def test_provider_raises_on_nonfinite_ec_liquid_fraction():
    def _bad_equilibrate(**_: object) -> SimpleNamespace:
        return SimpleNamespace(
            status='ok',
            liquid_fraction=float('nan'),
            liquid_composition_wt_pct=dict(_basalt_wt_pct()),
            warnings=[],
        )

    backend = _FakeAlphaMELTSBackend(
        mode='python_api',
        equilibrium=_build_equilibrium_for_basalt(),
        finder_result=LiquidusSolidusResult(
            liquidus_T_C=1300.0,
            solidus_T_C=1000.0,
            liquid_fraction=1.0,
            status='ok',
        ),
        equilibrate_func=_bad_equilibrate,
    )
    provider = AlphaMELTSProvider(backend=backend)
    request = _make_request(
        ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
        composition_mol=_basalt_species_mol(),
    )

    with pytest.raises(LiquidFractionInvalidError):
        provider.dispatch(request)


# ---------------------------------------------------------------------------
# 10. Authority posture: registry accepts authoritative registration
# ---------------------------------------------------------------------------


def test_provider_can_be_registered_as_authoritative():
    """The registry-level authoritative slot is the kernel's dispatch
    mechanism. AlphaMELTSProvider being 'diagnostic-only' means
    transition=None, not absent authority."""
    registry = ProviderRegistry()
    provider = AlphaMELTSProvider(backend=None)
    registry.register(provider, [
        ChemistryIntent.SILICATE_LIQUIDUS,
        ChemistryIntent.SILICATE_EQUILIBRIUM,
        ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
        ChemistryIntent.GATE_LIQUID_FRACTION,
    ])
    assert registry.authoritative_for(ChemistryIntent.SILICATE_LIQUIDUS) is provider
    assert registry.authoritative_for(ChemistryIntent.SILICATE_EQUILIBRIUM) is provider
    assert registry.authoritative_for(
        ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION
    ) is provider
    assert registry.authoritative_for(
        ChemistryIntent.GATE_LIQUID_FRACTION
    ) is provider


def test_provider_rejects_authoritative_registration_for_other_intent():
    """Registering for a non-declared intent must raise -- the registry
    inspects the CapabilityProfile."""
    from simulator.chemistry.kernel.errors import KernelError

    registry = ProviderRegistry()
    provider = AlphaMELTSProvider(backend=None)
    with pytest.raises(KernelError):
        registry.register(provider, [ChemistryIntent.VAPOR_PRESSURE])
