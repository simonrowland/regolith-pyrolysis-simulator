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


# ---------------------------------------------------------------------------
# b-311: an absent sticking coefficient is not a perfectly non-sticking wall.
# The two production record-consumption sites previously read a missing /
# unparseable ``alpha_s`` as 0.0 — indistinguishable from a measured
# category-3 non-sticking surface, and both report a clean furnace. The
# sites now refuse absence BY NAME (same ``DepositionInputRefusal`` as
# b-304); only an explicitly configured 0.0 keeps the category-3 zero.
# ---------------------------------------------------------------------------


def test_required_record_alpha_s_refuses_absent_key():
    """A record that never carried a sticking coefficient is an unknown,
    not a measured 0.0."""
    with pytest.raises(
        condensation.DepositionInputRefusal,
        match="parameter=alpha_s",
    ):
        condensation._required_record_alpha_s(
            {"species": "Fe"}, species="Fe", site="test_site"
        )


def test_required_record_alpha_s_refuses_none_value():
    with pytest.raises(
        condensation.DepositionInputRefusal,
        match="no sticking coefficient recorded",
    ):
        condensation._required_record_alpha_s(
            {"alpha_s": None}, species="Fe", site="test_site"
        )


def test_required_record_alpha_s_refuses_unparseable_marked_record():
    """The ``_coerce_alpha_s`` unparseable branch keeps its b-149 zero+note
    contract, but the marker now bars the record from the flux path: a
    coefficient minted out of an unparseable spec is an unknown, not a
    measurement."""
    record = condensation._alpha_record(
        species="Fe",
        entry={"status": "CITED", "source": "metadata-only, no value"},
        source="test",
    )
    assert record["alpha_s"] == 0.0
    assert record["alpha_s_unparseable"] is True
    with pytest.raises(
        condensation.DepositionInputRefusal,
        match="unparseable sticking coefficient spec",
    ):
        condensation._required_record_alpha_s(
            record, species="Fe", site="test_site"
        )


def test_required_record_alpha_s_passes_explicit_zero_and_measured_values():
    """An explicitly configured 0.0 is the category-3 real limit and must
    NOT refuse; declared values in [0, 1] pass through unchanged."""
    zero_record = condensation._alpha_record(
        species="Fe",
        entry={"value": 0.0, "status": "CITED", "source": "measured"},
        source="test",
    )
    assert condensation._required_record_alpha_s(
        zero_record, species="Fe", site="test_site"
    ) == 0.0
    value_record = condensation._alpha_record(
        species="Fe",
        entry={"value": 0.02, "status": "CITED", "source": "measured"},
        source="test",
    )
    assert condensation._required_record_alpha_s(
        value_record, species="Fe", site="test_site"
    ) == pytest.approx(0.02)


def _configured_model_with_materials(materials):
    model = condensation.CondensationModel(
        CondensationTrain.create_default(), materials=materials
    )
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


def test_stage_deposition_refuses_metadata_only_alpha_entry():
    """Site 1 (stage deposition): a materials entry with no parseable
    coefficient used to reach the stage loop as a silent 0.0 (clean stage);
    it now refuses by name through the real route() path."""
    model = _configured_model_with_materials(
        {
            "stages": {
                1: {
                    "alpha_s_by_species": {
                        "Fe": {"status": "CITED", "source": "no value"}
                    }
                }
            }
        }
    )
    with pytest.raises(
        condensation.DepositionInputRefusal,
        match="parameter=alpha_s",
    ):
        model.route(
            EvaporationFlux(species_kg_hr={"Fe": 1.0}, total_kg_hr=1.0),
            MeltState(temperature_C=1700.0),
        )


def test_wall_segment_alpha_refuses_metadata_only_entry():
    """Site 2 (wall-segment max): a metadata-only wall-surface entry used to
    read as 0.0 across every segment (clean furnace); it now refuses by
    name through the real route() path."""
    model = _configured_model_with_materials(
        {
            "wall_surfaces": {
                "interstage_duct": {
                    "alpha_s_by_species": {
                        "Fe": {"status": "CITED", "source": "no value"}
                    }
                }
            }
        }
    )
    assert model._mixed_temperature_wall_candidate_segments("Fe")
    with pytest.raises(
        condensation.DepositionInputRefusal,
        match="parameter=alpha_s",
    ):
        model.route(
            EvaporationFlux(species_kg_hr={"Fe": 1.0}, total_kg_hr=1.0),
            MeltState(temperature_C=1700.0),
        )


