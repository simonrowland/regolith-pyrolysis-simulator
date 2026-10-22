"""b-1016: an imposed carrier partial is not a vapour back-pressure."""
import pytest

from engines.builtin.overhead_bleed import controlled_flow_capacity
from simulator.overhead import OverheadGasModel
from simulator.state import Atmosphere, CondensationTrain, EvaporationFlux, MeltState
from tests.test_overhead_accounting import _gas_train_sim


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


@pytest.mark.xfail(strict=True, reason="b-1016: full total pressure is assigned to vapour alone")
@pytest.mark.parametrize("carrier", ["Kr", "N2"])
@pytest.mark.parametrize("fraction,expected_fe_mbar", [(1.0, 0.0), (0.5, 5.0)])
def test_carrier_present_dalton_partials(carrier, fraction, expected_fe_mbar):
    model, melt, flux, capacity = _boundary(carrier, fraction)
    transport = model.estimate_transport_state(flux, melt, effective_transport_capacity=capacity)
    # Physical reference: total 10.2 mbar, oxygen 0.2, carrier 10 or 5.
    # The remaining pressure budget is 0 or 5 mbar, all Fe for this source.
    assert transport["pressure_mbar"] == 10.2
    gas = model.update(flux, melt, CondensationTrain.create_default(), effective_transport_capacity=capacity)
    assert gas.composition["Fe"] == pytest.approx(expected_fe_mbar, abs=1e-14)
    partials = transport["species_partial_pressures_mbar"]
    assert gas.composition["Fe"] == partials["Fe"]
    assert gas.composition[carrier] == (10.0 if fraction == 1.0 else 5.0)
    assert gas.composition["O2"] == 0.2
    # Core and evaporation consume this very projection on the next tick.
    sim = _gas_train_sim()
    sim._melt_headspace_composition_mbar = partials
    assert sim._evaporation_bulk_partial_pressure_pa("Fe") == pytest.approx(expected_fe_mbar * 100.0, abs=1e-12)


@pytest.mark.xfail(strict=True, reason="b-1016: closed line and downstream reference include carrier")
@pytest.mark.parametrize("closed", [False, True])
def test_carrier_projection_preserves_pressure_origin(closed):
    model, melt, flux, _ = _boundary("Kr")
    downstream = 0.010199999
    if closed:
        model._pipe_conductance = lambda *_args, **_kwargs: 0.0
    else:
        assert model._pipe_conductance(
            1020.0, melt.temperature_C, p_downstream_Pa=downstream * 1e5,
            species_kg_for_M_avg=flux.species_kg_hr,
        ) > 0.0
    raw_total = (10.2 if closed else model._vapor_pressure_mbar_from_flux(
        flux.total_kg_hr / 3600.0, melt.temperature_C,
        p_downstream_bar=downstream, species_kg_for_M_avg=flux.species_kg_hr,
    ))
    gas = model.update(flux, melt, CondensationTrain.create_default(), p_downstream_bar=downstream)
    assert gas.composition["Fe"] == pytest.approx(max(0.0, raw_total - 10.2), abs=1e-12)
    assert gas.composition["O2"] == 0.2
    assert gas.composition["Kr"] == 10.0


@pytest.mark.xfail(strict=True, reason="b-1016: oxygen must occupy its own share exactly once")
@pytest.mark.parametrize("oxygen_share,expected_fe,expected_o2", [(0.5, 2.6, 2.6), (0.01, 5.0, 0.2)])
def test_emitted_oxygen_and_controlled_floor_share_one_budget(oxygen_share, expected_fe, expected_o2):
    from simulator.accounting import resolve_species_formula
    model, melt, _, capacity = _boundary("Kr", 0.5)
    # One mol/hr total source, with independently chosen O2 molar shares.
    flux = EvaporationFlux(species_kg_hr={
        "Fe": (1.0 - oxygen_share) * resolve_species_formula("Fe").molar_mass_kg_per_mol(),
        "O2": oxygen_share * resolve_species_formula("O2").molar_mass_kg_per_mol(),
    })
    flux.update_totals()
    gas = model.update(flux, melt, CondensationTrain.create_default(), effective_transport_capacity=capacity)
    assert gas.composition["Fe"] == pytest.approx(expected_fe)
    assert gas.composition["O2"] == pytest.approx(expected_o2)
    assert gas.composition["Kr"] == 5.0
    assert sum(gas.composition.values()) == pytest.approx(10.2)


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
