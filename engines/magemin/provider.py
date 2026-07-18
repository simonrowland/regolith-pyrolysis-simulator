"""MAGEMin kernel-shadow provider.

Promoted from the original scaffold to a kernel-registered SHADOW
provider under goal #9 ``MAGEMIN-SHADOW-PARITY``. The provider:

- declares the :data:`SILICATE_LIQUIDUS` + :data:`SILICATE_EQUILIBRIUM`
  shadow intent set and :data:`GATE_LIQUID_FRACTION` fallback intent,
- declares ``process.cleaned_melt`` as its sole accessible account; the
  kernel filter drops every other account before dispatch (same
  silicate-oxide isolation contract AlphaMELTS uses),
- runs the :class:`MAGEMinDomainGate` on the cleaned-melt composition
  before delegating to the today-hook adapter,
- delegates to :mod:`simulator.melt_backend.magemin.MAGEMinBackend`
  for the chemistry with ``python_bridge='subprocess'`` pinned for
  shadow determinism,
- returns a :class:`MAGEMinShadowDiagnostics` payload on
  :attr:`IntentResult.diagnostic`, with ``transition=None`` always --
  MAGEMin has no ledger-write authority.

Authority posture
-----------------
MAGEMin is registered with the :class:`ProviderRegistry` as a SHADOW
for the full silicate-state intents. It is authority-capable only for
``GATE_LIQUID_FRACTION``, where the fallback slot answers the gate's
narrow scalar liquid_fraction(T) question without granting ledger-write
authority. The kernel's planner runs the authoritative provider AND every
shadow on each silicate dispatch; only the authoritative result becomes a
``LedgerTransition``. Shadow results land on
``Planner.shadow_trace`` as ``{provider_id, intent, result}`` records,
and the planner additionally appends a ``parity_warning`` event when
the per-provider parity comparator reports disagreement (goal #9
checklist 3).

This module MUST NOT import :class:`LedgerTransitionProposal` from
anywhere -- not even for type hints. The
``test_magemin_shadow_provider.py::test_no_ledger_transition_import``
test enforces this with an AST walk over the module source. The
writer-purity invariant in ``tests/chemistry/test_writer_purity.py``
catches any accidental ledger write attempt at runtime.

Parity tolerances (binding spec §4 MAGEMin):

    |T_liquidus_authoritative - T_liquidus_shadow| <= 50 K
    |mode_pct per phase|                            <= 2 wt%
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from engines.magemin.domain import MAGEMinDomainGate
from engines.magemin.parity import MAGEMinParityComparator, ParityReport
from engines.magemin.result import MAGEMinShadowDiagnostics
from simulator.chemistry.kernel.capabilities import (
    CapabilityProfile,
    ChemistryIntent,
)
from simulator.chemistry.kernel.dto import (
    ControlAudit,
    IntentRequest,
    IntentResult,
)
from simulator.chemistry.kernel.provider import ChemistryProvider
from simulator.melt_backend.base import EquilibriumResult
from simulator.melt_backend.liquidus import LiquidusSolidusResult
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET


# Intent set: silicate parity shadows plus the freeze-gate scalar fallback.
_INTENTS = frozenset({
    ChemistryIntent.SILICATE_LIQUIDUS,
    ChemistryIntent.SILICATE_EQUILIBRIUM,
    ChemistryIntent.GATE_LIQUID_FRACTION,
})
_FALLBACK_INTENTS = frozenset({ChemistryIntent.GATE_LIQUID_FRACTION})

# Sole declared account: silicate-oxide melt (binding spec §7 isolation).
# Same constraint as AlphaMELTS -- MAGEMin operates on silicate-oxide
# bulk only, no gas / metal / salt / sulfide. The kernel filter blocks
# every other account before dispatch.
_DECLARED_ACCOUNT = 'process.cleaned_melt'
_SHADOW_BACKEND_CONFIG = {'python_bridge': 'subprocess'}

# Note attached to the ControlAudit for every dispatch. MAGEMin is
# shadow-only -- it consumes T / P / fO2 as inputs but never enforces
# them on the ledger (no ledger writes at all). Same posture as
# AlphaMELTS's ``"diagnostic, not enforced"`` note; we use a distinct
# wording so trace consumers can tell shadow from diagnostic.
_SHADOW_AUDIT_NOTE = 'shadow, not enforced'


class MAGEMinShadowProvider(ChemistryProvider):
    """MAGEMin provider for silicate shadows and gate fallback.

    See module docstring. The provider is constructed with an optional
    live :class:`simulator.melt_backend.magemin.MAGEMinBackend` instance;
    if omitted, the provider lazily constructs one on first dispatch
    (matching the legacy "today-hook reconciliation" behaviour the
    scaffold documented). The lazy path lets the provider register at
    kernel-build time without forcing a MAGEMin binary probe -- the probe
    happens on first dispatch and surfaces ``status='unavailable'`` if
    no binary is reachable.

    ``backend`` may be ``None`` -- in that case :meth:`dispatch` lazily
    constructs a :class:`MAGEMinBackend` and calls
    ``initialize({'python_bridge': 'subprocess'})``;
    if the binary is absent the result carries ``status='unavailable'``
    with an empty :class:`MAGEMinShadowDiagnostics`. This matches the
    kernel's ``status='unavailable'`` vocabulary for absent engines.
    """

    name = 'magemin-shadow'

    PROVIDER_ID = 'magemin-shadow'
    DECLARED_ACCOUNT = _DECLARED_ACCOUNT

    def __init__(self, backend: Optional[Any] = None) -> None:
        # Lazy adapter: the provider must be importable / registerable
        # without the MAGEMin binary being present. The first dispatch
        # constructs and initialises the adapter; if that fails the
        # dispatch returns ``status='unavailable'`` cleanly.
        self._backend: Optional[Any] = backend
        self._backend_initialised: bool = backend is not None
        self._parity_comparator: MAGEMinParityComparator = MAGEMinParityComparator()

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.PROVIDER_ID,
            intents=_INTENTS,
            # Authority-capable only for the gate's scalar fallback
            # intent. SILICATE_LIQUIDUS / SILICATE_EQUILIBRIUM remain
            # shadow-only parity surfaces.
            is_authoritative_for=_FALLBACK_INTENTS,
            declared_accounts=frozenset({self.DECLARED_ACCOUNT}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        """Run MAGEMin for ``request.intent``; return a diagnostic.

        See module docstring for the contract. The provider:

        1. Validates the intent is one it serves (defence in depth --
           the kernel registry already routes correctly).
        2. Builds the ControlAudit with ``applied == requested`` and
           the shadow note.
        3. Runs :class:`MAGEMinDomainGate`. If rejected, returns
           ``status='out_of_domain'`` with the warnings recorded.
        4. Lazily initialises the adapter; if the MAGEMin binary is
           absent, returns ``status='unavailable'``.
        5. Calls the adapter's :meth:`equilibrate`.
        6. Projects the adapter's :class:`EquilibriumResult` into a
           :class:`MAGEMinShadowDiagnostics`.
        7. Returns the :class:`IntentResult` with ``transition=None``.
        """
        # Defence in depth: the registry routes only declared intents
        # here, but a future caller bypassing the registry must hit a
        # clean ``unsupported`` instead of a silent mis-answer.
        if request.intent not in _INTENTS:
            return IntentResult(
                intent=request.intent,
                status='unsupported',
                diagnostic={
                    'reason': (
                        'MAGEMinShadowProvider serves SILICATE_LIQUIDUS + '
                        'SILICATE_EQUILIBRIUM + GATE_LIQUID_FRACTION only'
                    ),
                },
            )

        control_audit = self._build_control_audit(request)

        composition_mol_by_account = self._composition_from_view(request)
        composition_wt_pct = self._composition_wt_pct(
            composition_mol_by_account.get(self.DECLARED_ACCOUNT, {}),
            request.account_view.species_formula_registry,
        )

        # Run the domain gate even when the adapter is None so callers
        # see a meaningful rejection (rather than a silent
        # 'unavailable' surface that hides the input issue).
        valid, gate_warnings, gate_reason = (
            MAGEMinDomainGate.validate_with_reason(composition_wt_pct)
        )
        if not valid:
            return IntentResult(
                intent=request.intent,
                status='out_of_domain',
                transition=None,
                control_audit=control_audit,
                diagnostic=MAGEMinShadowDiagnostics(
                    mode='unavailable',
                    engine_version=self._engine_version(),
                    backend_status='out_of_domain',
                    backend_status_reason=gate_reason,
                    backend_warnings=tuple(gate_warnings),
                ).as_diagnostic(),
                warnings=tuple(gate_warnings),
            )

        backend = self._ensure_backend()
        if backend is None or not self._backend_available(backend):
            return IntentResult(
                intent=request.intent,
                status='unavailable',
                transition=None,
                control_audit=control_audit,
                diagnostic=MAGEMinShadowDiagnostics(
                    mode='unavailable',
                    engine_version=self._engine_version(),
                    backend_status='unavailable',
                ).as_diagnostic(),
                warnings=(
                    'MAGEMin adapter not available (binary not located '
                    'and no Python bridge importable)',
                ),
            )

        if request.intent in (
            ChemistryIntent.SILICATE_LIQUIDUS,
            ChemistryIntent.GATE_LIQUID_FRACTION,
        ):
            equilibrium = self._run_liquidus_finder(
                backend,
                request,
                composition_mol_by_account=composition_mol_by_account,
            )
        else:
            equilibrium = self._run_backend(
                backend,
                request,
                composition_mol_by_account=composition_mol_by_account,
            )
        diagnostics = self._project_equilibrium(
            equilibrium,
            mode=self._mode_label(backend),
            engine_version=self._engine_version(),
        )

        backend_status = diagnostics.backend_status
        kernel_status = backend_status if backend_status in (
            'ok', 'not_converged', 'out_of_domain', 'unavailable'
        ) else 'ok'

        return IntentResult(
            intent=request.intent,
            status=kernel_status,
            transition=None,
            control_audit=control_audit,
            diagnostic=diagnostics.as_diagnostic(),
            warnings=tuple(diagnostics.backend_warnings),
        )

    # ------------------------------------------------------------------
    # Parity hook (called by the planner after authoritative dispatch).
    # ------------------------------------------------------------------

    def parity_compare(
        self,
        authoritative_result: Any,
        shadow_result: Any,
    ) -> Optional[ParityReport]:
        """Compare authoritative vs shadow results; return a parity report.

        The :class:`Planner` calls this after the authoritative provider
        has run, passing the authoritative :class:`IntentResult` and the
        shadow's :class:`IntentResult`. Returning a
        :class:`ParityReport` with ``agreement=False`` triggers a
        ``parity_warning`` entry in the shadow trace. Returning ``None``
        skips the parity record entirely (e.g. when either side
        reported ``unavailable`` / ``out_of_domain``).

        The comparison runs on the ``diagnostic`` dict of each result;
        the comparator already accepts a duck-typed mapping with the
        documented keys (``liquidus_T_K`` / ``liquidus_T_C`` /
        ``phase_modes_wt_pct``).
        """
        if authoritative_result is None or shadow_result is None:
            return None
        auth_diag = self._extract_diagnostic(authoritative_result)
        shadow_diag = self._extract_diagnostic(shadow_result)
        # Both sides must have produced a usable diagnostic. If either
        # came back with ``status='unavailable'`` / ``'out_of_domain'``
        # the parity check is meaningless -- skip and let the
        # individual statuses surface in the trace.
        auth_status = self._extract_status(authoritative_result)
        shadow_status = self._extract_status(shadow_result)
        skippable = {'unavailable', 'out_of_domain', 'unsupported'}
        if auth_status in skippable or shadow_status in skippable:
            return None
        return self._parity_comparator.compare(auth_diag, shadow_diag)

    @staticmethod
    def _extract_diagnostic(result: Any) -> Mapping[str, Any]:
        if isinstance(result, Mapping):
            diagnostic = dict(result.get('diagnostic') or result)
            control_audit = result.get('control_audit')
        else:
            diagnostic = dict(getattr(result, 'diagnostic', None) or {})
            control_audit = getattr(result, 'control_audit', None)
        if isinstance(control_audit, Mapping):
            applied = control_audit.get('applied')
        else:
            applied = getattr(control_audit, 'applied', None)
        if isinstance(applied, Mapping):
            for key in ('temperature_C', 'pressure_bar', 'fO2_log'):
                if key in applied:
                    diagnostic.setdefault(key, applied[key])
        return diagnostic

    @staticmethod
    def _extract_status(result: Any) -> str:
        if isinstance(result, Mapping):
            return str(result.get('status', 'ok'))
        return str(getattr(result, 'status', 'ok'))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_control_audit(self, request: IntentRequest) -> ControlAudit:
        """ControlAudit with applied=requested and 'shadow' note.

        MAGEMin consumes T / P / fO2 as inputs but never enforces them
        (it has no ledger authority). The audit records the values
        verbatim with the shadow note so a trace consumer sees the
        engine ran but didn't enforce.
        """
        requested = {
            'temperature_C': float(request.temperature_C),
            'pressure_bar': float(request.pressure_bar),
            'fO2_log': (
                float(request.fO2_log) if request.fO2_log is not None else None
            ),
        }
        return ControlAudit(
            requested=requested,
            applied=dict(requested),
            notes=(_SHADOW_AUDIT_NOTE,),
        )

    def _composition_from_view(self, request: IntentRequest) -> dict:
        """Extract account -> species_mol from the account view."""
        accounts = request.account_view.accounts
        result: dict = {}
        for account in (self.DECLARED_ACCOUNT,):
            species_mol = dict(accounts.get(account, {}) or {})
            result[account] = {
                str(sp): float(mol)
                for sp, mol in species_mol.items()
                if _is_finite(mol) and float(mol) > 0.0
            }
        return result

    def _composition_wt_pct(
        self,
        species_mol: Mapping[str, float],
        species_formula_registry: Mapping[str, Any],
    ) -> dict:
        """Project mol -> wt% for the domain gate."""
        if not species_mol:
            return {}
        from simulator.accounting.formulas import resolve_species_formula

        kg_by_species: dict = {}
        total_kg = 0.0
        for species, mol in species_mol.items():
            mol_val = float(mol)
            if mol_val <= 0.0:
                continue
            try:
                formula = resolve_species_formula(species, species_formula_registry)
            except Exception:
                continue
            mass_kg = mol_val * formula.molar_mass_kg_per_mol()
            if mass_kg <= 0.0:
                continue
            kg_by_species[str(species)] = (
                kg_by_species.get(str(species), 0.0) + mass_kg
            )
            total_kg += mass_kg
        if total_kg <= 0.0:
            return {}
        return {
            species: kg / total_kg * 100.0
            for species, kg in kg_by_species.items()
        }

    def _ensure_backend(self) -> Optional[Any]:
        """Return the live adapter, lazily constructing it on first use.

        The construction is wrapped in a broad ``except`` because
        importing :class:`MAGEMinBackend` should not crash the provider
        when MAGEMin is unavailable -- the provider must fall through
        to ``status='unavailable'`` cleanly.
        """
        if self._backend is not None:
            if not self._backend_initialised:
                try:
                    self._backend.initialize(dict(_SHADOW_BACKEND_CONFIG))
                except Exception:
                    pass
                self._backend_initialised = True
            return self._backend
        try:
            from simulator.melt_backend.magemin import MAGEMinBackend
            backend = MAGEMinBackend()
            backend.initialize(dict(_SHADOW_BACKEND_CONFIG))
        except Exception:
            self._backend = None
            return None
        self._backend = backend
        self._backend_initialised = True
        return backend

    @staticmethod
    def _backend_available(backend: Any) -> bool:
        is_avail = getattr(backend, 'is_available', None)
        if callable(is_avail):
            try:
                return bool(is_avail())
            except Exception:
                return False
        return False

    def _run_backend(
        self,
        backend: Any,
        request: IntentRequest,
        *,
        composition_mol_by_account: dict,
    ) -> Any:
        """Call the adapter's :meth:`equilibrate`.

        Returns the adapter's :class:`EquilibriumResult`. The adapter itself
        converts failure into an ``EquilibriumResult`` with
        ``status='not_converged'`` rather than raising, so the broad except
        here is a final safety net for unexpected exceptions only.
        """
        species_registry = dict(
            request.account_view.species_formula_registry or {}
        )
        try:
            return backend.equilibrate(
                temperature_C=float(request.temperature_C),
                pressure_bar=float(request.pressure_bar),
                fO2_log=(
                    float(request.fO2_log)
                    if request.fO2_log is not None
                    else -9.0
                ),
                composition_mol_by_account=composition_mol_by_account,
                species_formula_registry=species_registry,
            )
        except Exception as exc:  # noqa: BLE001 - optional shadow boundary
            return EquilibriumResult(
                status='not_converged',
                warnings=(f'MAGEMin equilibrate raised: {exc}',),
            )

    def _run_liquidus_finder(
        self,
        backend: Any,
        request: IntentRequest,
        *,
        composition_mol_by_account: dict,
    ) -> Any:
        finder = getattr(backend, 'find_liquidus_solidus', None)
        if not callable(finder):
            return self._run_backend(
                backend,
                request,
                composition_mol_by_account=composition_mol_by_account,
            )
        species_registry = dict(
            request.account_view.species_formula_registry or {}
        )
        try:
            return finder(
                pressure_bar=float(request.pressure_bar),
                fO2_log=(
                    float(request.fO2_log)
                    if request.fO2_log is not None
                    else -9.0
                ),
                composition_mol_by_account=composition_mol_by_account,
                species_formula_registry=species_registry,
            )
        except Exception as exc:  # noqa: BLE001 - optional engine boundary
            return LiquidusSolidusResult(
                status='not_converged',
                warnings=(f'MAGEMin liquidus finder failed: {exc}',),
            )

    @staticmethod
    def _mode_label(backend: Any) -> str:
        """Report which bridge the adapter is using.

        Mirrors :class:`AlphaMELTSProvider`'s 'mode' label (one of
        ``'petthermotools'`` / ``'subprocess'``). The MAGEMin adapter
        exposes the same posture via ``_bridge``; we surface it so a
        trace consumer can tell which path produced the answer.
        """
        return str(getattr(backend, '_bridge', None) or 'unavailable')

    def _engine_version(self) -> str:
        if self._backend is None:
            return 'unavailable'
        getter = getattr(self._backend, 'get_engine_version', None)
        if callable(getter):
            try:
                return str(getter())
            except Exception:
                return 'unavailable'
        return 'unavailable'

    @staticmethod
    def _project_equilibrium(
        equilibrium: Any,
        *,
        mode: str,
        engine_version: str,
    ) -> MAGEMinShadowDiagnostics:
        """Convert an adapter :class:`EquilibriumResult` to MAGEMin
        diagnostics.

        Mirrors :func:`engines.alphamelts.parser.
        project_equilibrium_to_diagnostics` so the parity comparator
        receives matching shapes from both engines.
        """
        if equilibrium is None:
            return MAGEMinShadowDiagnostics(
                mode=mode,
                engine_version=engine_version,
                backend_status='unavailable',
            )

        warnings = tuple(
            str(w) for w in (getattr(equilibrium, 'warnings', ()) or ())
        )
        phases_present = tuple(
            str(p) for p in (
                getattr(equilibrium, 'phases_present', ()) or ()
            )
        )
        phase_masses_kg = {
            str(k): float(v)
            for k, v in dict(
                getattr(equilibrium, 'phase_masses_kg', {}) or {}
            ).items()
            if _is_finite(v) and float(v) > 0.0
        }
        liquid_comp = {
            str(k): float(v)
            for k, v in dict(
                getattr(equilibrium, 'liquid_composition_wt_pct', {}) or {}
            ).items()
            if _is_finite(v)
        }
        liquid_fraction_raw = getattr(equilibrium, 'liquid_fraction', None)
        liquid_fraction = (
            float(liquid_fraction_raw)
            if liquid_fraction_raw is not None and _is_finite(liquid_fraction_raw)
            else None
        )

        # Modal abundance: project per-phase mass onto wt% summing to 100.
        # Same projection AlphaMELTS uses; the parity comparator looks up
        # ``phase_modes_wt_pct`` directly.
        total = sum(phase_masses_kg.values())
        if total > 0:
            phase_modes_wt_pct = {
                phase: mass / total * 100.0
                for phase, mass in phase_masses_kg.items()
            }
        else:
            phase_modes_wt_pct = {}

        # Liquidus: the today-hook adapter does not currently surface a
        # structured liquidus_T_K field. Fall back to the equilibration
        # temperature ONLY when there is unambiguous evidence we are at
        # liquidus (``liquid_fraction == 1.0`` from a known single-phase
        # MAGEMin run). Otherwise leave it None and let the parity
        # comparator hit its conservative "cannot evaluate" branch.
        liquidus_T_K = _safe_attr_float(equilibrium, 'liquidus_T_K')
        liquidus_T_C: Optional[float] = None
        if liquidus_T_K is not None:
            liquidus_T_C = liquidus_T_K - CELSIUS_TO_KELVIN_OFFSET
        else:
            # Fall back to a structured liquidus_T_C if the adapter ever
            # adds one.
            liquidus_T_C = _safe_attr_float(equilibrium, 'liquidus_T_C')
            if liquidus_T_C is not None:
                liquidus_T_K = liquidus_T_C + CELSIUS_TO_KELVIN_OFFSET
        solidus_T_C = _safe_attr_float(equilibrium, 'solidus_T_C')

        backend_status = str(getattr(equilibrium, 'status', 'ok') or 'ok')
        # Thread adapter diagnostics (incl. aggregate-budget exhaustion
        # reason/elapsed/call_count/last_T) through the provider envelope.
        # Pre-fix the liquidus finder produced these on
        # LiquidusSolidusResult.diagnostics for the in-process path only;
        # without this copy the subprocess/provider projection dropped them.
        backend_diagnostics = dict(
            getattr(equilibrium, 'diagnostics', {}) or {}
        )
        backend_status_reason = backend_diagnostics.get('backend_status_reason')
        if backend_status_reason is None:
            backend_status_reason = backend_diagnostics.get('reason')
        if backend_status_reason is not None:
            backend_status_reason = str(backend_status_reason)

        return MAGEMinShadowDiagnostics(
            liquidus_T_K=liquidus_T_K,
            liquidus_T_C=liquidus_T_C,
            solidus_T_C=solidus_T_C,
            phases_present=phases_present,
            phase_modes_wt_pct=phase_modes_wt_pct,
            liquid_composition_wt_pct=liquid_comp,
            phase_masses_kg=phase_masses_kg,
            liquid_fraction=liquid_fraction,
            mode=mode,
            engine_version=engine_version,
            backend_status=backend_status,
            backend_warnings=warnings,
            backend_diagnostics=backend_diagnostics,
            backend_status_reason=backend_status_reason,
        )


def _is_finite(value: Any) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    if numeric != numeric:
        return False
    if numeric in (float('inf'), float('-inf')):
        return False
    return True


def _safe_attr_float(obj: Any, name: str) -> Optional[float]:
    value = getattr(obj, name, None)
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not _is_finite(result):
        return None
    return result


__all__ = ('MAGEMinShadowProvider',)
