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
