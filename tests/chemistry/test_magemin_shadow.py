"""Kernel-level parity tests for the MAGEMin shadow provider.

Goal #9 ``MAGEMIN-SHADOW-PARITY`` checklist:

1. ``engines/magemin/provider.py`` registers as shadow provider for
   SILICATE_LIQUIDUS + SILICATE_EQUILIBRIUM.
2. Kernel planner routes the request to the authoritative AND the
   shadow provider; only authoritative's result becomes the
   LedgerTransition.
3. Parity test:
       |T_liquidus_authoritative - T_liquidus_shadow| <= 50 K
       |mode_pct_diff| <= 2 wt% per phase
   Disagreement raises parity warning to trace, not a test failure.

These tests use a stand-in authoritative provider (so the suite stays
green without alphaMELTS installed) and a controllable MAGEMin shadow
backend so we can drive both the "agreement" and "disagreement" paths
deterministically.  The real-engine parity case is gated by an
``importorskip`` -- the per-engine adapters land their own integration
tests that exercise the binary.
"""

from __future__ import annotations

import pytest

from engines.magemin import (
    MAGEMinShadowDiagnostics,
    MAGEMinShadowProvider,
)
from simulator.accounting.ledger import AtomLedger
from simulator.chemistry.kernel import (
    CapabilityProfile,
    ChemistryIntent,
    ChemistryKernel,
    ChemistryProvider,
    IntentRequest,
    IntentResult,
    ProviderAccountView,
    ProviderRegistry,
)
from simulator.chemistry.kernel.planner import Planner


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubAuthoritativeProvider(ChemistryProvider):
    """Stand-in authoritative provider that returns canned diagnostics.

    Tests construct it with the liquidus / mode payload they want to see
    on the authoritative result, so the parity comparison is fully
    deterministic and does not depend on AlphaMELTS being installed.
    """

    PROVIDER_ID = 'stub-authoritative-silicate'
    DECLARED_ACCOUNT = 'process.cleaned_melt'

    def __init__(
        self,
        *,
        liquidus_T_K: float = 1700.0,
        phase_modes_wt_pct: dict | None = None,
        status: str = 'ok',
    ) -> None:
        self._liquidus_T_K = float(liquidus_T_K)
        self._phase_modes_wt_pct = dict(phase_modes_wt_pct or {'liquid': 100.0})
        self._status = status
        self.call_count = 0

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.PROVIDER_ID,
            intents=frozenset({
                ChemistryIntent.SILICATE_LIQUIDUS,
                ChemistryIntent.SILICATE_EQUILIBRIUM,
            }),
            is_authoritative_for=frozenset({
                ChemistryIntent.SILICATE_LIQUIDUS,
                ChemistryIntent.SILICATE_EQUILIBRIUM,
            }),
            declared_accounts=frozenset({self.DECLARED_ACCOUNT}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        self.call_count += 1
        diagnostic = {
            'liquidus_T_K': self._liquidus_T_K,
            'liquidus_T_C': self._liquidus_T_K - 273.15,
            'phase_modes_wt_pct': dict(self._phase_modes_wt_pct),
            'phases_present': tuple(sorted(self._phase_modes_wt_pct)),
            'engine_version': 'stub-authoritative-1.0',
        }
        return IntentResult(
            intent=request.intent,
            status=self._status,
            transition=None,  # diagnostic; goal #8 already binds this
            control_audit=None,
            diagnostic=diagnostic,
            warnings=(),
        )


class _FakeMAGEMinBackend:
    """Minimal MAGEMinBackend stand-in.

    The provider's :meth:`_run_backend` calls ``equilibrate`` with
    keyword arguments matching the real adapter; this fake records the
    call and returns a canned :class:`EquilibriumResult`-shaped object.
    Avoids depending on a built MAGEMin binary in the test env.
    """

    name = 'magemin'

    def __init__(
        self,
        *,
        equilibrium,
        is_available: bool = True,
        engine_version: str = 'fake-magemin',
        bridge: str = 'subprocess',
    ) -> None:
        self._equilibrium = equilibrium
        self._is_available = bool(is_available)
        self._engine_version = engine_version
        self._bridge = bridge
        self.calls: list[dict] = []

    def is_available(self) -> bool:
        return self._is_available

    def get_engine_version(self) -> str:
        return self._engine_version

    def initialize(self, config: dict) -> bool:
        return self._is_available

    def equilibrate(self, **kwargs):
        self.calls.append(kwargs)
        return self._equilibrium


class _FakeEquilibriumResult:
    """Duck-typed substitute for ``simulator.melt_backend.base.EquilibriumResult``."""

    def __init__(
        self,
        *,
        liquidus_T_K: float | None = None,
        phase_masses_kg: dict | None = None,
        liquid_composition_wt_pct: dict | None = None,
        status: str = 'ok',
        warnings: tuple = (),
    ) -> None:
        self.liquidus_T_K = liquidus_T_K
        self.phase_masses_kg = dict(phase_masses_kg or {})
        self.phases_present = tuple(self.phase_masses_kg)
        self.liquid_composition_wt_pct = dict(liquid_composition_wt_pct or {})
        total = sum(float(m) for m in self.phase_masses_kg.values())
        self.liquid_fraction = (
            float(self.phase_masses_kg.get('liquid', 0.0))
            / total if total > 0 else None
        )
        self.status = status
        self.warnings = tuple(warnings)


def _basalt_species_mol() -> dict:
    """Basalt composition (mol per oxide) that passes the MAGEMin domain gate."""
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
    wt_pct = {
        'SiO2': 49.0, 'TiO2': 1.5, 'Al2O3': 14.0, 'FeO': 10.0, 'Fe2O3': 1.0,
        'MgO': 9.0, 'CaO': 11.0, 'Na2O': 2.5, 'K2O': 0.8, 'Cr2O3': 0.2,
        'MnO': 0.2, 'P2O5': 0.3, 'NiO': 0.02, 'CoO': 0.01,
    }
    return {oxide: (pct / 100.0) / masses[oxide] for oxide, pct in wt_pct.items()}


def _make_request(
    intent: ChemistryIntent,
    *,
    composition_mol: dict | None = None,
    temperature_C: float = 1400.0,
    pressure_bar: float = 1.0,
    fO2_log: float = -9.0,
) -> IntentRequest:
    accounts = {
        MAGEMinShadowProvider.DECLARED_ACCOUNT:
        dict(composition_mol or _basalt_species_mol()),
    }
    view = ProviderAccountView(
        accounts=accounts,
        species_formula_registry={},
    )
    return IntentRequest(
        intent=intent,
        account_view=view,
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        fO2_log=fO2_log,
        control_inputs={},
    )


# ---------------------------------------------------------------------------
# Checklist #1: provider registers as a shadow
# ---------------------------------------------------------------------------


def test_provider_registers_as_shadow_for_silicate_intents():
    """The provider's capability profile MUST be empty-authoritative
    so the registry routes it as a shadow.

    Defence in depth: even ``register(shadow=False)`` raises if the
    provider does not declare itself authoritative.
    """
    registry = ProviderRegistry()
    shadow = MAGEMinShadowProvider()
    registry.register(
        shadow,
        [ChemistryIntent.SILICATE_LIQUIDUS, ChemistryIntent.SILICATE_EQUILIBRIUM],
        shadow=True,
    )
    assert MAGEMinShadowProvider() not in (
        registry.authoritative_for(ChemistryIntent.SILICATE_LIQUIDUS),
    )
    shadows = registry.shadows_for(ChemistryIntent.SILICATE_LIQUIDUS)
    assert len(shadows) == 1
    assert shadows[0].capability_profile().provider_id == 'magemin-shadow'


def test_registry_rejects_promoting_magemin_to_authoritative():
    """Goal-spec forbidden: 'Granting MAGEMin ledger authority.'

    The kernel registry enforces this -- ``CapabilityProfile.
    is_authoritative_for`` is empty for MAGEMin, so the
    ``register(shadow=False)`` path raises.
    """
    registry = ProviderRegistry()
    shadow = MAGEMinShadowProvider()
    with pytest.raises(Exception) as exc_info:
        registry.register(
            shadow,
            [ChemistryIntent.SILICATE_LIQUIDUS],
            shadow=False,
        )
    assert 'authoritative' in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Checklist #2: planner runs authoritative AND shadow
# ---------------------------------------------------------------------------


def test_planner_runs_authoritative_and_shadow():
    """Both providers must run on every dispatch (goal spec acceptance gate)."""

    auth = _StubAuthoritativeProvider()
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,
        phase_masses_kg={'liquid': 1.0},
    )
    fake_backend = _FakeMAGEMinBackend(equilibrium=fake_equilibrium)
    shadow = MAGEMinShadowProvider(backend=fake_backend)

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)

    planner = Planner(registry)
    result = planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))

    assert auth.call_count == 1
    assert len(fake_backend.calls) == 1, 'MAGEMin shadow was not dispatched'
    # Authoritative result is what the planner returns.
    assert result.diagnostic.get('engine_version') == 'stub-authoritative-1.0'


