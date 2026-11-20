"""Partial molar enthalpy on a fixed phase model.

Hbar_i = mu_i - T (d mu_i / d T) at constant pressure and mole numbers.
mu_i is the endmember chemical potential in J/mol and T is kelvin, so
T * (J/mol/K) is J/mol and Hbar_i is J/mol on the same reference as mu_i.

The derivative is a central difference on one phase object and one mole
vector. The truncation is O(dT^2): the error in dmu/dT is about
(dT)^2/6 * d³mu/dT³. For a constant-Cp component that third derivative
is Cp/T^2, so the enthalpy error is about Cp (dT)^2 / (6 T). At
Cp = 100 J/mol/K, T = 1500 K and dT = 1 K the error is about 0.01 J/mol.
That is below a kJ/mol literature uncertainty and above float64
cancellation of a 10^6 J/mol potential: the 2 K chemical-potential
difference is order Cp*dT, about 100 J/mol.

dT is PARTIAL_MOLAR_ENTHALPY_DT_K. It is a code constant, not a cache
key and not a request input. Production callers omit the dt_K argument.
The optional argument exists so the same difference can be checked at
dT/2. A phase boundary is an equilibrium change of assemblage. This
stencil does not equilibrate, so the assemblage cannot move. If the
phase object's model identity changes across the stencil, the result
is a typed refusal, not a number.

An ideal-solution component mu_i = mu_i0(T) + R T ln x_i with mu_i0
linear in T has Hbar_i = H_i0, independent of composition. The central
difference of that linear mu is exact aside from roundoff.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

from simulator.physical_constants import J_PER_KJ
from simulator.reference_data.janaf import formula_composition

# One kelvin. See the module docstring for the truncation and the
# phase-boundary argument. Not a cache dimension.
PARTIAL_MOLAR_ENTHALPY_DT_K = 1.0

# Spellings of PerBasis.MOL_SPECIES, ReferenceStateConvention
# .CONDENSED_ELEMENT_REFERENCE, and Phase.L / CR / G. The simulation
# import closure cannot import simulator.battery, so this module holds
# the strings. tests/test_partial_molar_enthalpy.py pins the produced
# tokens to those enum values.
BASIS_MOL_SPECIES = "mol_species"
REFERENCE_CONDENSED_ELEMENT = "condensed_element_reference"
PHASE_LIQUID = "l"
PHASE_CRYSTAL = "cr"
PHASE_GAS = "g"

REASON_ASSEMBLAGE_CHANGED = "partial_enthalpy_phase_assemblage_changed"
REASON_PHASE_MODEL_CHANGED = "partial_enthalpy_phase_model_changed"
REASON_NONFINITE = "partial_enthalpy_nonfinite"
REASON_COMPONENT_MISMATCH = "partial_enthalpy_component_mismatch"
REASON_PHASE_MISMATCH = "partial_enthalpy_phase_mismatch"
REASON_BASIS_MISMATCH = "partial_enthalpy_basis_mismatch"
REASON_REFERENCE_MISMATCH = "partial_enthalpy_reference_mismatch"
REASON_COMPONENT_NOT_REPORTED = "partial_enthalpy_component_not_reported"
REASON_AMBIGUOUS = "partial_enthalpy_ambiguous_component"
REASON_NO_CHEM_POTENTIAL = "partial_enthalpy_no_chem_potential"
REASON_STENCIL_FAILED = "partial_enthalpy_stencil_failed"

# [component, phase, basis, reference, value_J_per_mol or None, reason or None]
_RECORD_LENGTH = 6


class PartialMolarEnthalpyRefusal(Exception):
    """The stencil has no partial molar enthalpy. ``reason`` is the token."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason)
        super().__init__(self.reason)


def temperature_K_from_celsius(temperature_C: float) -> float:
    """Kelvin from the owned Celsius offset. Do not pass Celsius to Hbar.

    The offset is read at call time. A test that replaces
    ``CELSIUS_TO_KELVIN_OFFSET`` must change the value this derivative uses.
    """

    from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET

    return float(temperature_C) + CELSIUS_TO_KELVIN_OFFSET


def partial_molar_enthalpy_j_per_mol(
    mu: float,
    dmu_dT: float,
    temperature_K: float,
) -> float:
    """Hbar_i in J/mol. mu is J/mol, dmu/dT is J/mol/K, T is K."""

    return float(mu) - float(temperature_K) * float(dmu_dT)


