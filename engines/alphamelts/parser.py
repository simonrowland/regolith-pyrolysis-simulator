"""Projection from :class:`EquilibriumResult` to :class:`LiquidusDiagnostics`.

The today-hook adapter (:mod:`simulator.melt_backend.alphamelts`)
returns the simulator's legacy :class:`EquilibriumResult` shape; the
kernel-side provider returns a kernel-native diagnostic. This module
owns the shape projection so the provider's :meth:`dispatch` stays a
thin orchestrator.

Goal #8 checklist item 5 binds the provider to "no
``LedgerTransitionProposal``" -- this projection never reads
``EquilibriumResult.ledger_transition``. If the adapter ever attempts
to attach one (it should not for SILICATE_LIQUIDUS / SILICATE_EQUILIBRIUM
calls), the field is silently dropped; the writer-purity invariant
test at ``tests/chemistry/test_writer_purity.py`` is the longstanding
catch-all that ensures the adapter cannot smuggle the value past the
kernel writer gate, but this module's contract is "diagnostic out;
ledger writes never".
"""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional, Tuple

from engines.alphamelts.result import LiquidusDiagnostics

# Liquidus marker the subprocess stdout uses; mirrors
# ``AlphaMELTSBackend._parse_liquidus_C`` so the provider extracts the
# same number the adapter's warning would have surfaced.
_LIQUIDUS_C_WARNING_RE = re.compile(
    r'AlphaMELTS liquidus_C=\s*([0-9.+\-Ee]+)'
)


def project_equilibrium_to_diagnostics(
    equilibrium_result: Any,
    *,
    mode: str,
    engine_version: str,
) -> LiquidusDiagnostics:
    """Convert an :class:`EquilibriumResult` into :class:`LiquidusDiagnostics`.

    Parameters
    ----------
    equilibrium_result:
        The adapter's return value -- a
        :class:`simulator.melt_backend.base.EquilibriumResult`. Treated
        as duck-typed so this module does not need a hard import on
        the adapter package.
    mode:
        Which path produced the result -- ``'petthermotools'``,
        ``'subprocess'``, or ``'unavailable'``. Recorded on the
        diagnostic for trace.
    engine_version:
        Whatever the adapter's ``get_engine_version()`` returned.

    Returns
    -------
    A frozen :class:`LiquidusDiagnostics`. Empty / sentinel fields are
    fine -- the kernel just records the payload on
    ``IntentResult.diagnostic`` for trace + UI.
    """
    if equilibrium_result is None:
        return LiquidusDiagnostics(
            mode=mode,
            engine_version=engine_version,
            backend_status='unavailable',
        )

    warnings: Tuple[str, ...] = tuple(getattr(equilibrium_result, 'warnings', ()) or ())

    liquidus_T_C = _extract_liquidus_from_warnings(warnings)
    if liquidus_T_C is None:
        # The PetThermoTools / subprocess paths may surface liquidus
        # via different attributes in the future; the today-hook adapter
        # writes it as a warning string, so the warning scan is the
        # canonical source until the adapter grows a structured field.
        liquidus_T_C = _safe_attr_float(equilibrium_result, 'liquidus_T_C')

    phases_present = tuple(
        str(p) for p in (getattr(equilibrium_result, 'phases_present', ()) or ())
    )
    phase_modes = _phase_modes_wt_pct(equilibrium_result)
    liquid_comp = {
        str(k): float(v)
        for k, v in dict(
            getattr(equilibrium_result, 'liquid_composition_wt_pct', {}) or {}
        ).items()
        if _is_finite(v)
    }
    activities = {
        str(k): float(v)
        for k, v in dict(
            getattr(equilibrium_result, 'activity_coefficients', {}) or {}
        ).items()
        if _is_finite(v)
    }

    return LiquidusDiagnostics(
        liquidus_T_C=liquidus_T_C,
        phases_present=phases_present,
        phase_modes_wt_pct=phase_modes,
        liquid_composition_wt_pct=liquid_comp,
        activity_coefficients=activities,
        mode=mode,
        engine_version=engine_version,
        backend_status=str(getattr(equilibrium_result, 'status', 'ok')),
        backend_warnings=warnings,
    )


def _phase_modes_wt_pct(equilibrium_result: Any) -> Dict[str, float]:
    """Project per-phase masses (kg) onto wt% summing to 100.

    Mirrors what the legacy parity comparator
    (:class:`MAGEMinParityComparator`) treats as ``phase_modes_wt_pct``.
    Returns an empty dict if no phase-mass data is available.
    """
    masses_kg = dict(getattr(equilibrium_result, 'phase_masses_kg', {}) or {})
    total = sum(
        float(m) for m in masses_kg.values()
        if _is_finite(m) and float(m) > 0.0
    )
    if total <= 0.0:
        return {}
    return {
        str(phase): float(mass_kg) / total * 100.0
        for phase, mass_kg in masses_kg.items()
        if _is_finite(mass_kg) and float(mass_kg) > 0.0
    }


def _extract_liquidus_from_warnings(warnings: Tuple[str, ...]) -> Optional[float]:
    for warning in warnings:
        match = _LIQUIDUS_C_WARNING_RE.search(str(warning))
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


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


__all__ = ('project_equilibrium_to_diagnostics',)
