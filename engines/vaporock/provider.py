"""VapoRock kernel-authoritative VAPOR_PRESSURE provider.

Promoted under ``\\goal VAPOROCK-AUTHORITY-PROMOTION`` (#10).  The
provider wraps the :class:`simulator.melt_backend.vaporock.VapoRockBackend`
adapter (the library import + species-name normalization stay there) and
exposes it as a kernel-registered authoritative provider for
:attr:`ChemistryIntent.VAPOR_PRESSURE`.

Authority posture
-----------------
VapoRock is registered as the **authoritative** provider for
VAPOR_PRESSURE.  The original
:class:`engines.builtin.vapor_pressure.BuiltinVaporPressureProvider`
stays in the registry as the **fallback** -- consulted only when:

1. The VapoRock library is missing and the provider therefore raises
   :class:`ProviderUnavailableError` at dispatch time, AND
2. The caller (the simulator wiring layer) has set
   ``allow_fallback_vapor=True`` so the kernel's
   :attr:`ChemistryKernel.allow_fallback_intents` contains
   ``VAPOR_PRESSURE``.

Otherwise the kernel re-raises the
:class:`ProviderUnavailableError` -- silent fallback is forbidden by
the goal spec.

The intent itself is read-only / diagnostic at the kernel level (the
shape mirrors :class:`BuiltinVaporPressureProvider`):
:attr:`IntentResult.transition` is always ``None``.  The downstream
``EVAPORATION_TRANSITION`` writer consumes
``diagnostic['vapor_pressures_Pa']``.

This module MUST NOT import :class:`LedgerTransitionProposal` from
anywhere -- not even for type hints.  The
``test_writer_purity.py`` AST walk enforces this for every authoritative
provider; the same constraint applies here.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from engines.builtin._common import (
    diagnostic_control_audit,
    reject_wrong_intent,
)
from engines.vaporock.result import VapoRockDiagnostics
from simulator.chemistry.kernel.capabilities import (
    CapabilityProfile,
    ChemistryIntent,
)
from simulator.chemistry.kernel.dto import IntentRequest, IntentResult
from simulator.chemistry.kernel.errors import ProviderUnavailableError
from simulator.chemistry.kernel.provider import ChemistryProvider


_INTENTS = frozenset({ChemistryIntent.VAPOR_PRESSURE})
_DECLARED_ACCOUNT = 'process.cleaned_melt'


class VapoRockProvider(ChemistryProvider):
    """Authoritative VAPOR_PRESSURE provider backed by the VapoRock adapter.

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
            output to species that have ``parent_oxide`` metadata in
            the YAML (the universe the simulator's downstream
            ``EVAPORATION_FLUX`` step is wired to consume); without
            this filter VapoRock's broader ~30-species output (``O2``,
            ``Si2``, ``Al2O2``, etc.) would crash the downstream
            stoichiometry validator.  Goal #10 hard-constraint binds
            this: the authority swap must keep mass balance at 0.000%.
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
            # Authoritative for VAPOR_PRESSURE.  The intent itself is
            # read-only at the kernel level -- the provider returns
            # ``IntentResult.transition=None`` so no ledger write is
            # produced from this surface.  See module docstring.
            is_authoritative_for=_INTENTS,
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
        3. Builds the :class:`ControlAudit` with ``applied=requested``
           and the diagnostic note.
        4. Extracts ``process.cleaned_melt`` composition (the kernel
           filter has already restricted the view to this account).
        5. Calls the adapter's :meth:`equilibrate`.
        6. Projects the adapter's
           :class:`EquilibriumResult.vapor_pressures_Pa` into a
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
            # No silent fallback: raise ``ProviderUnavailableError``
            # and let the kernel decide (via
            # ``allow_fallback_intents``) whether the registered
            # fallback may take over.  Goal #10 binds this surface.
            last_error = getattr(backend, '_last_error', None) if backend else None
            raise ProviderUnavailableError(
                'VapoRock authoritative provider unavailable: '
                + (str(last_error) if last_error else 'upstream library not importable')
            )

        control_audit = diagnostic_control_audit(request)

        pO2_bar = self._resolve_pO2_bar(request)
        composition_mol_by_account = self._composition_from_view(request)
        # Compute total cleaned-melt kg for the fO2_log fallback in the
        # adapter call (the adapter expects an fO2_log, not a pO2_bar).
        # The simulator-level commanded pO2 is the canonical input;
        # convert to log10 only when the request lacks an explicit
        # fO2_log channel.
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
            fO2_log_resolved=self._resolve_fO2_log(request),
        )

        diagnostics = self._project_equilibrium(
            equilibrium,
            pO2_bar=pO2_bar,
            mode=self._mode_label(backend),
            engine_version=self._engine_version(backend),
            allowed_species=self._allowed_species,
        )
        # 0.5.2 Phase A3 (2026-05-27): pass through unrecognised backend
        # statuses verbatim rather than coercing them to 'ok'. The old
        # whitelist silently sanitised any non-vocabulary status (e.g.,
        # 'timeout', 'partial', 'no_data', 'failed', '' from a broken
        # adapter) into 'ok' -- defeating the core-level loud-fail gate
        # that simulator/core.py::_apply_kernel_vapor_pressures relies
        # on. Codex challenge r8 P1+P2 flagged this as a hidden silent
        # downgrade path. The core gate already accepts ANY non-'ok'
        # status with no pressures as a failure mode (post-0.5.1) so
        # passing the raw status through is safe and operator-visible.
        # The retained whitelist behaviour: empty/None status maps to
        # 'unknown' so the diagnostic carries an explicit signal.
        raw_status = diagnostics.backend_status
        if raw_status is None or str(raw_status).strip() == '':
            kernel_status = 'unknown'
        else:
            kernel_status = str(raw_status).strip().lower()

        return IntentResult(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            status=kernel_status,
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
        pO2 = (
            request.control_inputs.get('pO2_bar')
            if request.control_inputs
            else None
        )
        if pO2 is not None:
            return max(float(pO2), 1e-9)
        if request.fO2_log is not None:
            return max(10.0 ** float(request.fO2_log), 1e-9)
        return 1e-9

    @staticmethod
    def _resolve_fO2_log(request: IntentRequest) -> float:
        """Convert the request's pO2 / fO2 controls into the adapter's fO2_log."""
        if request.fO2_log is not None:
            return float(request.fO2_log)
        controls = request.control_inputs or {}
        pO2 = controls.get('pO2_bar') if controls else None
        if pO2 is not None:
            import math
            value = max(float(pO2), 1e-30)
            return float(math.log10(value))
        return -9.0

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

        ``allowed_species`` filters the VapoRock vapor-pressure dict
        to the universe ``data/vapor_pressures.yaml`` declares (the
        simulator's downstream ``EVAPORATION_FLUX`` step indexes the
        Antoine + Hertz-Knudsen tables on these species; emitting a
        broader set crashes the per-species ``parent_oxide``
        validator).  An empty ``allowed_species`` set disables the
        filter -- used by adapter-level tests that operate below the
        simulator.
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
        vapor_pressures_Pa: dict[str, float] = {}
        # Mirror the builtin Antoine path's vanishing-pressure floor
        # (``P_effective_Pa > 1e-15``): species at sub-femtopascal
        # pressures are below the per-transition mass-balance
        # tolerance and only generate numerical noise downstream
        # (the EVAPORATION_FLUX -> EVAPORATION_TRANSITION pipeline
        # uses provider ``atom_balance_proof`` checks that fail when
        # the projected mass loss is below the IEEE-754 floor for
        # the per-species kg conversion).  Filtering at the
        # vapor-pressure surface keeps the downstream conservation
        # numbers tidy and matches the builtin's behaviour for the
        # tail-end species.
        _VAPOR_PRESSURE_FLOOR_PA = 1e-15
        for species, value in raw_vapor.items():
            if not _is_finite(value):
                continue
            pressure = float(value)
            if pressure <= _VAPOR_PRESSURE_FLOOR_PA:
                continue
            name = str(species)
            if allowed_species and name not in allowed_species:
                continue
            vapor_pressures_Pa[name] = pressure
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

        Otherwise returns the species set the builtin Antoine path
        actually emits: the intersection of the YAML ``metals``
        section with the
        :data:`engines.builtin.vapor_pressure._ELLINGHAM_THERMO` table
        (the builtin only computes a vapor pressure for metals that
        also have an Ellingham entry -- Si is the canonical example
        of a YAML metal the builtin filters out for lack of an
        oxide-decomposition coupling), plus the entire YAML
        ``oxide_vapors`` section.

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

        metals = dict(vapor_pressure_data.get('metals', {}) or {})
        oxide_vapors = dict(vapor_pressure_data.get('oxide_vapors', {}) or {})
        ellingham_metals = frozenset(_ELLINGHAM_THERMO.keys())
        return (
            (frozenset(metals.keys()) & ellingham_metals)
            | frozenset(oxide_vapors.keys())
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