def partial_molar_enthalpy_kJ_per_mol(joules: float) -> Decimal:
    """J/mol divided by J/kJ is kJ/mol."""

    return Decimal(str(joules)) / Decimal(str(J_PER_KJ))


def enthalpy_record(
    component: str,
    phase: str,
    value_J_per_mol: float | None,
    reason: str | None,
) -> list[Any]:
    """One component row. A number has no reason. A refusal has no number."""

    if (value_J_per_mol is None) == (reason is None):
        raise ValueError("partial molar enthalpy record needs a value or a reason")
    value = None if value_J_per_mol is None else float(value_J_per_mol)
    if value is not None and not math.isfinite(value):
        raise ValueError("nonfinite partial molar enthalpy value")
    return [
        str(component),
        str(phase),
        BASIS_MOL_SPECIES,
        REFERENCE_CONDENSED_ELEMENT,
        value,
        None if reason is None else str(reason),
    ]


def refusal_record(component: str, phase: str, reason: str) -> list[Any]:
    return enthalpy_record(component, phase, None, reason)


def refusal_records(
    components: Sequence[str],
    phase: str,
    reason: str,
) -> list[list[Any]]:
    return [refusal_record(component, phase, reason) for component in components]


def parse_partial_molar_enthalpy_records(
    rows: Any,
) -> tuple[tuple[Any, ...], ...]:
    """Restore compact rows. Invalid shape is a cache error, not a number."""

    if rows is None:
        return ()
    if not isinstance(rows, (list, tuple)):
        raise ValueError("partial molar enthalpy records must be a list")
    parsed: list[tuple[Any, ...]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != _RECORD_LENGTH:
            raise ValueError("invalid partial molar enthalpy record")
        component, phase, basis, reference, value, reason = row
        if not all(
            isinstance(item, str) and item
            for item in (component, phase, basis, reference)
        ):
            raise ValueError("invalid partial molar enthalpy record labels")
        if (value is None) == (reason is None):
            raise ValueError("partial molar enthalpy record needs a value or a reason")
        if value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid partial molar enthalpy value") from exc
            if not math.isfinite(value):
                raise ValueError("nonfinite partial molar enthalpy value")
        if reason is not None and not isinstance(reason, str):
            raise ValueError("invalid partial molar enthalpy reason")
        parsed.append((component, phase, basis, reference, value, reason))
    return tuple(parsed)


def compact_partial_molar_enthalpy_records(rows: Any) -> list[list[Any]] | None:
    """Lean HOT rows. Empty input is omitted so other engines' bytes stay."""

    parsed = parse_partial_molar_enthalpy_records(rows)
    if not parsed:
        return None
    return [list(row) for row in parsed]


def _mu_components(value: Any) -> list[float]:
    reshape = getattr(value, "reshape", None)
    if callable(reshape):
        value = reshape(-1)
    if isinstance(value, (str, bytes)):
        raise PartialMolarEnthalpyRefusal(REASON_NONFINITE)
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise PartialMolarEnthalpyRefusal(REASON_NONFINITE) from exc


def central_difference_partial_molar_enthalpies(
    mu_at_temperature: Callable[[float], Sequence[float]],
    phase_identity_at: Callable[[float], Any],
    components: Sequence[str],
    temperature_K: float,
    phase: str,
    dt_K: float = PARTIAL_MOLAR_ENTHALPY_DT_K,
) -> list[list[Any]]:
    """Hbar for each component. Raises when the stencil is not one model.

    ``dt_K`` defaults to the code constant. Callers that pass it are tests
    of the same difference. The cache key does not include it.
    """

    if not components:
        return []
    if (
        not math.isfinite(temperature_K)
        or temperature_K <= 0.0
        or not math.isfinite(dt_K)
        or dt_K <= 0.0
        or dt_K >= temperature_K
    ):
        raise PartialMolarEnthalpyRefusal(REASON_NONFINITE)
    low = float(temperature_K) - float(dt_K)
    high = float(temperature_K) + float(dt_K)
    identity_low = phase_identity_at(low)
    identity_mid = phase_identity_at(float(temperature_K))
    identity_high = phase_identity_at(high)
    if identity_low != identity_mid or identity_high != identity_mid:
        raise PartialMolarEnthalpyRefusal(REASON_PHASE_MODEL_CHANGED)
    mu_low = _mu_components(mu_at_temperature(low))
    mu_high = _mu_components(mu_at_temperature(high))
    mu_mid = _mu_components(mu_at_temperature(float(temperature_K)))
    if phase_identity_at(float(temperature_K)) != identity_mid:
        raise PartialMolarEnthalpyRefusal(REASON_PHASE_MODEL_CHANGED)
    expected = len(components)
    if len(mu_low) != expected or len(mu_high) != expected or len(mu_mid) != expected:
        raise PartialMolarEnthalpyRefusal(REASON_PHASE_MODEL_CHANGED)
    if not all(math.isfinite(value) for value in (*mu_low, *mu_high, *mu_mid)):
        raise PartialMolarEnthalpyRefusal(REASON_NONFINITE)
    step = 2.0 * float(dt_K)
    rows: list[list[Any]] = []
    for index, component in enumerate(components):
        dmu_dT = (mu_high[index] - mu_low[index]) / step
        rows.append(
            enthalpy_record(
                component,
                phase,
                partial_molar_enthalpy_j_per_mol(
                    mu_mid[index], dmu_dT, temperature_K
                ),
                None,
            )
        )
    return rows


def partial_molar_enthalpies_from_phase(
    phase: Any,
    *,
    temperature_K: float,
    pressure_bar: float,
    moles: Sequence[float],
    components: Sequence[str],
    phase_token: str,
    phase_name: str,
    dt_K: float = PARTIAL_MOLAR_ENTHALPY_DT_K,
) -> list[list[Any]]:
    """Hbar from ``phase.chem_potential`` at fixed moles. Does not equilibrate."""

    mole_vector = [float(value) for value in moles]

    def fingerprint() -> tuple[Any, ...]:
        names = getattr(phase, "endmember_names", ()) or ()
        return (
            type(phase).__name__,
            str(phase_name),
            tuple(str(name) for name in names),
        )

    def mu_at(temperature: float) -> list[float]:
        before = fingerprint()
        values = phase.chem_potential(
            float(temperature),
            float(pressure_bar),
            mol=[mole_vector],
        )
        if fingerprint() != before:
            raise PartialMolarEnthalpyRefusal(REASON_PHASE_MODEL_CHANGED)
        return _mu_components(values)

    def identity_at(_temperature: float) -> tuple[Any, ...]:
        return fingerprint()

    return central_difference_partial_molar_enthalpies(
        mu_at,
        identity_at,
        components,
        temperature_K,
        phase_token,
        dt_K=dt_K,
    )


def select_partial_molar_enthalpy_j_per_mol(
    records: Any,
    *,
    component: tuple[tuple[str, float], ...],
    endmember: tuple[tuple[str, float], ...],
    component_basis: tuple[tuple[str, float], ...] | None,
    phase: str,
    basis: str,
    reference: str,
) -> tuple[float | None, str]:
    """Match one component, phase, basis and reference. Never a silent number.

    Component identity is the closed element-count parse, not string equality.
    """

    if (
        not component
        or component != endmember
        or component_basis != component
    ):
        return None, REASON_COMPONENT_MISMATCH
    parsed = parse_partial_molar_enthalpy_records(records)
    component_hits = [
        row
        for row in parsed
        if formula_composition(row[0]) == component
    ]
    if not component_hits:
        return None, REASON_COMPONENT_NOT_REPORTED
    phase_hits = [row for row in component_hits if row[1] == phase]
    if not phase_hits:
        return None, REASON_PHASE_MISMATCH
    basis_hits = [row for row in phase_hits if row[2] == basis]
    if not basis_hits:
        return None, REASON_BASIS_MISMATCH
    reference_hits = [row for row in basis_hits if row[3] == reference]
    if not reference_hits:
        return None, REASON_REFERENCE_MISMATCH
    if len(reference_hits) != 1:
        return None, REASON_AMBIGUOUS
    _component, _phase, _basis, _reference, value, reason = reference_hits[0]
    if reason is not None or value is None:
        return None, reason or REASON_NONFINITE
    if not math.isfinite(float(value)):
        return None, REASON_NONFINITE
    return float(value), ""
