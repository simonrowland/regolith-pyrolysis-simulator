"""Projection between ``EquilibriumResult`` and ``LiquidusDiagnostics``.

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
import math
from typing import Any, Dict, Mapping, Optional, Tuple

from engines.alphamelts.result import LiquidusDiagnostics


class ParserError(RuntimeError):
    """Raised when adapter output cannot be projected safely."""

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
    fe_redox_policy: str = 'intrinsic',
    applied_fe3fet: Optional[float] = None,
    intrinsic_fO2_log: Optional[float] = None,
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
            fe_redox_policy=fe_redox_policy,
            applied_fe3fet=applied_fe3fet,
            intrinsic_fO2_log=intrinsic_fO2_log,
        )

    warnings: Tuple[str, ...] = tuple(getattr(equilibrium_result, 'warnings', ()) or ())
    backend_status = str(getattr(equilibrium_result, 'status', 'ok'))

    # 0.5.4 W6 (M3 historical-audit closure, 2026-05-28): prefer the
    # structured ``EquilibriumResult.liquidus_T_C`` field; fall back to
    # the legacy ``AlphaMELTS liquidus_C=`` warning-string regex for
    # consumers / backends that haven't migrated yet. Pre-W6 the
    # ordering was reversed because no backend exposed the field.
    liquidus_T_C = _safe_attr_float(equilibrium_result, 'liquidus_T_C')
    if liquidus_T_C is None:
        liquidus_T_C = _extract_liquidus_from_warnings(warnings)

    phases_present = tuple(
        str(p) for p in (getattr(equilibrium_result, 'phases_present', ()) or ())
    )
    phase_masses = _phase_masses_kg(equilibrium_result)
    phase_modes = _phase_modes_wt_pct(phase_masses)
    liquid_fraction = _safe_attr_float(equilibrium_result, 'liquid_fraction')
    if liquid_fraction is None and backend_status == 'ok':
        raise ParserError('liquid_fraction_missing')
    fO2_log = _safe_attr_float(equilibrium_result, 'fO2_log')
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
        liquidus_T_K=(
            liquidus_T_C + 273.15 if liquidus_T_C is not None else
            _safe_attr_float(equilibrium_result, 'liquidus_T_K')
        ),
        solidus_T_C=_safe_attr_float(equilibrium_result, 'solidus_T_C'),
        phases_present=phases_present,
        phase_modes_wt_pct=phase_modes,
        phase_masses_kg=phase_masses,
        liquid_fraction=liquid_fraction,
        liquid_composition_wt_pct=liquid_comp,
        liquid_fraction_path=_liquid_fraction_path(equilibrium_result),
        activity_coefficients=activities,
        fO2_log=fO2_log,
        fe_redox_policy=fe_redox_policy,
        applied_fe3fet=applied_fe3fet,
        intrinsic_fO2_log=intrinsic_fO2_log,
        mode=mode,
        engine_version=engine_version,
        backend_status=backend_status,
        backend_warnings=warnings,
        backend_diagnostics=dict(
            getattr(equilibrium_result, 'diagnostics', {}) or {}
        ),
    )


def diagnostics_to_equilibrium(
    diagnostics: LiquidusDiagnostics,
    request_controls: Mapping[str, Any],
) -> 'EquilibriumResult':
    """Rebuild legacy ``EquilibriumResult`` from a kernel diagnostic."""
    from simulator.melt_backend.base import (
        EquilibriumResult,
        LiquidFractionInvalidError,
        liquid_fraction_from_phase_masses,
    )

    controls = dict(request_controls or {})
    fO2_log = diagnostics.fO2_log
    if fO2_log is None:
        fO2_log = _control_float(controls, 'fO2_log', -9.0)
    status = str(diagnostics.backend_status)
    backend_diagnostics = dict(diagnostics.backend_diagnostics)
    requested_point_non_authoritative = (
        bool(backend_diagnostics.get('operating_point_clamped'))
        or backend_diagnostics.get('authoritative_for_requested_conditions')
        is False
    )
    if requested_point_non_authoritative:
        status = 'out_of_domain'
        backend_diagnostics['backend_status'] = 'out_of_domain'
        backend_diagnostics.setdefault(
            'backend_status_reason',
            'clamped_operating_point',
        )
    phase_masses_kg = dict(diagnostics.phase_masses_kg)
    liquid_fraction = (
        None if diagnostics.liquid_fraction is None
        else float(diagnostics.liquid_fraction)
    )
    if status == 'ok':
        computed = liquid_fraction_from_phase_masses(phase_masses_kg)
        if computed is None:
            raise LiquidFractionInvalidError('liquid_fraction_missing')
        if liquid_fraction is not None:
            if not math.isfinite(liquid_fraction):
                raise LiquidFractionInvalidError(
                    f'liquid_fraction_invalid: {liquid_fraction!r}'
                )
            if not math.isclose(
                liquid_fraction, computed, rel_tol=1e-6, abs_tol=1e-6
            ):
                raise LiquidFractionInvalidError(
                    'liquid_fraction_mismatch: '
                    f'supplied={liquid_fraction!r} '
                    f'phase_masses={computed!r}'
                )
        liquid_fraction = computed
    requested_temperature_C = _control_float(controls, 'temperature_C', 0.0)
    temperature_C = _control_float(
        backend_diagnostics,
        'executed_temperature_C',
        requested_temperature_C,
    )
    pressure_bar = _control_float(controls, 'pressure_bar', 0.0)
    if requested_point_non_authoritative:
        temperature_C = _control_float(
            backend_diagnostics,
            'solved_temperature_C',
            temperature_C,
        )
        pressure_bar = _control_float(
            backend_diagnostics,
            'solved_pressure_bar',
            pressure_bar,
        )
    return EquilibriumResult(
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        phases_present=list(diagnostics.phases_present),
        phase_masses_kg=phase_masses_kg,
        liquid_fraction=liquid_fraction,
        liquid_composition_wt_pct=dict(diagnostics.liquid_composition_wt_pct),
        liquid_viscosity_Pa_s=_control_float(
            backend_diagnostics,
            'liquid_viscosity_Pa_s',
            0.0,
        ) or None,
        activity_coefficients=dict(diagnostics.activity_coefficients),
        fO2_log=float(fO2_log),
        warnings=list(diagnostics.backend_warnings),
        status=status,
        diagnostics=backend_diagnostics,
        requested_temperature_C=_control_float(
            backend_diagnostics,
            'requested_temperature_C',
            requested_temperature_C,
        ),
        liquid_density_kg_m3=_control_float(
            backend_diagnostics,
            'liquid_density_kg_m3',
            0.0,
        ) or None,
    )


def _phase_masses_kg(equilibrium_result: Any) -> Dict[str, float]:
    return {
        str(phase): float(mass_kg)
        for phase, mass_kg in dict(
            getattr(equilibrium_result, 'phase_masses_kg', {}) or {}
        ).items()
        if _is_finite(mass_kg) and float(mass_kg) > 0.0
    }


def _phase_modes_wt_pct(masses_kg: Mapping[str, float]) -> Dict[str, float]:
    """Project per-phase masses (kg) onto wt% summing to 100.

    Mirrors what the legacy parity comparator
    (:class:`MAGEMinParityComparator`) treats as ``phase_modes_wt_pct``.
    Returns an empty dict if no phase-mass data is available.
    """
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


def _liquid_fraction_path(equilibrium_result: Any) -> Tuple[Dict[str, Any], ...]:
    path = []
    for point in tuple(getattr(equilibrium_result, 'liquid_fraction_path', ()) or ()):
        if isinstance(point, Mapping):
            temperature_C = point.get('temperature_C')
            if temperature_C is None:
                temperature_C = point.get('T_C', point.get('T'))
            liquid_fraction = point.get('liquid_fraction')
            composition = point.get('liquid_composition_wt_pct', {})
        else:
            temperature_C = getattr(point, 'temperature_C')
            liquid_fraction = getattr(point, 'liquid_fraction')
            composition = getattr(point, 'liquid_composition_wt_pct', {})
        try:
            path.append({
                'temperature_C': float(temperature_C),
                'liquid_fraction': float(liquid_fraction),
                'liquid_composition_wt_pct': {
                    str(k): float(v)
                    for k, v in dict(composition or {}).items()
                    if _is_finite(v)
                },
            })
        except (TypeError, ValueError):
            continue
    return tuple(path)


def _control_float(
    controls: Mapping[str, Any],
    name: str,
    default: float,
) -> float:
    value = controls.get(name, default)
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not _is_finite(result):
        return float(default)
    return result


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


__all__ = ('diagnostics_to_equilibrium', 'project_equilibrium_to_diagnostics')
