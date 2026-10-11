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
    FittedCurve,
    _element_reference_terms,
    _evaluate,
    fit_compilation_thermo_range,
    generate_janaf_sidecar,
    render_sidecar,
)
from simulator.reference_data.janaf import TABLES_DIR
from simulator.vapour_rail.nasa_cea import Nasa9Segment, R_J_PER_MOL_K


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


def _series(quantity: Quantity, pairs: list[tuple[float, float]], unit: str):
    tokens = tuple(f"{value:.9f}" for _temperature, value in pairs)
    values = tuple(
        (Decimal(str(temperature)), Decimal(token))
        for (temperature, _value), token in zip(pairs, tokens)
    )
    return _column_series(quantity, tokens, values, unit)


def _column_series(quantity, tokens, values, unit):
    from simulator.battery.migrate import CompilationColumnSeries

    return CompilationColumnSeries(
        quantity=quantity,
        series_key=quantity.value,
        value=Value(ValueKind.SERIES, series=values),
        temperature_K=State.unknown("temperature is a series coordinate"),
        raw_tokens=tokens,
        source_units=(unit,) * len(values),
        cell_uncertainties=(PRECISION,) * len(values),
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


def _source_series(segment, temperatures, *, reference_curves=None, formula="Fe", is_reference=True):
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
        _series(Quantity.CP, cp_pairs, "J mol^-1 K^-1"),
        _series(Quantity.H_MINUS_H298, h_pairs, "kJ mol^-1"),
        _series(Quantity.S, s_pairs, "J mol^-1 K^-1"),
        _series(Quantity.DELTA_FH, fh_pairs, "kJ mol^-1"),
        _series(Quantity.DELTA_FG, fg_pairs, "kJ mol^-1"),
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
    temperatures = [298.15, 400.0, 550.0, 700.0, 850.0, 1000.0]
    segment = _constant_cp_segment(30.0, 298.15, 1000.0)

    def generate_record():
        result = fit_compilation_thermo_range(
            record=_record("Fe", state="ref"),
            series=_source_series(segment, temperatures),
            phase_label="cr",
            standard_state="condensed_solid",
            T_min_K=298.15,
            T_max_K=1000.0,
            is_element_reference=True,
        )
        assert result.record["status"] == "accepted"
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


def test_pressure_convention_mismatch_rejects_compound_fit() -> None:
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
    assert result.record["reason_code"] == "pressure_convention_mismatch"
    assert result.record["fit_statistics"]["cp_J_per_mol_K"]["cell_count"] > 0


@pytest.mark.parametrize("cp", [0.0, -5.0])
def test_positive_cp_gate_rejects_nonpositive_fitted_cp(cp: float) -> None:
    segment = _constant_cp_segment(
        cp, 298.15, 1000.0, H_at_anchor_J_per_mol=0.0,
        S_at_anchor_J_per_mol_K=0.0,
    )
    result = fit_compilation_thermo_range(
        record=_record("Fe", state="ref"),
        series=_source_series(segment, [298.15, 400, 500, 600, 700, 800, 900, 1000]),
        phase_label="cr",
        standard_state="condensed_solid",
        T_min_K=298.15,
        T_max_K=1000.0,
        is_element_reference=True,
    )
    assert result.record["reason_code"] == "cp_nonpositive"


def test_zero_and_below_minimum_cells_have_typed_exclusions() -> None:
    segment = _constant_cp_segment(30.0, 100.0, 1000.0)
    result = fit_compilation_thermo_range(
        record=_record("Fe", state="ref"),
        series=_source_series(segment, [0.0, 50.0, 100, 200, 298.15, 400, 600, 800, 1000]),
        phase_label="cr",
        standard_state="condensed_solid",
        T_min_K=100.0,
        T_max_K=1000.0,
        is_element_reference=True,
    )
    codes = {(cell["T_K"], cell["reason_code"]) for cell in result.record["excluded_cells"]}
    assert (0.0, "nonpositive_fit_temperature") in codes
    assert (50.0, "below_fit_T_min") in codes
    assert result.record["status"] == "accepted", result.record


def test_janaf_o029_formation_gibbs_zero_pin() -> None:
    assert (TABLES_DIR / "O-029.yaml").is_file()
    records = generate_janaf_sidecar(table_ids=["O-029"])
    oxygen = next(record for record in records if record["source_record_id"] == "O-029")
    assert oxygen["fit_statistics"]["derived_formation_G_kJ_per_mol"]["max_abs"] == 0.0
