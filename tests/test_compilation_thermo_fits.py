from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import math
from decimal import Decimal

import pytest

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import (
    _convert_compilation_amount,
    _printed_rounding_allowance,
    _projected_cell_metadata,
    compilation_column_series_from_record,
)
from simulator.battery.records import State, Value
from simulator.diagnostic_helpers.gibbs_battery import formation_subtraction_kJ_mol
from simulator.battery.generators.compilation_thermo_fits import (
    _Cell,
    _add_janaf_table,
    FittedCurve,
    _element_reference_terms,
    _evaluate,
    _fit_piecewise_candidate,
    _interpolation_ringing,
    fit_compilation_thermo_range,
    evaluate_sidecar_fit,
    generate_janaf_sidecar,
    main as fit_cli_main,
    render_sidecar,
    source_metadata,
)
from simulator.battery.generators.janaf import generate_table
from simulator.reference_data.janaf import TABLES_DIR, load_table_document
from simulator.vapour_rail.nasa_cea import Nasa9Segment, R_J_PER_MOL_K
import simulator.battery.generators.compilation_thermo_fits as compilation_thermo_fits


PRESSURE_PA = 101325.0
PRECISION = Decimal("0.0000000005")


def _record(formula: str, *, state: str = "cr") -> dict:
    return {
        "source_id": "synthetic-source",
        "table": {
            "table_id": f"{formula}-synthetic",
            "reference_pressure_Pa": PRESSURE_PA,
            "index_entry": {"formula_normalised": formula, "state": state},
        },
    }


def _series(quantity: Quantity, pairs: list[tuple[float, float]], unit: str, *, places: int = 9):
    tokens = tuple(f"{value:.{places}f}" for _temperature, value in pairs)
    values = tuple(
        (Decimal(str(temperature)), Decimal(token))
        for (temperature, _value), token in zip(pairs, tokens)
    )
    return _column_series(
        quantity,
        tokens,
        values,
        unit,
        precision=Decimal(5).scaleb(-(places + 1)),
    )


def _column_series(quantity, tokens, values, unit, *, precision=PRECISION):
    from simulator.battery.migrate import CompilationColumnSeries

    return CompilationColumnSeries(
        quantity=quantity,
        series_key=quantity.value,
        value=Value(ValueKind.SERIES, series=values),
        temperature_K=State.unknown("temperature is a series coordinate"),
        raw_tokens=tokens,
        source_units=(unit,) * len(values),
        cell_uncertainties=(precision,) * len(values),
    )


def _constant_cp_segment(
    cp_J_per_mol_K: float,
    T_min: float,
    T_max: float,
    *,
    H_at_anchor_J_per_mol: float = 0.0,
    S_at_anchor_J_per_mol_K: float = 100.0,
    T_anchor: float = 298.15,
) -> Nasa9Segment:
    cp_R = cp_J_per_mol_K / R_J_PER_MOL_K
    coefficients = (0.0, 0.0, cp_R, 0.0, 0.0, 0.0, 0.0)
    b1 = H_at_anchor_J_per_mol / R_J_PER_MOL_K - cp_R * T_anchor
    b2 = S_at_anchor_J_per_mol_K / R_J_PER_MOL_K - cp_R * math.log(T_anchor)
    return Nasa9Segment(T_min, T_max, coefficients, b1, b2)


def _curve(formula, phase, record_id, segment, t_min, t_max):
    return FittedCurve(
        formula=formula,
        phase_label=phase,
        source_id="synthetic-source",
        source_record_id=record_id,
        T_min_K=t_min,
        T_max_K=t_max,
        reference_pressure_Pa=PRESSURE_PA,
        segment=segment,
        is_element_reference=True,
        fit_error={
            "H_minus_H298_kJ_per_mol": 0.0,
            "S_J_per_mol_K": 0.0,
        },
    )


def _source_series(
    segment,
    temperatures,
    *,
    reference_curves=None,
    formula="Fe",
    is_reference=True,
    places=9,
):
    cp_pairs = []
    h_pairs = []
    s_pairs = []
    fh_pairs = []
    fg_pairs = []
    h298 = _evaluate(segment, 298.15)[1]
    for temperature in temperatures:
        if temperature == 0:
            cp, h, entropy, gibbs = 0.0, 0.0, 0.0, 0.0
        else:
            cp, h, entropy, gibbs = _evaluate(segment, temperature)
        cp_pairs.append((temperature, cp))
        h_pairs.append((temperature, (h - h298) / 1000.0))
        s_pairs.append((temperature, entropy))
        if is_reference:
            fh, fg = 0.0, 0.0
        else:
            terms, error = _element_reference_terms(
                formula, temperature, PRESSURE_PA, reference_curves or {}
            )
            assert error is None and terms is not None
            fh = formation_subtraction_kJ_mol(
                h,
                [
                    (coefficient, h_ref)
                    for coefficient, h_ref, _g_ref, _curve in terms
                ],
            )
            fg = formation_subtraction_kJ_mol(
                gibbs,
                [
                    (coefficient, g_ref)
                    for coefficient, _h_ref, g_ref, _curve in terms
                ],
            )
        fh_pairs.append((temperature, fh))
        fg_pairs.append((temperature, fg))
    return (
        _series(Quantity.CP, cp_pairs, "J mol^-1 K^-1", places=places),
        _series(Quantity.H_MINUS_H298, h_pairs, "kJ mol^-1", places=places),
        _series(Quantity.S, s_pairs, "J mol^-1 K^-1", places=places),
        _series(Quantity.DELTA_FH, fh_pairs, "kJ mol^-1", places=places),
        _series(Quantity.DELTA_FG, fg_pairs, "kJ mol^-1", places=places),
    )


def _transition_reference_curves():
    alpha = _constant_cp_segment(
        25.0, 200.0, 1000.0, H_at_anchor_J_per_mol=0.0,
        S_at_anchor_J_per_mol_K=27.3,
    )
    at_transition = _evaluate(alpha, 1000.0)
    gamma = _constant_cp_segment(
        35.0, 1000.0, 2200.0, H_at_anchor_J_per_mol=at_transition[1],
        S_at_anchor_J_per_mol_K=at_transition[2], T_anchor=1000.0,
    )
    oxygen = _constant_cp_segment(
        29.0, 200.0, 2200.0, H_at_anchor_J_per_mol=0.0,
        S_at_anchor_J_per_mol_K=205.0,
    )
    return {
        "Fe": [
            _curve("Fe", "alpha", "Fe-alpha", alpha, 200.0, 1000.0),
            _curve("Fe", "gamma", "Fe-gamma", gamma, 1000.0, 2200.0),
        ],
        "O": [_curve("O2", "gas", "O2-ref", oxygen, 200.0, 2200.0)],
    }


