"""Consumer policy for alphaMELTS condensed-phase pressure evaluation."""

from __future__ import annotations

from typing import Any


# alphaMELTS is used here for silicate-melt and solid-phase speciation.  Those
# condensed phases are effectively incompressible, so d(mu)/dP = V_molar with
# V_molar about 10-40 cm^3/mol = 1e-5-4e-5 m^3/mol.  Between 1 bar and
# 1 mbar, delta P is about 1e5 Pa, hence V*delta P is about 1-4 J/mol: O(1)
# J/mol versus RT about 15 kJ/mol at 1500 C.  Gas-phase chemistry is handled
# separately by the simulator's vapor-pressure/Ellingham stack, not by
# alphaMELTS.  The subprocess transport hard-refuses sub-bar input, so its
# consumers evaluate condensed-phase speciation at the alphaMELTS 1-bar model
# reference whenever the physical overhead is sub-bar.  Ordinary ThermoEngine
# and Python-API calls retain their historical physical-pressure inputs; the
# EC/GATE intent-specific exception stays visible in the provider.  Whether
# 1e-6 bar is meaningfully inside the MELTS calibration for ThermoEngine remains
# an open physics question; see docs-private/tickler-2026-05-18.md.
# Unit check: the adapter argument is pressure_bar, so this value is in bar.
# Sanity: for subprocess this is exactly the old adapter's silent 1-bar clamp,
# made explicit at the consumer boundary, so subprocess goldens are unchanged.
ALPHAMELTS_CONDENSED_PHASE_REFERENCE_PRESSURE_BAR = 1.0


def alphamelts_condensed_phase_pressure_bar(
    physical_pressure_bar: float,
    *,
    transport: str | None,
) -> float:
    """Return the transport-specific alphaMELTS evaluation pressure."""

    pressure_bar = float(physical_pressure_bar)
    if (
        str(transport or "").strip().lower() == "subprocess"
        and pressure_bar < ALPHAMELTS_CONDENSED_PHASE_REFERENCE_PRESSURE_BAR
    ):
        return ALPHAMELTS_CONDENSED_PHASE_REFERENCE_PRESSURE_BAR
    return pressure_bar


def annotate_alphamelts_reference_pressure(
    result: Any,
    *,
    physical_pressure_bar: float,
    evaluation_pressure_bar: float,
) -> Any:
    """Record a consumer-side pressure substitution on a backend result."""

    physical = float(physical_pressure_bar)
    evaluation = float(evaluation_pressure_bar)
    if physical == evaluation:
        return result
    diagnostics = dict(getattr(result, "diagnostics", {}) or {})
    diagnostics.update(
        alphamelts_reference_pressure_diagnostics(
            physical_pressure_bar=physical,
            evaluation_pressure_bar=evaluation,
        )
    )
    result.diagnostics = diagnostics
    return result


def alphamelts_reference_pressure_diagnostics(
    *,
    physical_pressure_bar: float,
    evaluation_pressure_bar: float,
) -> dict[str, float]:
    """Return diagnostic fields for a real consumer-side substitution."""

    physical = float(physical_pressure_bar)
    evaluation = float(evaluation_pressure_bar)
    if physical == evaluation:
        return {}
    return {
        "physical_overhead_pressure_bar": physical,
        "condensed_phase_reference_pressure_bar": evaluation,
    }


__all__ = (
    "ALPHAMELTS_CONDENSED_PHASE_REFERENCE_PRESSURE_BAR",
    "alphamelts_condensed_phase_pressure_bar",
    "alphamelts_reference_pressure_diagnostics",
    "annotate_alphamelts_reference_pressure",
)
