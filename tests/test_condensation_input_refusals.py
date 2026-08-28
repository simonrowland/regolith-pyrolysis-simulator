import math

import pytest

from simulator import condensation
from simulator.state import (
    CondensationTrain,
    EvaporationFlux,
    MeltState,
    PipeSegment,
)


def _configured_model() -> condensation.CondensationModel:
    model = condensation.CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Fe": 1.0},
        pipe_diameter_m=0.12,
        gas_temperature_C=1700.0,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    return model


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_minimum_knudsen_pressure_refuses_nonfinite_temperature(value):
    with pytest.raises(
        condensation.KnudsenRegimeRefusal,
        match="gas_temperature_C must be finite and above absolute zero",
    ):
        condensation.minimum_pressure_mbar_for_knudsen(
            gas_temperature_C=value,
            pipe_diameter_m=0.12,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "overhead_pressure_mbar",
            math.nan,
            "overhead_pressure_mbar must be finite and non-negative",
        ),
        (
            "overhead_pressure_mbar",
            math.inf,
            "overhead_pressure_mbar must be finite and non-negative",
        ),
        (
            "gas_temperature_C",
            math.nan,
            "gas_temperature_C must be finite and above absolute zero",
        ),
        (
            "gas_temperature_C",
            math.inf,
            "gas_temperature_C must be finite and above absolute zero",
        ),
    ],
)
def test_knudsen_diagnostic_refuses_nonfinite_public_inputs(
    field,
    value,
    message,
):
    inputs = {
        "overhead_pressure_mbar": 10.0,
        "gas_temperature_C": 1700.0,
        "pipe_diameter_m": 0.12,
    }
    inputs[field] = value

    with pytest.raises(condensation.KnudsenRegimeRefusal, match=message):
        condensation.knudsen_regime_diagnostic(**inputs)


@pytest.mark.parametrize("rate_kg_hr", [math.nan, math.inf, -1.0])
def test_cold_spot_diagnostic_refuses_invalid_flow_rate(rate_kg_hr):
    segment = PipeSegment(
        name="cold",
        upstream_stage="stage_0",
        downstream_stage="stage_1",
        wall_temperature_C=1000.0,
        length_m=1.0,
        inner_diameter_m=0.12,
    )

    with pytest.raises(
        ValueError,
        match="vapor flow for Fe must be finite and non-negative",
    ):
        condensation.cold_spot_diagnostic(
            [segment],
            {"Fe": rate_kg_hr},
            upstream_hot_wall_min_C=None,
        )


@pytest.mark.parametrize("margin_C", [math.nan, math.inf, -1.0])
def test_cold_spot_diagnostic_refuses_invalid_margin(margin_C):
    with pytest.raises(
        ValueError,
        match="margin_C must be finite and non-negative",
    ):
        condensation.cold_spot_diagnostic(
            [],
            {"Fe": 1.0},
            margin_C=margin_C,
        )


@pytest.mark.parametrize("rate_kg_hr", [math.nan, math.inf, -1.0])
def test_route_refuses_invalid_species_inlet_mass(rate_kg_hr):
    flux = EvaporationFlux(
        species_kg_hr={"Fe": rate_kg_hr},
        total_kg_hr=rate_kg_hr,
    )

    with pytest.raises(
        ValueError,
        match="evaporated mass flow for Fe must be finite and non-negative",
    ):
        _configured_model().route(flux, MeltState(temperature_C=1700.0))


@pytest.mark.parametrize("inventory_kg", [math.nan, math.inf, -1.0])
def test_stage_purity_report_refuses_invalid_inventory(inventory_kg):
    train = CondensationTrain.create_default()
    train.stages[1].collected_kg["Fe"] = inventory_kg

    with pytest.raises(
        ValueError,
        match=(
            f"stage {train.stages[1].stage_number} inventory for Fe "
            "must be finite and non-negative"
        ),
    ):
        condensation.stage_purity_report(train)


def _efficiency_stage(model):
    return next(stage for stage in model.train.stages if stage.stage_number == 1)


@pytest.mark.parametrize("residence_s", [math.nan, math.inf, -math.inf])
def test_condensation_efficiency_refuses_nonfinite_residence(residence_s):
    model = _configured_model()
    with pytest.raises(ValueError, match="residence_s must be finite"):
        model._condensation_efficiency(
            stage=_efficiency_stage(model),
            species="Fe",
            T_cond_C=1250.0,
            residence_s=residence_s,
            available_kg=1.0,
            alpha_s_value=0.5,
        )


@pytest.mark.parametrize("alpha_s_value", [math.nan, math.inf, -math.inf])
def test_condensation_efficiency_refuses_nonfinite_alpha(alpha_s_value):
    model = _configured_model()
    with pytest.raises(ValueError, match="alpha_s_value must be finite"):
        model._condensation_efficiency(
            stage=_efficiency_stage(model),
            species="Fe",
            T_cond_C=1250.0,
            residence_s=5.0,
            available_kg=1.0,
            alpha_s_value=alpha_s_value,
        )


def test_condensation_efficiency_refuses_nonfinite_eta(monkeypatch):
    model = _configured_model()
    monkeypatch.setattr(
        condensation,
        "_series_resistance_deposition_flux_mol_m2_s",
        lambda *args, **kwargs: math.inf,
    )
    with pytest.raises(ValueError, match="condensation efficiency for Fe in stage 1 is not finite"):
        model._condensation_efficiency(
            stage=_efficiency_stage(model),
            species="Fe",
            T_cond_C=1250.0,
            residence_s=5.0,
            available_kg=1.0,
            alpha_s_value=0.5,
        )