def test_printed_token_precision_and_exact_unit_conversion() -> None:
    assert _printed_rounding_allowance("1.2300") == Decimal("0.00005")
    assert _convert_compilation_amount(
        Decimal("1.00"), Quantity.CP, "cal mol^-1 K^-1"
    ) == (Decimal("4.18400"), "thermochemical_calorie_to_J_exact")
    token, unit, uncertainty = _projected_cell_metadata(
        {"value": "1.230", "as_published": "1.230"},
        Quantity.CP,
        "cal mol^-1 K^-1",
    )
    assert (token, unit) == ("1.230", "cal mol^-1 K^-1")
    assert uncertainty == Decimal("0.002092")


def test_nested_compilation_projection_keeps_aligned_raw_metadata() -> None:
    document = {
        "table": {
            "units_as_published": {
                "temperature": "K",
                "heat_capacity": "cal mol^-1 K^-1",
            },
            "values": [
                {
                    "temperature": {"value": "298.15", "as_published": "298.15"},
                    "heat_capacity": {"value": "1.20", "as_published": "1.20"},
                },
                {
                    "temperature": {"value": "400", "as_published": "400"},
                    "heat_capacity": {"value": "1.30", "as_published": "1.30"},
                },
            ],
        }
    }
    (cp,) = compilation_column_series_from_record(document)
    assert cp.raw_tokens == ("1.20", "1.30")
    assert cp.source_units == ("cal mol^-1 K^-1",) * 2
    assert cp.cell_uncertainties == (Decimal("0.020920"), Decimal("0.020920"))
    assert cp.value.series == ((Decimal("298.15"), Decimal("5.02080")), (Decimal("400"), Decimal("5.43920")))


def test_sidecar_rendering_is_deterministic_and_sorted() -> None:
    temperatures = [298.15, 350.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0]
    segment = _constant_cp_segment(30.0, 298.15, 1000.0)

    def generate_record():
        result = fit_compilation_thermo_range(
            record=_record("Fe", state="ref"),
            series=_source_series(segment, temperatures, places=0),
            phase_label="cr",
            standard_state="condensed_solid",
            T_min_K=298.15,
            T_max_K=1000.0,
            is_element_reference=True,
        )
        assert result.record["status"] == "accepted"
        assert result.record["acceptance_tier"] == "table_equivalent"
        return result.record

    first = [generate_record(), {"record_id": "zz", "status": "rejected"}]
    second = [generate_record(), {"record_id": "zz", "status": "rejected"}]
    rendered = render_sidecar(first)
    assert rendered == render_sidecar(list(reversed(second)))
    document = json.loads(rendered)
    assert document["schema_version"] == "compilation_thermo_fits.v2"
    serialized_fit = document["records"][0]
    assert len(serialized_fit["generator_commit"]) == 40
    assert serialized_fit["input_digests"]
    assert serialized_fit["residual_summary"][Quantity.CP.value][
        "gate_tolerance"
    ] is not None
    assert "fit_statistics" not in serialized_fit
    assert "fit_error" not in serialized_fit
    assert "basis_terms" not in serialized_fit


def test_compound_fit_anchors_and_formation_acceptance_across_fe_reference_transition() -> None:
    references = _transition_reference_curves()
    terms_at_anchor, error = _element_reference_terms("FeO", 298.15, PRESSURE_PA, references)
    assert error is None and terms_at_anchor is not None
    elemental_H298 = sum(
        coefficient * h for coefficient, h, _g, _curve in terms_at_anchor
    )
    species = _constant_cp_segment(
        90.0, 298.15, 2200.0,
        H_at_anchor_J_per_mol=12500.0 + elemental_H298,
        S_at_anchor_J_per_mol_K=58.0,
    )
    temperatures = [298.15, 400.0, 700.0, 999.0, 1000.0, 1100.0, 1500.0, 1900.0, 2200.0]
    result = fit_compilation_thermo_range(
        record=_record("FeO"),
        series=_source_series(species, temperatures, reference_curves=references, formula="FeO", is_reference=False),
        phase_label="cr",
        standard_state="condensed_solid",
        T_min_K=298.15,
        T_max_K=2200.0,
        reference_curves=references,
    )
    assert result.record["status"] == "accepted", result.record
    assert result.record["acceptance_tier"] == "table_equivalent"
    assert result.record["formation_capable"] is True
    assert set(result.record["fit_error"]) == {
        "Cp_J_per_mol_K",
        "H_minus_H298_kJ_per_mol",
        "S_J_per_mol_K",
        "delta_fH_kJ_per_mol",
        "delta_fG_kJ_per_mol",
    }
    assert result.curve is not None
    _cp, h_rt, s_r, _g = result.curve.segment.evaluate_ratios(298.15)
    assert abs(h_rt * R_J_PER_MOL_K * 298.15 - (12500.0 + elemental_H298)) < 1e-6
    assert abs(s_r * R_J_PER_MOL_K - 58.0) < 1e-8
    stats = result.record["fit_statistics"]
    assert stats["derived_formation_H_kJ_per_mol"]["max_abs"] <= stats[
        "maximum_printed_rounding_allowance"
    ]["delta_fH_kJ_per_mol"]
    assert stats["derived_formation_G_kJ_per_mol"]["max_abs"] <= stats[
        "maximum_printed_rounding_allowance"
    ]["delta_fG_kJ_per_mol"]
    assert stats["derived_formation_H_kJ_per_mol"]["max_abs"] < 5e-9
    assert stats["derived_formation_G_kJ_per_mol"]["max_abs"] < 5e-9
    assert stats["maximum_printed_rounding_allowance"]["Cp_J_per_mol_K"] == 5e-10
    assert stats["maximum_printed_rounding_allowance"]["delta_fH_kJ_per_mol"] == 5e-10

    fe_at_transition, error = _element_reference_terms("FeO", 1000.0, PRESSURE_PA, references)
    assert error is None and fe_at_transition is not None
    assert fe_at_transition[0][1] == _evaluate(references["Fe"][1].segment, 1000.0)[1]


