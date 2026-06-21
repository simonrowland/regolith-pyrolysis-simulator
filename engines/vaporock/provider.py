"""VapoRock diagnostic VAPOR_PRESSURE shadow provider.

The provider wraps the :class:`simulator.melt_backend.vaporock.VapoRockBackend`
adapter (the library import + species-name normalization stay there) and
exposes its gas speciation as a kernel-registered diagnostic shadow for
:attr:`ChemistryIntent.VAPOR_PRESSURE`.

Authority posture
-----------------
VapoRock is **not** authoritative for VAPOR_PRESSURE. Builtin
Antoine/Ellingham owns the pressure dict consumed by evaporation; VapoRock
may run beside it as a diagnostic overlay.

The intent itself is read-only / diagnostic at the kernel level:
:attr:`IntentResult.transition` is always ``None``. Downstream
``EVAPORATION_TRANSITION`` consumes the builtin authoritative
``diagnostic['vapor_pressures_Pa']``. VapoRock reports an empty
``vapor_pressures_Pa`` and keeps every real gas pressure under
``vaporock_full_speciation_Pa`` for diagnostics only.

This module MUST NOT import :class:`LedgerTransitionProposal` from
anywhere -- not even for type hints. The writer-purity AST walk enforces
this for every provider that might be registered in the vapor-pressure
plane.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional

from engines.builtin._common import (
    reject_wrong_intent,
    resolve_transport_pO2_bar,
)
from engines.vaporock.result import VapoRockDiagnostics
from simulator.chemistry.kernel.capabilities import (
    CapabilityProfile,
    ChemistryIntent,
)
from simulator.chemistry.kernel.dto import (
    ControlAudit,
    IntentRequest,
    IntentResult,
)
from simulator.chemistry.kernel.errors import ProviderUnavailableError
from simulator.chemistry.kernel.provider import ChemistryProvider


_INTENTS = frozenset({ChemistryIntent.VAPOR_PRESSURE})
_DECLARED_ACCOUNT = 'process.cleaned_melt'


class VapoRockProvider(ChemistryProvider):
    """Diagnostic VAPOR_PRESSURE provider backed by the VapoRock adapter.

    Args:
        backend: Optional pre-initialised
            :class:`simulator.melt_backend.vaporock.VapoRockBackend`.
            When ``None`` the provider lazily constructs one on first
            dispatch.  Provider-level availability is decided by the
            adapter: if the upstream ``vaporock`` library is missing
            the adapter's ``is_available()`` returns False and this
            provider raises :class:`ProviderUnavailableError` instead
            of returning a result -- silent fallback is forbidden, the
            kernel handles fallback opt-in at the dispatch layer.
        vapor_pressure_data: Optional ``data/vapor_pressures.yaml``
            payload.  When supplied the provider filters the VapoRock
            output to active species the YAML declares (the universe
            the simulator's downstream ``EVAPORATION_FLUX`` step is
            wired to consume); without this filter VapoRock's broader
            ~30-species output (``O2``, ``Si2``, ``Al2O2``, etc.) would
            crash the downstream stoichiometry validator.  Rows marked
            ``consumer_status: inactive`` are excluded from the VapoRock
            consumer surface. Goal #10 hard-constraint binds this: the
            authority swap must keep mass balance at 0.000%.
            When omitted, no filtering happens and the full VapoRock
            output is returned -- appropriate for tests that operate
            below the simulator (the vapor-pressure dict alone is
            harmless when no evaporation step consumes it).
    """

    name = 'vaporock'

    PROVIDER_ID = 'vaporock'
    DECLARED_ACCOUNT = _DECLARED_ACCOUNT

    def __init__(
        self,
        backend: Optional[Any] = None,
        vapor_pressure_data: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._backend: Optional[Any] = backend
        # ``_backend_initialised`` mirrors the MAGEMin shadow's lazy
        # init posture: providers must be registerable without forcing
        # the upstream library to load at registry-build time.  The
        # first dispatch probes availability; if the probe fails the
        # provider raises ``ProviderUnavailableError`` cleanly.
        self._backend_initialised: bool = backend is not None
        # Set of vapor-species names the simulator knows how to
        # consume downstream.  Computed lazily from the YAML so an
        # empty payload yields no filter (the full VapoRock output
        # passes through -- used by adapter-level tests that do not
        # exercise EVAPORATION_FLUX).
        self._allowed_species: frozenset[str] = self._build_allowed_species(
            vapor_pressure_data
        )

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.PROVIDER_ID,
            intents=_INTENTS,
            # Diagnostic-only for VAPOR_PRESSURE. Builtin Antoine/Ellingham
            # is the authoritative provider consumed by evaporation.
            is_authoritative_for=frozenset(),
            declared_accounts=frozenset({self.DECLARED_ACCOUNT}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        """Run VapoRock for ``VAPOR_PRESSURE``; return a diagnostic.

        The provider:

        1. Validates the intent is one it serves (defence in depth).
        2. Lazily initialises the adapter.  If the upstream library is
           missing, raises :class:`ProviderUnavailableError` -- the
           kernel's fallback opt-in path (``allow_fallback_intents``)
           is the only legitimate way to demote a missing VapoRock to
           the builtin Antoine path.
        3. Builds the :class:`ControlAudit` with separate intrinsic-melt
           redox and transport-gas fO2 provenance.
        4. Extracts ``process.cleaned_melt`` composition (the kernel
           filter has already restricted the view to this account).
        5. Calls the adapter's :meth:`equilibrate`.
        6. Projects the adapter's gas pressures into a diagnostic-only
           :class:`VapoRockDiagnostics`.
        7. Returns the :class:`IntentResult` with ``transition=None``.

        Raises:
            ProviderUnavailableError: The VapoRock adapter could not be
                initialised (library not importable).
        """

        wrong_intent = reject_wrong_intent(
            request, ChemistryIntent.VAPOR_PRESSURE
        )
        if wrong_intent is not None:
            return wrong_intent

        backend = self._ensure_backend()
        if backend is None or not self._backend_available(backend):
            # As a shadow diagnostic, unavailable VapoRock surfaces as a
            # shadow error while builtin authority continues.
            last_error = getattr(backend, '_last_error', None) if backend else None
            raise ProviderUnavailableError(
                'VapoRock diagnostic provider unavailable: '
                + (str(last_error) if last_error else 'upstream library not importable')
            )

        pO2_bar = self._resolve_pO2_bar(request)
        fO2_log_resolved = self._resolve_fO2_log(request)
        control_audit = self._control_audit(
            request,
            pO2_bar=pO2_bar,
            fO2_log_resolved=fO2_log_resolved,
        )
        composition_mol_by_account = self._composition_from_view(request)
        # VapoRock's vapor solver equilibrates gas species against the
        # supplied gas fO2/log pO2.  Keep melt-liquidus redox intrinsic in
        # the caller; VAPOR_PRESSURE uses the commanded overhead pO2.
        species_registry = dict(
            request.account_view.species_formula_registry or {}
        )

        # ``EquilibriumResult.vapor_pressures_Pa`` is the adapter's
        # diagnostic output.  The adapter never raises; it returns
        # ``status='unavailable'`` / ``'out_of_domain'`` /
        # ``'not_converged'`` when it has nothing useful to say.  We
        # therefore translate those onto the kernel-level status
        # vocabulary (``ok`` / ``not_converged`` / ``out_of_domain``)
        # rather than re-raising -- the only legitimate raise on the
        # provider surface is the up-front ``ProviderUnavailableError``
        # above when the library itself is missing.
        equilibrium = self._run_backend(
            backend,
            request,
            composition_mol_by_account=composition_mol_by_account,
            species_formula_registry=species_registry,
            fO2_log_resolved=fO2_log_resolved,
        )

        diagnostics = self._project_equilibrium(
            equilibrium,
            pO2_bar=pO2_bar,
            mode=self._mode_label(backend),
            engine_version=self._engine_version(backend),
            allowed_species=self._allowed_species,
        )
        return IntentResult(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            status='non_authoritative',
            transition=None,  # diagnostic-only -- mirrors builtin shape
            control_audit=control_audit,
            diagnostic=diagnostics.as_diagnostic(),
            warnings=tuple(diagnostics.backend_warnings),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_pO2_bar(request: IntentRequest) -> float:
        """Pick up the commanded pO2 (bar) from the caller.

        Mirrors :meth:`BuiltinVaporPressureProvider._resolve_pO2_bar`
        so a parity comparison against the builtin path sees the same
        pO2 input.
        """
        return resolve_transport_pO2_bar(request, floor_bar=1e-9)

    @staticmethod
    def _resolve_fO2_log(request: IntentRequest) -> float:
        """Convert commanded vapor pO2 into the adapter's gas fO2_log."""
        controls = request.control_inputs or {}
        if controls.get('pO2_bar') is not None:
            return float(
                math.log10(resolve_transport_pO2_bar(request, floor_bar=1e-9))
            )
        if request.fO2_log is not None:
            return float(
                math.log10(resolve_transport_pO2_bar(request, floor_bar=1e-9))
            )
        return -9.0

    @staticmethod
    def _control_audit(
        request: IntentRequest,
        *,
        pO2_bar: float,
        fO2_log_resolved: float,
    ) -> ControlAudit:
        controls = request.control_inputs or {}
        requested_transport_pO2 = (
            float(controls['pO2_bar'])
            if controls.get('pO2_bar') is not None
            else None
        )
        intrinsic_fO2_log = (
            float(request.fO2_log) if request.fO2_log is not None else None
        )
        requested = {
            'temperature_C': float(request.temperature_C),
            'pressure_bar': float(request.pressure_bar),
            'fO2_log': intrinsic_fO2_log,
            'intrinsic_fO2_log': intrinsic_fO2_log,
            'transport_pO2_bar': requested_transport_pO2,
        }
        applied = {
            'temperature_C': float(request.temperature_C),
            'pressure_bar': float(request.pressure_bar),
            'fO2_log': float(fO2_log_resolved),
            'intrinsic_fO2_log': intrinsic_fO2_log,
            'transport_pO2_bar': float(pO2_bar),
            'transport_fO2_log': float(fO2_log_resolved),
        }
        return ControlAudit(
            requested=requested,
            applied=applied,
            notes=(
                'VapoRock vapor pressure applies transport gas fO2 from '
                'transport_pO2_bar; intrinsic_fO2_log is retained as melt '
                'redox provenance.',
            ),
        )

    def _composition_from_view(self, request: IntentRequest) -> dict:
        """Extract ``account -> species_mol`` for the cleaned-melt slice."""
        accounts = request.account_view.accounts
        species_mol = dict(accounts.get(self.DECLARED_ACCOUNT, {}) or {})
        return {
            self.DECLARED_ACCOUNT: {
                str(sp): float(mol)
                for sp, mol in species_mol.items()
                if _is_finite(mol) and float(mol) > 0.0
            }
        }

    def _ensure_backend(self) -> Optional[Any]:
        """Return the live adapter, lazily constructing it on first use.

        Construction is wrapped in a broad ``except`` so import failures
        on the upstream library surface as
        :class:`ProviderUnavailableError` in :meth:`dispatch`, not as a
        traceback at provider-construction time.  This matches the MAGEMin
        shadow's lazy posture.
        """
        if self._backend is not None:
            if not self._backend_initialised:
                try:
                    self._backend.initialize({})
                except Exception:  # noqa: BLE001 - library-boundary catch
                    pass
                self._backend_initialised = True
            return self._backend
        try:
            from simulator.melt_backend.vaporock import VapoRockBackend

            backend = VapoRockBackend()
            backend.initialize({})
        except Exception:  # noqa: BLE001 - library-boundary catch
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
            except Exception:  # noqa: BLE001
                return False
        return False

    def _run_backend(
        self,
        backend: Any,
        request: IntentRequest,
        *,
        composition_mol_by_account: dict,
        species_formula_registry: Mapping[str, Any],
        fO2_log_resolved: float,
    ) -> Any:
        """Call the adapter's :meth:`equilibrate` with the kernel-derived inputs.

        The adapter handles its own library-boundary catches and
        surfaces failure via ``EquilibriumResult.status`` /
        ``warnings``; we wrap one final broad except as a safety net
        for unexpected exceptions.  Returns the adapter's
        :class:`EquilibriumResult` or ``None`` on catastrophic failure.
        """
        try:
            return backend.equilibrate(
                temperature_C=float(request.temperature_C),
                pressure_bar=float(request.pressure_bar),
                fO2_log=fO2_log_resolved,
                composition_mol_by_account=composition_mol_by_account,
                species_formula_registry=species_formula_registry,
            )
        except Exception:  # noqa: BLE001 - library-boundary catch
            return None

    @staticmethod
    def _project_equilibrium(
        equilibrium: Any,
        *,
        pO2_bar: float,
        mode: str,
        engine_version: str,
        allowed_species: frozenset[str],
    ) -> VapoRockDiagnostics:
        """Convert an adapter :class:`EquilibriumResult` into VapoRock diagnostics.

        Mirrors the MAGEMin shadow's projection.  Missing or empty
        results surface as ``backend_status='unavailable'``.

        ``allowed_species`` is retained in the signature for callers
        that share construction code with the builtin provider. VapoRock
        no longer exports a filtered authoritative pressure dict; every
        finite positive pressure stays under
        ``vaporock_full_speciation_Pa``.
        """
        if equilibrium is None:
            return VapoRockDiagnostics(
                pO2_bar=pO2_bar,
                mode=mode,
                engine_version=engine_version,
                backend_status='unavailable',
            )
        raw_vapor = dict(
            getattr(equilibrium, 'vapor_pressures_Pa', {}) or {}
        )
        raw_full_speciation = dict(
            getattr(
                equilibrium,
                'vaporock_full_speciation_Pa',
                raw_vapor,
            ) or {}
        )
        vaporock_full_speciation_Pa: dict[str, float] = {}
        for species, value in raw_full_speciation.items():
            if not _is_finite(value):
                continue
            pressure = float(value)
            if pressure > 0.0:
                vaporock_full_speciation_Pa[str(species)] = pressure
        del allowed_species
        vapor_pressures_Pa: dict[str, float] = {}
        warnings = tuple(
            str(w) for w in (getattr(equilibrium, 'warnings', ()) or ())
        )
        backend_status = str(
            getattr(equilibrium, 'status', None) or 'unavailable'
        )
        return VapoRockDiagnostics(
            vapor_pressures_Pa=vapor_pressures_Pa,
            vaporock_full_speciation_Pa=vaporock_full_speciation_Pa,
            activities={},  # VapoRock has no per-oxide activity surface
            pO2_bar=pO2_bar,
            mode=mode,
            engine_version=engine_version,
            backend_status=backend_status,
            backend_warnings=warnings,
        )

    @staticmethod
    def _build_allowed_species(
        vapor_pressure_data: Optional[Mapping[str, Any]],
    ) -> frozenset[str]:
        """Compute the allowed-vapor-species filter from the YAML payload.

        Returns ``frozenset()`` (empty / disabled filter) when the
        payload is missing or carries no ``metals`` / ``oxide_vapors``
        sections.

        Otherwise returns the active species set the builtin Antoine
        path actually emits: the intersection of the YAML ``metals``
        section with the
        :data:`engines.builtin.vapor_pressure._ELLINGHAM_THERMO` table
        (the builtin only computes a vapor pressure for metals that
        also have an Ellingham entry), plus the active YAML
        ``oxide_vapors`` section. Rows explicitly marked
        ``consumer_status: inactive`` are excluded; missing status is
        active.

        Goal #10 hard constraint binds this: the authority swap must
        not change the species set the downstream
        ``EVAPORATION_FLUX`` / ``EVAPORATION_TRANSITION`` pipeline
        sees.  VapoRock's broader ~30-species output is a richer
        chemistry surface than the simulator's downstream is wired
        for; pinning the filter to the builtin's effective output
        keeps mass balance closed at 0.000% while still letting
        VapoRock be the canonical source for the species both
        engines compute.  Future work can widen this set per-species
        as the downstream stoichiometry validators learn the new
        species.
        """
        if not vapor_pressure_data:
            return frozenset()
        # Lazy import: the builtin module re-enters this engine at
        # package-init under some test orderings (the cycle
        # documented in engines/builtin/__init__.py).  Deferring the
        # import to first-call keeps the module pair importable in
        # any order.
        from engines.builtin.vapor_pressure import _ELLINGHAM_THERMO

        def active_species(section: Mapping[str, Any]) -> frozenset[str]:
            names: set[str] = set()
            for species, row in section.items():
                status = ''
                if isinstance(row, Mapping):
                    status = str(row.get('consumer_status') or '').lower()
                if status == 'inactive':
                    continue
                names.add(str(species))
            return frozenset(names)

        metals = dict(vapor_pressure_data.get('metals', {}) or {})
        oxide_vapors = dict(vapor_pressure_data.get('oxide_vapors', {}) or {})
        ellingham_metals = frozenset(_ELLINGHAM_THERMO.keys())
        return (
            (active_species(metals) & ellingham_metals)
            | active_species(oxide_vapors)
        )

    @staticmethod
    def _mode_label(backend: Any) -> str:
        """Report which VapoRock entry point answered (best-effort)."""
        if backend is None:
            return 'unavailable'
        if getattr(backend, 'is_available', None) and bool(backend.is_available()):
            return 'vaporock'
        return 'unavailable'

    @staticmethod
    def _engine_version(backend: Any) -> str:
        if backend is None:
            return 'unavailable'
        getter = getattr(backend, 'get_engine_version', None)
        if callable(getter):
            try:
                return str(getter())
            except Exception:  # noqa: BLE001
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


__all__ = ('VapoRockProvider',)
