"""Published limiting laws and both live callers of the pipe-flow owner."""

import math
from types import SimpleNamespace

import pytest

from simulator import transport_regime as tr
from simulator.core import PyrolysisSimulator
from simulator.equipment import EquipmentDesigner
from simulator.overhead import OverheadConfigurationError, OverheadGasModel
from simulator.state import Atmosphere, CampaignPhase, CondensationTrain, EvaporationFlux, MeltState
from simulator.thermal_train import NoColdTrain


@pytest.mark.parametrize("aspect, transmission", [
    # Clausing's circular diffuse-wall tube, tabulated L/D=0 and 1:
    # O'Hanlon, A User's Guide to Vacuum Technology, molecular tube flow.
    (0.0, 1.0), (1.0, 0.51423),
])
def test_santeler_matches_clausing_transmission(aspect, transmission):
    assert tr.clausing_transmission_probability(0.12, 0.12 * aspect) == (
        pytest.approx(transmission, rel=0.007)
    )


def test_clausing_long_tube_recovers_existing_molecular_owner():
    d, length, temperature, mass = 0.01, 1e4, 300.0, 0.0280134
    actual = tr.molecular_tube_conductance_m3_s(
        d, length, temperature, mass, knudsen_number=math.inf,
        transmission_probability=tr.clausing_transmission_probability(d, length),
    )
    long_limit = tr.long_tube_molecular_conductance_m3_s(
        d, length, temperature, mass, knudsen_number=math.inf,
    )
    assert actual == pytest.approx(long_limit, rel=3e-6)


@pytest.mark.parametrize("species", ["N2", "Ar"])
@pytest.mark.parametrize("pressure_pa, tolerance", [(0.1, 0.02), (1e-6, 1e-6)])
def test_low_pressure_recovers_pressure_independent_molecular_conductance(
    species, pressure_pa, tolerance,
):
    mass = tr.MOLAR_MASSES_KG_PER_MOL[species]
    temperature = 1773.15
    molecular = tr.molecular_tube_conductance_m3_s(
        0.12, 1.0, temperature, mass, knudsen_number=math.inf,
        transmission_probability=tr.clausing_transmission_probability(0.12, 1.0),
    )
    mass_flow = tr.pipe_mass_flow_kg_s(
        0.12, 1.0, mass, tr.gas_dynamic_viscosity_Pa_s(temperature),
        temperature, pressure_pa, 0.0,
    )
    conductance = mass_flow * tr.PIPE_GAS_CONSTANT_J_MOL_K * temperature / mass / pressure_pa
    assert conductance == pytest.approx(molecular, rel=tolerance)


@pytest.mark.parametrize("pressure_pa, tolerance", [(1000.0, 0.01), (1e7, 1e-6)])
def test_high_pressure_recovers_poiseuille_component(pressure_pa, tolerance):
    args = (0.12, 1.0, 0.040, tr.gas_dynamic_viscosity_Pa_s(1773.15),
            1773.15, pressure_pa, 0.0)
    viscous = tr.compressible_poiseuille_mass_flow(*args)
    actual = tr.pipe_mass_flow_kg_s(*args)
    assert actual > viscous
    assert actual == pytest.approx(viscous, rel=tolerance)


def test_published_knudsen_transition_factor_at_unit_parameter():
    # Odnoralov (2005), Eqs. 4-5: at x=1 the molecular multiplier is
    # 3.507/4.095 = 0.8564102564102564. Independent 50-digit Decimal arithmetic
    # gives P1=2*eta/d*sqrt(2*pi*RT/M)=2.6642721774654123 Pa for P2=0,
    # d=.01 m, T=300 K, M=.0280134 kg/mol, eta=17.81e-6 Pa s, R=8.31446.
    # This anchors the interior of the bridge, not just its two asymptotes.
    args = (0.01, 1.0, 0.0280134, 17.81e-6, 300.0, 2.6642721774654123, 0.0)
    actual = tr.pipe_mass_flow_kg_s(*args)
    viscous = tr.compressible_poiseuille_mass_flow(*args)
    molecular = tr.molecular_tube_conductance_m3_s(
        0.01, 1.0, 300.0, 0.0280134, knudsen_number=math.inf,
        transmission_probability=tr.clausing_transmission_probability(0.01, 1.0),
    ) * args[-2] * 0.0280134 / (tr.PIPE_GAS_CONSTANT_J_MOL_K * 300.0)
    assert (actual - viscous) / molecular == pytest.approx(0.8564102564102564, rel=1e-12)


