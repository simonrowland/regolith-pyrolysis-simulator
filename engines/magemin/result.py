"""Frozen shadow-diagnostic result for the MAGEMin provider.

The MAGEMin provider is **shadow-only** for ``SILICATE_LIQUIDUS`` and
``SILICATE_EQUILIBRIUM`` (goal #9 ``MAGEMIN-SHADOW-PARITY``). Its
:meth:`dispatch` MUST return a payload that cannot become a ledger
transition -- no :class:`LedgerTransitionProposal`, no path into
:meth:`ChemistryKernel.commit_batch`. This module defines
:class:`MAGEMinShadowDiagnostics`, the canonical shape the provider
attaches to ``IntentResult.diagnostic``.

The dataclass is frozen; the provider builds one per dispatch and
attaches a plain dict projection to ``IntentResult.diagnostic`` so the
kernel-level DTO stays a mapping (matching the kernel contract in
``simulator/chemistry/kernel/dto.py``).

This module deliberately mirrors :class:`engines.alphamelts.result.
LiquidusDiagnostics` so :class:`engines.magemin.parity.MAGEMinParityComparator`
    can compare the two via the same ``liquidus_T_K`` / ``phase_modes_wt_pct``
    keys without any reshape glue.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Mapping, Optional, Tuple


@dataclass(frozen=True)
class MAGEMinShadowDiagnostics:
    """Frozen shadow-diagnostic payload returned by :class:`MAGEMinShadowProvider`.

    See module docstring. All fields are read-only; the provider
    constructs an instance per dispatch and projects it to a plain dict
    via :meth:`as_diagnostic` for inclusion in
    :attr:`IntentResult.diagnostic`.

    The class deliberately carries NO ledger transition / proposal
    fields. Goal #9 binds this -- a :class:`MAGEMinShadowDiagnostics`
    instance must be impossible to convert into a
    :class:`LedgerTransitionProposal`. The provider module is
    additionally subject to the writer-purity AST test that forbids
    any import of ``LedgerTransitionProposal``.

    ``liquidus_T_K`` is in Kelvin to match the binding-spec parity
    tolerance (``|T_liquidus_authoritative - T_liquidus_shadow| <= 50 K``).
    A ``liquidus_T_C`` convenience field is also written so trace
    consumers that prefer Celsius do not need to convert.
    """

    liquidus_T_K: Optional[float] = None
    liquidus_T_C: Optional[float] = None
    solidus_T_C: Optional[float] = None
    phases_present: Tuple[str, ...] = ()
    phase_modes_wt_pct: Mapping[str, float] = field(default_factory=dict)
    liquid_composition_wt_pct: Mapping[str, float] = field(default_factory=dict)
    phase_masses_kg: Mapping[str, float] = field(default_factory=dict)
    liquid_fraction: Optional[float] = None
    mode: str = 'unavailable'
    engine_version: str = 'unavailable'
    backend_status: str = 'unavailable'
    backend_warnings: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            'phase_modes_wt_pct',
            {str(k): float(v) for k, v in dict(self.phase_modes_wt_pct or {}).items()},
        )
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
            'phase_masses_kg',
            {str(k): float(v) for k, v in dict(self.phase_masses_kg or {}).items()},
        )
        object.__setattr__(
            self,
            'phases_present',
            tuple(str(p) for p in self.phases_present),
        )
        object.__setattr__(
            self,
            'backend_warnings',
            tuple(str(w) for w in self.backend_warnings),
        )
        object.__setattr__(self, 'mode', str(self.mode))
        object.__setattr__(self, 'engine_version', str(self.engine_version))
        object.__setattr__(self, 'backend_status', str(self.backend_status))
        if self.liquidus_T_K is not None:
            object.__setattr__(self, 'liquidus_T_K', float(self.liquidus_T_K))
        if self.liquidus_T_C is not None:
            object.__setattr__(self, 'liquidus_T_C', float(self.liquidus_T_C))
        if self.solidus_T_C is not None:
            object.__setattr__(self, 'solidus_T_C', float(self.solidus_T_C))
        if self.liquid_fraction is not None:
            object.__setattr__(self, 'liquid_fraction', float(self.liquid_fraction))

    def as_diagnostic(self) -> Dict[str, Any]:
        """Plain-dict projection for the kernel's ``IntentResult.diagnostic``."""
        return asdict(self)


__all__ = ('MAGEMinShadowDiagnostics',)
