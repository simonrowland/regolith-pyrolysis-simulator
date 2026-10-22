"""C4 integrated-baseline pins, captured before moving pressure projections.

Expected snapshots are produced by the production resolvers, never a test-side
EOS or transport implementation. Hex strings make rounding changes visible.
"""
from types import SimpleNamespace

import pytest

from simulator.condensation import _knudsen_number, _mean_free_path_m
from simulator.environment import ideal_gas_partial_pressures_bar
from simulator.overhead import OverheadGasModel
from simulator.runner import _carrier_pressure_observables
from simulator.state import Atmosphere, CampaignPhase, CondensationTrain, EvaporationFlux, MeltState
from tests.test_capacity_coupling import _o2_fixture, _real_capacity_sim
from tests.test_overhead_accounting import _gas_train_sim


def _hex(value):
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, dict):
        return {str(k): _hex(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_hex(v) for v in value]
    return value


def projection_outputs():
    holdup = {"Fe": 0.123456789, "O2": 0.75, "Kr": 2.5, "zero": 0.0, "negative": -1.0}
    volume, temperature = 0.137, 1873.15
    output = {"overhead": OverheadGasModel._compute_partial_pressures(holdup, volume, temperature)}
    assert output["overhead"] == ideal_gas_partial_pressures_bar(holdup, volume, temperature)
    sim = _gas_train_sim()
    sim._headspace_volume_m3 = lambda: volume
    sim._headspace_temperature_K = lambda: temperature
    output["core"] = [sim._headspace_ledger_pO2_bar_from_o2_mol(n) for n in (0.0, 0.123456789, 0.75)]
    output["inverse"] = [sim._headspace_o2_mol_for_pO2_bar(p) for p in (0.0, 0.00123, 1.0)]
    output["native_fe"] = sim._compute_native_fe_saturation_extent(
        {"FeO": 100.0}, fe3_over_sigma_fe=0.1,
        T_K=temperature, pressure_bar=0.0102, fO2_log=-12.0,
    )
    native_sim = _real_capacity_sim()
    native_sim.melt.temperature_C = 1600.0
    native_sim._melt_headspace_composition_mbar = {"Fe": 0.001, "O2": 0.2, "Kr": 10.0}
    output["native_fe_source"] = []
    for target in (0.0, 12.0):
        native_sim.melt.p_total_mbar = target
        output["native_fe_source"].append({
            "pressure_bar": native_sim._melt_headspace_total_pressure_bar(),
            "extent": native_sim._compute_native_fe_saturation_extent(),
            "partition": native_sim._native_fe_partition_diagnostic(1.0),
        })
    result, _ = _o2_fixture(0.25)
    output["picard"] = {
        "partials": dict(result.partial_pressures_Pa),
        "deltas": result.max_delta_history_Pa,
        "iterations": result.iterations,
        "bled": dict(result.bled_species_mol),
    }
    sim.melt.campaign = CampaignPhase.C2A_STAGED
    sim.melt.background_gas_species = "Kr"
    sim.campaign_mgr.last_c2a_staged_gas_control = {"pO2_mbar": 0.2, "p_total_mbar": 10.2, "pN2_mbar": 10.0}
    sim._sync_c2a_staged_overhead_gas_control()
    output["staged"] = {"pressure": sim.overhead.pressure_mbar, "composition": sim.overhead.composition.copy(),
                        "Kn": _knudsen_number(1020.0, temperature, 0.12, carrier_gas="Kr")}
    flux = EvaporationFlux(total_kg_hr=0.3, species_kg_hr={"Fe": 0.1, "O2": 0.2})
    for body, atmosphere, ambient in (("moon", Atmosphere.PN2_SWEEP, 0.0),
                                      ("mars", Atmosphere.CO2_BACKPRESSURE, 6.1),
                                      ("asteroid", Atmosphere.HARD_VACUUM, 0.0)):
        melt = MeltState(body=body, temperature_C=1600.0, atmosphere=atmosphere,
                         p_total_mbar=10.2 if body != "asteroid" else 0.0,
                         pO2_mbar=0.2, ambient_pressure_mbar=ambient)
        melt.ambient_atmosphere = "CO2" if body == "mars" else ""
        melt.background_gas_species = "N2" if body == "moon" else ""
        gas = OverheadGasModel({"enabled": False}).update(flux, melt, CondensationTrain.create_default())
        output[body] = {"pressure": gas.pressure_mbar, "composition": gas.composition,
                        "carrier_report": _carrier_pressure_observables(SimpleNamespace(melt=melt), SimpleNamespace(overhead=gas))}
    return _hex(output)


def test_integrated_pressure_projections_exact():
    from tests.fixtures.pressure_projection_pin import EXPECTED
    assert projection_outputs() == EXPECTED


@pytest.mark.parametrize("volume,temperature", [(0.0, 1800.0), (1.0, 0.0), (-1.0, 1800.0)])
def test_eos_zero_shortcut(volume, temperature):
    assert OverheadGasModel._compute_partial_pressures({"O2": 1.0}, volume, temperature) == {}


@pytest.mark.parametrize("value", [None, "bad", [1.0]])
def test_eos_invalid_scalar_preserves_error(value):
    with pytest.raises((TypeError, ValueError)):
        OverheadGasModel._compute_partial_pressures({"O2": value}, 1.0, 1800.0)


def test_transport_wrappers_use_owner_and_vacuum_limit():
    from simulator.transport_regime import single_species_mean_free_path_m
    assert _mean_free_path_m(1020.0, 1873.15, 3.6e-10) == single_species_mean_free_path_m(1020.0, 1873.15, 3.6e-10)
    assert _knudsen_number(0.0, 1873.15, 0.12) == float("inf")
    assert _mean_free_path_m(1.0, 0.0, 3.6e-10) == 0.0
