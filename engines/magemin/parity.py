"""MAGEMin shadow-vs-authoritative parity comparator.

The kernel runs MAGEMin as a shadow alongside the authoritative
``SILICATE_LIQUIDUS`` / ``SILICATE_EQUILIBRIUM`` engine (alphaMELTS today;
possibly FactSAGE post-license). The shadow result NEVER becomes a
``LedgerTransition`` — its only job is to flag disagreement so an
operator can investigate fO2 sensitivity, solid-solution differences, or
basis-projection bugs.

Tolerance per
``docs-private/chemistry-engine-binding-spec-2026-05-14.md`` §4 (MAGEMin):

    |T_liquidus_authoritative - T_liquidus_shadow| <= 50 K
    |mode_pct per phase| <= 2 wt%

Disagreement above tolerance sets ``ParityReport.agreement = False`` and
appends a warning. The comparator **never** raises and **never** silently
averages — silent averaging is explicitly forbidden by §7 of the binding
spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional, Tuple

# Tolerances match the binding spec §4 MAGEMin entry.
LIQUIDUS_TOLERANCE_K = 50.0
MODAL_TOLERANCE_WT_PCT = 2.0


@dataclass
class ParityReport:
    """Result of a single shadow-vs-authoritative parity comparison.

    Fields
    ------
    liquidus_T_delta_K:
        Authoritative T_liquidus minus shadow T_liquidus, in Kelvin.
        ``None`` if either side did not report a liquidus.
    mode_pct_max_delta:
        Maximum absolute modal-abundance disagreement across all phases
        present in either side (wt%). ``None`` if neither side reports
        modal data.
    phases_only_in_authoritative:
        Sorted tuple of phase names that the authoritative engine
        reports but the shadow does not.
    phases_only_in_shadow:
        Sorted tuple of phase names that the shadow engine reports but
        the authoritative does not.
    agreement:
        ``True`` iff every metric is within tolerance and neither side
        has phases the other is missing above the modal-tolerance
        threshold.
    warnings:
        Human-readable reasons for any disagreement. Empty when
        ``agreement`` is True.
    """

    liquidus_T_delta_K: Optional[float] = None
    mode_pct_max_delta: Optional[float] = None
    phases_only_in_authoritative: Tuple[str, ...] = field(default_factory=tuple)
    phases_only_in_shadow: Tuple[str, ...] = field(default_factory=tuple)
    agreement: bool = True
    warnings: List[str] = field(default_factory=list)


class MAGEMinParityComparator:
    """Compare an authoritative engine result against a MAGEMin shadow.

    The comparator is intentionally tolerant of input shape: it accepts
    either an ``EquilibriumResult`` (today-hook path) or a plain mapping
    with the documented keys (future kernel ``IntentResult`` path). It
    looks up ``liquidus_T_K``, ``phase_modes_wt_pct`` (or
    ``phase_masses_kg`` which it converts to modal wt%), and
    ``phases_present``.
    """

    liquidus_tolerance_K: float = LIQUIDUS_TOLERANCE_K
    modal_tolerance_wt_pct: float = MODAL_TOLERANCE_WT_PCT

    def compare(
        self,
        authoritative_result: Any,
        shadow_result: Any,
    ) -> ParityReport:
        """Build a :class:`ParityReport` for the two results.

        Never raises. On unparseable input the report carries a warning
        and ``agreement=False``.
        """
        report = ParityReport()

        auth_liquidus_K = _extract_liquidus_K(authoritative_result)
        shadow_liquidus_K = _extract_liquidus_K(shadow_result)
        if auth_liquidus_K is not None and shadow_liquidus_K is not None:
            delta = auth_liquidus_K - shadow_liquidus_K
            report.liquidus_T_delta_K = delta
            if abs(delta) > self.liquidus_tolerance_K:
                report.agreement = False
                report.warnings.append(
                    f'MAGEMin parity: liquidus delta = {delta:+.1f} K '
                    f'exceeds tolerance ±{self.liquidus_tolerance_K:.0f} K. '
                    'Likely fO2-sensitivity or solid-solution model difference.'
                )

        auth_modes = _extract_phase_modes_wt_pct(authoritative_result)
        shadow_modes = _extract_phase_modes_wt_pct(shadow_result)

        if auth_modes or shadow_modes:
            all_phases = set(auth_modes) | set(shadow_modes)
            max_delta = 0.0
            per_phase_deltas: List[Tuple[str, float]] = []
            for phase in all_phases:
                a = auth_modes.get(phase, 0.0)
                s = shadow_modes.get(phase, 0.0)
                delta = abs(a - s)
                per_phase_deltas.append((phase, delta))
                if delta > max_delta:
                    max_delta = delta
            report.mode_pct_max_delta = max_delta
            offending = sorted(
                (phase, delta)
                for phase, delta in per_phase_deltas
                if delta > self.modal_tolerance_wt_pct
            )
            if offending:
                report.agreement = False
                report.warnings.append(
                    'MAGEMin parity: modal disagreement above '
                    f'±{self.modal_tolerance_wt_pct:.1f} wt% on '
                    f'{[f"{p}={d:.2f}" for p, d in offending]}.'
                )

            report.phases_only_in_authoritative = tuple(sorted(
                phase
                for phase in auth_modes
                if phase not in shadow_modes and auth_modes[phase] > 0.0
            ))
            report.phases_only_in_shadow = tuple(sorted(
                phase
                for phase in shadow_modes
                if phase not in auth_modes and shadow_modes[phase] > 0.0
            ))
            for missing in report.phases_only_in_authoritative:
                if auth_modes[missing] > self.modal_tolerance_wt_pct:
                    report.agreement = False
                    report.warnings.append(
                        f'MAGEMin parity: phase {missing!r} present in '
                        'authoritative but absent from shadow '
                        f'({auth_modes[missing]:.2f} wt%).'
                    )
            for missing in report.phases_only_in_shadow:
                if shadow_modes[missing] > self.modal_tolerance_wt_pct:
                    report.agreement = False
                    report.warnings.append(
                        f'MAGEMin parity: phase {missing!r} present in '
                        f'shadow but absent from authoritative '
                        f'({shadow_modes[missing]:.2f} wt%).'
                    )

        if (auth_liquidus_K is None and shadow_liquidus_K is None
                and not auth_modes and not shadow_modes):
            report.agreement = False
            report.warnings.append(
                'MAGEMin parity: neither side reported liquidus T or '
                'phase modes; cannot evaluate parity.'
            )

        return report


def _extract_liquidus_K(result: Any) -> Optional[float]:
    """Pull a liquidus temperature (Kelvin) from a result-shaped object.

    Only explicit ``liquidus_*`` fields are accepted.  The equilibration
    temperature (``temperature_C`` on an ``EquilibriumResult``) is
    deliberately NOT a fallback: it is the temperature the melt was
    *equilibrated at*, not its liquidus.  Treating it as a liquidus would
    make two results equilibrated at the same T report
    ``liquidus_T_delta_K = 0`` / ``agreement = True`` — a silent false
    positive.  When neither side reports a real liquidus, returning
    ``None`` routes the comparator into its conservative "cannot evaluate
    parity" branch (``agreement = False``).
    """
    if result is None:
        return None

    # Try common attribute / dict keys.  Liquidus-only — see docstring.
    candidates_K = ('liquidus_T_K', 'T_liquidus_K', 'liquidus_temperature_K')
    candidates_C = ('liquidus_T_C', 'T_liquidus_C', 'liquidus_temperature_C')

    for key in candidates_K:
        value = _lookup(result, key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue

    for key in candidates_C:
        value = _lookup(result, key)
        if value is not None:
            try:
                return float(value) + 273.15
            except (TypeError, ValueError):
                continue

    return None


def _extract_phase_modes_wt_pct(result: Any) -> Mapping[str, float]:
    """Pull a {phase: wt%} mapping. Converts phase_masses_kg if needed."""
    if result is None:
        return {}

    modes = _lookup(result, 'phase_modes_wt_pct') or _lookup(
        result, 'phase_modes_pct')
    if isinstance(modes, Mapping) and modes:
        return {
            str(name): float(value)
            for name, value in modes.items()
            if _is_finite(value)
        }

    masses = _lookup(result, 'phase_masses_kg')
    if isinstance(masses, Mapping) and masses:
        total = sum(
            float(value)
            for value in masses.values()
            if _is_finite(value)
        )
        if total > 0:
            return {
                str(name): float(value) / total * 100.0
                for name, value in masses.items()
                if _is_finite(value) and float(value) > 0.0
            }

    return {}


def _lookup(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _is_finite(value: object) -> bool:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return numeric == numeric and numeric not in (float('inf'), float('-inf'))