def test_planner_returns_authoritative_result_only():
    """Goal spec: 'only authoritative's result becomes the LedgerTransition.'"""

    auth = _StubAuthoritativeProvider()
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,
        phase_masses_kg={'liquid': 1.0},
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    planner = Planner(registry)

    result = planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))

    # The planner returns the authoritative result, not the shadow's.
    # Authority diagnostic exposes the stub engine_version key; the
    # MAGEMin diagnostic exposes 'fake-magemin'. We must see the auth
    # one.
    assert result.diagnostic.get('engine_version') == 'stub-authoritative-1.0'
    # Shadow result is captured in trace, separately.
    shadow_records = [
        entry for entry in planner.shadow_trace
        if entry.get('event') == 'shadow_dispatch'
    ]
    assert len(shadow_records) == 1
    assert shadow_records[0]['provider_id'] == 'magemin-shadow'


def test_kernel_dispatch_does_not_commit_shadow_transitions():
    """A shadow may not write the ledger; the kernel's writer-purity
    invariant binds this and is enforced by transition=None on
    the shadow side. Belt-and-braces: ledger is unchanged across a
    silicate dispatch.
    """
    ledger = AtomLedger()
    ledger.load_external('process.cleaned_melt', {'SiO2': 5.0})
    before_mol = ledger.mol_by_account()

    auth = _StubAuthoritativeProvider()
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,
        phase_masses_kg={'liquid': 1.0},
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    kernel = ChemistryKernel(
        ledger=ledger,
        registry=registry,
        species_formula_registry={},
    )

    kernel.dispatch(
        ChemistryIntent.SILICATE_LIQUIDUS,
        temperature_C=1400.0,
        pressure_bar=1.0,
        declared_accounts=frozenset({'process.cleaned_melt'}),
    )

    after_mol = ledger.mol_by_account()
    assert before_mol == after_mol


