"""Pre-move binary64 pins for the pressure owners (fd288e7d7)."""

import pytest
from copy import deepcopy

from simulator.core import _feedstock_surface_pressure_mbar
from simulator.chemistry.offgas_fo2 import co2_dissociation_partials_bar
from simulator.pumping_cost import estimate_subambient_pump_cost
from simulator.campaigns import CampaignManager
from simulator.state import CampaignPhase, MeltState
from simulator.config import load_config_bundle


@pytest.mark.parametrize('carrier,expected', [('pN2', 'N2'), ('pAr', 'Ar'), ('pCO2', 'CO2')])
@pytest.mark.parametrize('campaign', [CampaignPhase.C2A, CampaignPhase.C2A_STAGED])
def test_campaign_carrier_pre_move_pin(carrier, expected, campaign):
    key = 'C2A_continuous' if campaign is CampaignPhase.C2A else 'C2A_staged'
    setpoints = deepcopy(load_config_bundle().setpoints)
    setpoints['campaigns'][key]['carrier_gas'] = carrier
    manager = CampaignManager(setpoints)
    melt = MeltState()
    manager.configure_campaign(melt, campaign)
    assert melt.background_gas_species == expected


@pytest.mark.parametrize(
    "pressure,energy,speed",
    [
        (100.0, "0x1.b3b21a94bfa75p+3", "0x1.4c9418e2a4facp+5"),
        (0.1, "0x1.144d1020caaecp+6", "0x1.44c8a04d551cdp+15"),
        (610.0, "0x0.0p+0", "0x0.0p+0"),
    ],
)
def test_staged_compression_pre_move_pin(pressure, energy, speed):
    result = estimate_subambient_pump_cost(
        pressure, 1.0, 3600.0, ambient_pressure_pa=610.0,
        gas_temperature_K=500.0, max_pump_speed_m3_s=50.0,
        validated_line_conductance_m3_s=50.0,
    )
    assert result.energy_kWh.hex() == energy
    assert result.required_pump_speed_m3_s.hex() == speed


@pytest.mark.parametrize("declaration,expected", [
    ({}, 0.0),
    ({"surface_pressure_mbar": 6.0}, 6.0),
    ({"environment": {"surface_pressure_mbar": 0.72}}, 0.72),
    ({"surface_pressure_mbar": 1.0,
      "environment": {"surface_pressure_mbar": 6.0}}, 1.0),
])
def test_surface_pressure_pre_move_pin(declaration, expected):
    assert _feedstock_surface_pressure_mbar(declaration).hex() == expected.hex()


@pytest.mark.parametrize('co2,oxygen,expected', [
    (0.006, 0.0, ('0x1.7fe015d85ababp-8', '0x1.2ae6bdc9a69efp-13',
                  '0x1.2ae6bdc9a69efp-14')),
    (0.00576, 3.7e-7, ('0x1.706a5330e399dp-8', '0x1.224e0de338e44p-13',
                       '0x1.23db56b691666p-14')),
])
def test_co2_fixed_volume_pre_change_pin(co2, oxygen, expected):
    result = co2_dissociation_partials_bar(1800.0, co2, oxygen)
    assert tuple(result[k].hex() for k in ('p_CO2_bar', 'p_CO_bar', 'p_O2_bar')) == expected