def test_pressure_convention_mismatch_disables_formation_without_rejecting_fit() -> None:
    references = _transition_reference_curves()
    terms, error = _element_reference_terms("FeO", 298.15, PRESSURE_PA, references)
    assert error is None and terms is not None
    species = _constant_cp_segment(
        90.0,
        298.15,
        2200.0,
        H_at_anchor_J_per_mol=12500.0
        + sum(c * h for c, h, _g, _curve in terms),
        S_at_anchor_J_per_mol_K=58.0,
    )
    series = _source_series(
        species,
        [298.15, 400, 700, 999, 1000, 1100, 1500, 1900, 2200],
        reference_curves=references,
        formula="FeO",
        is_reference=False,
    )
    wrong_pressure = {
        element: [
            FittedCurve(
                curve.formula,
                curve.phase_label,
                curve.source_id,
                curve.source_record_id,
                curve.T_min_K,
                curve.T_max_K,
                100000.0,
                curve.segment,
                curve.is_element_reference,
                fit_error=curve.fit_error,
            )
            for curve in curves
        ]
        for element, curves in references.items()
    }
    result = fit_compilation_thermo_range(
        record=_record("FeO"),
        series=series,
        phase_label="cr",
        standard_state="condensed_solid",
        T_min_K=298.15,
        T_max_K=2200.0,
        reference_curves=wrong_pressure,
    )
    assert result.record["status"] == "accepted", result.record
    assert result.record["acceptance_tier"] == "smooth_fit"
    assert result.record["formation_capable"] is False
    assert result.record["formation_reason_code"] == "pressure_convention_mismatch"
    assert "Fe" in result.record["formation_reason"]
    assert not any(key.startswith("delta_f") for key in result.record["fit_error"])
    assert result.record["fit_statistics"]["cp_J_per_mol_K"]["cell_count"] > 0


def test_missing_element_reference_keeps_compound_fit_without_formation() -> None:
    temperatures = [298.15, 400, 600, 800, 1000, 1200, 1500, 1900, 2200]
    species = _constant_cp_segment(
        90.0,
        temperatures[0],
        temperatures[-1],
        H_at_anchor_J_per_mol=12500.0,
        S_at_anchor_J_per_mol_K=58.0,
    )
    result = fit_compilation_thermo_range(
        record=_record("FeO"),
        series=_source_series(
            species, temperatures, formula="FeO", is_reference=True
        ),
        phase_label="cr",
        standard_state="condensed_solid",
        T_min_K=temperatures[0],
        T_max_K=temperatures[-1],
        reference_curves={},
    )

    assert result.record["status"] == "accepted", result.record
    assert result.record["acceptance_tier"] == "smooth_fit"
    assert result.record["formation_capable"] is False
    assert result.record["formation_reason_code"] == "missing_element_reference_phase"
    assert "Fe, none" in result.record["formation_reason"]
    assert not any(key.startswith("delta_f") for key in result.record["fit_error"])


def test_missing_formation_h_anchor_keeps_species_fit() -> None:
    temperatures = [298.15, 400, 600, 800, 1000, 1200, 1500, 1900, 2200]
    species = _constant_cp_segment(
        90.0,
        temperatures[0],
        temperatures[-1],
        H_at_anchor_J_per_mol=12500.0,
        S_at_anchor_J_per_mol_K=58.0,
    )
    series = tuple(
        column
        for column in _source_series(
            species, temperatures, formula="FeO", is_reference=True
        )
        if column.quantity is not Quantity.DELTA_FH
    )
    result = fit_compilation_thermo_range(
        record=_record("FeO"),
        series=series,
        phase_label="cr",
        standard_state="condensed_solid",
        T_min_K=temperatures[0],
        T_max_K=temperatures[-1],
        reference_curves={},
    )

    assert result.record["status"] == "accepted", result.record
    assert result.record["acceptance_tier"] == "smooth_fit"
    assert result.record["formation_capable"] is False
    assert result.record["formation_reason_code"] == "missing_formation_h_anchor"
    assert not any(key.startswith("delta_f") for key in result.record["fit_error"])


@pytest.mark.parametrize(("table_id", "expected_charge"), [("Al-006", 1), ("Al-038", -1)])
def test_charged_janaf_species_keep_fit_but_disable_formation(
    table_id: str, expected_charge: int
) -> None:
    records = generate_janaf_sidecar(table_ids=[table_id])
    fit = next(
        record
        for record in records
        if record.get("source_record_id") == table_id and record.get("status") == "accepted"
    )

    assert fit["charge"] == expected_charge
    assert fit["charge_source"] == "index_entry"
    assert fit["formation_capable"] is False
    assert fit["formation_reason_code"] == "electron_reference_unsupported"
    assert fit["acceptance_tier"] in {"table_equivalent", "smooth_fit"}


def test_charge_is_parsed_from_printed_formula_when_index_charge_is_missing() -> None:
    record = _record("Al")
    record["table"]["index_entry"]["formula_as_published"] = "Al+"

    metadata = source_metadata(record)

    assert metadata["charge"] == 1
    assert metadata["charge_source"] == "formula_as_published"


def test_ambiguous_printed_formula_charge_disables_formation() -> None:
    record = _record("Al")
    record["table"]["index_entry"]["formula_as_published"] = "Al^{?}+"
    temperatures = [298.15, 400, 600, 800, 1000, 1200, 1500, 1900, 2200]
    species = _constant_cp_segment(30.0, temperatures[0], temperatures[-1])

    result = fit_compilation_thermo_range(
        record=record,
        series=_source_series(species, temperatures, formula="Al", is_reference=True),
        phase_label="g",
        standard_state="gas",
        T_min_K=temperatures[0],
        T_max_K=temperatures[-1],
        reference_curves={},
    )

    assert result.record["status"] == "accepted", result.record
    assert result.record["charge"] is None
    assert result.record["formation_capable"] is False
    assert result.record["formation_reason_code"] == "charge_unknown"


