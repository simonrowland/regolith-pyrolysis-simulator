"""Numeric residual transformation.

Owns metric selection and converts a candidate and a reference into a typed
``ResidualNumeric``. It does not select observations, build engine requests,
dispatch predictions, construct scoring refusals, or render reports.

Imports stay on records, enums, and identity. Callers that still import these
names from ``score`` receive the same objects through that facade.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Mapping

from simulator.battery.enums import (
    QUANTITY_UNITS,
    MetricOperation,
    MethodToken,
    PRINTED_CAPACITY_QUANTITIES,
    Quantity,
    Rail,
    RefusalReason,
    ResidualStatus,
    SourceRelation,
    ValenceValueForm,
    effective_phase_basis,
)
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.records import (
    DecisionBand,
    Experiment,
    Observation,
    ResidualNumeric,
    Uncertainty,
)


# Quantity → metric operation.
# Premise: SCHEMA-PROPOSAL-v2.1 metrics {absolute, relative, dex}.
# Algebra: absolute = C−R in canonical unit; relative = (C−R)/|R| (R≠0);
# dex = log10(C/R) with both positive.
# Unit check: absolute carries QUANTITY_UNITS; relative and dex are
# dimensionless. Sanity: equal endpoints → 0 on every operation.
QUANTITY_METRIC: dict[Quantity, MetricOperation] = {
    Quantity.P_SAT: MetricOperation.DEX,
    Quantity.P_PARTIAL: MetricOperation.DEX,
    Quantity.P_REFERENCE: MetricOperation.DEX,
    Quantity.ACTIVITY: MetricOperation.DEX,
    Quantity.ACTIVITY_COEFFICIENT: MetricOperation.DEX,
    Quantity.DELTA_FG: MetricOperation.ABSOLUTE,
    Quantity.ENTHALPY_OF_FUSION: MetricOperation.ABSOLUTE,
    Quantity.ENTROPY_OF_FUSION: MetricOperation.ABSOLUTE,
    Quantity.H_MINUS_H298: MetricOperation.ABSOLUTE,
    Quantity.PARTIAL_MOLAR_ENTHALPY: MetricOperation.ABSOLUTE,
    Quantity.ENTHALPY_OF_VAPORIZATION_2ND_LAW: MetricOperation.ABSOLUTE,
    Quantity.ENTHALPY_OF_VAPORIZATION_3RD_LAW: MetricOperation.ABSOLUTE,
    Quantity.CP: MetricOperation.ABSOLUTE,
    Quantity.S: MetricOperation.ABSOLUTE,
    Quantity.LOG10_KF: MetricOperation.ABSOLUTE,
    Quantity.LOG10_K_STAR: MetricOperation.ABSOLUTE,
    Quantity.EVAPORATION_COEFFICIENT_ALPHA: MetricOperation.ABSOLUTE,
    Quantity.MASS_LOSS_FRACTION: MetricOperation.ABSOLUTE,
    Quantity.MASS_LOSS_FRACTION_VS_T: MetricOperation.RELATIVE,
    Quantity.YIELD_FRACTION: MetricOperation.RELATIVE,
    Quantity.O2_YIELD: MetricOperation.RELATIVE,
    Quantity.EVAPORATION_RATE: MetricOperation.RELATIVE,
    Quantity.MASS_LOSS_RATE: MetricOperation.RELATIVE,
    Quantity.WALL_DEPOSIT_MASS: MetricOperation.RELATIVE,
    Quantity.WALL_DEPOSIT_AREAL_RATE: MetricOperation.RELATIVE,
    Quantity.TRANSITION_TEMPERATURE: MetricOperation.ABSOLUTE,
    Quantity.FE3_FE2_RATIO: MetricOperation.DEX,
    Quantity.VISCOSITY: MetricOperation.DEX,
    Quantity.DENSITY: MetricOperation.RELATIVE,
    Quantity.ELECTRICAL_CONDUCTIVITY: MetricOperation.DEX,
    Quantity.ION_INTENSITY_RATIO: MetricOperation.DEX,
    Quantity.ION_INTENSITY: MetricOperation.RELATIVE,
    Quantity.INTERACTION_PARAMETER: MetricOperation.ABSOLUTE,
    Quantity.CONDENSATE_COMPOSITION: MetricOperation.ABSOLUTE,
    Quantity.LIQUIDUS_COMPOSITION: MetricOperation.ABSOLUTE,
    Quantity.EVOLVED_GAS_YIELD: MetricOperation.RELATIVE,
    Quantity.ISOTOPE_DELTA: MetricOperation.ABSOLUTE,
    Quantity.RESIDUE_COMPONENT_COMPOSITION: MetricOperation.ABSOLUTE,
    Quantity.ELEMENT_RETENTION_RATIO: MetricOperation.DEX,
    # Equilibrium ratios (not a closed phase basis) are absolute. Printed
    # component amounts and L_X are dex; metric_operation_for_identity
    # returns dex when the subtype is a PHASE_BASIS_TOKENS value.
    # Printed log10 C_S / log10 C_Cl are already logs and are often negative,
    # so they take absolute, the same shape as log10_Kf.
    Quantity.EQUILIBRIUM_PHASE_COMPOSITION: MetricOperation.ABSOLUTE,
    Quantity.DISTRIBUTION_RATIO: MetricOperation.DEX,
    Quantity.SULFIDE_CAPACITY: MetricOperation.ABSOLUTE,
    Quantity.CHLORIDE_CAPACITY: MetricOperation.ABSOLUTE,
}

# Physical dimension of a stored unit. Unknown units match nothing.
_UNIT_DIMENSION: dict[str, str] = {
    "kJ_per_declared_mol_basis": "energy_per_mol",
    "J_per_declared_mol_basis_per_K": "energy_per_mol_per_K",
    "dimensionless": "dimensionless",
}


def metric_operation(quantity: Quantity | None) -> MetricOperation | None:
    if quantity is None:
        return None
    return QUANTITY_METRIC.get(quantity)


def metric_operation_for_identity(identity: Identity) -> MetricOperation | None:
    """Select an identity-aware metric when the quantity's form requires it."""
    quantity = quantity_token(identity)
    if quantity is Quantity.VALENCE_RATIO:
        basis = identity.valence_basis
        if basis is None or not basis.is_value:
            return None
        if basis.value.value_form in {
            ValenceValueForm.FRACTION,
            ValenceValueForm.RATIO,
        }:
            return MetricOperation.DEX
        if basis.value.value_form in {
            ValenceValueForm.LOG10_RATIO,
            ValenceValueForm.AVERAGE_OXIDATION_STATE,
        }:
            return MetricOperation.ABSOLUTE
        return None
    if quantity in PRINTED_CAPACITY_QUANTITIES:
        return MetricOperation.ABSOLUTE
    if quantity is Quantity.DISTRIBUTION_RATIO:
        return MetricOperation.DEX
    if quantity is Quantity.EQUILIBRIUM_PHASE_COMPOSITION:
        subtype = (
            identity.subtype.value
            if identity.subtype is not None and identity.subtype.is_value
            else None
        )
        # Prose wt% and mole-percent synonyms are the same dex observable
        # as a token phase row. The stored subtype is not rewritten.
        if effective_phase_basis(subtype) is not None:
            return MetricOperation.DEX
        return MetricOperation.ABSOLUTE
    if quantity is not Quantity.RESIDUE_COMPONENT_COMPOSITION:
        return metric_operation(quantity)
    subtype = identity.subtype.value if identity.subtype.is_value else None
    if subtype == "element_ppm_by_mass":
        return MetricOperation.DEX
    if subtype == "oxide_wt_percent":
        return MetricOperation.ABSOLUTE
    return metric_operation(quantity)