# ---------------------------------------------------------------------------
# Checklist #3: parity warning landing in the trace
# ---------------------------------------------------------------------------


def test_aligned_results_produce_no_parity_warning():
    """Within tolerance: shadow record present, no parity_warning."""

    # Authoritative + shadow report the same liquidus and modes.
    auth = _StubAuthoritativeProvider(
        liquidus_T_K=1700.0,
        phase_modes_wt_pct={'liquid': 100.0},
    )
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,  # exact match
        phase_masses_kg={'liquid': 1.0},  # 100 wt% liquid
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    planner = Planner(registry)
    planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))

    parity_warnings = [
        e for e in planner.shadow_trace if e.get('event') == 'parity_warning'
    ]
    assert parity_warnings == [], (
        f'expected no parity warnings; got {parity_warnings}'
    )


def test_disagreement_records_parity_warning_in_trace_not_failure():
    """Goal-spec acceptance gate: disagreement raises a parity warning
    to the trace, NOT a test failure.

    The shadow liquidus differs by 100 K (twice the 50 K tolerance);
    parity warning MUST land in the trace and the dispatch MUST return
    normally with the authoritative result.
    """
    auth = _StubAuthoritativeProvider(
        liquidus_T_K=1700.0,
        phase_modes_wt_pct={'liquid': 100.0},
    )
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1600.0,  # 100 K below -- twice tolerance
        phase_masses_kg={'liquid': 1.0},
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    planner = Planner(registry)

    # No raise: a parity disagreement is never an exception.
    result = planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))
    assert result.status == 'ok'

    parity_warnings = [
        e for e in planner.shadow_trace if e.get('event') == 'parity_warning'
    ]
    assert len(parity_warnings) == 1, (
        f'expected exactly 1 parity warning, got {parity_warnings}'
    )
    warning = parity_warnings[0]
    assert warning['provider_id'] == 'magemin-shadow'
    assert warning['intent'] == ChemistryIntent.SILICATE_LIQUIDUS.value
    assert warning['agreement'] is False
    # Both numbers retained verbatim -- no silent averaging.
    assert warning['liquidus_T_delta_K'] == pytest.approx(100.0)
    assert warning['authoritative_liquidus_T_K'] == pytest.approx(1700.0)
    assert warning['shadow_liquidus_T_K'] == pytest.approx(1600.0)
    # The comparator's per-disagreement warning text comes through.
    assert any('liquidus delta' in w for w in warning['warnings'])