def test_formation_precision_failure_keeps_species_fit_at_t2() -> None:
    references = _transition_reference_curves()
    terms, error = _element_reference_terms("FeO", 298.15, PRESSURE_PA, references)
    assert error is None and terms is not None
    elemental_H298 = sum(
        coefficient * h for coefficient, h, _g, _curve in terms
    )
    temperatures = [298.15, 400, 600, 800, 1000, 1200, 1500, 1900, 2200]
    species = _constant_cp_segment(
        90.0,
        temperatures[0],
        temperatures[-1],
        H_at_anchor_J_per_mol=12500.0 + elemental_H298,
        S_at_anchor_J_per_mol_K=58.0,
    )
    series = list(_source_series(
        species,
        temperatures,
        reference_curves=references,
        formula="FeO",
        is_reference=False,
    ))
    formation_g = next(index for index, column in enumerate(series)
                       if column.quantity is Quantity.DELTA_FG)
    series[formation_g] = _series(
        Quantity.DELTA_FG,
        [
            (float(temperature), float(value) + 100.0)
            for temperature, value in series[formation_g].value.series
        ],
        "kJ mol^-1",
    )

    result = fit_compilation_thermo_range(
        record=_record("FeO"),
        series=series,
        phase_label="cr",
        standard_state="condensed_solid",
        T_min_K=temperatures[0],
        T_max_K=temperatures[-1],
        reference_curves=references,
    )

    assert result.record["status"] == "accepted", result.record
    assert result.record["acceptance_tier"] == "smooth_fit"
    assert result.record["formation_capable"] is False
    assert result.record["formation_reason_code"] == "formation_inconsistent"
    assert result.record["fit_error"]["delta_fG_kJ_per_mol"] == pytest.approx(100.0)
    assert result.record["fit_statistics"]["derived_formation_G_kJ_per_mol"][
        "max_abs"
    ] == pytest.approx(100.0)


def test_broken_piecewise_join_is_typed_rejection_even_when_cells_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temperatures = [298.15, 350, 400, 450, 500, 550, 600, 650, 700,
                    750, 800, 900, 1000, 1100, 1200]
    source = _constant_cp_segment(30.0, temperatures[0], temperatures[-1])
    original_solver = compilation_thermo_fits._constrained_weighted_lstsq

    def break_right_enthalpy_anchor(matrix, target, constraints, constraint_target):
        values, rank, residuals = original_solver(
            matrix, target, constraints, constraint_target
        )
        values[9 + 7] += 1e-5
        return values, rank, residuals

    monkeypatch.setattr(
        compilation_thermo_fits,
        "_constrained_weighted_lstsq",
        break_right_enthalpy_anchor,
    )
    result = _fit_piecewise_candidate(
        record=_record("Fe", state="ref"),
        series=_source_series(source, temperatures, places=0),
        phase_label="cr:broken-join",
        standard_state="condensed_solid",
        is_element_reference=True,
        reference_curves={},
        segment_bounds=[(298.15, 700.0), (700.0, 1200.0)],
        fit_lo=298.15,
        fit_hi=1200.0,
    )

    smooth_checks = result.record["fit_statistics"]["smooth_fit_checks"]
    assert smooth_checks["join_continuity_passed"] is False
    join = smooth_checks["join_evaluation_errors"][0]
    assert join["H_absolute_mismatch_J_per_mol"] > join[
        "H_total_bound_J_per_mol"
    ]
    assert join["H_solve_constraint_passed"] is False
    assert result.record["status"] == "rejected", result.record
    assert result.record["reason_code"] == "join_continuity_failed"


def test_k001_g_roundoff_join_refines_before_interpolation_ringing_refusal() -> None:
    records = generate_janaf_sidecar(table_ids=["K-001"])
    gas = next(
        record
        for record in records
        if record.get("source_record_id") == "K-001" and record.get("phase") == "g"
    )

    assert gas["status"] == "rejected"
    assert gas["reason_code"] == "interpolation_ringing"
    smooth_checks = gas["fit_statistics"]["smooth_fit_checks"]
    assert smooth_checks["join_continuity_passed"] is True
    join = next(
        item
        for item in smooth_checks["join_evaluation_errors"]
        if item["T_K"] == 4300.0
    )
    assert join["Cp_term_magnitude_sum_J_per_mol_K"] > 0.0
    assert join["Cp_absolute_mismatch_J_per_mol_K"] < join[
        "Cp_evaluation_bound_J_per_mol_K"
    ]


@pytest.mark.parametrize("table_id", ["Al-001", "Al-004"])
def test_janaf_high_temperature_liquid_uses_printed_h298_baseline(table_id: str) -> None:
    document = load_table_document(TABLES_DIR / f"{table_id}.yaml")
    columns = compilation_column_series_from_record(document)
    cells = {
        column.quantity: {
            float(temperature): float(value)
            for temperature, value in column.value.series
            if 1000.0 <= float(temperature) <= 2500.0
        }
        for column in columns
        if column.quantity in {Quantity.CP, Quantity.H_MINUS_H298, Quantity.S}
    }
    fit = _fit_piecewise_candidate(
        record=document,
        series=columns,
        phase_label="l",
        standard_state="condensed_liquid",
        is_element_reference=True,
        reference_curves={},
        segment_bounds=[(1000.0, 2700.0)],
        fit_lo=1000.0,
        fit_hi=2700.0,
    )

    assert fit.record["status"] == "accepted", fit.record
    assert fit.record["acceptance_tier"] == "smooth_fit"
    assert fit.record["fit_statistics"]["segment_count"] == 1
    assert fit.record["fit_statistics"]["smooth_fit_checks"][
        "maximum_relative_Cp_residual"
    ] <= 3e-5
    segment_data = fit.record["thermo_record"]["segments"][0]
    segment = Nasa9Segment(
        segment_data["T_min_K"],
        segment_data["T_max_K"],
        tuple(segment_data["a_coefficients"]),
        segment_data["b1"],
        segment_data["b2"],
    )
    for temperature in (1000.0, 1500.0):
        _cp, enthalpy, entropy, _gibbs = _evaluate(segment, temperature)
        assert abs(enthalpy / 1000.0 - cells[Quantity.H_MINUS_H298][temperature]) <= 0.0011
        assert abs(entropy - cells[Quantity.S][temperature]) <= 0.0011


def test_missing_in_range_h_cell_is_typed_rejection() -> None:
    temperatures = [298.15, 400, 500, 600, 700]
    species = _constant_cp_segment(30.0, temperatures[0], temperatures[-1])
    series = tuple(
        column for column in _source_series(species, temperatures, places=0)
        if column.quantity is not Quantity.H_MINUS_H298
    )

    result = fit_compilation_thermo_range(
        record=_record("Fe", state="ref"),
        series=series,
        phase_label="cr",
        standard_state="condensed_solid",
        T_min_K=temperatures[0],
        T_max_K=temperatures[-1],
        is_element_reference=True,
    )

    assert result.record["status"] == "rejected"
    assert result.record["reason_code"] == "missing_in_range_h_cell"