def _phase_component_reference_wt_percent(
    identity: Identity, reference: Decimal
) -> Decimal:
    """Convert a printed mass-ppm phase amount for comparison with wt% engines."""
    quantity = quantity_token(identity)
    subtype = (
        identity.subtype.value
        if identity.subtype is not None and identity.subtype.is_value
        else None
    )
    if (
        quantity is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        and subtype == "element_ppm_by_mass"
    ):
        # 1 ppm = 1e-6 mass fraction = 1e-4 wt%; 250 ppm = 0.025 wt%.
        return reference / Decimal("10000")
    return reference


def _residual_metric_operation(
    quantity: Quantity | None, observation: object | None
) -> MetricOperation | None:
    """Metric of a residual on ``observation``: identity-aware when typed."""

    identity = getattr(observation, "identity", None)
    if isinstance(identity, Identity) and quantity_token(identity) is quantity:
        return metric_operation_for_identity(identity)
    return metric_operation(quantity)


def compute_metric(
    operation: MetricOperation,
    candidate: Decimal,
    reference: Decimal,
) -> Decimal | None:
    """Return the metric or None when the domain is invalid.

    Premise: SCHEMA-PROPOSAL-v2.1 metrics block.
    Algebra:
      absolute = C − R
      relative = (C − R) / |R|  requiring R ≠ 0
      dex = log10(C / R) requiring C > 0 and R > 0
    Unit check: absolute inherits the quantity unit; relative and dex are
    dimensionless. Sanity: C = R → 0; R = 0 forbids relative (never price
    a zero reference as a successful relative residual); non-positive
    forbids dex (never take log10 of zero).
    """

    if not candidate.is_finite() or not reference.is_finite():
        return None
    if operation is MetricOperation.ABSOLUTE:
        return candidate - reference
    if operation is MetricOperation.RELATIVE:
        if reference == 0:
            return None
        return (candidate - reference) / abs(reference)
    if operation is MetricOperation.DEX:
        if candidate <= 0 or reference <= 0:
            return None
        ratio = candidate / reference
        # Decimal.ln / ln(10); math.log10 on float is the same operation
        # for the reported grain. Keep Decimal via log10 of the ratio.
        return Decimal(str(math.log10(float(ratio))))
    return None