@pytest.mark.parametrize("pressure_mbar", [1e-4, 1e-3, 0.1, 10.0, 100.0])
def test_equipment_and_overhead_use_one_owner(pressure_mbar):
    pipe = EquipmentDesigner().size_collection_pipe(0.01, pressure_mbar)
    overhead = OverheadGasModel({})
    assert pipe.conductance_kg_s == overhead._pipe_conductance(pressure_mbar * 100, 1500)


@pytest.mark.parametrize("upstream, downstream", [(0.01, 0), (0.1, 0.05), (10, 2), (1000, 600)])
def test_forward_inverse_close_across_regimes(upstream, downstream):
    overhead = OverheadGasModel({})
    mass_flow = overhead._pipe_conductance(upstream, 1500, p_downstream_Pa=downstream)
    inferred = overhead._vapor_pressure_mbar_from_flux(
        mass_flow, 1500, p_downstream_bar=downstream / 1e5,
    ) * 100
    assert inferred == pytest.approx(upstream, rel=1e-12)


@pytest.mark.parametrize("upstream, downstream", [(0.0, 0.0), (0.01, 0.1), (10.0, 10.0)])
def test_no_forward_pressure_drop_has_zero_capacity(upstream, downstream):
    assert tr.pipe_mass_flow_kg_s(0.12, 1, 0.04, 6.243e-5, 1773.15, upstream, downstream) == 0


def test_interpolation_authority_reaches_existing_snapshot_warning_surface():
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim._overhead_model = OverheadGasModel({})
    sim._overhead_model._pipe_conductance(40.0, 1500.0, species_kg_for_M_avg={"N2": 1.0})
    sim._condensation_model = SimpleNamespace(
        # Upstream condensation can be viscous while the pipe's mean-pressure
        # basis is transitional. Only the pipe owner may classify its authority.
        last_knudsen_regime_diagnostic={"status": "ok", "knudsen_number": 0.008, "warnings": []},
        knudsen_regime=tr.KnudsenRegime.VISCOUS,
    )
    summary = sim._latest_knudsen_summary()
    assert any("Pipe conductance authority=interpolated" in w for w in summary["warnings"])


def test_interpolation_notice_does_not_label_limits_transitional():
    args = (0.12, 0.0280134, tr.gas_dynamic_viscosity_Pa_s(1773.15), 1773.15)
    assert tr.pipe_conductance_authority_notice(*args, 1000.0, 0.0) is None
    assert tr.pipe_conductance_authority_notice(*args, 1e-6, 0.0) is None


@pytest.mark.parametrize("pressure_mbar", [1e-4, 1e-3, 0.1])
def test_runtime_capacity_paths_preserve_actual_low_pressure(pressure_mbar):
    model = OverheadGasModel({})
    melt = MeltState()
    melt.temperature_C = 1500.0
    melt.p_total_mbar = pressure_mbar
    melt.atmosphere = Atmosphere.PN2_SWEEP
    flux = EvaporationFlux(species_kg_hr={"N2": 1e-6}, total_kg_hr=1e-6)
    actual = model.estimate_transport_state(flux, melt)
    expected = model._pipe_conductance(
        pressure_mbar * 100, 1500.0, species_kg_for_M_avg=flux.species_kg_hr,
    )
    assert actual["p_mean_Pa"] == pressure_mbar * 100
    assert actual["conductance_kg_s"] == expected

    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = melt
    sim._overhead_model = model
    sim._overhead_headspace_config = {}
    assert sim._headspace_bleed_conductance_kg_s(
        species_kg_for_M_avg=flux.species_kg_hr,
        p_upstream_bar=pressure_mbar / 1000, p_downstream_bar=0.0,
    ) == expected

    melt.atmosphere = Atmosphere.CONTROLLED_O2
    capacity = model.controlled_o2_transport_capacity(
        flux, melt, cold_train_capacity=NoColdTrain("runtime_enforcement_disabled"),
    )
    assert capacity.pipe_capacity_kg_hr == expected * 3600.0


def test_explicit_conductance_override_does_not_claim_interpolation():
    model = OverheadGasModel({"conductance_kg_s": 0.25})
    model._pipe_conductance(10, 1500.0)
    assert model.last_pipe_conductance_authority_notice
    melt = MeltState()
    melt.atmosphere = Atmosphere.CONTROLLED_O2
    melt.p_total_mbar = 0.1
    model.controlled_o2_transport_capacity(
        EvaporationFlux(total_kg_hr=0), melt,
        cold_train_capacity=NoColdTrain("runtime_enforcement_disabled"),
    )
    assert model.last_pipe_conductance_authority_notice is None