@pytest.mark.parametrize("cp", [0.0, -5.0])
def test_positive_cp_gate_rejects_nonpositive_fitted_cp(cp: float) -> None:
    segment = _constant_cp_segment(
        cp, 298.15, 1000.0, H_at_anchor_J_per_mol=0.0,
        S_at_anchor_J_per_mol_K=0.0,
    )
    result = fit_compilation_thermo_range(
        record=_record("Fe", state="ref"),
        series=_source_series(segment, [298.15, 350, 400, 500, 600, 700, 800, 900, 1000]),
        phase_label="cr",
        standard_state="condensed_solid",
        T_min_K=298.15,
        T_max_K=1000.0,
        is_element_reference=True,
    )
    assert result.record["reason_code"] == "cp_nonpositive"


def test_phase_range_with_fewer_than_four_cp_cells_is_insufficient() -> None:
    temperatures = [298.15, 600, 1000]
    segment = _constant_cp_segment(30.0, temperatures[0], temperatures[-1])
    result = fit_compilation_thermo_range(
        record=_record("Fe", state="ref"),
        series=_source_series(segment, temperatures),
        phase_label="cr:too-few-cells",
        standard_state="condensed_solid",
        T_min_K=temperatures[0],
        T_max_K=temperatures[-1],
        is_element_reference=True,
    )
    assert result.record["status"] == "rejected"
    assert result.record["reason_code"] == "insufficient_cells"
    assert result.record["acceptance_tier"] is None
    assert result.record["cell_count"] == 3


def test_zero_and_below_minimum_cells_have_typed_exclusions() -> None:
    segment = _constant_cp_segment(30.0, 100.0, 1000.0)
    result = fit_compilation_thermo_range(
        record=_record("Fe", state="ref"),
        series=_source_series(
            segment,
            [0.0, 50.0, 100, 150, 200, 298.15, 400, 500, 600, 700, 800, 1000],
            places=3,
        ),
        phase_label="cr",
        standard_state="condensed_solid",
        T_min_K=100.0,
        T_max_K=1000.0,
        is_element_reference=True,
    )
    codes = {(cell["T_K"], cell["reason_code"]) for cell in result.record["excluded_cells"]}
    assert (0.0, "nonpositive_fit_temperature") in codes
    assert (50.0, "below_fit_T_min") in codes
    assert (100.0, "below_fit_T_min") in codes
    assert (150.0, "below_fit_T_min") in codes
    assert result.record["status"] == "accepted", result.record
    assert result.record["validity"]["T_min_K"] == 200.0


def test_piecewise_constraints_keep_cp_h_s_continuous_at_join() -> None:
    temperatures = [298.15, 350, 400, 450, 500, 550, 600, 650, 700, 750, 800, 900, 1000, 1100, 1200]
    segment = _constant_cp_segment(30.0, temperatures[0], temperatures[-1])
    series = list(_source_series(segment, temperatures, places=0))
    entropy_pairs = [
        (temperature, value + (Decimal("0.1") if temperature > 700 else Decimal(0)))
        for temperature, value in series[2].value.series
    ]
    entropy_tokens = tuple(f"{value:.1f}" for _temperature, value in entropy_pairs)
    entropy_values = tuple(
        (Decimal(str(temperature)), Decimal(token))
        for (temperature, _value), token in zip(entropy_pairs, entropy_tokens)
    )
    series[2] = _column_series(
        Quantity.S,
        entropy_tokens,
        entropy_values,
        "J mol^-1 K^-1",
        precision=Decimal("0.5"),
    )
    result = _fit_piecewise_candidate(
        record=_record("Fe", state="ref"),
        series=series,
        phase_label="cr:synthetic",
        standard_state="condensed_solid",
        is_element_reference=True,
        reference_curves={},
        segment_bounds=[(298.15, 700.0), (700.0, 1200.0)],
        fit_lo=298.15,
        fit_hi=1200.0,
    )
    assert result.record["status"] == "accepted", result.record
    segments = [
        Nasa9Segment(
            item["T_min_K"], item["T_max_K"], tuple(item["a_coefficients"]), item["b1"], item["b2"]
        )
        for item in result.record["thermo_record"]["segments"]
    ]
    at_join = [_evaluate(item, 700.0)[:3] for item in segments]
    for left, right in zip(*at_join):
        assert abs(left - right) <= 1e-9 * max(abs(left), abs(right), 1.0)


def test_smooth_fit_rejects_cp_relative_error_above_one_per_thousand() -> None:
    temperatures = [298.15, 350, 400, 450, 500, 550, 600, 650, 700, 800, 900, 1000]
    source = _constant_cp_segment(30.0, temperatures[0], temperatures[-1])
    series = list(_source_series(source, temperatures, places=6))
    cp_pairs = [
        (temperature, value * (Decimal("1.05") if temperature == 550 else Decimal(1)))
        for temperature, value in series[0].value.series
    ]
    series[0] = _series(Quantity.CP, cp_pairs, "J mol^-1 K^-1", places=6)
    result = _fit_piecewise_candidate(
        record=_record("Fe", state="ref"),
        series=series,
        phase_label="cr:synthetic",
        standard_state="condensed_solid",
        is_element_reference=True,
        reference_curves={},
        segment_bounds=[(temperatures[0], temperatures[-1])],
        fit_lo=temperatures[0],
        fit_hi=temperatures[-1],
    )
    assert result.record["status"] == "rejected"
    assert result.record["reason_code"] in {
        "smooth_fit_tolerance_exceeded",
        "interpolation_ringing",
    }
    assert result.record["fit_statistics"]["smooth_fit_checks"][
        "maximum_relative_Cp_residual"
    ] > 1e-3


