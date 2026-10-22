"""C11 neutral-move receipts captured before changing the source geometry."""
import json
from pathlib import Path

import pytest

from engines.builtin.evaporation_flux import _series_resistance_evaporation_flux_kg_m2_s
from tests.chemistry.test_builtin_evaporation_flux_provider import _w3_result_with_controls


PINS = json.loads((Path(__file__).parents[1] / "fixtures/c11_legacy_series_pins.json").read_text())


@pytest.mark.parametrize("pin", PINS, ids=lambda p: f"{p['feedstock']}-{p['hour']}-{p['inputs']['species']}")
def test_legacy_series_exact_receipts(pin):
    result = _series_resistance_evaporation_flux_kg_m2_s(**pin["inputs"])
    for key, expected in pin["hex"].items():
        assert getattr(result, key).hex() == expected, key
    assert result.r_melt == 0.0
    assert result.alpha_intrinsic == pin["inputs"]["alpha_i"]
    assert result.transport_length_m == pin["inputs"]["pipe_diameter_m"]
    assert result.transport_length_m in (0.08, 0.16)


def test_fixed_partial_source_is_insensitive_to_net_sweep():
    diagnostics = [
        _w3_result_with_controls(
            {"axial": 6.0, "radial": 1.0},
            overhead_pressure_pa=1000.0,
            overhead_partials_Pa={"Na": 1.0},
            pipe_diameter_m=0.16,
            sweep_mol_per_s=flow,
        ).diagnostic["evaporation_series_resistance"]
        for flow in (0.0, 1.0, 10.0)
    ]
    assert diagnostics[0] == diagnostics[1] == diagnostics[2]