# ---------------------------------------------------------------------------
# b-304: degenerate inputs to the deposition-flux helpers refuse via
# ``DepositionInputRefusal`` — never a silent 0.0 with an empty diagnostic.
# For a deposition model a clean zero is failing OPEN: zero wall deposit is
# the optimistic answer and propagates to ``campaigns_to_resinter`` ->
# "this furnace never needs re-sintering".
# ---------------------------------------------------------------------------

_SERIES_SIO_KWARGS = {
    "species": "SiO",
    "P_local_pa": 100.0,
    "T_surface_K": 1500.0,
    "alpha_s": 0.7,
    "pipe_diameter_m": 0.12,
    "T_gas_K": 1700.0,
    "overhead_pressure_pa": 1000.0,
}


def _series_call(**overrides):
    kwargs = dict(_SERIES_SIO_KWARGS)
    kwargs.update(overrides)
    return condensation._series_resistance_deposition_flux_mol_m2_s(**kwargs)


@pytest.mark.parametrize(
    "bad_field,bad_value",
    [
        ("T_surface_K", math.nan),
        ("T_surface_K", math.inf),
        ("T_surface_K", -1.0),
        ("T_surface_K", 0.0),
        ("P_local_pa", math.nan),
        ("P_local_pa", math.inf),
        ("P_local_pa", -math.inf),
        ("alpha_s", math.nan),
        ("alpha_s", math.inf),
        ("alpha_s", -1.0),
        ("alpha_s", 1.5),
        ("pipe_diameter_m", math.nan),
        ("pipe_diameter_m", 0.0),
        ("pipe_diameter_m", -0.12),
        ("T_gas_K", math.nan),
        ("T_gas_K", math.inf),
        ("T_gas_K", 0.0),
        ("T_gas_K", -100.0),
    ],
)
def test_series_flux_degenerate_inputs_refuse(bad_field, bad_value):
    """b-304 category 1 (missing/invalid input): degenerate geometry,
    non-physical temperatures, sticking coefficients outside [0, 1], and
    any non-finite input must REFUSE via ``DepositionInputRefusal`` naming
    the offending parameter. A zero-diameter pipe is not a pipe that
    deposits nothing; it is not a pipe."""
    diagnostic = {}
    with pytest.raises(
        condensation.DepositionInputRefusal,
        match=f"parameter={bad_field}",
    ):
        _series_call(**{bad_field: bad_value}, diagnostic_out=diagnostic)
    # The refusal mints no flux and no optimistic diagnostic keys.
    assert diagnostic == {}


@pytest.mark.parametrize("bad_value", ["bad", True, None])
def test_series_flux_non_numeric_inputs_refuse(bad_value):
    """Non-numeric / boolean inputs are category-1 invalid input, not a
    zero answer (bool rejected per the ``_validate_sticking_value``
    'numeric, not boolean' contract)."""
    with pytest.raises(
        condensation.DepositionInputRefusal,
        match="parameter=alpha_s",
    ):
        _series_call(alpha_s=bad_value)


def test_series_flux_refusal_is_terminal_value_error():
    """The refusal subclasses ValueError (the module invalid-input
    convention: ``coating_rate.continuous_wall_deposition_flux``,
    ``_alpha_s_evaluation``, ``_condensation_efficiency``) and carries
    ``terminal_refusal`` so the engine restores the attempted hour
    (``core._restore_terminal_refusal_hour_state``) instead of poisoning
    the run."""
    assert issubclass(condensation.DepositionInputRefusal, ValueError)
    with pytest.raises(condensation.DepositionInputRefusal) as excinfo:
        _series_call(pipe_diameter_m=0.0)
    assert excinfo.value.terminal_refusal is True
    assert excinfo.value.parameter == "pipe_diameter_m"


# ``_hkl_surface_deposition_flux_mol_m2_s`` gets the same gate. Pre-fix it
# had NO input gate at all: alpha_s multiplies the flux directly, so
# alpha_s = -1 returned a NEGATIVE deposition flux (silently un-depositing
# wall inventory) and non-finite pressure could mint +inf into the ledger.

_HKL_SIO_KWARGS = {
    "species": "SiO",
    "P_local_pa": 100.0,
    "T_surface_K": 1500.0,
    "alpha_s": 0.7,
}


def _hkl_call(**overrides):
    kwargs = dict(_HKL_SIO_KWARGS)
    kwargs.update(overrides)
    return condensation._hkl_surface_deposition_flux_mol_m2_s(**kwargs)


@pytest.mark.parametrize(
    "bad_field,bad_value",
    [
        ("alpha_s", -1.0),
        ("alpha_s", 1.5),
        ("alpha_s", math.nan),
        ("alpha_s", math.inf),
        ("T_surface_K", 0.0),
        ("T_surface_K", -50.0),
        ("T_surface_K", math.nan),
        ("P_local_pa", math.nan),
        ("P_local_pa", math.inf),
    ],
)
def test_hkl_surface_flux_degenerate_inputs_refuse(bad_field, bad_value):
    """b-304 category 1: the HKL surface helper refuses degenerate input
    by name instead of silently multiplying it into the flux."""
    with pytest.raises(
        condensation.DepositionInputRefusal,
        match=f"parameter={bad_field}",
    ):
        _hkl_call(**{bad_field: bad_value})


def test_hkl_surface_flux_zero_alpha_returns_zero():
    """Category 3 (real limit), same justification as the series helper:
    a perfectly non-sticking surface deposits nothing."""
    assert _hkl_call(alpha_s=0.0) == 0.0


def test_hkl_surface_flux_healthy_input_unchanged():
    """Continuity: a healthy call still returns the alpha-weighted HKL
    impingement flux (the gate adds refusals, not new physics)."""
    assert _hkl_call() > 0.0