def test_janaf_o029_formation_gibbs_zero_pin() -> None:
    assert (TABLES_DIR / "O-029.yaml").is_file()
    records = generate_janaf_sidecar(table_ids=["O-029"])
    assert Counter(record["status"] for record in records) == Counter({"accepted": 1})
    oxygen = next(record for record in records if record["source_record_id"] == "O-029")
    assert oxygen["status"] == "accepted", oxygen
    assert oxygen["acceptance_tier"] == "smooth_fit"
    assert oxygen["formation_capable"] is True
    assert oxygen["validity"]["T_min_K"] == 200.0
    assert oxygen["fit_statistics"]["segment_count"] >= 2
    assert oxygen["fit_error"]["Cp_J_per_mol_K"] > 0.0
    assert oxygen["fit_error"]["delta_fH_kJ_per_mol"] == 0.0
    assert oxygen["fit_error"]["delta_fG_kJ_per_mol"] == 0.0
    assert any(
        cell["T_K"] == 100.0 and cell["reason_code"] == "below_fit_T_min"
        for cell in oxygen["excluded_cells"]
    )
    assert oxygen["fit_statistics"]["smooth_fit_checks"]["maximum_relative_Cp_residual"] <= 1e-3
    assert oxygen["fit_statistics"]["derived_formation_G_kJ_per_mol"]["max_abs"] == 0.0
    segment = oxygen["thermo_record"]["segments"][0]
    assert tuple(float(value).hex() for value in segment["a_coefficients"]) == (
        "-0x1.32a0dca63f355p+15",
        "0x1.1916cf2960d16p+9",
        "0x1.4e0af97dd3cc1p-1",
        "0x1.74b22eaf4f3fcp-8",
        "-0x1.81e4031cc678fp-19",
        "-0x1.3ee65d5b2f475p-32",
        "0x1.16ca172f1b4d2p-41",
    )
    assert (float(segment["b1"]).hex(), float(segment["b2"]).hex()) == (
        "-0x1.d5881f0e11a42p+11",
        "0x1.50e0b9ef64478p+4",
    )
    segments = [
        Nasa9Segment(
            T_min_K=item["T_min_K"],
            T_max_K=item["T_max_K"],
            coefficients=tuple(item["a_coefficients"]),
            b1=item["b1"],
            b2=item["b2"],
        )
        for item in oxygen["thermo_record"]["segments"]
    ]
    for left, right in zip(segments, segments[1:]):
        join = left.T_max_K
        cp_left, h_left, s_left, _g_left = _evaluate(left, join)
        cp_right, h_right, s_right, _g_right = _evaluate(right, join)
        for a, b in ((cp_left, cp_right), (h_left, h_right), (s_left, s_right)):
            assert abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1.0)


@pytest.mark.parametrize(
    ("table_id", "phase", "temperature_K", "printed_identity"),
    [
        (
            "Al-008",
            "g",
            2700.0,
            Decimal("-541.410") + Decimal("183.881") - Decimal("84.782")
            - Decimal("113.667") - Decimal("86.141"),
        ),
        (
            "Al-033",
            "g",
            2000.0,
            Decimal("-265.684") + Decimal("62.368") - Decimal("62.557")
            - Decimal("0.5") * Decimal("62.745"),
        ),
        (
            "Al-043",
            "l",
            2700.0,
            Decimal("-1510.424") + Decimal("352.843") - Decimal("84.782")
            - Decimal("1.5") * Decimal("89.876"),
        ),
    ],
)
def test_janaf_printed_formation_h_uses_exact_elemental_h298_gauge(
    table_id: str,
    phase: str,
    temperature_K: float,
    printed_identity: Decimal,
) -> None:
    records = generate_janaf_sidecar(table_ids=[table_id])
    record = next(
        item for item in records
        if item["source_record_id"] == table_id and item["phase"] == phase
    )

    assert record["status"] == "accepted", record
    assert record["formation_capable"] is True, record
    assert (
        record["validity"]["T_min_K"]
        <= temperature_K
        <= record["validity"]["T_max_K"]
    )
    if table_id == "Al-033":
        assert abs(printed_identity - Decimal("-297.245")) <= Decimal("0.0005")
    else:
        assert printed_identity == {
            "Al-008": Decimal("-642.119"),
            "Al-043": Decimal("-1377.177"),
        }[table_id]
    assert record["fit_statistics"]["derived_formation_H_kJ_per_mol"]["cell_count"] > 0


def test_printed_formation_cells_are_a_separate_pointwise_path() -> None:
    records = generate_janaf_sidecar(table_ids=["O-029", "Fe-018", "Al-006"])
    by_id = {
        record["source_record_id"]: record
        for record in records
        if record.get("status") == "accepted"
    }

    oxygen = by_id["O-029"]
    oxygen_cell = next(
        cell for cell in oxygen["printed_formation_cells"]
        if cell["quantity"] == Quantity.DELTA_FG.value
        and 298.15 in cell["T_K"]
    )
    assert set(oxygen_cell) == {"table_id", "quantity", "T_K"}
    assert oxygen_cell["table_id"] == "O-029"
    assert 298.15 in oxygen_cell["T_K"]
    compact_oxygen = json.loads(render_sidecar([oxygen]))["records"][0]
    assert all(
        set(reference) == {"table_id", "quantity", "T_K"}
        for reference in compact_oxygen["printed_formation_cells"]
    )

    iron_oxide = by_id["Fe-018"]
    assert iron_oxide["formation_capable"] is False
    assert iron_oxide["formation_reason_code"] == "missing_element_reference_phase"
    source = load_table_document(TABLES_DIR / "Fe-018.yaml")
    source_series = compilation_column_series_from_record(source)
    for quantity in (Quantity.DELTA_FH, Quantity.DELTA_FG):
        oxide_cell = next(
            cell for cell in iron_oxide["printed_formation_cells"]
            if cell["quantity"] == quantity.value and 298.15 in cell["T_K"]
        )
        source_cell = next(
            (value, token, unit, uncertainty)
            for item in source_series
            if item.quantity is quantity
            for (temperature, value), token, unit, uncertainty in zip(
                item.value.series or (),
                item.raw_tokens,
                item.source_units,
                item.cell_uncertainties,
            )
            if temperature == Decimal("298.15")
        )
        assert set(oxide_cell) == {"table_id", "quantity", "T_K"}
        assert oxide_cell["table_id"] == "Fe-018"
        assert 298.15 in oxide_cell["T_K"]
        assert source_cell[1] is not None

    charged = by_id["Al-006"]
    assert charged["charge"] == 1
    assert "printed_formation_cells" not in charged


def test_printed_formation_cells_respect_janaf_source_cell_refusals() -> None:
    path = TABLES_DIR / "Fe-018.yaml"

    def fit(document: dict) -> list[dict]:
        records, _curves = _add_janaf_table(
            record=document,
            path=path,
            series=compilation_column_series_from_record(document),
            is_element_reference=False,
            reference_curves={},
        )
        return records

    def cells_at_298(records: list[dict]) -> list[dict]:
        return [
            {
                "table_id": cell["table_id"],
                "quantity": cell["quantity"],
                "T_K": temperature,
            }
            for record in records
            for cell in record.get("printed_formation_cells", ())
            for temperature in cell["T_K"]
            if cell["quantity"] == Quantity.DELTA_FG.value
            and temperature == 298.15
        ]

    healthy = deepcopy(load_table_document(path))
    healthy_cells = cells_at_298(fit(healthy))
    assert healthy_cells == [
        {
            "table_id": "Fe-018",
            "quantity": Quantity.DELTA_FG.value,
            "T_K": 298.15,
        }
    ]

    mutated = deepcopy(healthy)
    row = next(
        row
        for row in mutated["table"]["values"]
        if row["temperature"]["as_published"] == "298.15"
    )
    row["formation_gibbs_energy"].update(
        value=-2514.29,
        as_published="-2514.290",
    )

    owner = generate_table(mutated)
    assert any(
        refusal["temperature_as_published"] == "298.15"
        for refusal in owner.report["scale_error_refusals"]
    )
    assert not any(
        observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.DELTA_FG
        and any(
            temperature == Decimal("298.15")
            for temperature, _value in observation.value.series or ()
        )
        for observation in owner.observations
    )
    assert cells_at_298(fit(mutated)) == []