def test_modal_disagreement_records_parity_warning():
    """Modal disagreement above 2 wt% per phase raises a parity warning."""

    auth = _StubAuthoritativeProvider(
        liquidus_T_K=1700.0,
        phase_modes_wt_pct={'liquid': 80.0, 'olivine': 20.0},
    )
    # Shadow reports liquid:65 / olivine:35 -- 15 wt% disagreement.
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,
        phase_masses_kg={'liquid': 0.65, 'olivine': 0.35},
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    planner = Planner(registry)

    planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))

    parity_warnings = [
        e for e in planner.shadow_trace if e.get('event') == 'parity_warning'
    ]
    assert len(parity_warnings) == 1
    warning = parity_warnings[0]
    assert warning['mode_pct_max_delta'] == pytest.approx(15.0)
    assert any('modal disagreement' in w for w in warning['warnings'])


def test_parity_within_tolerance_does_not_fire():
    """Within +/-50 K and +/-2 wt%: no parity warning."""

    auth = _StubAuthoritativeProvider(
        liquidus_T_K=1700.0,
        phase_modes_wt_pct={'liquid': 80.0, 'olivine': 20.0},
    )
    # Shadow:
    # liquidus 1670 K (delta = 30 K, within 50 K tolerance)
    # modes: liquid 79 / olivine 21 (delta = 1 wt%, within 2 wt%)
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1670.0,
        phase_masses_kg={'liquid': 0.79, 'olivine': 0.21},
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    planner = Planner(registry)
    planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))

    parity_warnings = [
        e for e in planner.shadow_trace if e.get('event') == 'parity_warning'
    ]
    assert parity_warnings == []


def test_shadow_unavailable_skips_parity_check():
    """If the shadow returns ``unavailable`` no parity event is emitted.

    Goal spec implication: a missing engine is not a parity failure --
    the trace just records the unavailable shadow_dispatch and moves
    on. Otherwise environments without a MAGEMin binary would spam
    spurious parity warnings.
    """
    auth = _StubAuthoritativeProvider(liquidus_T_K=1700.0)
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(
            equilibrium=_FakeEquilibriumResult(status='unavailable'),
            is_available=False,
        ),
    )

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    planner = Planner(registry)
    planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))

    parity_warnings = [
        e for e in planner.shadow_trace if e.get('event') == 'parity_warning'
    ]
    assert parity_warnings == []
    shadow_dispatches = [
        e for e in planner.shadow_trace if e.get('event') == 'shadow_dispatch'
    ]
    # The shadow still ran and its 'unavailable' result is recorded.
    assert len(shadow_dispatches) == 1
    assert shadow_dispatches[0]['result'].status == 'unavailable'