def _reference_from_metric(
    operation: MetricOperation,
    candidate: Decimal,
    residual: Decimal,
) -> Decimal | None:
    """Recover the reference a scored residual was measured against.

    Premise: invert the SCHEMA metric. The candidate is the predicted value.
    Algebra:
      absolute: R = C − residual, because residual = C − R.
      relative: residual = (C − R) / |R|.
        Positive root first: R = C / (1 + residual) when that R is > 0.
        Otherwise R = C / (1 − residual) when that R is < 0.
        A residual that fits both signs returns the positive root
        (C = 1, residual = 1.5 fits R = 0.4 and R = −2; the result is 0.4).
      dex: residual = log10(C / R), so R = C / 10^residual, requiring C > 0.
    Unit check: R is in the candidate's unit. Sanity: residual 0 returns
    R = C on every operation; relative uses the sign of C.
    """

    if not candidate.is_finite() or not residual.is_finite():
        return None
    if operation is MetricOperation.ABSOLUTE:
        return candidate - residual
    if operation is MetricOperation.RELATIVE:
        for denominator, positive_reference in (
            (Decimal(1) + residual, True),
            (Decimal(1) - residual, False),
        ):
            if denominator == 0:
                continue
            reference = candidate / denominator
            if (positive_reference and reference > 0) or (
                not positive_reference and reference < 0
            ):
                return reference
        return None
    if operation is MetricOperation.DEX:
        if candidate <= 0:
            return None
        try:
            ratio = Decimal(str(10.0 ** float(residual)))
        except (OverflowError, ValueError):
            return None
        return candidate / ratio if ratio.is_finite() and ratio > 0 else None
    return None


def unit_dimension(unit: str) -> str | None:
    """Physical dimension of a stored unit. Unknown units match nothing."""

    return _UNIT_DIMENSION.get(unit)


def band_dimension_matches(
    quantity: Quantity,
    band: DecisionBand,
    *,
    operation: MetricOperation | None = None,
) -> bool:
    """True only when the band and the quantity share one dimension.

    Residual metrics (relative and dex) are dimensionless even when their
    source quantity has a physical unit. kJ/mol does not match J/mol/K.
    Scale aliases that are not in ``_UNIT_DIMENSION`` fail closed.
    ``operation`` is the residual's own (identity-aware) metric when the
    caller knows it; default is the quantity's metric.
    """

    if band.unit == "dimensionless" and (
        operation or metric_operation(quantity)
    ) in {MetricOperation.RELATIVE, MetricOperation.DEX}:
        return True
    # These two output dimensions are intentionally subtype-defined. The
    # printed-width resolver matches this scale to identity.subtype; permit
    # only its absolute path here and keep other units fail-closed.
    if (
        quantity is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and band.unit == "mass_percent"
    ):
        return operation is MetricOperation.ABSOLUTE
    if (
        quantity is Quantity.EQUILIBRIUM_PHASE_COMPOSITION
        and band.unit in {"mass_percent", "mole_percent"}
    ):
        return operation is MetricOperation.ABSOLUTE
    quantity_dim = unit_dimension(QUANTITY_UNITS[quantity])
    band_dim = unit_dimension(band.unit)
    return quantity_dim is not None and quantity_dim == band_dim