def test_sidecar_fit_matches_an_independent_accepted_janaf_cell() -> None:
    table_id = "O-029"
    table_path = TABLES_DIR / f"{table_id}.yaml"
    table = load_table_document(table_path)
    accepted_owner = generate_table(table, source_path=str(table_path))
    printed = next(
        (temperature, value)
        for observation in accepted_owner.observations
        if observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.DELTA_FG
        for temperature, value in observation.value.series or ()
        if temperature == Decimal("298.15")
    )
    record = next(
        item
        for item in generate_janaf_sidecar(table_ids=[table_id])
        if item["source_record_id"] == table_id
        and item["status"] == "accepted"
        and item.get("formation_capable")
        and any(
            reference["quantity"] == Quantity.DELTA_FG.value
            and 298.15 in reference["T_K"]
            for reference in item.get("printed_formation_cells", ())
        )
    )
    fitted, reason = evaluate_sidecar_fit(
        record,
        Quantity.DELTA_FG,
        float(printed[0]),
        reference_curves={},
    )
    assert reason is None
    assert fitted == pytest.approx(float(printed[1]), abs=record["residual_summary"][
        Quantity.DELTA_FG.value
    ]["gate_tolerance"])


def test_al091_enthalpy_evaluator_current_output_pin() -> None:
    from simulator.battery.generators.compilation_thermo_fits import load_sidecar

    record = next(
        item
        for item in load_sidecar()
        if item["source_record_id"] == "Al-091"
        and item["phase"] == "l"
        and item["status"] == "accepted"
    )

    fitted, reason = evaluate_sidecar_fit(
        record,
        Quantity.H_MINUS_H298,
        2500.0,
        reference_curves={},
    )

    assert reason is None
    assert fitted == pytest.approx(-1686.0569999998672, abs=1e-9)


def test_janaf_formation_anchor_does_not_require_element_fit_at_298_15_k() -> None:
    expected_missing_comparison_reference = {
        "Al-009": ("g", "Br, none, no accepted reference fit covers 200 K"),
        "Al-011": ("l", "Br, none, no accepted reference fit covers 298.15 K"),
        "Al-013": ("g", "Br, none, no accepted reference fit covers 200 K"),
        "Al-029": ("cr", "K, none, no accepted reference fit covers 298.15 K"),
        "Al-031": ("cr", "K, none, no accepted reference fit covers 298.15 K"),
        "Al-032": ("cr", "Na, none, no accepted reference fit covers 298.15 K"),
        "Al-046": ("g", "Li, none, no accepted reference fit covers 200 K"),
        "Al-047": ("g", "Na, none, no accepted reference fit covers 200 K"),
        "Al-048": ("cr", "K, none, no accepted reference fit covers 298.15 K"),
        "Al-049": ("cr:beta", "Li, none, no accepted reference fit covers 200 K"),
    }
    table_ids = list(expected_missing_comparison_reference)
    records = [
        record for record in generate_janaf_sidecar(table_ids=table_ids)
        if record["source_record_id"] in table_ids
        and record["phase"] == expected_missing_comparison_reference[
            record["source_record_id"]
        ][0]
    ]
    by_id = {record["source_record_id"]: record for record in records}

    assert set(by_id) == set(table_ids)
    assert all(record["status"] == "accepted" for record in by_id.values())
    for table_id, (_phase, expected_reason) in (
        expected_missing_comparison_reference.items()
    ):
        record = by_id[table_id]
        assert record["formation_capable"] is False
        assert record["formation_reason_code"] == "missing_element_reference_phase"
        assert expected_reason in record["formation_reason"]


def test_janaf_na_cr_uses_reduced_basis_and_keeps_typed_rejection() -> None:
    assert (TABLES_DIR / "Na-001.yaml").is_file()
    records = generate_janaf_sidecar(table_ids=["Na-001"])
    assert Counter(record["status"] for record in records) == Counter(
        {"accepted": 1, "rejected": 2}
    )
    sodium = next(record for record in records if record["phase"] == "cr")

    assert sodium["status"] == "rejected", sodium
    assert sodium["reason_code"] == "segmentation_exhausted"
    liquid = next(record for record in records if record["phase"] == "l")
    segment = liquid["thermo_record"]["segments"][0]
    assert tuple(float(value).hex() for value in segment["a_coefficients"]) == (
        "0x1.dc7f59959a2b3p+15",
        "-0x1.e2a669b2f4ed8p+8",
        "0x1.7ea7a1b0fc0d8p+2",
        "-0x1.24a87dfdbb2d8p-8",
        "0x1.78da2f19d4adfp-19",
        "-0x1.cbeffa315b2fep-32",
        "0x0.0p+0",
    )
    assert (float(segment["b1"]).hex(), float(segment["b2"]).hex()) == (
        "0x1.98c7a5f21f584p+10",
        "-0x1.b2c83c048b4c1p+4",
    )
    cp_count = sodium["fit_statistics"]["cp_J_per_mol_K"]["cell_count"]
    assert 4 <= cp_count <= 8
    assert sodium["basis_terms"] == cp_count - 2
    assert 2 <= sodium["basis_terms"] <= 6


@pytest.mark.parametrize(
    ("table_id", "phase", "join_K"),
    (("C-002", "cr", 800.0), ("N-023", "g", 600.0)),
)
def test_element_reference_search_finds_passing_two_segment_boundary(
    table_id: str, phase: str, join_K: float
) -> None:
    assert (TABLES_DIR / f"{table_id}.yaml").is_file()
    records = generate_janaf_sidecar(table_ids=[table_id])
    fit = next(
        record
        for record in records
        if record["source_record_id"] == table_id and record["phase"] == phase
    )

    assert fit["status"] == "accepted", fit
    segments = fit["thermo_record"]["segments"]
    assert len(segments) == 2
    assert segments[0]["T_max_K"] == join_K
    assert segments[1]["T_min_K"] == join_K
    checks = fit["fit_statistics"]["smooth_fit_checks"]
    assert checks["anti_ringing_passed"] is True
    assert checks["join_continuity_passed"] is True


