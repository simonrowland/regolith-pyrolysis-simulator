"""AlphaMELTS kernel-registered diagnostic provider.

First third-party adapter promoted to the kernel plane (goal #8
``ALPHAMELTS-DIAGNOSTIC-GATE``). The provider:

- declares the :data:`SILICATE_LIQUIDUS` + :data:`SILICATE_EQUILIBRIUM`
  intent set (matching the binding-spec authority matrix for
  AlphaMELTS),
- declares ``process.cleaned_melt`` as its sole accessible account; the
  kernel filter drops every other account before dispatch (checklist
  item 4),
- runs the :class:`AlphaMELTSDomainGate` on the cleaned-melt
  composition before delegating to the today-hook adapter,
- delegates to :mod:`simulator.melt_backend.alphamelts.AlphaMELTSBackend`
  for the chemistry (subprocess + PetThermoTools paths owned by the
  adapter; this module orchestrates path selection only),
- returns a :class:`LiquidusDiagnostics` payload on
  :attr:`IntentResult.diagnostic`, with ``transition=None`` always --
  AlphaMELTS is **diagnostic-only** under goal #8 checklist item 5.

The provider class MUST NOT import :class:`LedgerTransitionProposal`
from anywhere -- not even for type hints. The
``test_alphamelts_provider.py::test_no_ledger_transition_import`` test
enforces this with an AST walk over the module source.

Authority posture
-----------------
AlphaMELTS is registered as the **authoritative** provider for both
intents in the registry sense (so the kernel can dispatch to it), but
the provider's :meth:`dispatch` never builds a
:class:`LedgerTransitionProposal`. The :class:`IntentResult` always has
``transition=None``; the kernel cannot construct a ledger write from a
None proposal. This is the "diagnostic gate" of the goal title.

Per goal #8 checklist item 6, the :class:`ControlAudit` records
``applied == requested`` for T / P / fO2 with the note
``"diagnostic, not enforced"`` so a trace consumer can see the engine
ran but didn't enforce the requested controls.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from engines.alphamelts.domain import AlphaMELTSDomainGate
from engines.alphamelts.parser import project_equilibrium_to_diagnostics
from engines.alphamelts.petthermo import (
    equilibrate_via_python_api,
    python_api_available,
)
from engines.alphamelts.result import LiquidusDiagnostics
from engines.alphamelts.subprocess_runner import (
    equilibrate_via_subprocess,
    subprocess_available,
)
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


# Intent set: SILICATE_LIQUIDUS + SILICATE_EQUILIBRIUM. The goal queue
# §8 text mentions FREEZE_PATH, but the binding-spec authority matrix
# and the MAGEMin shadow provider both list this pair; the bracketed
# reviewer note in the goal spec resolves the discrepancy in favour of
# the LIQUIDUS / EQUILIBRIUM pair.
_INTENTS = frozenset({
    ChemistryIntent.SILICATE_LIQUIDUS,
    ChemistryIntent.SILICATE_EQUILIBRIUM,
})

# Sole declared account: silicate-oxide melt (binding-spec §7 isolation).
# Checklist item 4 binds this -- the kernel filter blocks every other
# account before dispatch.
_DECLARED_ACCOUNT = 'process.cleaned_melt'

# Note attached to the ControlAudit for every dispatch (checklist 6).
_DIAGNOSTIC_AUDIT_NOTE = 'diagnostic, not enforced'


class AlphaMELTSProvider(ChemistryProvider):
    """Diagnostic-only provider for AlphaMELTS via the kernel.

    See module docstring. The provider is constructed with a live
    :class:`simulator.melt_backend.alphamelts.AlphaMELTSBackend`
    instance (typically the same one the simulator already holds for
    the legacy ``MeltBackend.equilibrate`` path); ``initialize()``
    must already have run so ``backend._mode`` reflects the available
    path.

    ``backend`` may be ``None`` -- in that case :meth:`dispatch`
    returns ``status='unavailable'`` with an empty
    :class:`LiquidusDiagnostics`. This matches the kernel's
    ``status='unavailable'`` vocabulary for absent engines.
    """

    name = 'alphamelts-diagnostic'

    DECLARED_ACCOUNT = _DECLARED_ACCOUNT

    def __init__(self, backend: Optional[Any] = None) -> None:
        self._backend = backend

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id='alphamelts-diagnostic',
            intents=_INTENTS,
            # Registered as authoritative so :class:`ProviderRegistry` will
            # accept ``register(shadow=False)``. The provider never builds
            # a :class:`LedgerTransitionProposal`; :attr:`IntentResult.
            # transition` is always None. Goal #8 checklist item 5 binds
            # this, and the writer-purity invariant test catches any
            # accidental write attempt.
            is_authoritative_for=_INTENTS,
            declared_accounts=frozenset({self.DECLARED_ACCOUNT}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        """Run AlphaMELTS for ``request.intent``; return a diagnostic.

        See module docstring for the contract. The provider:

        1. Validates the intent is one it serves (defence in depth --
           the kernel registry already routes correctly).
        2. Builds the ControlAudit with ``applied == requested`` and
           the diagnostic note.
        3. Runs :class:`AlphaMELTSDomainGate`. If rejected, returns
           ``status='out_of_domain'`` with the warnings recorded.
        4. Selects the python_api / subprocess path on the live
           adapter; if neither is available, returns
           ``status='unavailable'``.
        5. Projects the adapter's :class:`EquilibriumResult` into a
           :class:`LiquidusDiagnostics`.
        6. Returns the :class:`IntentResult` with ``transition=None``.
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
                        'AlphaMELTSProvider serves SILICATE_LIQUIDUS + '
                        'SILICATE_EQUILIBRIUM only'
                    ),
                },
            )

        control_audit = self._build_control_audit(request)

        # Compose the account-view-derived composition. The kernel
        # filter already restricts the view to ``process.cleaned_melt``
        # (checklist 4 enforcement); we re-extract the per-species mol
        # map to feed the adapter's ``composition_mol_by_account``
        # kwarg.
        composition_mol_by_account = self._composition_from_view(request)
        composition_wt_pct = self._composition_wt_pct(
            composition_mol_by_account.get(self.DECLARED_ACCOUNT, {}),
            request.account_view.species_formula_registry,
        )

        # Run the domain gate even when the adapter is None so callers
        # see a meaningful rejection (rather than a silent
        # 'unavailable' surface that hides the input issue).
        valid, gate_warnings = AlphaMELTSDomainGate.validate(composition_wt_pct)
        if not valid:
            return IntentResult(
                intent=request.intent,
                status='out_of_domain',
                transition=None,
                control_audit=control_audit,
                diagnostic=LiquidusDiagnostics(
                    mode='unavailable',
                    engine_version=self._engine_version(),
                    backend_status='out_of_domain',
                    backend_warnings=tuple(gate_warnings),
                ).as_diagnostic(),
                warnings=tuple(gate_warnings),
            )

        if self._backend is None or not self._backend_available():
            return IntentResult(
                intent=request.intent,
                status='unavailable',
                transition=None,
                control_audit=control_audit,
                diagnostic=LiquidusDiagnostics(
                    mode='unavailable',
                    engine_version=self._engine_version(),
                    backend_status='unavailable',
                ).as_diagnostic(),
                warnings=(
                    'AlphaMELTS adapter not available '
                    '(neither PetThermoTools nor subprocess binary loaded)',
                ),
            )

        mode, equilibrium = self._run_backend(
            request,
            composition_mol_by_account=composition_mol_by_account,
        )
        diagnostics = project_equilibrium_to_diagnostics(
            equilibrium,
            mode=mode,
            engine_version=self._engine_version(),
        )

        backend_status = diagnostics.backend_status
        # Map the adapter's status vocabulary onto the kernel's:
        # 'ok' / 'not_converged' / 'out_of_domain' / 'unavailable' /
        # 'unsupported'. The adapter already uses the same vocabulary so
        # this is mostly a pass-through; we surface 'unavailable' when
        # the adapter signalled it during the run (e.g. PetThermoTools
        # path crashed mid-call and the adapter zeroed _mode).
        kernel_status = backend_status if backend_status in (
            'ok', 'not_converged', 'out_of_domain', 'unavailable'
        ) else 'ok'

        return IntentResult(
            intent=request.intent,
            status=kernel_status,
            transition=None,  # Diagnostic-only -- checklist item 5.
            control_audit=control_audit,
            diagnostic=diagnostics.as_diagnostic(),
            warnings=tuple(diagnostics.backend_warnings),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_control_audit(self, request: IntentRequest) -> ControlAudit:
        """ControlAudit with applied=requested and 'diagnostic' note.

        Checklist item 6: AlphaMELTS does not enforce fO2 / P (it
        consumes the requested values as inputs to the MELTS run; the
        engine's actual applied fO2/P depends on the redox buffer
        choice). The audit records the values verbatim with the
        diagnostic note so a trace consumer sees the engine ran but
        didn't enforce.
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
            notes=(_DIAGNOSTIC_AUDIT_NOTE,),
        )

    def _composition_from_view(
        self, request: IntentRequest
    ) -> dict:
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
        """Project mol -> wt% for the domain gate.

        Lazy import on ``simulator.accounting.formulas`` to avoid the
        package-init cycle (see ``engines/builtin/__init__.py`` for the
        canonical description of the cycle). Mirrors the projection
        :meth:`AlphaMELTSBackend._composition_kg_to_wt_pct` runs on the
        adapter side.
        """
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
                # An unregistered species at this layer is the same
                # condition the adapter would hit downstream; surface
                # it through the domain gate by NOT including the
                # species (the gate will then report a low major-oxide
                # sum or missing-species warning). The kernel-level
                # writer-purity invariants are unaffected because we
                # never emit a transition.
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

    def _backend_available(self) -> bool:
        if self._backend is None:
            return False
        is_avail = getattr(self._backend, 'is_available', None)
        if callable(is_avail):
            return bool(is_avail())
        # Backends without is_available() are treated as unavailable.
        return False

    def _run_backend(
        self,
        request: IntentRequest,
        *,
        composition_mol_by_account: dict,
    ) -> tuple:
        """Select the path (python_api / subprocess) and run.

        Returns ``(mode_label, equilibrium_result)``. ``mode_label`` is
        ``'petthermotools'`` or ``'subprocess'``; the
        :class:`LiquidusDiagnostics` ``mode`` field surfaces this so a
        trace consumer can tell which path produced the answer.
        """
        species_registry = dict(
            request.account_view.species_formula_registry or {}
        )
        if python_api_available(self._backend):
            equilibrium = equilibrate_via_python_api(
                self._backend,
                temperature_C=request.temperature_C,
                pressure_bar=request.pressure_bar,
                fO2_log=request.fO2_log if request.fO2_log is not None else -9.0,
                composition_mol_by_account=composition_mol_by_account,
                species_formula_registry=species_registry,
            )
            return 'petthermotools', equilibrium
        if subprocess_available(self._backend):
            equilibrium = equilibrate_via_subprocess(
                self._backend,
                temperature_C=request.temperature_C,
                pressure_bar=request.pressure_bar,
                fO2_log=request.fO2_log if request.fO2_log is not None else -9.0,
                composition_mol_by_account=composition_mol_by_account,
                species_formula_registry=species_registry,
            )
            return 'subprocess', equilibrium
        # _backend_available returned True so this branch should never
        # fire; treat it as unavailable defensively.
        return 'unavailable', None

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


__all__ = ('AlphaMELTSProvider',)
