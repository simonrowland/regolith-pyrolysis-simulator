"""b-312: coating-rail invalid input must refuse, not mint a clean zero.

A false "clean" verdict green-lights a recipe that destroys the furnace;
a false "fouling" verdict only costs a replan. Each test names the
condition it pins. Reverting the matching production site turns that
test red.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from simulator.accounting.exceptions import AccountingError
from simulator.accounting.queries import (
    _wall_geometry_conductance_weight,
    wall_deposit_candidate_for_surface_kg,
    wall_deposit_candidates_by_segment_kg,
)
from simulator.condensation import (
    CondensationModel,
    DepositionInputRefusal,
    _series_resistance_deposition_flux_mol_m2_s,
)
from simulator.stage0_foulant_report_markdown import (
    _format_kg,
    format_stage0_foulant_report_markdown,
)
from simulator.state import CondensationTrain, PipeSegment


# ---------------------------------------------------------------------------
# F5: NaN regime_factor was computed as viscous, understating deposition ~120x
# ---------------------------------------------------------------------------

_FE_F5_KWARGS = {
    "species": "Fe",
    "P_local_pa": 100.0,
    "T_surface_K": 1200.0,
    "alpha_s": 0.5,
    "pipe_diameter_m": 0.12,
    "T_gas_K": 1700.0,
    "overhead_pressure_pa": 1000.0,
}


def _fe_series(**overrides):
    kwargs = dict(_FE_F5_KWARGS)
    kwargs.update(overrides)
    return _series_resistance_deposition_flux_mol_m2_s(**kwargs)


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_f5_nonfinite_regime_factor_refuses_by_name(bad_value):
    """NaN / +/-inf regime_factor must raise DepositionInputRefusal naming
    regime_factor. Pre-fix, NaN produced the viscous flux (0.00589) instead
    of refusing, 120x below free-molecular (0.710)."""
    with pytest.raises(
        DepositionInputRefusal,
        match="parameter=regime_factor",
    ):
        _fe_series(regime_factor=bad_value)


def test_f5_nan_regime_factor_is_not_the_viscous_flux():
    """What would make F5 wrong: coercing NaN to f=0 (viscous), the
    pre-fix bound that understated Fe deposition 120x. Evidence it is
    absent: NaN refuses, and the viscous/free-mol pair still has the
    ~120x gap the coerce was hiding."""
    flux_freemol = _fe_series(regime_factor=1.0)
    flux_viscous = _fe_series(regime_factor=0.0)
    assert flux_freemol == pytest.approx(0.710, rel=1e-2)
    assert flux_viscous == pytest.approx(0.00589, rel=1e-2)
    assert flux_freemol / flux_viscous > 100.0
    with pytest.raises(DepositionInputRefusal, match="parameter=regime_factor"):
        _fe_series(regime_factor=math.nan)


def test_f5_finite_out_of_range_regime_factor_still_clamps():
    """What would make F5 wrong: refusing regime_factor=2, or letting it
    take the unbounded-HKL shortcut the clamp exists to stop. Evidence
    it is absent: f=2 still equals f=1, f=-1 still equals f=0."""
    assert _fe_series(regime_factor=2.0) == pytest.approx(
        _fe_series(regime_factor=1.0), rel=1e-12,
    )
    assert _fe_series(regime_factor=-1.0) == pytest.approx(
        _fe_series(regime_factor=0.0), rel=1e-12,
    )


# ---------------------------------------------------------------------------
# F3: degenerate segment geometry silently omitted the segment
# ---------------------------------------------------------------------------

def _segment(**overrides) -> PipeSegment:
    kwargs = dict(
        name="ok",
        upstream_stage="stage_0",
        downstream_stage="stage_1",
        wall_temperature_C=900.0,
        length_m=1.0,
        inner_diameter_m=0.12,
    )
    kwargs.update(overrides)
    return PipeSegment(**kwargs)


def _model_with(segment: PipeSegment, *, gas_temperature_C: float = 1600.0):
    model = CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Fe": 1.0},
        pipe_diameter_m=0.12,
        gas_temperature_C=gas_temperature_C,
        campaign_name="C2A",
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    model.pipe_segments = [segment]
    model.lab_geometry = object()
    return model


def test_f3_valid_segment_still_deposits():
    segment = _segment()
    result = wall_deposit_candidates_by_segment_kg(
        _model_with(segment),
        species="Fe",
        rate_kg_hr=1.0,
        T_cond_C=1250.0,
        melt_temperature_C=1700.0,
        supply_by_segment_kg={segment.name: 1.0},
    )
    assert segment.name in result
    assert result[segment.name] > 0.0


@pytest.mark.parametrize(
    "overrides,named_condition",
    [
        ({"length_m": 0.0}, "length_m"),
        ({"declared_area_m2": -5.0}, "declared_area_m2"),
        ({"view_factor_from_melt": -1.0, "line_of_sight_to_melt": True}, "view_factor"),
    ],
)
def test_f3_degenerate_geometry_refuses_by_named_condition(overrides, named_condition):
    """length_m=0, declared_area_m2=-5, and view_factor=-1 with LOS True
    used to drop the segment from the candidate dict (a silent clean
    wall). Invalid geometry must refuse, naming the condition."""
    segment = _segment(**overrides)
    with pytest.raises(AccountingError, match=named_condition):
        wall_deposit_candidates_by_segment_kg(
            _model_with(segment),
            species="Fe",
            rate_kg_hr=1.0,
            T_cond_C=1250.0,
            melt_temperature_C=1700.0,
            supply_by_segment_kg={segment.name: 1.0},
        )


def test_f3_missing_surface_area_attr_refuses():
    """getattr(segment, 'surface_area_m2', 0.0) treated a missing area as
    a proven zero weight. Missing area is not proof of zero deposition."""
    missing = SimpleNamespace(
        name="missing",
        view_factor_from_melt=None,
        line_of_sight_to_melt=None,
    )
    with pytest.raises(
        AccountingError,
        match="surface_area_m2 is missing",
    ):
        _wall_geometry_conductance_weight(missing)


def test_f3_nonnumeric_view_factor_still_refuses():
    """Internal contradiction close-out: non-numeric view factor already
    refused; the numeric-invalid case must agree."""
    segment = _segment(view_factor_from_melt="nope", line_of_sight_to_melt=True)
    with pytest.raises(
        AccountingError,
        match="unknown view factor is not proof of zero",
    ):
        _wall_geometry_conductance_weight(segment)


def test_f3_line_of_sight_false_is_a_legitimate_zero():
    """Category 3: line_of_sight_to_melt=False is a real physical limit,
    not invalid input. Weight 0 and omission from the candidate dict
    are correct. Refusing this case would break working code."""
    segment = _segment(line_of_sight_to_melt=False)
    assert _wall_geometry_conductance_weight(segment) == 0.0
    result = wall_deposit_candidates_by_segment_kg(
        _model_with(segment),
        species="Fe",
        rate_kg_hr=1.0,
        T_cond_C=1250.0,
        melt_temperature_C=1700.0,
        supply_by_segment_kg={segment.name: 1.0},
    )
    assert result == {}


def test_f3_zero_length_pipe_property_names_length_m():
    segment = _segment(length_m=0.0)
    with pytest.raises(ValueError, match="length_m"):
        _ = segment.surface_area_m2


# ---------------------------------------------------------------------------
# F4: +inf / sub-zero-Kelvin gas temperature must not mint a kg number
# ---------------------------------------------------------------------------

def test_f4_infinite_gas_temperature_refuses_by_name():
    """+inf gas temperature used to return 0.0 kg (a clean wall). It must
    raise DepositionInputRefusal naming T_gas_K."""
    segment = _segment()
    model = _model_with(segment)
    model.gas_temperature_C = math.inf
    with pytest.raises(DepositionInputRefusal, match="parameter=T_gas_K"):
        wall_deposit_candidate_for_surface_kg(
            model,
            species="Fe",
            rate_kg_hr=1.0,
            T_cond_C=1250.0,
            melt_temperature_C=1700.0,
            wall_temperature_C=segment.wall_temperature_C,
            surface_area_m2=segment.surface_area_m2,
            segment=segment,
        )


def test_f4_nan_gas_temperature_still_refuses():
    """nan gas temperature already refused (leave that). Must not become
    a silent 0.0 kg."""
    segment = _segment()
    model = _model_with(segment)
    model.gas_temperature_C = math.nan
    with pytest.raises(DepositionInputRefusal, match="parameter=T_gas_K"):
        wall_deposit_candidate_for_surface_kg(
            model,
            species="Fe",
            rate_kg_hr=1.0,
            T_cond_C=1250.0,
            melt_temperature_C=1700.0,
            wall_temperature_C=segment.wall_temperature_C,
            surface_area_m2=segment.surface_area_m2,
            segment=segment,
        )


def test_f4_below_absolute_zero_gas_temperature_refuses():
    """-400 C was clamped to 1 K and computed a number ~200x below the
    valid 1600 C result. Sub-zero-Kelvin gas T is invalid input, not a
    1 K furnace."""
    segment = _segment()
    model = _model_with(segment)
    model.gas_temperature_C = -400.0
    with pytest.raises(
        DepositionInputRefusal,
        match="parameter=T_gas_K",
    ):
        wall_deposit_candidate_for_surface_kg(
            model,
            species="Fe",
            rate_kg_hr=1.0,
            T_cond_C=1250.0,
            melt_temperature_C=1700.0,
            wall_temperature_C=segment.wall_temperature_C,
            surface_area_m2=segment.surface_area_m2,
            segment=segment,
        )


def test_f4_healthy_gas_temperature_still_deposits():
    """What would make F4 wrong: refusing a finite in-range gas
    temperature. Evidence it is absent: 1600 C still returns a positive
    candidate."""
    segment = _segment()
    deposited = wall_deposit_candidate_for_surface_kg(
        _model_with(segment, gas_temperature_C=1600.0),
        species="Fe",
        rate_kg_hr=1.0,
        T_cond_C=1250.0,
        melt_temperature_C=1700.0,
        wall_temperature_C=segment.wall_temperature_C,
        surface_area_m2=segment.surface_area_m2,
        segment=segment,
    )
    assert deposited > 0.0


# ---------------------------------------------------------------------------
# F6: unmeasured Stage-0 foulant must not print as "0 kg"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, "nope", ""])
def test_f6_unmeasured_kg_is_not_printed_as_zero(value):
    """_format_kg(None / 'nope' / '') returned '0', so an omitted
    wall-deposit group looked like a fully accounted non-fouling
    bakeoff on an operator-facing surface."""
    assert _format_kg(value) != "0"
    assert _format_kg(value) == "unmeasured"


def test_f6_measured_zero_still_prints_zero():
    """What would make F6 wrong: rendering a measured 0.0 kg as
    unmeasured. Evidence it is absent: a real zero still prints '0'."""
    assert _format_kg(0.0) == "0"


def test_f6_omitted_group_does_not_look_like_a_clean_bakeoff():
    rendered = format_stage0_foulant_report_markdown({})
    assert "wall-deposited=0 kg" not in rendered
    assert "wall-deposited=unmeasured" in rendered


# ---------------------------------------------------------------------------
# b-304 non-regression
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_field,bad_value",
    [
        ("alpha_s", -1.0),
        ("pipe_diameter_m", 0.0),
        ("pipe_diameter_m", -0.12),
        ("T_surface_K", 0.0),
        ("T_surface_K", -50.0),
        ("T_surface_K", math.nan),
        ("P_local_pa", math.nan),
        ("alpha_s", math.nan),
    ],
)
def test_b304_degenerate_inputs_still_refuse_by_name(bad_field, bad_value):
    kwargs = dict(
        species="SiO",
        P_local_pa=100.0,
        T_surface_K=1500.0,
        alpha_s=0.7,
        pipe_diameter_m=0.12,
        T_gas_K=1700.0,
        overhead_pressure_pa=1000.0,
    )
    kwargs[bad_field] = bad_value
    with pytest.raises(DepositionInputRefusal, match=f"parameter={bad_field}"):
        _series_resistance_deposition_flux_mol_m2_s(**kwargs)


def test_b304_zero_local_pressure_and_driving_pressure_stay_zero():
    assert _series_resistance_deposition_flux_mol_m2_s(
        "SiO", 0.0, 1500.0, 0.7, pipe_diameter_m=0.12,
    ) == 0.0
    assert _series_resistance_deposition_flux_mol_m2_s(
        "Na",
        1.0,
        5000.0,
        1.0,
        pipe_diameter_m=0.12,
    ) == 0.0


def test_b304_healthy_sio_flux_at_ten_pa_1200k_alpha_004():
    flux = _series_resistance_deposition_flux_mol_m2_s(
        "SiO", 10.0, 1200.0, 0.04,
    )
    assert flux == pytest.approx(2.9388e-4, rel=1e-4)