def test_reduced_basis_record_evaluates_through_existing_nasa9_owner() -> None:
    temperatures = [298.15, 400, 600, 800, 1000]
    source = _constant_cp_segment(30.0, temperatures[0], temperatures[-1])
    result = fit_compilation_thermo_range(
        record=_record("Fe", state="ref"),
        series=_source_series(source, temperatures),
        phase_label="cr:short-range",
        standard_state="condensed_solid",
        T_min_K=temperatures[0],
        T_max_K=temperatures[-1],
        is_element_reference=True,
    )

    assert result.record["status"] == "accepted", result.record
    assert result.record["basis_terms"] == 3
    fit_segment = result.record["thermo_record"]["segments"][0]
    nasa9 = Nasa9Segment(
        fit_segment["T_min_K"],
        fit_segment["T_max_K"],
        tuple(fit_segment["a_coefficients"]),
        fit_segment["b1"],
        fit_segment["b2"],
    )
    selected_terms = set((2, 3, 4, 1, 0, 5, 6)[: result.record["basis_terms"]])
    assert all(
        coefficient == 0.0
        for index, coefficient in enumerate(nasa9.coefficients)
        if index not in selected_terms
    )
    ratios = nasa9.evaluate_ratios(nasa9.T_min_K)
    assert all(math.isfinite(value) for value in ratios)
    assert ratios[0] > 0.0


def test_fit_cli_accepts_sampling_options_and_reports_summary(
    tmp_path, monkeypatch, capsys
) -> None:
    selected: dict[str, object] = {}

    def fake_generate(*, table_ids=None, limit=None):
        selected["table_ids"] = table_ids
        selected["limit"] = limit
        return [
            {
                "record_id": "accepted",
                "status": "accepted",
                "acceptance_tier": "table_equivalent",
                "formation_capable": True,
                "fit_error": {
                    "Cp_J_per_mol_K": 0.25,
                    "H_minus_H298_kJ_per_mol": 0.01,
                    "S_J_per_mol_K": 0.02,
                    "delta_fH_kJ_per_mol": 0.0,
                    "delta_fG_kJ_per_mol": 0.0,
                },
            },
            {
                "record_id": "rejected",
                "status": "rejected",
                "reason_code": "segmentation_exhausted",
                "fit_statistics": {
                    "smooth_fit_checks": {
                        "only_relative_Cp_residual_failed": True,
                        "maximum_relative_Cp_residual": 0.003,
                        "worst_relative_Cp_cell": {
                            "T_K": 550.0,
                            "residual_J_per_mol_K": 0.09,
                            "relative_residual": 0.003,
                        },
                    }
                },
            },
        ]

    monkeypatch.setattr(
        "simulator.battery.generators.compilation_thermo_fits.generate_janaf_sidecar",
        fake_generate,
    )
    output = tmp_path / "fits.json"
    assert fit_cli_main(
        ["--tables", "O-029", "Na-001", "--limit", "2", "--output", str(output)]
    ) == 0

    assert selected == {"table_ids": ["O-029", "Na-001"], "limit": 2}
    summary = capsys.readouterr().out
    assert "1 T1, 0 T2, 1 rejected" in summary
    assert "formation_capable=1" in summary
    assert "reasons={'segmentation_exhausted': 1}" in summary
    assert "max_fit_error=" in summary
    assert "Cp_tolerance_only=1" in summary
    assert "'5e-3': 1" in summary
    assert "'T_K': 550.0" in summary
    assert output.is_file()


def test_fe_alpha_curie_cusp_is_split_or_typed_as_unresolved() -> None:
    assert (TABLES_DIR / "Fe-003.yaml").is_file()
    records = generate_janaf_sidecar(table_ids=["Fe-003"])
    alpha = next(
        record
        for record in records
        if record["source_record_id"] == "Fe-003" and record["phase"] == "cr:alpha"
    )
    if alpha["status"] == "accepted":
        assert alpha["fit_statistics"]["segment_count"] >= 2
        assert any(
            segment["T_max_K"] == 1000.0
            for segment in alpha["thermo_record"]["segments"][:-1]
        )
    else:
        assert alpha["reason_code"] == "lambda_anomaly_unresolved", alpha
        assert alpha.get("fit_statistics", {}).get("segment_count", 0) >= 1
        assert alpha.get("failed_cells") or alpha.get("fit_statistics")


def test_interpolation_ringing_detects_synthetic_midpoint_oscillation() -> None:
    oscillating = Nasa9Segment(
        T_min_K=100.0,
        T_max_K=200.0,
        coefficients=(0.0, 0.0, 2.0, 0.03, -0.0001, 0.0, 0.0),
        b1=0.0,
        b2=0.0,
    )
    cells = (
        _Cell(100.0, _evaluate(oscillating, 100.0)[0], "left", "J mol^-1 K^-1", 0.0),
        _Cell(200.0, _evaluate(oscillating, 200.0)[0], "right", "J mol^-1 K^-1", 0.0),
    )
    assert _interpolation_ringing(oscillating, cells)


def test_oscillating_candidate_is_rejected_by_anti_ringing() -> None:
    temperatures = [298.15, 350, 400, 450, 500, 550, 600, 650, 700, 750, 800, 900, 1000]
    oscillating = Nasa9Segment(
        min(temperatures),
        max(temperatures),
        (
            0.0,
            0.0,
            3.5,
            -0.0044041635341935444,
            -9.764832415001191e-06,
            1.9615401928782244e-08,
            3.705751826049212e-11,
        ),
        b1=0.0,
        b2=0.0,
    )
    result = _fit_piecewise_candidate(
        record=_record("Fe", state="ref"),
        series=_source_series(oscillating, temperatures, places=9),
        phase_label="cr:synthetic",
        standard_state="condensed_solid",
        is_element_reference=True,
        reference_curves={},
        segment_bounds=[(temperatures[0], temperatures[-1])],
        fit_lo=temperatures[0],
        fit_hi=temperatures[-1],
    )
    assert result.record["status"] == "rejected"
    assert result.record["reason_code"] == "interpolation_ringing"
    assert result.record["ring_failures"]
