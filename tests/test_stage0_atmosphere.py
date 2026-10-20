from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.core import Atmosphere, CampaignPhase, PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend


C0_ENDPOINT_SETPOINTS = {
    "temp_range_C": [20, 950],
    "dT_dt_C_per_hr": 50,
    "max_hold_hr": 25,
    "soft_endpoint": {
        "min_hold_hr": 10,
        "temperature_min_C": 940,
    },
}


def _sim(feedstocks, *, finite_headspace=False):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    # Stepping crosses the strict VR-11 vapour boundary even though these tests
    # assert only Stage-0 atmosphere state. Use the canonical schema-v2 catalog
    # instead of the retired empty schema-v1 compatibility shape.
    vapor_pressures = yaml.safe_load(
        (Path(__file__).parent.parent / "data" / "vapor_pressures.yaml").read_text()
    )
    return PyrolysisSimulator(
        backend,
        {
            "campaigns": {"C0": deepcopy(C0_ENDPOINT_SETPOINTS)},
            "overhead_headspace": {"enabled": finite_headspace, "volume_m3": 1.0},
        },
        feedstocks,
        vapor_pressures,
    )


def test_stage0_lunar_feedstock_uses_hard_vacuum():
    sim = _sim(
        {"lunar": {"label": "Lunar", "composition_wt_pct": {"SiO2": 100.0}}}
    )

    sim.load_batch("lunar")
    sim.start_campaign(CampaignPhase.C0)

    assert sim.melt.atmosphere is Atmosphere.HARD_VACUUM
    assert sim.melt.p_total_mbar == 0.0
    assert sim.melt.pO2_mbar == 0.0


@pytest.mark.parametrize("finite_headspace", [False, True])
def test_stage0_mars_feedstock_uses_surface_co2_backpressure(finite_headspace):
    sim = _sim(
        {
            "mars": {
                "label": "Mars",
                "composition_wt_pct": {"SiO2": 100.0},
                "surface_pressure_mbar": 6,
                "atmosphere": "96% CO2",
            }
        },
        finite_headspace=finite_headspace,
    )

    sim.load_batch("mars")
    sim.start_campaign(CampaignPhase.C0)
    snapshot = sim.step()

    assert sim.melt.atmosphere is Atmosphere.CO2_BACKPRESSURE
    assert sim.melt.p_total_mbar == 6.0
    assert sim.melt.pO2_mbar == 0.0
    assert snapshot.overhead.pressure_mbar == 6.0
    assert snapshot.overhead.composition["CO2"] == 5.76
    # Pin both overhead implementations before consolidating the cover-gas rule.
    assert snapshot.overhead.composition["CO2"].hex() == (5.76).hex()


def test_all_builtin_mars_feedstocks_define_co2_environment():
    data_path = Path(__file__).parent.parent / "data" / "feedstocks.yaml"
    feedstocks = yaml.safe_load(data_path.read_text())

    for key, feedstock in feedstocks.items():
        if not key.startswith("mars_"):
            continue
        sim = _sim({key: feedstock})
        required_c = 0.0
        if PyrolysisSimulator._uses_mars_carbon_cleanup(feedstock):
            required_c = PyrolysisSimulator._carbon_reductant_required_kg(
                feedstock, 1000.0)
        additives = {"C": required_c} if required_c > 0.0 else None
        sim.load_batch(key, additives_kg=additives)

        assert sim.melt.ambient_pressure_mbar > 0.0
        assert sim.melt.ambient_atmosphere == "96% CO2"


@pytest.mark.parametrize('finite_headspace', [False, True])
def test_controlled_background_pressure_pin_before_owner_reuse(finite_headspace):
    from simulator.overhead import OverheadGasModel
    from simulator.state import CondensationTrain, EvaporationFlux, MeltState

    melt = MeltState()
    melt.atmosphere = Atmosphere.CONTROLLED_O2
    melt.p_total_mbar = 1000.0
    melt.pO2_mbar = 900.0
    melt.background_gas_species = 'CO2'
    melt.background_gas_mole_fraction = 1.0
    model = OverheadGasModel({'enabled': finite_headspace, 'volume_m3': 1.0})
    gas = model.update(EvaporationFlux(), melt, CondensationTrain.create_default())
    # Independent Dalton-law anchor: the commanded oxygen occupies 900 of
    # 1000 mbar. Pin before reusing the two existing background projections.
    assert gas.composition['CO2'].hex() == (100.0).hex()
    assert gas.composition['O2'].hex() == (900.0).hex()