def test_explicit_zero_stage_alpha_stays_category3():
    """Guard against overreach: an explicitly configured 0.0 stage entry is
    a declared non-sticking surface, not an absence — the run must route
    without refusal."""
    model = _configured_model_with_materials(
        {
            "stages": {
                1: {
                    "alpha_s_by_species": {
                        "Fe": {
                            "value": 0.0,
                            "status": "CITED",
                            "source": "measured non-sticking",
                        }
                    }
                }
            }
        }
    )
    route = model.route(
        EvaporationFlux(species_kg_hr={"Fe": 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1700.0),
    )
    # Routed mass closes: condensed + wall + remaining accounts for all Fe.
    accounted = (
        route.condensed_for_species("Fe")
        + route.wall_deposit_by_species.get("Fe", 0.0)
        + route.remaining_by_species.get("Fe", 0.0)
        + route.retained_in_source_by_species.get("Fe", 0.0)
    )
    assert accounted == pytest.approx(1.0, abs=1e-9)


def test_series_flux_zero_local_pressure_stays_zero():
    """Category 3: no local pressure means no impingement — a true zero,
    not a refusal."""
    assert _series_call(P_local_pa=0.0) == 0.0


def test_series_flux_zero_driving_pressure_stays_zero():
    """Category 3: P_local below the wall saturation pressure gives a
    non-positive driving pressure — a true zero deposit."""
    assert (
        condensation._series_resistance_deposition_flux_mol_m2_s(
            "Na",
            1e-6,
            1200.0,
            0.5,
            pipe_diameter_m=0.12,
            T_gas_K=1700.0,
            overhead_pressure_pa=1000.0,
        )
        == 0.0
    )


def test_wall_alpha_s_wrapper_refuses_metadata_only_entry():
    """Class closure for the per-segment wall-flux query path
    (``simulator/accounting/queries.py`` calls ``_wall_alpha_s`` and treats
    ``alpha_s <= 0.0`` as a clean zero deposit): absence/unparseable must
    refuse at the wrapper, not read as a measured non-sticking surface."""
    with pytest.raises(
        condensation.DepositionInputRefusal,
        match="parameter=alpha_s",
    ):
        condensation._wall_alpha_s(
            "Fe",
            {
                "wall_surfaces": {
                    "interstage_duct": {
                        "alpha_s_by_species": {
                            "Fe": {"status": "CITED", "source": "no value"}
                        }
                    }
                }
            },
            T_K=1200.0,
        )


def test_wall_alpha_s_wrapper_healthy_and_explicit_zero():
    """Same wrapper: the cited sidecar Fe value loads, and an explicitly
    configured 0.0 stays the category-3 real limit (no refusal)."""
    assert condensation._wall_alpha_s("Fe", T_K=1200.0) == pytest.approx(0.02)
    assert (
        condensation._wall_alpha_s(
            "Fe",
            {
                "wall_surfaces": {
                    "interstage_duct": {
                        "alpha_s_by_species": {
                            "Fe": {
                                "value": 0.0,
                                "status": "CITED",
                                "source": "measured non-sticking",
                            }
                        }
                    }
                }
            },
            T_K=1200.0,
        )
        == 0.0
    )


def test_arrhenius_alpha_underflow_refuses_out_of_domain():
    """Secondary b-311 route: A*exp(-B/T) is strictly positive
    mathematically, so an exact 0.0 is floating-point underflow far below
    the fit's valid range — out-of-domain, not a non-sticking zero."""
    spec = {
        "form": "arrhenius",
        "A": 0.52,
        "B": 3685.0,
        "valid_range_K": [1000.0, 1800.0],
        "uncertainty_envelope": [0.003, 0.067],
        "cite": "Wetzel&Gail 2013",
        "status": "UNCERTIFIED",
    }
    with pytest.raises(ValueError, match="underflowed.*out-of-domain"):
        condensation.alpha_s("Hypothetical", 1.0, {"coefficient_spec": spec})


def test_arrhenius_alpha_denormal_above_underflow_still_evaluates():
    """Just above the underflow point the evaluation still returns the
    (denormal) value and marks it extrapolated, not a fabricated zero."""
    spec = {
        "form": "arrhenius",
        "A": 0.52,
        "B": 3685.0,
        "valid_range_K": [1000.0, 1800.0],
        "uncertainty_envelope": [0.003, 0.067],
        "cite": "Wetzel&Gail 2013",
        "status": "UNCERTIFIED",
    }
    context = {"coefficient_spec": spec}
    value = condensation.alpha_s("Hypothetical", 5.0, context)
    assert 0.0 < value < 1e-300
    assert context["alpha_s_evaluation"]["alpha_s_extrapolated"] is True


def test_sio_cold_wall_override_survives_underflow_floor():
    """The documented SiO cold-wall contract (Pound high-supersaturation
    limit below the Arrhenius validity floor) still owns the underflow
    zone instead of inheriting the refusal."""
    spec = {
        "form": "arrhenius",
        "A": 0.52,
        "B": 3685.0,
        "valid_range_K": [1000.0, 1800.0],
        "uncertainty_envelope": [0.003, 0.067],
        "cite": "Wetzel&Gail 2013",
        "status": "UNCERTIFIED",
    }
    context = {"coefficient_spec": spec}
    assert condensation._condensation_alpha_s("SiO", 1.0, context) == 1.0
    evaluation = context["alpha_s_evaluation"]
    assert evaluation["alpha_s_cold_wall_condensation"] is True
    assert evaluation["alpha_s_form"] == "cold_wall_condensation"