def decision_band_for(
    quantity: Quantity | None,
    source_relation: SourceRelation,
    *,
    printed_band: DecisionBand | None = None,
    printed_refusal: Mapping[str, object] | None = None,
    derived_band: DecisionBand | None = None,
    operation: MetricOperation | None = None,
) -> DecisionBand | None:
    """Choose a printed or derived band, then apply its quantity and unit gates.

    ``source_relation`` labels lineage only. A KEMS pressure envelope covers
    admitted measured partial-pressure rows whose cell uncertainty is unparseable;
    other printed-uncertainty refusals block derived-band fallthrough.
    """
    del source_relation
    band = printed_band if printed_band is not None else derived_band
    if band is None:
        return None
    kems_pressure_band = band.rule.startswith(
        "KEMS p_partial measured uncertainty:"
    )
    if kems_pressure_band:
        if quantity is not Quantity.P_PARTIAL:
            return None
    elif printed_refusal is not None:
        return None
    if not band_dimension_matches(quantity, band, operation=operation):
        return None
    return band


def populate_numeric(
    *,
    quantity: Quantity,
    candidate: Decimal,
    reference: Decimal,
    source_relation: SourceRelation,
    metric_uncertainty: Uncertainty | None = None,
    rail: Rail | None = None,
    method: MethodToken | None = None,
    observations: Mapping[str, Observation] | None = None,
    experiments: Mapping[str, Experiment] | None = None,
    printed_band: DecisionBand | None = None,
    printed_refusal: Mapping[str, object] | None = None,
    derived_band: DecisionBand | None = None,
    operation_override: MetricOperation | None = None,
    band_operation: MetricOperation | None = None,
) -> tuple[ResidualNumeric | None, RefusalReason | None, dict[str, object]]:
    """``band_operation`` checks a row's OWN printed band against the row's
    identity-aware metric. It is None for every borrowed/derived band, which
    keeps the quantity-default dimension check (no envelope transfer).
    """
    operation = operation_override or metric_operation(quantity)
    if operation is None:
        return None, RefusalReason.METRIC_DOMAIN, {"quantity": quantity.value}
    unit = QUANTITY_UNITS[quantity]
    if operation in {MetricOperation.RELATIVE, MetricOperation.DEX}:
        unit = "dimensionless"
    value = compute_metric(operation, candidate, reference)
    if value is None:
        return None, RefusalReason.METRIC_DOMAIN, {
            "operation": operation.value,
            "candidate": str(candidate),
            "reference": str(reference),
        }
    band = decision_band_for(
        quantity,
        source_relation,
        printed_band=printed_band,
        printed_refusal=printed_refusal,
        derived_band=derived_band,
        operation=band_operation,
    )
    # Dimension guard. decision_band_for normally filters mismatched bands;
    # keep this check for callers that replace it in a focused test.
    if band is not None and not band_dimension_matches(
        quantity, band, operation=band_operation
    ):
        return None, RefusalReason.DECISION_RULE_MISSING, {
            "reason": f"band_dimension_mismatch:{quantity.value}",
            "quantity": quantity.value,
            "quantity_unit": QUANTITY_UNITS[quantity],
            "band_unit": band.unit,
            "operation": operation.value,
        }
    numeric = ResidualNumeric(
        operation=operation,
        unit=unit,
        value=value,
        decision_band=band,
        metric_uncertainty=metric_uncertainty,
    )
    return numeric, None, {}


def match_status(numeric: ResidualNumeric) -> ResidualStatus:
    if numeric.decision_band is None:
        return ResidualStatus.NO_BAND
    if abs(numeric.value) <= numeric.decision_band.value:
        return ResidualStatus.MATCH
    return ResidualStatus.MISMATCH
