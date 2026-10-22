"""Additional C4 pins for the remaining shared Dalton/background copies."""
from itertools import product

import pytest

from simulator.condensation import _flowing_species_partial_pressures_pa
from simulator.overhead import OverheadGasModel, sweep_boundary_partials_bar
from simulator.state import Atmosphere, CondensationTrain, EvaporationFlux, MeltState
from tests.test_pressure_projection_pins import _hex
from tests.fixtures.pressure_background_pin import BACKGROUND


def background_outputs():
    output = {}
    for finite, fill, holdup in (
        (False, "none", None), (True, "empty", {}),
        (True, "loaded", {"Fe": 0.03, "O2": 0.5, "Kr": 1.0}),
    ):
        for atmosphere, pressure in product(Atmosphere, (0.0, 10.2)):
            melt = MeltState(temperature_C=1600.0, atmosphere=atmosphere,
                             p_total_mbar=pressure, pO2_mbar=0.2 if pressure else 0.0)
            melt.background_gas_species = "Kr"
            melt.background_gas_mole_fraction = 0.5
            melt.ambient_atmosphere = "CO2"
            flux = EvaporationFlux(total_kg_hr=0.3, species_kg_hr={"Fe": 0.1, "O2": 0.2})
            model = OverheadGasModel({"enabled": finite, "volume_m3": 0.137})
            gas = model.update(flux, melt, CondensationTrain.create_default(),
                               overhead_holdup_mol=holdup)
            output[f"{finite}:{fill}:{atmosphere.name}:{pressure}"] = {
                "pressure": gas.pressure_mbar, "composition": gas.composition,
            }
    return _hex(output)


def flowing_outputs():
    output = {
        "sweep_mixture": sweep_boundary_partials_bar(
            {"O2": 0.75, "Kr": 2.5}, pressure_bar=0.0102, temperature_K=1873.15,
        ),
        "sweep_zero": sweep_boundary_partials_bar(
            {}, pressure_bar=0.0102, temperature_K=1873.15,
        ),
    }
    for name, rates in {
        "mixture": {"Fe": 0.1, "O2": 0.2, "Kr": 0.3},
        "zero": {"Fe": 0.0}, "unknown": {"unregistered": 1.0},
        "scalar": {"Fe": "0.1", "O2": 0.2},
        "invalid": {"Fe": None, "O2": 0.2},
    }.items():
        try:
            output[name] = _flowing_species_partial_pressures_pa(rates, 1020.0)
        except (TypeError, ValueError) as exc:
            output[name] = {"error_type": type(exc).__name__, "error": str(exc)}
    return _hex(output)


@pytest.fixture(scope="module")
def background_actual():
    return background_outputs()


@pytest.mark.parametrize("key", BACKGROUND)
def test_background_floor_projection_exact(key, background_actual):
    assert background_actual[key] == BACKGROUND[key]


def test_flowing_dalton_projection_exact():
    from tests.fixtures.pressure_background_pin import FLOWING
    assert flowing_outputs() == FLOWING
