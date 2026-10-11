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
        scale_K * math.log(x),
        scale_K * x,
        scale_K * x**2 / 2.0,
        scale_K * x**3 / 3.0,
        scale_K * x**4 / 4.0,
        scale_K * x**5 / 5.0,
    )


def _fit_basis_s_over_R(x: float) -> tuple[float, ...]:
    return (-0.5 / x**2, -1.0 / x, math.log(x), x, x**2 / 2.0, x**3 / 3.0, x**4 / 4.0)


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
        return None, "unsupported_formula"
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
                return None, "pressure_convention_mismatch"
            return None, "missing_element_reference_phase"
        if len(pressure_matches) > 1:
            # At a shared phase boundary the right-hand source interval is the
            # JANAF reference schedule's phase at T; otherwise fail closed.
            starts_at_T = [
                curve for curve in pressure_matches if curve.T_min_K == temperature_K
            ]
            if len(starts_at_T) == 1:
                pressure_matches = starts_at_T
            else:
                return None, "ambiguous_element_reference_phase"
        curve = pressure_matches[0]
        reference_composition = formula_composition(curve.formula)
        if (
            not reference_composition
            or len(reference_composition) != 1
            or reference_composition[0][0] != element
            or reference_composition[0][1] <= 0
        ):
            return None, "missing_element_reference_phase"
        coefficient = float(atom_count / reference_composition[0][1])
        _cp, h, _s, g = _evaluate(curve.segment, temperature_K)
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
    """Fit one source species phase/range and reject it unless every gate passes."""

    metadata = source_metadata(record)
    base = _record_base(
        metadata=metadata,
        phase_label=phase_label,
        T_min_K=T_min_K,
        T_max_K=T_max_K,
    )
    reference_curves = reference_curves or {}
    pressure = metadata["reference_pressure_Pa"]
    if pressure is None:
        return _rejected(
            base,
            "missing_pressure_convention",
            "source does not declare a standard pressure convention",
        )
    if standard_state not in {"gas", "condensed_solid", "condensed_liquid", "condensed"}:
        return _rejected(
            base,
            "missing_element_reference_phase",
            f"source phase {phase_label!r} has no closed NASA-9 standard-state mapping",
        )
    channels = _channel_cells(series)
    cp_cells = channels.get(Quantity.CP, ())
    h_cells = channels.get(Quantity.H_MINUS_H298, ())
    s_cells = channels.get(Quantity.S, ())
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

    excluded: list[dict[str, Any]] = []
    fit_cells = {
        Quantity.CP: cp_cells,
        Quantity.H_MINUS_H298: h_cells,
        Quantity.S: s_cells,
    }
    all_cells = {
        **fit_cells,
        Quantity.DELTA_FH: channels.get(Quantity.DELTA_FH, ()),
        Quantity.DELTA_FG: channels.get(Quantity.DELTA_FG, ()),
    }
    positive_temperatures: list[float] = []
    for quantity, cells in all_cells.items():
        for cell in cells:
            reason = None
            if cell.temperature_K <= 0.0:
                reason = "nonpositive_fit_temperature"
            elif T_min_K is not None and T_min_K > 0 and cell.temperature_K < T_min_K:
                reason = "below_fit_T_min"
            elif T_max_K is not None and cell.temperature_K > T_max_K:
                reason = "above_fit_T_max"
            if reason:
                excluded.append(
                    {
                        "quantity": quantity.value,
                        "T_K": cell.temperature_K,
                        "raw_token": cell.raw_token,
                        "reason_code": reason,
                    }
                )
            elif quantity in fit_cells:
                positive_temperatures.append(cell.temperature_K)
    if not positive_temperatures:
        return _rejected(
            base,
            "no_positive_fit_cells",
            "no printed cell lies in the positive declared fit range",
            excluded_cells=excluded,
        )

    fit_lo = max(T_min_K or min(positive_temperatures), min(positive_temperatures))
    fit_hi = min(T_max_K or max(positive_temperatures), max(positive_temperatures))
    if fit_lo <= 0.0 or fit_hi <= fit_lo:
        return _rejected(
            base,
            "insufficient_fit_range",
            f"no positive interval remains after exclusions: [{fit_lo}, {fit_hi}] K",
            excluded_cells=excluded,
        )

    def in_range(cells: Sequence[_Cell]) -> list[_Cell]:
        return [cell for cell in cells if fit_lo <= cell.temperature_K <= fit_hi]

    cp_fit = in_range(cp_cells)
    h_fit = in_range(h_cells)
    s_fit = in_range(s_cells)
    used_cells = [("cp", cp_fit), ("H-H298", h_fit), ("S", s_fit)]
    missing_precision = [
        name
        for name, cells in used_cells
        if any(cell.raw_token is None or cell.uncertainty is None for cell in cells)
    ]
    if missing_precision:
        return _rejected(
            base,
            "missing_printed_precision",
            "one or more fit cells lack a raw token or converted rounding allowance",
            channels=missing_precision,
            excluded_cells=excluded,
        )

    s_anchor = next(
        (cell for cell in s_fit if cell.temperature_K == REFERENCE_T_K),
        s_fit[0] if s_fit else None,
    )
    if s_anchor is None:
        return _rejected(
            base,
            "missing_absolute_entropy_anchor",
            "no absolute entropy cell is available in the fitted phase range",
            excluded_cells=excluded,
        )

    delta_fh_anchor = next(
        (
            cell
            for cell in channels.get(Quantity.DELTA_FH, ())
            if cell.temperature_K == REFERENCE_T_K
        ),
        None,
    )
    if delta_fh_anchor is None or delta_fh_anchor.raw_token is None:
        return _rejected(
            base,
            "missing_formation_h_anchor",
            "source has no printed ΔfH°(298.15 K) anchor",
            excluded_cells=excluded,
        )

    formula = str(metadata["formula"])
    composition = formula_composition(formula)
    if not composition:
        return _rejected(
            base,
            "unsupported_formula",
            f"cannot resolve elemental composition for {formula!r}",
            excluded_cells=excluded,
        )
    anchor_error = None
    if is_element_reference:
        h_anchor_J = 0.0
    else:
        anchor_terms, anchor_error = _element_reference_terms(
            formula, REFERENCE_T_K, pressure, reference_curves
        )
        if anchor_error:
            # H-H298 residuals do not depend on the absolute integration
            # constant. Solve their common basis to report fit quality, then
            # reject before producing an absolute-H/G-capable curve.
            h_anchor_J = 0.0
        else:
            assert anchor_terms is not None
            h_anchor_J = float(delta_fh_anchor.value) * 1000.0 + sum(
                coefficient * h_ref for coefficient, h_ref, _g_ref in anchor_terms
            )

    scale_K = math.sqrt(fit_lo * fit_hi)
    s_anchor_value = s_anchor.value
    s_anchor_unc = s_anchor.uncertainty
    assert s_anchor_unc is not None
    design_rows: list[tuple[float, ...]] = []
    targets: list[float] = []
    weights: list[float] = []

    def add_row(row: Sequence[float], target: float, uncertainty: float) -> None:
        if not math.isfinite(uncertainty) or uncertainty <= 0.0:
            raise ValueError("fit uncertainty must be finite and positive")
        design_rows.append(tuple(row))
        targets.append(target)
        weights.append(1.0 / (uncertainty / R_J_PER_MOL_K))

    try:
        for cell in cp_fit:
            assert cell.uncertainty is not None
            x = cell.temperature_K / scale_K
            add_row(_fit_basis_cp(x), cell.value / R_J_PER_MOL_K, cell.uncertainty)
        for cell in h_fit:
            assert cell.uncertainty is not None
            if cell.temperature_K == REFERENCE_T_K:
                if abs(cell.value) > cell.uncertainty:
                    return _rejected(
                        base,
                        "h_increment_anchor_nonzero",
                        "printed H-H298 cell disagrees with the required zero anchor",
                        excluded_cells=excluded,
                    )
                continue
            x = cell.temperature_K / scale_K
            x_ref = REFERENCE_T_K / scale_K
            row = np.subtract(
                _fit_basis_h_over_R(x, scale_K),
                _fit_basis_h_over_R(x_ref, scale_K),
            )
            add_row(row, cell.value * 1000.0 / R_J_PER_MOL_K, cell.uncertainty * 1000.0)
        for cell in s_fit:
            assert cell.uncertainty is not None
            if cell is s_anchor:
                continue
            x = cell.temperature_K / scale_K
            x_anchor = s_anchor.temperature_K / scale_K
            row = np.subtract(_fit_basis_s_over_R(x), _fit_basis_s_over_R(x_anchor))
            add_row(
                row,
                (cell.value - s_anchor_value) / R_J_PER_MOL_K,
                cell.uncertainty + s_anchor_unc,
            )
    except ValueError as exc:
        return _rejected(
            base,
            "missing_printed_precision",
            str(exc),
            excluded_cells=excluded,
        )

    matrix = np.asarray(design_rows, dtype=float)
    target = np.asarray(targets, dtype=float)
    weight = np.asarray(weights, dtype=float)
    weighted = matrix * weight[:, None]
    weighted_target = target * weight
    if matrix.shape[0] < 7 or np.linalg.matrix_rank(weighted) < 7:
        return _rejected(
            base,
            "insufficient_cells",
            f"NASA-9 basis rank is {np.linalg.matrix_rank(weighted)}; seven are required",
            cell_count=int(matrix.shape[0]),
            excluded_cells=excluded,
        )
    scaled_coefficients, *_ = np.linalg.lstsq(weighted, weighted_target, rcond=None)
    A1, A2, A3, A4, A5, A6, A7 = (float(value) for value in scaled_coefficients)
    # Convert x=T/Tscale coefficients back to the evaluator's kelvin basis.
    coefficients = (
        A1 * scale_K**2,
        A2 * scale_K,
        A3,
        A4 / scale_K,
        A5 / scale_K**2,
        A6 / scale_K**3,
        A7 / scale_K**4,
    )
    # Evaluate the zero-constant polynomial through the existing NASA-9 owner.
    # H/R is in kelvin and S/R is dimensionless, so each anchor subtracts its
    # corresponding ratio value before setting b1 and b2.
    unanchored = Nasa9Segment(
        T_min_K=fit_lo,
        T_max_K=fit_hi,
        coefficients=coefficients,
        b1=0.0,
        b2=0.0,
    )
    _cp_R, h_RT_without_b1, _s_R, _g_RT = unanchored.evaluate_ratios(
        REFERENCE_T_K
    )
    b1 = h_anchor_J / R_J_PER_MOL_K - h_RT_without_b1 * REFERENCE_T_K
    _cp_R, _h_RT, s_R_without_b2, _g_RT = unanchored.evaluate_ratios(
        s_anchor.temperature_K
    )
    b2 = s_anchor_value / R_J_PER_MOL_K - s_R_without_b2
    segment = Nasa9Segment(fit_lo, fit_hi, coefficients, b1, b2)
    if not _check_positive_cp(segment):
        return _rejected(
            base,
            "cp_nonpositive",
            "fitted Cp is not strictly positive over the declared positive interval",
            excluded_cells=excluded,
        )

    cp_residuals: list[float] = []
    h_residuals: list[float] = []
    s_residuals: list[float] = []
    over_precision: list[dict[str, Any]] = []
    for cell in cp_fit:
        residual = _evaluate(segment, cell.temperature_K)[0] - cell.value
        cp_residuals.append(residual)
        if cell.uncertainty is None or abs(residual) > cell.uncertainty:
            over_precision.append({"quantity": "cp", "T_K": cell.temperature_K, "residual": residual})
    for cell in h_fit:
        _cp, h, _s, _g = _evaluate(segment, cell.temperature_K)
        h_ref = _evaluate(segment, REFERENCE_T_K)[1]
        residual = (h - h_ref) / 1000.0 - cell.value
        h_residuals.append(residual)
        if cell.uncertainty is None or abs(residual) > cell.uncertainty:
            over_precision.append({"quantity": "H-H298", "T_K": cell.temperature_K, "residual": residual})
    for cell in s_fit:
        residual = _evaluate(segment, cell.temperature_K)[2] - cell.value
        s_residuals.append(residual)
        if cell.uncertainty is None or abs(residual) > cell.uncertainty:
            over_precision.append({"quantity": "S", "T_K": cell.temperature_K, "residual": residual})
    rounding_allowances = {
        name: max((cell.uncertainty or 0.0 for cell in cells), default=0.0)
        for name, cells in (
            ("Cp_J_per_mol_K", cp_fit),
            ("H_minus_H298_kJ_per_mol", h_fit),
            ("S_J_per_mol_K", s_fit),
            (
                "delta_fH_kJ_per_mol",
                in_range(channels.get(Quantity.DELTA_FH, ())),
            ),
            (
                "delta_fG_kJ_per_mol",
                in_range(channels.get(Quantity.DELTA_FG, ())),
            ),
        )
    }
    fit_statistics = {
        "cp_J_per_mol_K": _residual_stats(cp_residuals),
        "H_minus_H298_kJ_per_mol": _residual_stats(h_residuals),
        "S_J_per_mol_K": _residual_stats(s_residuals),
        "maximum_printed_rounding_allowance": rounding_allowances,
    }
    if anchor_error:
        return _rejected(
            base,
            anchor_error,
            f"cannot resolve source elemental references at {REFERENCE_T_K:g} K",
            fit_statistics=fit_statistics,
            excluded_cells=excluded,
        )

    formation_residuals: dict[str, list[float]] = {"delta_fH_kJ_mol": [], "delta_fG_kJ_mol": []}
    formation_over_precision: list[dict[str, Any]] = []
    for quantity, result_key in (
        (Quantity.DELTA_FH, "delta_fH_kJ_mol"),
        (Quantity.DELTA_FG, "delta_fG_kJ_mol"),
    ):
        for cell in channels.get(quantity, ()):
            if not fit_lo <= cell.temperature_K <= fit_hi:
                continue
            if cell.raw_token is None or cell.uncertainty is None:
                return _rejected(
                    base,
                    "missing_printed_precision",
                    f"{quantity.value} check cell at {cell.temperature_K:g} K lacks precision metadata",
                    fit_statistics=fit_statistics,
                    excluded_cells=excluded,
                )
            _cp, h, _s, g = _evaluate(segment, cell.temperature_K)
            if is_element_reference:
                species_property = h if quantity is Quantity.DELTA_FH else g
                references = [(1.0, species_property)]
            else:
                terms, error = _element_reference_terms(
                    formula, cell.temperature_K, pressure, reference_curves
                )
                if error:
                    return _rejected(
                        base,
                        error,
                        f"cannot resolve source elemental references at {cell.temperature_K:g} K",
                        fit_statistics=fit_statistics,
                        excluded_cells=excluded,
                    )
                assert terms is not None
                references = [
                    (coefficient, h_ref if quantity is Quantity.DELTA_FH else g_ref)
                    for coefficient, h_ref, g_ref in terms
                ]
                species_property = h if quantity is Quantity.DELTA_FH else g
            derived = formation_subtraction_kJ_mol(species_property, references)
            residual = derived - cell.value
            formation_residuals[result_key].append(residual)
            if abs(residual) > cell.uncertainty:
                formation_over_precision.append(
                    {"quantity": quantity.value, "T_K": cell.temperature_K, "residual": residual}
                )

    fit_statistics["derived_formation_H_kJ_per_mol"] = _residual_stats(
        formation_residuals["delta_fH_kJ_mol"]
    )
    fit_statistics["derived_formation_G_kJ_per_mol"] = _residual_stats(
        formation_residuals["delta_fG_kJ_mol"]
    )
    if over_precision or formation_over_precision:
        return _rejected(
            base,
            "residual_exceeds_printed_precision",
            "one or more fitted or derived cells exceed their printed rounding allowance",
            fit_statistics=fit_statistics,
            failed_cells=over_precision + formation_over_precision,
            excluded_cells=excluded,
        )

    curve = FittedCurve(
        formula=formula,
        phase_label=phase_label,
        source_id=str(metadata["source_id"]),
        source_record_id=str(metadata["source_record_id"]),
        T_min_K=fit_lo,
        T_max_K=fit_hi,
        reference_pressure_Pa=pressure,
        segment=segment,
        is_element_reference=is_element_reference,
    )
    thermo_record = {
        "evaluator_family": "nasa_cea_9",
        "standard_state": standard_state,
        "standard_state_convention": metadata["standard_state_convention"],
        "formula": formula,
        "reference_pressure_Pa": pressure,
        "segments": [
            {
                "T_min_K": fit_lo,
                "T_max_K": fit_hi,
                "a_coefficients": list(coefficients),
                "b1": b1,
                "b2": b2,
            }
        ],
    }
    record_out = {
        **base,
        "record_id": base["record_id"],
        "status": "accepted",
        "fit_method": "weighted_linear_least_squares_on_scaled_temperature",
        "quantity_convention": {
            "Cp": "J_per_mol_K",
            "H": "J_per_mol",
            "S": "J_per_mol_K",
            "formation_H_G": "kJ_per_mol",
        },
        "fit_statistics": fit_statistics,
        "excluded_cells": excluded,
        "thermo_record": thermo_record,
    }
    return FitResult(record_out, curve)


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


