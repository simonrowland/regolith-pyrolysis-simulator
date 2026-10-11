"""Fit compilation Cp/H/S series to the existing NASA-9 evaluator.

The fitted basis is the one evaluated by
``simulator.vapour_rail.nasa_cea.Nasa9Segment``:

    Cp/R = a1/T² + a2/T + a3 + a4 T + a5 T² + a6 T³ + a7 T⁴
    H/(R T) = -a1/T² + a2 ln(T)/T + a3 + a4 T/2 + a5 T²/3
              + a6 T³/4 + a7 T⁴/5 + a8/T
    S/R = -a1/(2T²) - a2/T + a3 ln(T) + a4 T + a5 T²/2
          + a6 T³/3 + a7 T⁴/4 + a9

The linear solve uses x=T/Tscale. It fixes the integration constants, rather
than fitting them: a8 (``b1``) = H_anchor/R - H_without_a8(T_anchor)/R and
a9 (``b2``) = S_anchor/R - S_without_a9(T_anchor)/R. T is kelvin; dividing
Cp/S in J/(mol K) by R gives dimensionless ratios, and converting H-H298
from kJ/mol to J/mol before dividing by R gives kelvin, matching H/R.
JANAF O-029 at 298.15 K is a reference-state sanity pin: its derived
formation Gibbs energy is zero when its source reference is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from simulator.battery.enums import Quantity
from simulator.battery.records import ValueKind
from simulator.diagnostic_helpers.gibbs_battery import (
    formation_subtraction_kJ_mol,
)
from simulator.reference_data.janaf import formula_composition
from simulator.vapour_rail.nasa_cea import Nasa9Segment, R_J_PER_MOL_K

REFERENCE_T_K = 298.15
# NASA Glenn NASA-9 fits start at 200 K; lower printed cells remain available
# for cellwise scoring.
FIT_T_MIN_K = 200.0
NASA9_CP_TERM_ORDER = (2, 3, 4, 1, 0, 5, 6)
DEFAULT_SIDECAR_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "literature"
    / "compilation-thermo-fits.generated.json"
)


@dataclass(frozen=True)
class _Cell:
    temperature_K: float
    value: float
    raw_token: str | None
    source_unit: str | None
    uncertainty: float | None


@dataclass(frozen=True)
class FittedCurve:
    formula: str
    phase_label: str
    source_id: str
    source_record_id: str
    T_min_K: float
    T_max_K: float
    reference_pressure_Pa: float
    segment: Nasa9Segment
    is_element_reference: bool
    segments: tuple[Nasa9Segment, ...] = ()

    def segment_at(self, temperature_K: float) -> Nasa9Segment:
        """Return the right-hand segment at a shared endpoint."""

        segments = self.segments or (self.segment,)
        for segment in reversed(segments):
            if segment.T_min_K <= temperature_K <= segment.T_max_K:
                return segment
        raise ValueError(
            f"{self.formula} reference curve does not cover {temperature_K:g} K"
        )


@dataclass(frozen=True)
class FitResult:
    record: Mapping[str, Any]
    curve: FittedCurve | None


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def source_pressure_Pa(record: Mapping[str, Any]) -> float | None:
    """Read an explicit source pressure convention; never assume one."""

    table = record.get("table")
    table = table if isinstance(table, Mapping) else record
    explicit = table.get("reference_pressure_Pa") or record.get("reference_pressure_Pa")
    if explicit is not None:
        pressure = _decimal(explicit)
        return float(pressure) if pressure is not None and pressure > 0 else None
    text = str(
        table.get("standard_state_as_published")
        or record.get("standard_state_as_published")
        or ""
    )
    match = re.search(
        r"(?:p|P)\s*(?:°|\^o)?\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*(MPa|kPa|bar|atm|Pa)",
        text,
    )
    if match is None:
        return None
    amount = Decimal(match.group(1))
    scale = {
        "MPa": Decimal("1e6"),
        "kPa": Decimal("1e3"),
        "bar": Decimal("1e5"),
        "atm": Decimal("101325"),
        "Pa": Decimal(1),
    }[match.group(2)]
    return float(amount * scale)


def source_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    table = record.get("table")
    table = table if isinstance(table, Mapping) else record
    entry = table.get("index_entry")
    entry = entry if isinstance(entry, Mapping) else {}
    source_id = str(record.get("source_id") or "unknown-source")
    source_record_id = str(
        table.get("table_id") or record.get("record_id") or "unknown-record"
    )
    formula = str(
        entry.get("formula_normalised")
        or entry.get("formula_as_published")
        or record.get("formula")
        or record.get("formula_as_published")
        or ""
    )
    return {
        "source_id": source_id,
        "source_record_id": source_record_id,
        "formula": formula,
        "source_state": str(entry.get("state") or record.get("phase") or ""),
        "source_name": str(entry.get("name") or ""),
        "source_title": str(table.get("title_as_published") or ""),
        "source_locator": str(
            table.get("url") or record.get("source_locator") or source_record_id
        ),
        "standard_state_convention": str(
            table.get("standard_state_as_published")
            or record.get("standard_state_as_published")
            or ""
        ),
        "reference_pressure_Pa": source_pressure_Pa(record),
        "input_digest": hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest(),
    }


def _cell_series(series: object) -> tuple[Quantity, tuple[_Cell, ...]]:
    quantity = series.quantity
    value = series.value
    if value.kind is ValueKind.SERIES:
        points = tuple(value.series or ())
    elif value.kind is ValueKind.POINT:
        temperature = series.temperature_K
        if not temperature.is_value or value.point is None:
            return quantity, ()
        points = ((temperature.value, value.point),)
    else:
        return quantity, ()

    raw_tokens = tuple(getattr(series, "raw_tokens", ()))
    source_units = tuple(getattr(series, "source_units", ()))
    uncertainties = tuple(getattr(series, "cell_uncertainties", ()))
    cells: list[_Cell] = []
    for index, (temperature, amount) in enumerate(points):
        token = raw_tokens[index] if index < len(raw_tokens) else None
        unit = source_units[index] if index < len(source_units) else None
        uncertainty = uncertainties[index] if index < len(uncertainties) else None
        cells.append(
            _Cell(
                float(temperature),
                float(amount),
                token,
                unit,
                float(uncertainty) if uncertainty is not None else None,
            )
        )
    return quantity, tuple(cells)


def _channel_cells(series: Sequence[object]) -> dict[Quantity, tuple[_Cell, ...]]:
    collected: dict[Quantity, list[_Cell]] = {}
    for item in series:
        quantity, cells = _cell_series(item)
        if cells:
            collected.setdefault(quantity, []).extend(cells)
    return {
        quantity: tuple(sorted(cells, key=lambda cell: cell.temperature_K))
        for quantity, cells in collected.items()
    }


def _record_base(
    *,
    metadata: Mapping[str, Any],
    phase_label: str,
    T_min_K: float | None,
    T_max_K: float | None,
) -> dict[str, Any]:
    record_id = ":".join(
        (
            str(metadata["source_id"]),
            str(metadata["source_record_id"]),
            phase_label,
            "open" if T_min_K is None else f"{T_min_K:.9g}",
            "open" if T_max_K is None else f"{T_max_K:.9g}",
        )
    )
    return {
        "record_id": record_id,
        "record_kind": "derived_fit",
        "source_id": metadata["source_id"],
        "source_record_id": metadata["source_record_id"],
        "source_locator": metadata["source_locator"],
        "input_digest": metadata["input_digest"],
        "formula": metadata["formula"],
        "phase": phase_label,
        "source_standard_state": metadata["source_state"],
        "source_standard_state_convention": metadata[
            "standard_state_convention"
        ],
        "reference_pressure_Pa": metadata["reference_pressure_Pa"],
        "validity": {"T_min_K": T_min_K, "T_max_K": T_max_K},
        "lineage": {"kind": "source_cells", "source_id": metadata["source_id"]},
        "circularity": "fit_from_same_compilation_cells",
    }


def _rejected(
    base: Mapping[str, Any], reason_code: str, reason: str, **details: Any
) -> FitResult:
    return FitResult(
        {
            **dict(base),
            "status": "rejected",
            "acceptance_tier": None,
            "reason_code": reason_code,
            "reason": reason,
            **details,
        },
        None,
    )


def _fit_basis_cp(x: float) -> tuple[float, ...]:
    return (x**-2, x**-1, 1.0, x, x**2, x**3, x**4)


def _fit_basis_h_over_R(x: float, scale_K: float) -> tuple[float, ...]:
    return (
        scale_K * -1.0 / x,
        scale_K * (math.log(x) + math.log(scale_K)),
        scale_K * x,
        scale_K * x**2 / 2.0,
        scale_K * x**3 / 3.0,
        scale_K * x**4 / 4.0,
        scale_K * x**5 / 5.0,
    )


def _fit_basis_s_over_R(x: float, scale_K: float = 1.0) -> tuple[float, ...]:
    return (
        -0.5 / x**2,
        -1.0 / x,
        math.log(x) + math.log(scale_K),
        x,
        x**2 / 2.0,
        x**3 / 3.0,
        x**4 / 4.0,
    )


def _evaluate(segment: Nasa9Segment, temperature_K: float) -> tuple[float, float, float, float]:
    cp_R, h_RT, s_R, g_RT = segment.evaluate_ratios(temperature_K)
    return (
        cp_R * R_J_PER_MOL_K,
        h_RT * R_J_PER_MOL_K * temperature_K,
        s_R * R_J_PER_MOL_K,
        g_RT * R_J_PER_MOL_K * temperature_K,
    )


def _element_reference_terms(
    formula: str,
    temperature_K: float,
    pressure_Pa: float,
    reference_curves: Mapping[str, Sequence[FittedCurve]],
) -> tuple[list[tuple[float, float, float]] | None, str | None]:
    composition = formula_composition(formula)
    if not composition:
        return None, f"unsupported_formula: formula {formula!r} has no elemental composition"
    terms: list[tuple[float, float, float]] = []
    for element, atom_count in composition:
        candidates = list(reference_curves.get(element, ()))
        in_range = [
            curve
            for curve in candidates
            if curve.T_min_K <= temperature_K <= curve.T_max_K
        ]
        pressure_matches = [
            curve
            for curve in in_range
            if curve.reference_pressure_Pa == pressure_Pa
        ]
        if not pressure_matches:
            if in_range:
                phases = ", ".join(sorted({curve.phase_label for curve in in_range}))
                return None, (
                    f"pressure_convention_mismatch: {element}, {phases}, "
                    f"no fit matches {pressure_Pa:g} Pa"
                )
            return None, (
                f"missing_element_reference_phase: {element}, none, "
                f"no accepted reference fit covers {temperature_K:g} K"
            )
        if len(pressure_matches) > 1:
            # At a shared phase boundary the right-hand source interval is the
            # JANAF reference schedule's phase at T; otherwise fail closed.
            starts_at_T = [
                curve for curve in pressure_matches if curve.T_min_K == temperature_K
            ]
            if len(starts_at_T) == 1:
                pressure_matches = starts_at_T
            else:
                phases = ", ".join(
                    sorted({curve.phase_label for curve in pressure_matches})
                )
                return None, (
                    f"ambiguous_element_reference_phase: {element}, {phases}, "
                    f"multiple fits cover {temperature_K:g} K"
                )
        curve = pressure_matches[0]
        reference_composition = formula_composition(curve.formula)
        if (
            not reference_composition
            or len(reference_composition) != 1
            or reference_composition[0][0] != element
            or reference_composition[0][1] <= 0
        ):
            return None, (
                f"missing_element_reference_phase: {element}, {curve.phase_label}, "
                f"candidate formula {curve.formula!r} is not an elemental reference"
            )
        coefficient = float(atom_count / reference_composition[0][1])
        _cp, h, _s, g = _evaluate(curve.segment_at(temperature_K), temperature_K)
        terms.append((coefficient, h, g))
    return terms, None


def _check_positive_cp(segment: Nasa9Segment) -> bool:
    lo, hi = segment.T_min_K, segment.T_max_K
    a1, a2, _a3, a4, a5, a6, a7 = segment.coefficients
    derivative_times_t3 = np.array(
        [-2.0 * a1, -a2, 0.0, a4, 2.0 * a5, 3.0 * a6, 4.0 * a7],
        dtype=float,
    )
    critical: list[float] = []
    roots = np.polynomial.polynomial.polyroots(derivative_times_t3)
    for root in roots:
        if abs(float(root.imag)) <= 1e-9 * max(1.0, abs(float(root.real))):
            point = float(root.real)
            if lo < point < hi:
                critical.append(point)
    for temperature in (lo, hi, *critical):
        cp = _evaluate(segment, temperature)[0]
        if not math.isfinite(cp) or cp <= 0.0:
            return False
    return True


def _residual_stats(residuals: Sequence[float]) -> dict[str, float | int]:
    if not residuals:
        return {"cell_count": 0, "mean_signed": 0.0, "max_abs": 0.0, "rms": 0.0}
    values = np.asarray(residuals, dtype=float)
    return {
        "cell_count": int(values.size),
        "mean_signed": float(np.mean(values)),
        "max_abs": float(np.max(np.abs(values))),
        "rms": float(np.sqrt(np.mean(values**2))),
    }


def _segment_index(temperature_K: float, bounds: Sequence[tuple[float, float]]) -> int:
    """Choose the right-hand segment at a shared endpoint."""

    for index in range(len(bounds) - 1, -1, -1):
        lower, upper = bounds[index]
        if lower <= temperature_K <= upper:
            return index
    return min(
        range(len(bounds)),
        key=lambda index: min(
            abs(temperature_K - bounds[index][0]), abs(temperature_K - bounds[index][1])
        ),
    )


def _piecewise_evaluate(
    segments: Sequence[Nasa9Segment], temperature_K: float
) -> tuple[float, float, float, float]:
    for segment in reversed(segments):
        if segment.T_min_K <= temperature_K <= segment.T_max_K:
            return _evaluate(segment, temperature_K)
    bounds = [(segment.T_min_K, segment.T_max_K) for segment in segments]
    return _evaluate(segments[_segment_index(temperature_K, bounds)], temperature_K)


def _printed_cp_extrema(cells: Sequence[_Cell]) -> set[float]:
    values = sorted(cells, key=lambda cell: cell.temperature_K)
    return {
        values[index].temperature_K
        for index in range(1, len(values) - 1)
        if (values[index].value - values[index - 1].value)
        * (values[index + 1].value - values[index].value)
        <= 0.0
        and (
            values[index].value != values[index - 1].value
            or values[index].value != values[index + 1].value
        )
    }


def _interpolation_ringing(
    segment: Nasa9Segment, cp_cells: Sequence[_Cell]
) -> list[dict[str, float]]:
    """Find midpoint Cp values outside adjacent printed values and their precision."""

    cells = sorted(
        (cell for cell in cp_cells if segment.T_min_K <= cell.temperature_K <= segment.T_max_K),
        key=lambda cell: cell.temperature_K,
    )
    extrema = _printed_cp_extrema(cells)
    failures: list[dict[str, float]] = []
    for left, right in zip(cells, cells[1:]):
        if any(left.temperature_K <= peak <= right.temperature_K for peak in extrema):
            continue
        midpoint = (left.temperature_K + right.temperature_K) / 2.0
        cp_mid = _evaluate(segment, midpoint)[0]
        allowance = max(left.uncertainty or 0.0, right.uncertainty or 0.0)
        lower = min(left.value, right.value) - allowance
        upper = max(left.value, right.value) + allowance
        if cp_mid < lower or cp_mid > upper:
            failures.append(
                {
                    "T_left_K": left.temperature_K,
                    "T_mid_K": midpoint,
                    "T_right_K": right.temperature_K,
                    "Cp_mid_J_per_mol_K": cp_mid,
                    "allowed_min_J_per_mol_K": lower,
                    "allowed_max_J_per_mol_K": upper,
                }
            )
    return failures


def _constrained_weighted_lstsq(
    matrix: np.ndarray,
    target: np.ndarray,
    constraints: np.ndarray,
    constraint_target: np.ndarray,
) -> tuple[np.ndarray, int]:
    """Solve weighted least squares in the equality-constraint null space."""

    row_norms = np.linalg.norm(constraints, axis=1)
    normalized = constraints / row_norms[:, None]
    normalized_target = constraint_target / row_norms
    column_norms = np.linalg.norm(
        np.vstack((matrix, normalized)), axis=0
    )
    column_norms = np.where(column_norms > 0.0, column_norms, 1.0)
    scaled_constraints = normalized / column_norms[None, :]
    scaled_matrix = matrix / column_norms[None, :]
    scaled_row_norms = np.linalg.norm(scaled_constraints, axis=1)
    scaled_constraints = scaled_constraints / scaled_row_norms[:, None]
    scaled_target = normalized_target / scaled_row_norms
    # A complete QR of C.T gives both the minimum-norm constrained solution
    # and an orthonormal basis for null(C), without repeatedly taking a full
    # SVD as adaptive segmentation adds variables.
    orthogonal, upper = np.linalg.qr(scaled_constraints.T, mode="complete")
    constraint_count = scaled_constraints.shape[0]
    triangular = upper[:constraint_count, :]
    diagonal = np.abs(np.diag(triangular))
    tolerance = np.finfo(float).eps * max(scaled_constraints.shape) * diagonal.max()
    rank = int(np.count_nonzero(diagonal > tolerance))
    if rank != constraint_count:
        # This is unusual for the open-chain anchor/join constraints, but the
        # SVD fallback keeps a clear typed failure if a future shape is rank poor.
        _u, singular, vh = np.linalg.svd(scaled_constraints, full_matrices=True)
        tolerance = np.finfo(float).eps * max(scaled_constraints.shape) * singular[0]
        rank = int(np.count_nonzero(singular > tolerance))
        particular, *_ = np.linalg.lstsq(scaled_constraints, scaled_target, rcond=None)
        null_space = vh[rank:].T
    else:
        particular_coordinates = np.linalg.solve(triangular.T, scaled_target)
        particular = orthogonal[:, :constraint_count] @ particular_coordinates
        null_space = orthogonal[:, constraint_count:]
    constraint_error = np.max(np.abs(scaled_constraints @ particular - scaled_target))
    if constraint_error > 1e-10 * max(1.0, float(np.max(np.abs(scaled_target)))):
        raise ValueError(
            "NASA-9 continuity and anchor constraints are inconsistent "
            f"(scaled residual {constraint_error:.3g})"
        )
    if null_space.shape[1] == 0:
        return particular / column_norms, 0
    reduced = scaled_matrix @ null_space
    reduced_target = target - scaled_matrix @ particular
    correction, _residuals, reduced_rank, reduced_singular = np.linalg.lstsq(
        reduced, reduced_target, rcond=1e-15
    )
    if reduced_singular.size:
        rank_tolerance = 1e-15 * reduced_singular[0]
        reduced_rank = int(np.count_nonzero(reduced_singular > rank_tolerance))
    solution = (particular + null_space @ correction) / column_norms
    return solution, int(reduced_rank)


def _fit_piecewise_candidate(
    *,
    record: Mapping[str, Any],
    series: Sequence[object],
    phase_label: str,
    standard_state: str,
    is_element_reference: bool,
    reference_curves: Mapping[str, Sequence[FittedCurve]],
    segment_bounds: Sequence[tuple[float, float]],
    fit_lo: float,
    fit_hi: float,
) -> FitResult:
    """Jointly fit bounded NASA-9 segments with exact anchor and join constraints."""

    metadata = source_metadata(record)
    base = _record_base(
        metadata=metadata,
        phase_label=phase_label,
        T_min_K=fit_lo,
        T_max_K=fit_hi,
    )
    channels = _channel_cells(series)
    cp_cells = tuple(
        cell for cell in channels.get(Quantity.CP, ()) if fit_lo <= cell.temperature_K <= fit_hi
    )
    h_cells = tuple(
        cell
        for cell in channels.get(Quantity.H_MINUS_H298, ())
        if fit_lo <= cell.temperature_K <= fit_hi
    )
    s_cells = tuple(
        cell for cell in channels.get(Quantity.S, ()) if fit_lo <= cell.temperature_K <= fit_hi
    )
    if standard_state not in {"gas", "condensed_solid", "condensed_liquid", "condensed"}:
        return _rejected(
            base,
            "unsupported_standard_state",
            f"source phase {phase_label!r} has no closed NASA-9 standard-state mapping",
        )
    missing = [
        name
        for name, cells in (("Cp", cp_cells), ("H-H298", h_cells), ("S", s_cells))
        if not cells
    ]
    if missing:
        return _rejected(
            base,
            "missing_fit_channel",
            "required printed channels are absent: " + ", ".join(missing),
        )
    all_fit_cells = (*cp_cells, *h_cells, *s_cells)
    if any(cell.raw_token is None or cell.uncertainty is None for cell in all_fit_cells):
        return _rejected(
            base,
            "missing_printed_precision",
            "one or more fit cells lack a raw token or converted rounding allowance",
        )
    # H and S are coupled observations, not extra Cp-basis rows. Four Cp cells
    # support two reduced-basis terms plus the two residual degrees of freedom.
    basis_terms = min(7, len(cp_cells) - 2)
    if len(cp_cells) < 4:
        return _rejected(
            base,
            "insufficient_cells",
            f"phase range has {len(cp_cells)} printed Cp cells; at least 4 are required",
            cell_count=len(cp_cells),
        )
    base["basis_terms"] = basis_terms

    s_anchor = next(
        (cell for cell in s_cells if cell.temperature_K == REFERENCE_T_K),
        s_cells[0] if s_cells else None,
    )
    delta_fh_anchor = next(
        (
            cell
            for cell in channels.get(Quantity.DELTA_FH, ())
            if cell.temperature_K == REFERENCE_T_K
        ),
        None,
    )
    if s_anchor is None:
        return _rejected(base, "missing_absolute_entropy_anchor", "no absolute entropy cell is available")

    formula = str(metadata["formula"])
    composition = formula_composition(formula)
    pressure = metadata["reference_pressure_Pa"]
    formation_capable = False
    formation_reason_code: str | None = None
    formation_reason: str | None = None
    h_anchor_J = 0.0
    if is_element_reference and pressure is not None:
        formation_capable = True
    elif pressure is None:
        formation_reason_code = "missing_pressure_convention"
        formation_reason = "source does not declare a standard pressure convention"
    elif not composition:
        formation_reason_code = "unsupported_formula"
        formation_reason = f"formula {formula!r} has no elemental composition"
    elif not is_element_reference and (
        delta_fh_anchor is None or delta_fh_anchor.raw_token is None
    ):
        formation_reason_code = "missing_formation_h_anchor"
        formation_reason = "source has no printed ΔfH°(298.15 K) anchor"
    else:
        resolved, error = _element_reference_terms(formula, REFERENCE_T_K, pressure, reference_curves)
        if error:
            formation_reason_code, _separator, _detail = error.partition(":")
            formation_reason = error
        else:
            assert resolved is not None
            formation_capable = True
            h_anchor_J = float(delta_fh_anchor.value) * 1000.0 + sum(
                coefficient * h_ref for coefficient, h_ref, _g_ref in resolved
            )

    segment_count = len(segment_bounds)
    scale_K = tuple(math.sqrt(lower * upper) for lower, upper in segment_bounds)
    variable_count = 9 * segment_count
    rows: list[np.ndarray] = []
    targets: list[float] = []
    weights: list[float] = []
    constraints: list[np.ndarray] = []
    constraint_targets: list[float] = []

    def coefficient_row(index: int, values: Sequence[float], *, offset: int = 0) -> np.ndarray:
        row = np.zeros(variable_count, dtype=float)
        start = 9 * index + offset
        row[start : start + len(values)] = values
        return row

    def cp_row(index: int, temperature_K: float) -> np.ndarray:
        return coefficient_row(index, _fit_basis_cp(temperature_K / scale_K[index]))

    def h_row(index: int, temperature_K: float) -> np.ndarray:
        row = coefficient_row(
            index,
            _fit_basis_h_over_R(temperature_K / scale_K[index], scale_K[index]),
        )
        row[9 * index + 7] = 1.0
        return row

    def s_row(index: int, temperature_K: float) -> np.ndarray:
        row = coefficient_row(
            index,
            _fit_basis_s_over_R(temperature_K / scale_K[index], scale_K[index]),
        )
        row[9 * index + 8] = 1.0
        return row

    def add_observation(row: np.ndarray, target: float, uncertainty_in_target_unit: float) -> None:
        if not math.isfinite(uncertainty_in_target_unit) or uncertainty_in_target_unit <= 0.0:
            raise ValueError("fit uncertainty must be finite and positive")
        rows.append(row)
        targets.append(target)
        weights.append(R_J_PER_MOL_K / uncertainty_in_target_unit)

    bounds = tuple(segment_bounds)
    anchor_index = _segment_index(REFERENCE_T_K, bounds)
    s_anchor_index = _segment_index(s_anchor.temperature_K, bounds)
    try:
        for index in range(segment_count):
            for coefficient_index in NASA9_CP_TERM_ORDER[basis_terms:]:
                zero_coefficient = np.zeros(variable_count, dtype=float)
                zero_coefficient[9 * index + coefficient_index] = 1.0
                constraints.append(zero_coefficient)
                constraint_targets.append(0.0)

        for cell in cp_cells:
            index = _segment_index(cell.temperature_K, bounds)
            add_observation(cp_row(index, cell.temperature_K), cell.value / R_J_PER_MOL_K, cell.uncertainty)

        for cell in h_cells:
            if cell.temperature_K == REFERENCE_T_K:
                if abs(cell.value) > cell.uncertainty:
                    return _rejected(
                        base,
                        "h_increment_anchor_nonzero",
                        "printed H-H298 cell disagrees with the required zero anchor",
                    )
                continue
            index = _segment_index(cell.temperature_K, bounds)
            row = h_row(index, cell.temperature_K) - h_row(anchor_index, REFERENCE_T_K)
            add_observation(
                row,
                cell.value * 1000.0 / R_J_PER_MOL_K,
                cell.uncertainty * 1000.0,
            )

        for cell in s_cells:
            if cell is s_anchor:
                continue
            index = _segment_index(cell.temperature_K, bounds)
            row = s_row(index, cell.temperature_K) - s_row(
                s_anchor_index, s_anchor.temperature_K
            )
            add_observation(
                row,
                (cell.value - s_anchor.value) / R_J_PER_MOL_K,
                cell.uncertainty + s_anchor.uncertainty,
            )

        # At join Tj, the Cp/R row is [phi_cp(Tj), -phi_cp(Tj)] = 0;
        # H/(RT) is [phi_h(Tj), a8, -phi_h(Tj), -a8]/Tj = 0; and
        # S/R is [phi_s(Tj), a9, -phi_s(Tj), -a9] = 0. These expressions
        # are dimensionless (Cp/R, H/(RT), S/R); equality of H/(RT) at the
        # same positive Tj is equality of H. The first a8/a9 are fixed by the
        # printed H and S anchors, and each later pair follows from these rows.
        h_anchor = h_row(anchor_index, REFERENCE_T_K)
        constraints.append(h_anchor)
        constraint_targets.append(h_anchor_J / R_J_PER_MOL_K)
        s_anchor_row = s_row(s_anchor_index, s_anchor.temperature_K)
        constraints.append(s_anchor_row)
        constraint_targets.append(s_anchor.value / R_J_PER_MOL_K)

        for index in range(segment_count - 1):
            join_K = segment_bounds[index][1]
            cp_continuity = cp_row(index, join_K) - cp_row(index + 1, join_K)
            # H/(RT) equality is equivalent to H equality because both sides
            # are evaluated at the same positive T. The row is divided by T
            # to match the displayed NASA-9 H/(RT) form and is dimensionless.
            h_continuity = (
                h_row(index, join_K) - h_row(index + 1, join_K)
            ) / join_K
            s_continuity = s_row(index, join_K) - s_row(index + 1, join_K)
            constraints.extend((cp_continuity, h_continuity, s_continuity))
            constraint_targets.extend((0.0, 0.0, 0.0))
    except ValueError as exc:
        return _rejected(base, "missing_printed_precision", str(exc))

    matrix = np.asarray(rows, dtype=float)
    target = np.asarray(targets, dtype=float)
    weight = np.asarray(weights, dtype=float)
    weighted = matrix * weight[:, None]
    weighted_target = target * weight
    constraint_matrix = np.asarray(constraints, dtype=float)
    constraint_target = np.asarray(constraint_targets, dtype=float)
    try:
        scaled, reduced_rank = _constrained_weighted_lstsq(
            weighted, weighted_target, constraint_matrix, constraint_target
        )
    except (ValueError, np.linalg.LinAlgError) as exc:
        return _rejected(base, "insufficient_cells", f"constrained NASA-9 system is singular: {exc}")
    segments: list[Nasa9Segment] = []
    for index, (lower_K, upper_K) in enumerate(segment_bounds):
        local_scale_K = scale_K[index]
        values = scaled[9 * index : 9 * index + 9]
        A1, A2, A3, A4, A5, A6, A7 = (float(value) for value in values[:7])
        coefficients = (
            A1 * local_scale_K**2,
            A2 * local_scale_K,
            A3,
            A4 / local_scale_K,
            A5 / local_scale_K**2,
            A6 / local_scale_K**3,
            A7 / local_scale_K**4,
        )
        segments.append(
            Nasa9Segment(
                lower_K,
                upper_K,
                coefficients,
                float(values[7]),
                float(values[8]),
            )
        )

    positive_cp = all(_check_positive_cp(segment) for segment in segments)

    cp_residuals: list[float] = []
    h_residuals: list[float] = []
    s_residuals: list[float] = []
    over_precision: list[dict[str, Any]] = []
    for cell in cp_cells:
        residual = _piecewise_evaluate(segments, cell.temperature_K)[0] - cell.value
        cp_residuals.append(residual)
        if abs(residual) > cell.uncertainty:
            over_precision.append({
                "quantity": "cp", "T_K": cell.temperature_K,
                "residual": residual, "allowance": cell.uncertainty,
            })
    h_at_298 = _piecewise_evaluate(segments, REFERENCE_T_K)[1]
    for cell in h_cells:
        residual = (_piecewise_evaluate(segments, cell.temperature_K)[1] - h_at_298) / 1000.0 - cell.value
        h_residuals.append(residual)
        if abs(residual) > cell.uncertainty:
            over_precision.append({
                "quantity": "H-H298", "T_K": cell.temperature_K,
                "residual": residual, "allowance": cell.uncertainty,
            })
    for cell in s_cells:
        residual = _piecewise_evaluate(segments, cell.temperature_K)[2] - cell.value
        s_residuals.append(residual)
        if abs(residual) > cell.uncertainty:
            over_precision.append({
                "quantity": "S", "T_K": cell.temperature_K,
                "residual": residual, "allowance": cell.uncertainty,
            })

    rounding_allowances = {
        name: max((cell.uncertainty or 0.0 for cell in cells), default=0.0)
        for name, cells in (
            ("Cp_J_per_mol_K", cp_cells),
            ("H_minus_H298_kJ_per_mol", h_cells),
            ("S_J_per_mol_K", s_cells),
            ("delta_fH_kJ_per_mol", channels.get(Quantity.DELTA_FH, ())),
            ("delta_fG_kJ_per_mol", channels.get(Quantity.DELTA_FG, ())),
        )
    }
    fit_statistics: dict[str, Any] = {
        "cp_J_per_mol_K": _residual_stats(cp_residuals),
        "H_minus_H298_kJ_per_mol": _residual_stats(h_residuals),
        "S_J_per_mol_K": _residual_stats(s_residuals),
        "maximum_printed_rounding_allowance": rounding_allowances,
        "segment_count": segment_count,
        "constrained_basis_rank": reduced_rank,
    }

    formation_residuals: dict[str, list[float]] = {
        "delta_fH_kJ_per_mol": [],
        "delta_fG_kJ_per_mol": [],
    }
    if formation_capable:
        for quantity, result_key in (
            (Quantity.DELTA_FH, "delta_fH_kJ_per_mol"),
            (Quantity.DELTA_FG, "delta_fG_kJ_per_mol"),
        ):
            for cell in channels.get(quantity, ()):
                if not fit_lo <= cell.temperature_K <= fit_hi:
                    continue
                _cp, enthalpy, _entropy, gibbs = _piecewise_evaluate(
                    segments, cell.temperature_K
                )
                species_property = enthalpy if quantity is Quantity.DELTA_FH else gibbs
                if is_element_reference:
                    references = [(1.0, species_property)]
                else:
                    resolved, error = _element_reference_terms(
                        formula, cell.temperature_K, pressure, reference_curves
                    )
                    if error:
                        formation_reason_code, _separator, _detail = error.partition(":")
                        formation_reason = error
                        formation_capable = False
                        break
                    assert resolved is not None
                    references = [
                        (coefficient, h_ref if quantity is Quantity.DELTA_FH else g_ref)
                        for coefficient, h_ref, g_ref in resolved
                    ]
                derived = formation_subtraction_kJ_mol(species_property, references)
                formation_residuals[result_key].append(derived - cell.value)
            if not formation_capable:
                break

    if formation_capable:
        fit_statistics["derived_formation_H_kJ_per_mol"] = _residual_stats(
            formation_residuals["delta_fH_kJ_per_mol"]
        )
        fit_statistics["derived_formation_G_kJ_per_mol"] = _residual_stats(
            formation_residuals["delta_fG_kJ_per_mol"]
        )
    else:
        formation_residuals["delta_fH_kJ_per_mol"].clear()
        formation_residuals["delta_fG_kJ_per_mol"].clear()
    cp_node_residuals = [
        {
            "T_K": cell.temperature_K,
            "residual": _piecewise_evaluate(segments, cell.temperature_K)[0] - cell.value,
        }
        for cell in cp_cells
    ]
    failed_cells = over_precision
    ring_failures = [
        failure
        for segment in segments
        for failure in _interpolation_ringing(segment, cp_cells)
    ]
    fit_error = {
        "Cp_J_per_mol_K": fit_statistics["cp_J_per_mol_K"]["max_abs"],
        "H_minus_H298_kJ_per_mol": fit_statistics["H_minus_H298_kJ_per_mol"]["max_abs"],
        "S_J_per_mol_K": fit_statistics["S_J_per_mol_K"]["max_abs"],
    }
    if formation_capable:
        fit_error["delta_fH_kJ_per_mol"] = (
            fit_statistics["derived_formation_H_kJ_per_mol"]["max_abs"]
            if formation_residuals["delta_fH_kJ_per_mol"]
            else None
        )
        fit_error["delta_fG_kJ_per_mol"] = (
            fit_statistics["derived_formation_G_kJ_per_mol"]["max_abs"]
            if formation_residuals["delta_fG_kJ_per_mol"]
            else None
        )
    cp_source_positive = all(cell.value > 0.0 for cell in cp_cells)
    cp_relative_residuals = [
        (cell, residual, abs(residual) / abs(cell.value))
        for cell, residual in zip(cp_cells, cp_residuals)
        if cell.value > 0.0
    ]
    worst_relative_cp_cell = max(
        cp_relative_residuals, key=lambda item: item[2], default=None
    )
    max_relative_cp = (
        worst_relative_cp_cell[2] if worst_relative_cp_cell is not None else None
    )

    join_errors: list[dict[str, float]] = []
    for left, right in zip(segments, segments[1:]):
        join_K = left.T_max_K
        left_values = _evaluate(left, join_K)[:3]
        right_values = _evaluate(right, join_K)[:3]
        relative = [
            abs(left_value - right_value) / max(abs(left_value), abs(right_value), 1.0)
            for left_value, right_value in zip(left_values, right_values)
        ]
        join_errors.append(
            {
                "T_K": join_K,
                "Cp_relative": relative[0],
                "H_relative": relative[1],
                "S_relative": relative[2],
            }
        )
    join_continuity_max_relative = max(
        (max(item["Cp_relative"], item["H_relative"], item["S_relative"]) for item in join_errors),
        default=0.0,
    )
    join_continuity = join_continuity_max_relative <= 1e-9

    # If |dCp| <= 1e-3 Cp, then dH=int(dCp)dT is bounded by
    # 1e-3 mean(Cp) ΔT (J/mol), and dS=int(dCp/T)dT by
    # 1e-3 mean(Cp) ln(T2/T1) (J/mol/K). For O2, mean Cp≈32 J/mol/K
    # over 300–1000 K gives bounds ≈22.4 J/mol and 0.0386 J/mol/K.
    h_integral_ratios: list[float | None] = []
    s_integral_ratios: list[float | None] = []
    for segment in segments:
        local_cp = [
            cell.value for cell in cp_cells
            if segment.T_min_K <= cell.temperature_K <= segment.T_max_K
        ]
        local_h_errors = [
            residual for cell, residual in zip(h_cells, h_residuals)
            if segment.T_min_K <= cell.temperature_K <= segment.T_max_K
        ]
        local_s_errors = [
            residual for cell, residual in zip(s_cells, s_residuals)
            if segment.T_min_K <= cell.temperature_K <= segment.T_max_K
        ]
        if not local_cp or not local_h_errors or not local_s_errors:
            h_integral_ratios.append(None)
            s_integral_ratios.append(None)
            continue
        mean_cp = float(np.mean(local_cp))
        h_bound_J_per_mol = 1e-3 * mean_cp * (segment.T_max_K - segment.T_min_K)
        s_bound_J_per_mol_K = 1e-3 * mean_cp * math.log(
            segment.T_max_K / segment.T_min_K
        )
        # The residual span is the largest observed integrated dCp over this
        # segment: H residual differences are kJ/mol, S residual differences
        # are J/(mol K), so convert only the H span to J/mol.
        h_error_span_J_per_mol = (max(local_h_errors) - min(local_h_errors)) * 1000.0
        s_error_span_J_per_mol_K = max(local_s_errors) - min(local_s_errors)
        h_integral_ratios.append(h_error_span_J_per_mol / max(h_bound_J_per_mol, 1e-300))
        s_integral_ratios.append(s_error_span_J_per_mol_K / max(s_bound_J_per_mol_K, 1e-300))
    h_integrals_pass = bool(h_integral_ratios) and all(
        ratio is not None and ratio <= 1.0 for ratio in h_integral_ratios
    )
    s_integrals_pass = bool(s_integral_ratios) and all(
        ratio is not None and ratio <= 1.0 for ratio in s_integral_ratios
    )
    ring_pass = not ring_failures
    t1_pass = not failed_cells and ring_pass and positive_cp
    t2_pass = (
        ring_pass
        and positive_cp
        and join_continuity
        and cp_source_positive
        and max_relative_cp is not None
        and max_relative_cp <= 1e-3
        and h_integrals_pass
        and s_integrals_pass
    )
    fit_statistics["smooth_fit_checks"] = {
        "maximum_relative_Cp_residual": max_relative_cp,
        "maximum_H_integral_bound_ratio": max(
            (value for value in h_integral_ratios if value is not None), default=None
        ),
        "maximum_S_integral_bound_ratio": max(
            (value for value in s_integral_ratios if value is not None), default=None
        ),
        "join_continuity_max_relative": join_continuity_max_relative,
        "anti_ringing_passed": ring_pass,
        "positive_Cp": positive_cp,
        "source_Cp_positive": cp_source_positive,
        "only_relative_Cp_residual_failed": (
            max_relative_cp is not None
            and max_relative_cp > 1e-3
            and ring_pass
            and positive_cp
            and cp_source_positive
            and join_continuity
            and h_integrals_pass
            and s_integrals_pass
        ),
        "worst_relative_Cp_cell": (
            {
                "T_K": worst_relative_cp_cell[0].temperature_K,
                "residual_J_per_mol_K": worst_relative_cp_cell[1],
                "relative_residual": worst_relative_cp_cell[2],
            }
            if worst_relative_cp_cell is not None
            else None
        ),
    }

    if not t1_pass and not t2_pass:
        if not positive_cp:
            reason_code = "cp_nonpositive"
            reason = "fitted Cp is not strictly positive over the declared positive interval"
        elif ring_failures:
            reason_code = "interpolation_ringing"
            reason = "NASA-9 Cp leaves the widened range of adjacent printed cells between nodes"
        else:
            reason_code = "smooth_fit_tolerance_exceeded"
            reason = "fit exceeds the smooth-fit Cp or implied integral error bounds"
        return _rejected(
            base,
            reason_code,
            reason,
            fit_statistics=fit_statistics,
            fit_error=fit_error,
            failed_cells=failed_cells,
            ring_failures=ring_failures,
            _cp_node_residuals=cp_node_residuals,
        )

    thermo_segments = [
        {
            "T_min_K": segment.T_min_K,
            "T_max_K": segment.T_max_K,
            "a_coefficients": list(segment.coefficients),
            "b1": segment.b1,
            "b2": segment.b2,
        }
        for segment in segments
    ]
    record_out = {
        **base,
        "status": "accepted",
        "acceptance_tier": "table_equivalent" if t1_pass else "smooth_fit",
        "basis_terms": basis_terms,
        "formation_capable": formation_capable,
        "fit_error": fit_error,
        "fit_method": "weighted_constrained_linear_least_squares_on_scaled_temperature",
        "quantity_convention": {
            "Cp": "J_per_mol_K",
            "H": "J_per_mol",
            "S": "J_per_mol_K",
            "formation_H_G": "kJ_per_mol",
        },
        "fit_statistics": fit_statistics,
        "thermo_record": {
            "evaluator_family": "nasa_cea_9",
            "standard_state": standard_state,
            "standard_state_convention": metadata["standard_state_convention"],
            "formula": formula,
            "reference_pressure_Pa": pressure,
            "segments": thermo_segments,
        },
    }
    if not formation_capable:
        record_out["formation_reason_code"] = formation_reason_code or "formation_unavailable"
        record_out["formation_reason"] = formation_reason or "formation references are unavailable"
    curve = None
    if pressure is not None:
        curve = FittedCurve(
            formula=formula,
            phase_label=phase_label,
            source_id=str(metadata["source_id"]),
            source_record_id=str(metadata["source_record_id"]),
            T_min_K=fit_lo,
            T_max_K=fit_hi,
            reference_pressure_Pa=pressure,
            segment=segments[0],
            is_element_reference=is_element_reference,
            segments=tuple(segments),
        )
    return FitResult(record_out, curve)


def fit_compilation_thermo_range(
    *,
    record: Mapping[str, Any],
    series: Sequence[object],
    phase_label: str,
    standard_state: str,
    T_min_K: float | None,
    T_max_K: float | None,
    is_element_reference: bool = False,
    reference_curves: Mapping[str, Sequence[FittedCurve]] | None = None,
) -> FitResult:
    """Fit a source phase, refining its segmentation only until T2 passes."""

    references = reference_curves or {}
    channels = _channel_cells(series)
    metadata = source_metadata(record)
    base = _record_base(
        metadata=metadata,
        phase_label=phase_label,
        T_min_K=T_min_K,
        T_max_K=T_max_K,
    )
    excluded: list[dict[str, Any]] = []
    in_range_temperatures: list[float] = []
    fit_T_min = max(FIT_T_MIN_K, T_min_K or FIT_T_MIN_K)
    for quantity, cells in channels.items():
        for cell in cells:
            reason = None
            if cell.temperature_K <= 0.0:
                reason = "nonpositive_fit_temperature"
            elif cell.temperature_K < fit_T_min:
                reason = "below_fit_T_min"
            elif T_max_K is not None and cell.temperature_K > T_max_K:
                reason = "above_fit_T_max"
            if reason:
                excluded.append({
                    "quantity": quantity.value,
                    "T_K": cell.temperature_K,
                    "raw_token": cell.raw_token,
                    "reason_code": reason,
                })
            elif quantity in {Quantity.CP, Quantity.H_MINUS_H298, Quantity.S}:
                in_range_temperatures.append(cell.temperature_K)

    def with_exclusions(result: FitResult) -> FitResult:
        if not excluded:
            return result
        output = dict(result.record)
        output["excluded_cells"] = excluded
        return FitResult(output, result.curve)

    if not in_range_temperatures:
        return with_exclusions(_rejected(
            base,
            "no_positive_fit_cells",
            "no printed cell lies in the positive declared fit range",
        ))
    fit_lo = max(fit_T_min, min(in_range_temperatures))
    fit_hi = min(T_max_K or max(in_range_temperatures), max(in_range_temperatures))
    if fit_lo <= 0.0 or fit_hi <= fit_lo:
        return with_exclusions(_rejected(
            base,
            "insufficient_fit_range",
            f"no positive interval remains after exclusions: [{fit_lo}, {fit_hi}] K",
        ))

    cp_fit = tuple(
        cell for cell in channels.get(Quantity.CP, ())
        if fit_lo <= cell.temperature_K <= fit_hi
    )
    cp_nodes = tuple(sorted(cp_fit, key=lambda cell: cell.temperature_K))
    peaks = {
        cp_nodes[index].temperature_K
        for index in range(1, len(cp_nodes) - 1)
        if cp_nodes[index].value >= cp_nodes[index - 1].value
        and cp_nodes[index].value >= cp_nodes[index + 1].value
        and (cp_nodes[index].value > cp_nodes[index - 1].value
             or cp_nodes[index].value > cp_nodes[index + 1].value)
    }
    near_peaks = set(peaks)
    for index, cell in enumerate(cp_nodes):
        if cell.temperature_K in peaks:
            near_peaks.update(
                cp_nodes[neighbor].temperature_K
                for neighbor in (index - 1, index + 1)
                if 0 <= neighbor < len(cp_nodes)
            )

    bounds = [(fit_lo, fit_hi)]
    best: FitResult | None = None
    best_score = math.inf
    any_peak_split = False
    while True:
        candidate = _fit_piecewise_candidate(
            record=record,
            series=series,
            phase_label=phase_label,
            standard_state=standard_state,
            is_element_reference=is_element_reference,
            reference_curves=references,
            segment_bounds=bounds,
            fit_lo=fit_lo,
            fit_hi=fit_hi,
        )
        if candidate.record.get("status") == "accepted":
            return with_exclusions(candidate)

        stats = candidate.record.get("fit_statistics", {})
        smooth = stats.get("smooth_fit_checks", {})
        score = smooth.get("maximum_relative_Cp_residual")
        if score is None:
            score = stats.get("cp_J_per_mol_K", {}).get("max_abs", math.inf)
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = math.inf
        if best is None or score < best_score:
            best, best_score = candidate, score

        reason = candidate.record.get("reason_code")
        if reason == "insufficient_cells":
            return with_exclusions(candidate)
        if reason not in {
            "smooth_fit_tolerance_exceeded",
            "interpolation_ringing",
            "cp_nonpositive",
        }:
            return with_exclusions(candidate)
        if reason == "cp_nonpositive" and not any(
            lower <= peak <= upper for peak in peaks for lower, upper in bounds
        ):
            return with_exclusions(candidate)

        eligible: list[tuple[float, int, float, bool]] = []
        residual_by_temperature = {
            float(item["T_K"]): abs(float(item["residual"]))
            for item in candidate.record.get("_cp_node_residuals", ())
        }
        split_nodes = tuple(
            cell for cell in cp_nodes
            if not any(cell.temperature_K == lower for lower, _upper in bounds[1:])
        )
        for index, (lower, upper) in enumerate(bounds):
            for cell in split_nodes:
                join = cell.temperature_K
                if not lower < join < upper:
                    continue
                left_count = sum(
                    lower <= item.temperature_K <= join
                    for item in channels.get(Quantity.CP, ())
                )
                right_count = sum(
                    join <= item.temperature_K <= upper
                    for item in channels.get(Quantity.CP, ())
                )
                if left_count >= 9 and right_count >= 9:
                    eligible.append((
                        residual_by_temperature.get(join, 0.0),
                        index,
                        join,
                        join in peaks,
                    ))

        gas_1000 = [
            item for item in eligible
            if standard_state == "gas" and item[2] == 1000.0
        ]
        if gas_1000:
            _score, split_index, join_K, is_peak = gas_1000[0]
        elif eligible:
            _score, split_index, join_K, is_peak = max(
                eligible, key=lambda item: (item[0], -item[2])
            )
        else:
            assert best is not None
            result = dict(best.record)
            result.pop("_cp_node_residuals", None)
            best_stats = result.get("fit_statistics", {})
            near_peak_failure = any(
                float(item.get("T_K", -1.0)) in near_peaks
                for item in result.get("failed_cells", ())
            ) or any(
                item.get("T_left_K") in near_peaks or item.get("T_right_K") in near_peaks
                for item in result.get("ring_failures", ())
            )
            if any_peak_split or near_peak_failure:
                code = "lambda_anomaly_unresolved"
                message = "smooth-fit or anti-ringing gate remains unresolved at a printed Cp peak"
            elif best.record.get("ring_failures"):
                code = "interpolation_ringing"
                message = "NASA-9 Cp leaves the widened range of adjacent printed cells between nodes"
            else:
                code = "segmentation_exhausted"
                message = "no printed-node split leaves at least 9 fitted cells in each child segment"
            return with_exclusions(_rejected(
                result,
                code,
                message,
                fit_statistics=best_stats,
                fit_error=result.get("fit_error"),
                failed_cells=result.get("failed_cells", []),
                ring_failures=result.get("ring_failures", []),
                segment_count=best_stats.get("segment_count", len(bounds)),
            ))

        any_peak_split = any_peak_split or is_peak
        start, end = bounds[split_index]
        bounds[split_index : split_index + 1] = [(start, join_K), (join_K, end)]

def render_sidecar(records: Sequence[Mapping[str, Any]]) -> str:
    ordered = sorted(records, key=lambda record: str(record.get("record_id", "")))
    return json.dumps(
        {"schema_version": "compilation_thermo_fits.v1", "records": ordered},
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def _phase_standard_state(phase: str) -> str | None:
    token = phase.lower()
    if token in {"g", "gas"}:
        return "gas"
    if token in {"l", "liquid"}:
        return "condensed_liquid"
    if token in {"cr", "solid", "condensed"}:
        return "condensed_solid"
    return None


def _phase_ranges_from_janaf(record: Mapping[str, Any], source_path: Path) -> list[dict[str, Any]]:
    from simulator.battery.generators.janaf import generate_table

    generated = generate_table(record, source_path=str(source_path))
    ranges: list[dict[str, Any]] = []
    for observation in generated.observations:
        identity = observation.identity
        if not identity.quantity.is_value or identity.quantity.value is not Quantity.CP:
            continue
        points = observation.value.series or ()
        if not points:
            continue
        species = identity.species
        phase = species.phase.value if species.phase.is_value else ""
        polymorph = species.polymorph.value if species.polymorph.is_value else None
        phase_label = str(phase)
        if polymorph is not None:
            phase_label += f":{polymorph}"
        ranges.append(
            {
                "phase_label": phase_label,
                "standard_state": _phase_standard_state(str(phase)),
                "T_min_K": float(min(point[0] for point in points)),
                "T_max_K": float(max(point[0] for point in points)),
            }
        )
    return ranges


def _add_janaf_table(
    *,
    record: Mapping[str, Any],
    path: Path,
    series: Sequence[object],
    is_element_reference: bool,
    reference_curves: Mapping[str, Sequence[FittedCurve]],
) -> tuple[list[dict[str, Any]], list[FittedCurve]]:
    metadata = source_metadata(record)
    ranges = _phase_ranges_from_janaf(record, path)
    if not ranges:
        base = _record_base(
            metadata=metadata,
            phase_label="unknown",
            T_min_K=None,
            T_max_K=None,
        )
        return [
            _rejected(base, "missing_phase_range", "JANAF table has no Cp phase range").record
        ], []
    records: list[dict[str, Any]] = []
    curves: list[FittedCurve] = []
    for phase_range in ranges:
        result = fit_compilation_thermo_range(
            record=record,
            series=series,
            phase_label=phase_range["phase_label"],
            standard_state=phase_range["standard_state"] or "unknown",
            T_min_K=phase_range["T_min_K"],
            T_max_K=phase_range["T_max_K"],
            is_element_reference=is_element_reference,
            reference_curves=reference_curves,
        )
        records.append(dict(result.record))
        if result.curve is not None:
            curves.append(result.curve)
    return records, curves


def _janaf_documents(
    table_ids: set[str] | None, limit: int | None = None
) -> list[tuple[Path, dict[str, Any]]]:
    from simulator.reference_data.janaf import iter_table_paths, load_table_document

    result: list[tuple[Path, dict[str, Any]]] = []
    if limit is not None and limit <= 0:
        return result
    paths = list(iter_table_paths())
    if table_ids is None and limit is not None:
        paths = paths[:limit]
    for path in paths:
        record = load_table_document(path)
        table = record.get("table") or {}
        table_id = str(table.get("table_id") or path.stem)
        if table_ids is None or table_id in table_ids:
            result.append((path, record))
            if table_ids is not None and limit is not None and len(result) >= limit:
                break
    return result


def generate_janaf_sidecar(
    *, table_ids: Sequence[str] | None = None, limit: int | None = None
) -> list[dict[str, Any]]:
    """Generate selected JANAF fits and the elemental-reference curves they need."""

    from simulator.battery.generators.janaf import _element_reference_schedule
    from simulator.battery.migrate import compilation_column_series_from_record
    from simulator.reference_data.janaf import TABLES_DIR

    selected_ids = set(table_ids) if table_ids else None
    selected = _janaf_documents(selected_ids, limit)
    by_id = {
        str((record.get("table") or {}).get("table_id") or path.stem): (path, record)
        for path, record in selected
    }
    support_ids: set[str] = set()
    if selected_ids is not None or limit is not None:
        for _path, record in selected:
            metadata = source_metadata(record)
            composition = formula_composition(str(metadata["formula"]))
            if not composition:
                continue
            for element, _count in composition:
                schedule = _element_reference_schedule(element)
                if schedule is not None:
                    support_ids.add(schedule.table_id)
        missing_support = support_ids - set(by_id)
        for table_id in sorted(missing_support):
            path = TABLES_DIR / f"{table_id}.yaml"
            if path.is_file():
                from simulator.reference_data.janaf import load_table_document

                by_id[table_id] = (path, load_table_document(path))

    from simulator.battery.migrate import compilation_column_series_from_record

    def is_ref_table(record: Mapping[str, Any]) -> bool:
        table = record.get("table") or {}
        entry = table.get("index_entry") or {}
        formula = str(entry.get("formula_normalised") or "")
        composition = formula_composition(formula)
        return entry.get("state") == "ref" and composition is not None and len(composition) == 1

    # Fit reference tables first so every compound conversion uses this source's
    # own standard-state phase curves and pressure convention.
    all_records: list[dict[str, Any]] = []
    reference_curves: dict[str, list[FittedCurve]] = {}
    for table_id in sorted(by_id):
        path, record = by_id[table_id]
        if not is_ref_table(record):
            continue
        series = compilation_column_series_from_record(record)
        fits, curves = _add_janaf_table(
            record=record,
            path=path,
            series=series,
            is_element_reference=True,
            reference_curves={},
        )
        all_records.extend(fits)
        formula = source_metadata(record)["formula"]
        composition = formula_composition(formula) or ()
        if len(composition) == 1:
            reference_curves.setdefault(composition[0][0], []).extend(curves)

    for table_id in sorted(by_id):
        path, record = by_id[table_id]
        if is_ref_table(record):
            continue
        series = compilation_column_series_from_record(record)
        fits, _curves = _add_janaf_table(
            record=record,
            path=path,
            series=series,
            is_element_reference=False,
            reference_curves=reference_curves,
        )
        all_records.extend(fits)
    return sorted(all_records, key=lambda record: str(record.get("record_id", "")))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tables",
        "--table-id",
        dest="table_ids",
        nargs="+",
        action="extend",
        help="JANAF table ids to fit; reference tables are added as needed",
    )
    parser.add_argument(
        "--limit", type=int, help="fit at most N selected tables in sorted table-id order"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_SIDECAR_PATH)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be nonnegative")
    records = generate_janaf_sidecar(table_ids=args.table_ids, limit=args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_sidecar(records), encoding="utf-8")
    accepted = [record for record in records if record.get("status") == "accepted"]
    tier_counts = {
        tier: sum(record.get("acceptance_tier") == tier for record in accepted)
        for tier in ("table_equivalent", "smooth_fit")
    }
    rejected = [record for record in records if record.get("status") == "rejected"]
    reasons: dict[str, int] = {}
    for record in rejected:
        reason = str(record.get("reason_code", "unknown"))
        reasons[reason] = reasons.get(reason, 0) + 1
    cp_only_rejections = [
        record
        for record in rejected
        if record.get("fit_statistics", {}).get("smooth_fit_checks", {}).get(
            "only_relative_Cp_residual_failed"
        ) is True
    ]
    relaxed_cp_counts = {
        label: sum(
            float(record["fit_statistics"]["smooth_fit_checks"][
                "maximum_relative_Cp_residual"
            ]) <= threshold
            for record in cp_only_rejections
        )
        for label, threshold in (("2e-3", 2e-3), ("5e-3", 5e-3), ("1e-2", 1e-2))
    }
    worst_cp_only = sorted(
        cp_only_rejections,
        key=lambda record: float(
            record["fit_statistics"]["smooth_fit_checks"][
                "maximum_relative_Cp_residual"
            ]
        ),
        reverse=True,
    )[:10]
    worst_cp_only_report = [
        {
            "formula": record.get("formula"),
            "phase": record.get("phase"),
            **record["fit_statistics"]["smooth_fit_checks"][
                "worst_relative_Cp_cell"
            ],
        }
        for record in worst_cp_only
    ]
    error_keys = (
        "Cp_J_per_mol_K",
        "H_minus_H298_kJ_per_mol",
        "S_J_per_mol_K",
        "delta_fH_kJ_per_mol",
        "delta_fG_kJ_per_mol",
    )
    maximum_errors = {
        key: max(
            (
                value
                for record in accepted
                if (value := record.get("fit_error", {}).get(key)) is not None
            ),
            default=None,
        )
        for key in error_keys
    }
    formation_capable_count = sum(
        record.get("formation_capable") is True for record in accepted
    )
    print(
        f"wrote {len(records)} fit records "
        f"({tier_counts['table_equivalent']} T1, {tier_counts['smooth_fit']} T2, "
        f"{len(rejected)} rejected; formation_capable={formation_capable_count}; "
        f"reasons={reasons}; max_fit_error={maximum_errors}; "
        f"Cp_tolerance_only={len(cp_only_rejections)}; "
        f"would_pass_at={relaxed_cp_counts}; worst_Cp_tolerance_only={worst_cp_only_report}) "
        f"to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