def test_transitional_inferred_pressure_is_flagged_under_viscous_ceiling():
    model = OverheadGasModel({})
    flow_kg_s = model._pipe_conductance(20.0, 1500.0, species_kg_for_M_avg={"N2": 1.0})
    melt = MeltState()
    melt.temperature_C = 1500.0
    melt.p_total_mbar = 10.0
    melt.atmosphere = Atmosphere.PN2_SWEEP
    state = model.estimate_transport_state(
        EvaporationFlux(species_kg_hr={"N2": flow_kg_s * 3600}, total_kg_hr=flow_kg_s * 3600),
        melt,
    )
    assert state["vapor_pressure_mbar"] == pytest.approx(0.2)
    assert model.last_pipe_conductance_authority_notice


@pytest.mark.parametrize("source_kg_hr", [0.0, 1e-6, 0.1])
@pytest.mark.parametrize("sink_bar", [0.0, 1e-6])
def test_c0_vacuum_resolves_source_pressure_without_artificial_ramp_stall(source_kg_hr, sink_bar):
    model = OverheadGasModel({"headspace": {}})
    model.configure_headspace({"enabled": True, "volume_m3": 0.085})
    melt = MeltState()
    melt.temperature_C = 475.0
    melt.p_total_mbar = 0.0
    melt.atmosphere = Atmosphere.HARD_VACUUM
    melt.campaign = CampaignPhase.C0
    melt.campaign_hour = 9.0
    flux = EvaporationFlux(species_kg_hr={"Na": source_kg_hr}, total_kg_hr=source_kg_hr)
    state = model.estimate_transport_state(flux, melt, p_downstream_bar=sink_bar)
    assert state["p_mean_Pa"] >= sink_bar * 1e5
    # Actual pressure solves the source balance; the campaign setting stays 0.
    assert model._pipe_conductance(
        state["p_mean_Pa"], melt.temperature_C,
        p_downstream_Pa=sink_bar * 1e5,
        species_kg_for_M_avg=flux.species_kg_hr,
    ) * 3600.0 == pytest.approx(source_kg_hr, rel=1e-12)
    assert melt.p_total_mbar == 0.0
    gas = model.update(flux, melt, CondensationTrain.create_default(), p_downstream_bar=sink_bar)
    assert gas.transport_saturation_pct == 0.0
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt, sim.overhead = melt, gas
    sim.campaign_mgr = SimpleNamespace(get_temp_target=lambda *_: (1000.0, 50.0))
    sim._update_temperature()
    assert melt.temperature_C == 525.0


@pytest.mark.parametrize("upstream_pa", [1e-4, 10.0, 1000.0])
def test_controlled_downstream_diagnostic_inverts_same_forward_owner(upstream_pa):
    model = OverheadGasModel({})
    melt = MeltState()
    melt.temperature_C = 1500.0
    melt.p_total_mbar = upstream_pa / 100.0
    melt.atmosphere = Atmosphere.CONTROLLED_O2
    vacuum = model._pipe_conductance(upstream_pa, 1500.0, species_kg_for_M_avg={"Ar": 1.0})
    flux = EvaporationFlux(species_kg_hr={"Ar": vacuum * 900.0}, total_kg_hr=vacuum * 900.0)
    flow = model.controlled_o2_transport_capacity(
        flux, melt, cold_train_capacity=NoColdTrain("runtime_enforcement_disabled"),
    )
    actual = model._pipe_conductance(
        upstream_pa, 1500.0, p_downstream_Pa=flow.downstream_pressure_bar * 1e5,
        species_kg_for_M_avg=flux.species_kg_hr,
    )
    assert actual == pytest.approx(vacuum * 0.25, rel=1e-12)
    if upstream_pa == 1e-4:
        # Independently: molecular F=K(P1-P2); quarter flow means P2=.75 P1.
        assert flow.downstream_pressure_bar * 1e5 / upstream_pa == pytest.approx(0.75, rel=1e-4)


def test_c0_positive_source_refuses_invalid_pipe_instead_of_zero_pressure():
    model = OverheadGasModel({})
    model.pipe_diameter_m = 0.0
    melt = MeltState()
    melt.p_total_mbar = 0.0
    with pytest.raises(OverheadConfigurationError, match="valid pipe"):
        model.estimate_transport_state(
            EvaporationFlux(species_kg_hr={"Na": 1e-6}, total_kg_hr=1e-6), melt,
        )