def _janaf_documents(table_ids: set[str] | None) -> list[tuple[Path, dict[str, Any]]]:
    from simulator.reference_data.janaf import iter_table_paths, load_table_document

    result: list[tuple[Path, dict[str, Any]]] = []
    for path in iter_table_paths():
        record = load_table_document(path)
        table = record.get("table") or {}
        table_id = str(table.get("table_id") or path.stem)
        if table_ids is None or table_id in table_ids:
            result.append((path, record))
    return result


def generate_janaf_sidecar(
    *, table_ids: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """Generate selected JANAF fits and the elemental-reference curves they need."""

    from simulator.battery.generators.janaf import _element_reference_schedule
    from simulator.battery.migrate import compilation_column_series_from_record
    from simulator.reference_data.janaf import TABLES_DIR

    selected_ids = set(table_ids) if table_ids else None
    selected = _janaf_documents(selected_ids)
    by_id = {
        str((record.get("table") or {}).get("table_id") or path.stem): (path, record)
        for path, record in selected
    }
    support_ids: set[str] = set()
    if selected_ids is not None:
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
        "--table-id",
        action="append",
        help="JANAF table id to fit (may be repeated); default selects the whole JANAF compilation",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_SIDECAR_PATH)
    args = parser.parse_args(argv)
    records = generate_janaf_sidecar(table_ids=args.table_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_sidecar(records), encoding="utf-8")
    accepted = sum(record.get("status") == "accepted" for record in records)
    rejected = len(records) - accepted
    print(f"wrote {len(records)} fit records ({accepted} accepted, {rejected} rejected) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
