from __future__ import annotations

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
from simulator.reference_data.compilation_thermo_fits import (
    _Cell,
    FittedCurve,
    _element_reference_terms,
    _evaluate,
    _fit_piecewise_candidate,
    _interpolation_ringing,
    fit_compilation_thermo_range,
    generate_janaf_sidecar,
    main as fit_cli_main,
    render_sidecar,
)
from simulator.reference_data.janaf import TABLES_DIR, load_table_document
from simulator.vapour_rail.nasa_cea import Nasa9Segment, R_J_PER_MOL_K
import simulator.reference_data.compilation_thermo_fits as compilation_thermo_fits


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
                h, [(coefficient, h_ref) for coefficient, h_ref, _g_ref in terms]
            )
            fg = formation_subtraction_kJ_mol(
                gibbs, [(coefficient, g_ref) for coefficient, _h_ref, g_ref in terms]
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
    assert render_sidecar(first) == render_sidecar(list(reversed(second)))


def test_compound_fit_anchors_and_formation_acceptance_across_fe_reference_transition() -> None:
    references = _transition_reference_curves()
    terms_at_anchor, error = _element_reference_terms("FeO", 298.15, PRESSURE_PA, references)
    assert error is None and terms_at_anchor is not None
    elemental_H298 = sum(coefficient * h for coefficient, h, _g in terms_at_anchor)
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
        H_at_anchor_J_per_mol=12500.0 + sum(c * h for c, h, _g in terms),
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
    assert result.record["acceptance_tier"] == "table_equivalent"
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
    assert result.record["formation_capable"] is False
    assert result.record["formation_reason_code"] == "missing_formation_h_anchor"
    assert not any(key.startswith("delta_f") for key in result.record["fit_error"])


def test_formation_precision_failure_keeps_species_fit_at_t2() -> None:
    references = _transition_reference_curves()
    terms, error = _element_reference_terms("FeO", 298.15, PRESSURE_PA, references)
    assert error is None and terms is not None
    elemental_H298 = sum(coefficient * h for coefficient, h, _g in terms)
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
    assert result.record["formation_capable"] is True
    assert result.record["fit_error"]["delta_fG_kJ_per_mol"] == pytest.approx(100.0)


def test_broken_piecewise_join_is_typed_rejection_even_when_cells_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temperatures = [298.15, 350, 400, 450, 500, 550, 600, 650, 700,
                    750, 800, 900, 1000, 1100, 1200]
    source = _constant_cp_segment(30.0, temperatures[0], temperatures[-1])
    original_solver = compilation_thermo_fits._constrained_weighted_lstsq

    def break_right_enthalpy_anchor(matrix, target, constraints, constraint_target):
        values, rank = original_solver(matrix, target, constraints, constraint_target)
        values[9 + 7] += 1e-8
        return values, rank

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

    assert result.record["status"] == "rejected", result.record
    assert result.record["reason_code"] == "join_continuity_failed"
    assert result.record["fit_statistics"]["smooth_fit_checks"][
        "join_continuity_max_relative"
    ] > 1e-9


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


def test_janaf_na_cr_uses_reduced_basis_and_keeps_typed_rejection() -> None:
    assert (TABLES_DIR / "Na-001.yaml").is_file()
    records = generate_janaf_sidecar(table_ids=["Na-001"])
    sodium = next(record for record in records if record["phase"] == "cr")

    assert sodium["status"] == "rejected", sodium
    assert sodium["reason_code"] == "segmentation_exhausted"
    cp_count = sodium["fit_statistics"]["cp_J_per_mol_K"]["cell_count"]
    assert 4 <= cp_count <= 8
    assert sodium["basis_terms"] == cp_count - 2
    assert 2 <= sodium["basis_terms"] <= 6


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
        "simulator.reference_data.compilation_thermo_fits.generate_janaf_sidecar",
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
