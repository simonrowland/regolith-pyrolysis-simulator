"""Frozen diagnostic result for the AlphaMELTS provider.

Per goal #8 checklist item 5: AlphaMELTS is **diagnostic-only**. The
provider's :meth:`dispatch` MUST return a payload that cannot become a
ledger transition -- no :class:`LedgerTransitionProposal`, no path
into :meth:`ChemistryKernel.commit_batch`. This module defines
:class:`LiquidusDiagnostics`, the canonical shape the provider attaches
to ``IntentResult.diagnostic``.

The kernel's :class:`IntentResult` carries the dispatch-level status and
control-audit; this class carries the AlphaMELTS-specific fields the
caller wants for trace + UI:

* ``liquidus_T_C``         -- liquidus temperature in C (None if not
  parsed / not available).
* ``liquidus_T_K``         -- same liquidus temperature in K for
  MAGEMin parity traces.
* ``solidus_T_C``          -- solidus temperature in C (None if not
  parsed / not available).
* ``phases_present``       -- ordered tuple of phase names reported by
  the engine.
* ``phase_modes_wt_pct``   -- modal abundance per phase (wt%), normalised
  to 100 across the reported phases.
* ``phase_masses_kg``      -- legacy ``EquilibriumResult`` phase-mass
  projection, copied through for diagnostic-only reconstruction.
* ``liquid_fraction``      -- legacy ``EquilibriumResult`` liquid fraction.
* ``liquid_composition_wt_pct`` -- liquid-phase oxide composition (wt%).
* ``activity_coefficients`` -- per-species activity coefficients the
  engine returned (None when absent).
* ``fO2_log``              -- oxygen fugacity reported by the adapter
  when available.
* ``mode``                 -- which AlphaMELTS path produced the result:
  ``'petthermotools'``, ``'subprocess'``, or ``'unavailable'``.
* ``engine_version``       -- whatever the adapter reported.
* ``backend_status``       -- ``EquilibriumResult.status`` from the
  adapter (the kernel-level status on the :class:`IntentResult` is a
  separate signal).
* ``backend_warnings``     -- non-fatal warnings the adapter surfaced.

The dataclass is frozen; the provider builds one per dispatch and
attaches a plain dict projection to ``IntentResult.diagnostic`` so the
kernel-level DTO stays a mapping (matching the kernel contract in
``simulator/chemistry/kernel/dto.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Mapping, Optional, Tuple


@dataclass(frozen=True)
class LiquidusDiagnostics:
    """Frozen diagnostic payload returned by :class:`AlphaMELTSProvider`.

    See module docstring. All fields are read-only; the provider
    constructs an instance per dispatch and projects it to a plain dict
    via :meth:`as_diagnostic` for inclusion in
    :attr:`IntentResult.diagnostic`.

    The class deliberately carries NO ledger transition / proposal
    fields. Goal #8 checklist item 5 binds this -- a
    :class:`LiquidusDiagnostics` instance must be impossible to convert
    into a :class:`LedgerTransitionProposal`. Tests
    (``test_alphamelts_provider.py::test_no_ledger_transition_import``)
    enforce this at the AST level.
    """

    liquidus_T_C: Optional[float] = None
    liquidus_T_K: Optional[float] = None
    solidus_T_C: Optional[float] = None
    phases_present: Tuple[str, ...] = ()
    phase_modes_wt_pct: Mapping[str, float] = field(default_factory=dict)
    phase_masses_kg: Mapping[str, float] = field(default_factory=dict)
    liquid_fraction: float = 1.0
    liquid_composition_wt_pct: Mapping[str, float] = field(default_factory=dict)
    activity_coefficients: Mapping[str, float] = field(default_factory=dict)
    fO2_log: Optional[float] = None
    mode: str = 'unavailable'
    engine_version: str = 'unavailable'
    backend_status: str = 'unavailable'
    backend_warnings: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Coerce mappings to plain dict so the asdict() projection drops
        # frozen-dict / MappingProxy types that would confuse downstream
        # JSON serialisation in the kernel trace.
        object.__setattr__(
            self,
            'phase_modes_wt_pct',
            {str(k): float(v) for k, v in dict(self.phase_modes_wt_pct or {}).items()},
        )
        object.__setattr__(
            self,
            'phase_masses_kg',
            {str(k): float(v) for k, v in dict(self.phase_masses_kg or {}).items()},
        )
        object.__setattr__(self, 'liquid_fraction', float(self.liquid_fraction))
        object.__setattr__(
            self,
            'liquid_composition_wt_pct',
            {
                str(k): float(v)
                for k, v in dict(self.liquid_composition_wt_pct or {}).items()
            },
        )
        object.__setattr__(
            self,
            'activity_coefficients',
            {
                str(k): float(v)
                for k, v in dict(self.activity_coefficients or {}).items()
            },
        )
        object.__setattr__(self, 'phases_present', tuple(str(p) for p in self.phases_present))
        object.__setattr__(self, 'backend_warnings', tuple(str(w) for w in self.backend_warnings))
        object.__setattr__(self, 'mode', str(self.mode))
        object.__setattr__(self, 'engine_version', str(self.engine_version))
        object.__setattr__(self, 'backend_status', str(self.backend_status))
        if self.liquidus_T_C is not None:
            object.__setattr__(self, 'liquidus_T_C', float(self.liquidus_T_C))
        if self.liquidus_T_K is not None:
            object.__setattr__(self, 'liquidus_T_K', float(self.liquidus_T_K))
        if self.liquidus_T_K is None and self.liquidus_T_C is not None:
            object.__setattr__(self, 'liquidus_T_K', self.liquidus_T_C + 273.15)
        if self.liquidus_T_C is None and self.liquidus_T_K is not None:
            object.__setattr__(self, 'liquidus_T_C', self.liquidus_T_K - 273.15)
        if self.solidus_T_C is not None:
            object.__setattr__(self, 'solidus_T_C', float(self.solidus_T_C))
        if self.fO2_log is not None:
            object.__setattr__(self, 'fO2_log', float(self.fO2_log))

    def as_diagnostic(self) -> Dict[str, Any]:
        """Plain-dict projection for the kernel's ``IntentResult.diagnostic``."""
        return asdict(self)


__all__ = ('LiquidusDiagnostics',)
