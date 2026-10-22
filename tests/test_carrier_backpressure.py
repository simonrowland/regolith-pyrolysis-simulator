"""b-1016: an imposed carrier partial is not a vapour back-pressure."""
import pytest

from engines.builtin.overhead_bleed import controlled_flow_capacity
from simulator.overhead import OverheadConfigurationError, OverheadGasModel
from simulator.state import Atmosphere, CondensationTrain, EvaporationFlux, MeltState


def _boundary(carrier, fraction=1.0, *, atmosphere=Atmosphere.CONTROLLED_O2):
    melt = MeltState(temperature_C=1600.0, atmosphere=atmosphere,
                     p_total_mbar=10.2, pO2_mbar=0.2)
    melt.background_gas_species = carrier
    melt.background_gas_mole_fraction = fraction
    flux = EvaporationFlux(total_kg_hr=0.1, species_kg_hr={"Fe": 0.1})
    model = OverheadGasModel({"enabled": False})
    capacity = controlled_flow_capacity(
        pipe_capacity_kg_hr=1.0, equipment_capacity_kg_hr=1.0,
        evolved_flux_kg_hr=0.1, upstream_pressure_bar=0.0102,
        pipe_diameter_m=0.12, pipe_length_m=1.0,
        gas_temperature_K=1873.15, gas_molar_mass_kg_mol=0.055845,
    )
    return model, melt, flux, capacity


@pytest.mark.xfail(strict=True, reason="b-1016: achieved carrier composition unavailable; scope blocked")
@pytest.mark.parametrize("carrier", ["Kr", "N2"])
def test_carrier_full_pressure_requires_achieved_composition(carrier):
    model, melt, flux, capacity = _boundary(carrier)
    assert melt.sweep_mol_per_s is None
    # A pure carrier feed/declared pressure floor is not an achieved gas mole
    # fraction after vapour enters. No carrier inventory or removal closure is
    # supplied here. Reserving all nominal carrier/O2 pressure and assigning
    # zero vapour would merely replace one unsupported split with another.
    # The missing achieved-mixture input needs a typed refusal or an authorized
    # flow/inventory closure, rather than a guessed numerical back-pressure.
    with pytest.raises(OverheadConfigurationError, match="carrier"):
        model.update(flux, melt, CondensationTrain.create_default(),
                     effective_transport_capacity=capacity)


def test_carrier_absent_controlled_split_is_bit_identical():
    model, melt, flux, capacity = _boundary("")
    transport = model.estimate_transport_state(flux, melt, effective_transport_capacity=capacity)
    partials = model.species_partial_pressures(flux, transport["vapor_pressure_mbar"])
    assert partials["Fe"].hex() == "0x1.4666666666666p+3"
    assert transport["pressure_mbar"].hex() == "0x1.4666666666666p+3"
    gas = model.update(flux, melt, CondensationTrain.create_default(), effective_transport_capacity=capacity)
    assert {key: value.hex() for key, value in gas.composition.items()} == {
        "Fe": "0x1.4666666666666p+3", "O2": "0x1.999999999999ap-3",
    }
    assert gas.pressure_mbar.hex() == "0x1.4ccccccccccccp+3"


@pytest.mark.parametrize("carrier", ["Kr", "N2"])
def test_open_inert_pipe_keeps_source_only_pressure(carrier):
    model, melt, flux, _ = _boundary(carrier, atmosphere=Atmosphere.PN2_SWEEP)
    transport = model.estimate_transport_state(flux, melt)
    # An open pipe to vacuum has no carrier pressure in its source inversion.
    expected = model._vapor_pressure_mbar_from_flux(
        flux.total_kg_hr / 3600.0, melt.temperature_C,
        p_downstream_bar=0.0, species_kg_for_M_avg=flux.species_kg_hr,
    )
    assert transport["vapor_pressure_mbar"].hex() == expected.hex()
    assert model.species_partial_pressures(flux, expected)["Fe"].hex() == expected.hex()
