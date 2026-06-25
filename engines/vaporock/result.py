"""Frozen diagnostic result for the VapoRock provider.

The :class:`VapoRockProvider` is registered as a diagnostic shadow for
the ``VAPOR_PRESSURE`` intent. It owns no ledger transition and does not
provide the authoritative pressure dict consumed by evaporation. The
provider returns an :class:`IntentResult` with ``transition=None`` and
attaches an instance of this class (projected to a plain dict) on
:attr:`IntentResult.diagnostic`.

Schema:

* ``vapor_pressures_Pa`` -- always empty. The authoritative pressure surface
  for evaporation is builtin Antoine/Ellingham.
* ``vaporock_full_speciation_Pa`` -- unfiltered VapoRock gas speciation
  map for diagnostics, benchmarks, and cross-engine analysis only.
* ``activities`` -- ``species -> activity`` map (matches the
  Builtin-side ``a_oxide`` proxy; left empty for VapoRock which has
  no per-oxide activity surface).
* ``pO2_bar`` -- commanded oxygen partial pressure echoed back for
  trace.
* ``mode`` -- which VapoRock entry point produced the result
  (``'system_eval_gas_abundances'``, ``'library_function'``,
  ``'unavailable'``).
* ``engine_version`` -- whatever the adapter reported (best-effort).
* ``backend_status`` -- ``EquilibriumResult.status`` from the adapter.
* ``backend_warnings`` -- non-fatal warnings the adapter surfaced.

This module MUST NOT import :class:`LedgerTransitionProposal` -- the
writer-purity invariant test enforces this at the AST level.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Mapping, Tuple


@dataclass(frozen=True)
class VapoRockDiagnostics:
    """Frozen diagnostic payload returned by :class:`VapoRockProvider`."""

    vapor_pressures_Pa: Mapping[str, float] = field(default_factory=dict)
    vaporock_full_speciation_Pa: Mapping[str, float] = field(
        default_factory=dict
    )
    activities: Mapping[str, float] = field(default_factory=dict)
    pO2_bar: float = 0.0
    mode: str = 'unavailable'
    engine_version: str = 'unavailable'
    backend_status: str = 'unavailable'
    backend_warnings: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            'vapor_pressures_Pa',
            {},
        )
        object.__setattr__(
            self,
            'activities',
            {
                str(k): float(v)
                for k, v in dict(self.activities or {}).items()
            },
        )
        object.__setattr__(
            self,
            'vaporock_full_speciation_Pa',
            {
                str(k): float(v)
                for k, v in dict(
                    self.vaporock_full_speciation_Pa or {}
                ).items()
            },
        )
        object.__setattr__(self, 'pO2_bar', float(self.pO2_bar))
        object.__setattr__(self, 'mode', str(self.mode))
        object.__setattr__(self, 'engine_version', str(self.engine_version))
        object.__setattr__(self, 'backend_status', str(self.backend_status))
        object.__setattr__(
            self,
            'backend_warnings',
            tuple(str(w) for w in self.backend_warnings),
        )

    def as_diagnostic(self) -> Dict[str, Any]:
        """Plain-dict projection for ``IntentResult.diagnostic``."""

        return asdict(self)


__all__ = ('VapoRockDiagnostics',)
