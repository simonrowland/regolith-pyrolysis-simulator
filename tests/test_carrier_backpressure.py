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


@pytest.mark.parametrize("carrier", ["Kr", "N2"])
def test_declared_carrier_legacy_full_total_pin(carrier):
    model, melt, flux, capacity = _boundary(carrier)
    gas = model.update(flux, melt, CondensationTrain.create_default(),
                       effective_transport_capacity=capacity)
    # Current defect: all 10.2 mbar is charged to Fe, in addition to the
    # independently retained 0.2 mbar O2 and 10 mbar carrier floors.
    assert {key: value.hex() for key, value in gas.composition.items()} == {
        "Fe": "0x1.4666666666666p+3", "O2": "0x1.999999999999ap-3",
        carrier: "0x1.4000000000000p+3",
    }
    assert gas.pressure_mbar.hex() == "0x1.4666666666666p+4"


def test_lab_schedule_argon_legacy_pin():
    from simulator.campaigns import CampaignManager
    from simulator.state import CampaignPhase
    from test_lab_geometry_runtime import dynamic_lab_schedule

    manager = CampaignManager({})
    manager.overrides["C2A"] = {"lab_schedule": dynamic_lab_schedule()}
    melt = MeltState(temperature_C=1700.0, campaign=CampaignPhase.C2A)
    manager.apply_lab_schedule_controls(melt, melt.campaign, sample_time_h=1.0)
    manager.apply_sweep_flow_control(melt)
    assert melt.sweep_mol_per_s == pytest.approx(0.00022307516702922367)
    assert melt.p_total_mbar.hex() == "0x1.a000000000000p+3"
    assert melt.background_gas_species == "Ar"
    assert melt.background_gas_mole_fraction == 1.0
    model = OverheadGasModel({"enabled": False})
    flux = EvaporationFlux(total_kg_hr=0.02, species_kg_hr={"SiO": 0.02})
    transport = model.estimate_transport_state(flux, melt)
    assert transport["vapor_pressure_mbar"].hex() == "0x1.a000000000000p+3"
    assert model.species_partial_pressures(flux, transport["vapor_pressure_mbar"])["SiO"].hex() == "0x1.a000000000000p+3"


@pytest.mark.parametrize("requested_o2", [0.0, 0.2])
def test_controlled_carrier_sweep_preserves_oxygen_floors(requested_o2):
    from simulator.core import PyrolysisSimulator

    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = MeltState(atmosphere=Atmosphere.CONTROLLED_O2,
                         pO2_mbar=requested_o2, p_total_mbar=13.0)
    sim.melt.sweep_mol_per_s = 0.00022307516702922367
    sim._physical_sweep_boundary = lambda: {"partials_bar": {"Ar": 0.013}}
    floor = sim._oxygen_numerical_floor_bar()
    expected = max(requested_o2 / 1000.0, floor)
    assert sim._commanded_pO2_bar() == expected
    assert sim._headspace_transport_pO2_bar() == expected
    assert sim._headspace_transport_pO2_bar_from_ledger(0.0) == expected


@pytest.mark.parametrize("flow", [None, 0.0, 0.3])
def test_lab_flow_declaration_survives_repeated_sweep_assignment(flow):
    from simulator.campaigns import CampaignManager
    from simulator.state import CampaignPhase
    from test_lab_geometry_runtime import dynamic_lab_schedule

    schedule = dynamic_lab_schedule()
    if flow is None:
        schedule["gas_boundary"]["imposed_flow"] = {
            "reported_status": "not_reported", "source_class": "literature_sidecar",
            "citation_id": "test", "digest": "test", "reason": "flow not measured",
        }
    else:
        schedule["gas_boundary"]["imposed_flow"]["value"] = flow
    manager = CampaignManager({})
    manager.overrides["C2A"] = {"lab_schedule": schedule}
    melt = MeltState(campaign=CampaignPhase.C2A)
    for _ in range(2):
        manager.apply_lab_schedule_controls(melt, melt.campaign, sample_time_h=1.0)
        manager.apply_sweep_flow_control(melt)
        if flow is None:
            assert melt.sweep_mol_per_s is None
        else:
            assert melt.sweep_mol_per_s == pytest.approx(
                {0.0: 0.0, 0.3: 0.00022307516702922367}[flow])
