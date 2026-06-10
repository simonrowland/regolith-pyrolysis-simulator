"""AlphaMELTS kernel-registered diagnostic provider.

First third-party adapter promoted to the kernel plane (goal #8
``ALPHAMELTS-DIAGNOSTIC-GATE``). The provider:

- declares the AlphaMELTS silicate diagnostic intent set
  (``SILICATE_LIQUIDUS``, ``SILICATE_EQUILIBRIUM``,
  ``EQUILIBRIUM_CRYSTALLIZATION``, and
  ``GATE_LIQUID_FRACTION``),
- declares ``process.cleaned_melt`` as its sole accessible account; the
  kernel filter drops every other account before dispatch (checklist
  item 4),
- runs the :class:`AlphaMELTSDomainGate` on the cleaned-melt
  composition before delegating to the today-hook adapter,
- delegates to :mod:`simulator.melt_backend.alphamelts.AlphaMELTSBackend`
  for the chemistry (ThermoEngine + PetThermoTools + subprocess paths owned by the
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
AlphaMELTS is registered as the **authoritative** provider for these
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
from engines.alphamelts.thermoengine import (
    equilibrate_via_thermoengine,
    thermoengine_available,
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
from simulator.melt_backend.liquidus import (
    EquilibriumCrystallizationPathResult,
    LiquidusSolidusResult,
    build_equilibrium_crystallization_path,
)
from simulator.melt_backend.base import LiquidFractionInvalidError


# Intent set: AlphaMELTS-owned silicate diagnostic intents. MAGEMin shadows
# only SILICATE_LIQUIDUS + SILICATE_EQUILIBRIUM; the gate intent consumes
# the same EC liquid-fraction table when AlphaMELTS is available.
_INTENTS = frozenset({
    ChemistryIntent.SILICATE_LIQUIDUS,
    ChemistryIntent.SILICATE_EQUILIBRIUM,
    ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
    ChemistryIntent.GATE_LIQUID_FRACTION,
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
        transport path.

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
        4. Branches by intent: ``SILICATE_LIQUIDUS`` runs the
           liquidus/solidus finder; ``EQUILIBRIUM_CRYSTALLIZATION`` builds
           the monotone liquid-fraction table; ``SILICATE_EQUILIBRIUM``
           keeps the single-T equilibration path.
        5. Projects the adapter result into a
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
                        'SILICATE_EQUILIBRIUM + '
                        'EQUILIBRIUM_CRYSTALLIZATION + '
                        'GATE_LIQUID_FRACTION only'
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
        redox_diagnostic = self._redox_diagnostic(
            request,
            composition_mol_by_account.get(self.DECLARED_ACCOUNT, {}),
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
                    backend_diagnostics=_out_of_domain_diagnostics(
                        request,
                        composition_wt_pct=composition_wt_pct,
                        composition_mol_by_account=composition_mol_by_account,
                    ),
                    **redox_diagnostic,
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
                    **redox_diagnostic,
                ).as_diagnostic(),
                warnings=(
                'AlphaMELTS adapter not available '
                '(no ThermoEngine, PetThermoTools, or subprocess transport)',
                ),
            )

        if request.intent == ChemistryIntent.SILICATE_LIQUIDUS:
            mode, equilibrium = self._run_liquidus_finder(
                request,
                composition_mol_by_account=composition_mol_by_account,
            )
        elif request.intent in (
            ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION,
            ChemistryIntent.GATE_LIQUID_FRACTION,
        ):
            mode, equilibrium = self._run_equilibrium_crystallization_path(
                request,
                composition_mol_by_account=composition_mol_by_account,
            )
        else:
            mode, equilibrium = self._run_backend(
                request,
                composition_mol_by_account=composition_mol_by_account,
            )
        diagnostics = project_equilibrium_to_diagnostics(
            equilibrium,
            mode=mode,
            engine_version=self._engine_version(),
            **redox_diagnostic,
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
            'fe_redox_policy': request.fe_redox_policy,
        }
        return ControlAudit(
            requested=requested,
            applied=dict(requested),
            notes=(_DIAGNOSTIC_AUDIT_NOTE,),
        )

    def _redox_diagnostic(
        self,
        request: IntentRequest,
        species_mol: Mapping[str, float],
    ) -> dict:
        intrinsic_fO2_log = request.control_inputs.get(
            'intrinsic_fO2_log',
            request.fO2_log,
        )
        return {
            'fe_redox_policy': request.fe_redox_policy,
            'applied_fe3fet': self._applied_fe3fet(species_mol),
            'intrinsic_fO2_log': (
                float(intrinsic_fO2_log)
                if intrinsic_fO2_log is not None else None
            ),
        }

    def _applied_fe3fet(self, species_mol: Mapping[str, float]) -> Optional[float]:
        feo_mol = max(0.0, float((species_mol or {}).get('FeO', 0.0) or 0.0))
        fe2o3_mol = max(0.0, float((species_mol or {}).get('Fe2O3', 0.0) or 0.0))
        total_fe_mol = feo_mol + 2.0 * fe2o3_mol
        if total_fe_mol <= 0.0:
            return None
        return (2.0 * fe2o3_mol) / total_fe_mol

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
        """Select the transport path and run.

        Returns ``(mode_label, equilibrium_result)``. ``mode_label`` is
        ``'thermoengine'``, ``'petthermotools'``, or ``'subprocess'``; the
        :class:`LiquidusDiagnostics` ``mode`` field surfaces this so a
        trace consumer can tell which path produced the answer.
        """
        species_registry = dict(
            request.account_view.species_formula_registry or {}
        )
        if thermoengine_available(self._backend):
            equilibrium = equilibrate_via_thermoengine(
                self._backend,
                temperature_C=request.temperature_C,
                pressure_bar=request.pressure_bar,
                fO2_log=request.fO2_log if request.fO2_log is not None else -9.0,
                composition_mol_by_account=composition_mol_by_account,
                species_formula_registry=species_registry,
            )
            return 'thermoengine', equilibrium
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

    def _run_liquidus_finder(
        self,
        request: IntentRequest,
        *,
        composition_mol_by_account: dict,
    ) -> tuple:
        finder = getattr(self._backend, 'find_liquidus_solidus', None)
        if not callable(finder):
            return 'unavailable', LiquidusSolidusResult(
                status='unavailable',
                warnings=('AlphaMELTS backend has no liquidus finder',),
            )
        if thermoengine_available(self._backend):
            mode = 'thermoengine'
        elif python_api_available(self._backend):
            mode = 'petthermotools'
        elif subprocess_available(self._backend):
            mode = 'subprocess'
        else:
            mode = 'unavailable'
        species_registry = dict(
            request.account_view.species_formula_registry or {}
        )
        try:
            result = finder(
                pressure_bar=request.pressure_bar,
                fO2_log=request.fO2_log if request.fO2_log is not None else -9.0,
                composition_mol_by_account=composition_mol_by_account,
                species_formula_registry=species_registry,
            )
        except Exception as exc:  # noqa: BLE001 - optional engine boundary
            result = LiquidusSolidusResult(
                status='not_converged',
                warnings=(f'AlphaMELTS liquidus finder failed: {exc}',),
            )
        return mode, result

    def _run_equilibrium_crystallization_path(
        self,
        request: IntentRequest,
        *,
        composition_mol_by_account: dict,
    ) -> tuple:
        if thermoengine_available(self._backend):
            mode = 'thermoengine'
            equilibrate_transport = equilibrate_via_thermoengine
        elif python_api_available(self._backend):
            mode = 'petthermotools'
            equilibrate_transport = equilibrate_via_python_api
        elif subprocess_available(self._backend):
            return 'subprocess', EquilibriumCrystallizationPathResult(
                status='unavailable',
                warnings=(
                    'AlphaMELTS equilibrium crystallization requires '
                    'PetThermoTools python_api mode with residual liquid '
                    'composition; subprocess path is not EC-capable',
                ),
            )
        else:
            return 'unavailable', EquilibriumCrystallizationPathResult(
                status='unavailable',
                warnings=(
                    'AlphaMELTS equilibrium crystallization requires '
                    'PetThermoTools python_api mode with residual liquid '
                    'composition',
                ),
            )

        finder = getattr(self._backend, 'find_liquidus_solidus', None)
        if not callable(finder):
            return mode, EquilibriumCrystallizationPathResult(
                status='unavailable',
                warnings=('AlphaMELTS backend has no liquidus finder',),
            )
        species_registry = dict(
            request.account_view.species_formula_registry or {}
        )
        try:
            bounds = finder(
                pressure_bar=request.pressure_bar,
                fO2_log=request.fO2_log if request.fO2_log is not None else -9.0,
                composition_mol_by_account=composition_mol_by_account,
                species_formula_registry=species_registry,
            )
        except Exception as exc:  # noqa: BLE001 - optional engine boundary
            return mode, EquilibriumCrystallizationPathResult(
                status='not_converged',
                warnings=(f'AlphaMELTS EC liquidus finder failed: {exc}',),
            )
        if (
            bounds.status != 'ok'
            or bounds.solidus_T_C is None
            or bounds.liquidus_T_C is None
        ):
            return mode, EquilibriumCrystallizationPathResult(
                liquidus_T_C=bounds.liquidus_T_C,
                liquidus_T_K=bounds.liquidus_T_K,
                solidus_T_C=bounds.solidus_T_C,
                status=bounds.status,
                warnings=tuple(bounds.warnings) or (
                    'AlphaMELTS EC liquidus/solidus bounds unavailable',
                ),
                samples=bounds.samples,
                iterations=bounds.iterations,
            )

        sample_warnings: list[str] = []

        def sample_liquid_state(temperature_C: float) -> tuple[float, dict]:
            result = equilibrate_transport(
                self._backend,
                temperature_C=float(temperature_C),
                pressure_bar=request.pressure_bar,
                fO2_log=(
                    request.fO2_log if request.fO2_log is not None else -9.0
                ),
                composition_mol_by_account=composition_mol_by_account,
                species_formula_registry=species_registry,
            )
            if getattr(result, 'status', 'ok') != 'ok':
                warning = '; '.join(getattr(result, 'warnings', ()) or ())
                raise RuntimeError(warning or str(getattr(result, 'status')))
            composition = dict(
                getattr(result, 'liquid_composition_wt_pct', {}) or {}
            )
            raw_liquid_fraction = getattr(result, 'liquid_fraction', None)
            if not _is_finite(raw_liquid_fraction):
                raise LiquidFractionInvalidError(
                    f'liquid_fraction_invalid: {raw_liquid_fraction!r}'
                )
            liquid_fraction = float(raw_liquid_fraction)
            if liquid_fraction < 0.0 or liquid_fraction > 1.0:
                raise LiquidFractionInvalidError(
                    f'liquid_fraction_invalid: {raw_liquid_fraction!r}'
                )
            # Autoreview r7 P2 (2026-05-27): the EC path samples the
            # solidus-to-liquidus grid INCLUDING endpoints.  Post r4
            # ThermoEngine fix (the bulk_wt fabrication for subsolidus
            # states was removed), a valid solidus / subsolidus sample
            # reports ``liquid_fraction == 0`` with an EMPTY
            # ``liquid_composition_wt_pct`` -- the right signal for a
            # no-liquid endpoint.  This branch previously raised
            # unconditionally when composition was empty, turning a
            # legitimate zero-liquid endpoint into a ``not_converged``
            # status and breaking the EQUILIBRIUM_CRYSTALLIZATION /
            # GATE_LIQUID_FRACTION result.  Now we only treat an empty
            # composition as a sample error when the engine ALSO
            # reports residual liquid; zero-liquid endpoints pass
            # through with an empty composition dict and the
            # downstream path-builder handles them.
            if not composition and liquid_fraction > 0.0:
                raise RuntimeError(
                    'AlphaMELTS EC sample lacks residual liquid '
                    f'composition despite liquid_fraction={liquid_fraction!r}'
                )
            for warning in getattr(result, 'warnings', ()) or ():
                if warning not in sample_warnings:
                    sample_warnings.append(str(warning))
            return liquid_fraction, composition

        path = build_equilibrium_crystallization_path(
            sample_liquid_state,
            solidus_T_C=bounds.solidus_T_C,
            liquidus_T_C=bounds.liquidus_T_C,
        )
        return mode, EquilibriumCrystallizationPathResult(
            liquidus_T_C=path.liquidus_T_C,
            liquidus_T_K=path.liquidus_T_K,
            solidus_T_C=path.solidus_T_C,
            liquid_fraction=path.liquid_fraction,
            status=path.status,
            warnings=(
                *tuple(bounds.warnings),
                *tuple(path.warnings),
                *tuple(sample_warnings[:6]),
            ),
            liquid_fraction_path=path.liquid_fraction_path,
            samples=path.samples,
            iterations=bounds.iterations + path.iterations,
        )

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


def _out_of_domain_diagnostics(
    request: IntentRequest,
    *,
    composition_wt_pct: Mapping[str, float],
    composition_mol_by_account: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    crash_point: dict[str, Any] = {
        'temperature_C': float(request.temperature_C),
        'pressure_bar': float(request.pressure_bar),
        'composition_wt_pct': _finite_positive_mapping(composition_wt_pct),
    }
    if request.fO2_log is not None:
        crash_point['fO2_log'] = float(request.fO2_log)
    cleaned_melt = dict(
        composition_mol_by_account.get(AlphaMELTSProvider.DECLARED_ACCOUNT, {})
        or {}
    )
    crash_point['composition_mol'] = _finite_positive_mapping(cleaned_melt)
    by_account = _finite_positive_nested_mapping(composition_mol_by_account)
    if by_account:
        crash_point['composition_mol_by_account'] = by_account
    return {
        'backend_status': 'out_of_domain',
        'out_of_domain_crash_point': crash_point,
    }


def _finite_positive_mapping(values: Mapping[str, float]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, value in dict(values or {}).items():
        if _is_finite(value) and float(value) > 0.0:
            result[str(key)] = float(value)
    return result


def _finite_positive_nested_mapping(
    values: Mapping[str, Mapping[str, float]],
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for account, species_mol in dict(values or {}).items():
        compact = _finite_positive_mapping(species_mol)
        if compact:
            result[str(account)] = compact
    return result


__all__ = ('AlphaMELTSProvider',)
