"""Pin existing heat outputs before extending trace thermochemistry."""

import json
from pathlib import Path

import pytest

from simulator.config import load_config_bundle
from simulator.thermal_budget import evaporation_enthalpy_budget
from simulator.vapour_rail.catalog import compile_vapour_rail_catalog


@pytest.mark.parametrize("temperature_K", [1473.15, 1923.15])
def test_existing_energy_outputs_are_unchanged(temperature_K):
    pins = json.loads((Path(__file__).parent / "fixtures" /
                       "t1139_b4_energy_before.json").read_text())
    legacy = compile_vapour_rail_catalog(
        load_config_bundle().vapor_pressures.catalog_payload,
        emit_u0_request_rules=False,
    ).legacy_view()
    result = evaporation_enthalpy_budget(
        pins["species_kg_hr"], vapor_pressures=legacy,
        temperature_K=temperature_K,
    )
    assert result == pins["outputs"][str(temperature_K)]