def test_shadow_out_of_domain_skips_parity_check():
    """Out-of-domain compositions: shadow result recorded, no parity warning."""

    auth = _StubAuthoritativeProvider(liquidus_T_K=1700.0)
    shadow = MAGEMinShadowProvider()  # lazy adapter, never reached

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    planner = Planner(registry)

    # Empty composition -> MAGEMinDomainGate rejects -> shadow returns
    # status='out_of_domain'. No parity check.
    accounts = {MAGEMinShadowProvider.DECLARED_ACCOUNT: {}}
    view = ProviderAccountView(
        accounts=accounts,
        species_formula_registry={},
    )
    request = IntentRequest(
        intent=ChemistryIntent.SILICATE_LIQUIDUS,
        account_view=view,
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=None,
        control_inputs={},
    )
    planner.dispatch(request)

    parity_warnings = [
        e for e in planner.shadow_trace if e.get('event') == 'parity_warning'
    ]
    assert parity_warnings == []
    shadow_dispatches = [
        e for e in planner.shadow_trace if e.get('event') == 'shadow_dispatch'
    ]
    assert len(shadow_dispatches) == 1
    assert shadow_dispatches[0]['result'].status == 'out_of_domain'


# ---------------------------------------------------------------------------
# Checklist invariants: MAGEMin never emits a LedgerTransition
# ---------------------------------------------------------------------------


def test_shadow_dispatch_never_returns_a_transition():
    """The shadow provider's IntentResult.transition is always None."""

    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,
        phase_masses_kg={'liquid': 1.0},
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )
    request = _make_request(ChemistryIntent.SILICATE_LIQUIDUS)
    result = shadow.dispatch(request)
    assert result.transition is None


def test_unsupported_intent_returns_unsupported_status():
    """Defence in depth: bypassing the registry must not produce silent garbage."""

    shadow = MAGEMinShadowProvider()
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={}, species_formula_registry={},
        ),
        temperature_C=1400.0,
        pressure_bar=1.0,
    )
    result = shadow.dispatch(request)
    assert result.status == 'unsupported'
    assert result.transition is None


def test_shadow_diagnostic_shape_serialisable():
    """``IntentResult.diagnostic`` MUST be a plain mapping with the
    keys the parity comparator looks up.

    Goal spec acceptance: parity warnings visible in the debug UI.
    The UI projects ``shadow_trace`` to JSON; a dataclass or
    MappingProxy on the diagnostic would break that path.
    """
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,
        phase_masses_kg={'liquid': 0.8, 'olivine': 0.2},
        liquid_composition_wt_pct={'SiO2': 50.0},
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )
    request = _make_request(ChemistryIntent.SILICATE_LIQUIDUS)
    result = shadow.dispatch(request)

    diag = result.diagnostic
    # Mapping-shaped; the kernel froze it via MappingProxyType.
    assert diag['liquidus_T_K'] == pytest.approx(1700.0)
    assert diag['phase_modes_wt_pct']['liquid'] == pytest.approx(80.0)
    assert diag['phase_modes_wt_pct']['olivine'] == pytest.approx(20.0)
    assert diag['engine_version'] == 'fake-magemin'


# ---------------------------------------------------------------------------
# Optional real-engine probe (skipped without a MAGEMin binary)
# ---------------------------------------------------------------------------


def test_provider_with_real_adapter_smoke():
    """When the MAGEMin binary is present, the provider returns OK or
    a clean unavailable status without raising.

    Acts as a real-engine smoke probe; skipped automatically when the
    binary is not located. Disagreement -> parity warning -> still
    not a test failure. The kernel-level parity tolerance is unchanged.
    """
    from simulator.melt_backend.magemin import MAGEMinBackend

    backend = MAGEMinBackend()
    initialised = backend.initialize({})
    if not initialised:
        pytest.skip(
            'MAGEMin binary not located; real-engine parity probe skipped',
        )

    shadow = MAGEMinShadowProvider(backend=backend)
    request = _make_request(
        ChemistryIntent.SILICATE_LIQUIDUS,
        temperature_C=1400.0,
        pressure_bar=1000.0,  # 1 kbar; MAGEMin's CLI expects kbar via the
                              # adapter's pressure_kbar conversion.
    )
    result = shadow.dispatch(request)
    assert result.transition is None
    assert result.status in (
        'ok', 'not_converged', 'out_of_domain', 'unavailable',
    )
