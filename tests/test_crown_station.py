"""Balanced hand cases for the shared Mg/Ca station and net oxygen closure."""
import pytest

from simulator.accounting.formulas import parse_formula
from simulator.chemistry.langmuir_knudsen import langmuir_molar_flux
from simulator.condensation import (
    CrownStationRefusal, CrownSurfaceLaw, crown_carrier_allocation, crown_station,
)
from simulator.refractory_vaporization import (
    CongruentVaporizationError, solve_melt_vaporization,
)
from simulator.thermal_budget import crown_thermal_demand


ALPHA = {"Mg": .2, "Ca": 1., "O": 1., "O2": 1.}
ACTIVITIES = {"MgO": .326337, "CaO": .00344002}
LAWS = {s: CrownSurfaceLaw(0 if s in {"O", "O2"} else 1,
                           0 if s in {"O", "O2"} else 1, 1,
                           "assumed_pure_crust_or_nonretaining_oxygen") for s in ALPHA}
GEOMETRY = dict(source_temperature_K=2000., collector_temperature_K=500.,
                melt_area_m2=1., collector_area_m2=1., view_factor_from_melt=.9,
                melt_to_collector_m=1., duration_s=1.)


def allocation(E=4., R=2., N=10., dt=1., F=.5, r=.5, s=1., u=1., Q=0.):
    return crown_carrier_allocation(E, R, N, dt, view_factor_from_melt=F,
                                    return_view_factor=r, sticking=s,
                                    return_accommodation=u, oxidation_mol_s=Q)


def assert_balanced(a, initial, duration):
    assert a.net_source_mol_s == pytest.approx(a.dish_gain_mol_s+a.sky_mol_s+a.oxidized_mol_s)
    assert a.inventory_mol == pytest.approx(initial+a.dish_gain_mol_s*duration)
    assert a.inventory_mol >= 0


def test_independent_diffuse_return_hand_case():
    # 7 forward + 1 reflected return = 8 gross; 4 strike dish, 2 stick,
    # 2 re-emit, and 2 reflect. Half of those 4 return, half escape.
    # Source loses 8-2=6; dish gains 2-2=0; sky gets 4+2=6.
    a = allocation(E=7, R=2, F=.5, r=.5, s=.5, u=.5)
    assert (a.gross_mol_s, a.incidence_mol_s, a.return_mol_s,
            a.net_source_mol_s, a.dish_gain_mol_s, a.sky_mol_s) == pytest.approx((8,4,2,6,0,6))
    assert_balanced(a, 10, 1)


def test_balanced_MO_hand_case_with_metal_and_oxygen_return():
    # 1 mol/s Mg forward; .2 re-emits, .1 returns: net oxide debit .9.
    # .6 mol/s O2 forward; .15 returns: .45 vents = .9 mol/s O atoms.
    # Mg dish gains .3 and sky receives .6. Gross oxygen (1.2 atoms/s)
    # would over-debit the source; only .9 atoms/s are actually released.
    metal = allocation(E=1, R=.2, N=0, F=.5, r=.5)
    oxygen = allocation(E=.6, R=0, N=0, F=.5, r=.5, s=0)
    assert (metal.net_source_mol_s, metal.dish_gain_mol_s, metal.sky_mol_s) == pytest.approx((.9,.3,.6))
    assert oxygen.net_source_mol_s == oxygen.sky_mol_s == pytest.approx(.45)
    assert 2*oxygen.net_source_mol_s == pytest.approx(metal.net_source_mol_s)
    assert_balanced(metal, 0, 1)
    assert_balanced(oxygen, 0, 1)


@pytest.mark.parametrize("F,s,expected", [(0,1,0), (1,1,4), (.5,0,0), (.5,1,2)])
def test_cold_empty_dish_capture_limits(F, s, expected):
    a = allocation(R=0, N=0, F=F, r=0, s=s)
    assert a.dish_gain_mol_s == expected
    assert_balanced(a, 0, 1)


def test_inventory_exhausts_during_tick_then_arrivals_reemit():
    # Two moles initially; 2/s arrive and 4/s evaporate for one second.
    # Next second the empty surface can re-emit only the 2/s arriving.
    a = allocation(E=4, R=4, N=2, dt=2)
    assert a.reemission_mol_s == 3
    assert a.inventory_mol == 0
    assert a.dish_gain_mol_s == -1
    assert_balanced(a, 2, 2)


def test_evaporation_and_oxidation_share_finite_stock():
    # Supply is 1 stored + 2 arriving mol. Requested competing sinks 4+2
    # consume it in their 2:1 ratio: 2 evaporate, 1 oxidizes, none remains.
    a = allocation(E=4, R=4, N=1, r=0, Q=2)
    assert (a.reemission_mol_s, a.oxidized_mol_s, a.inventory_mol) == (2,1,0)
    assert_balanced(a, 1, 1)


def test_hot_empty_dish_reemission_is_incidence():
    a = allocation(E=4, R=1e6, N=0)
    assert a.reemission_mol_s == a.incidence_mol_s == 2
    assert a.dish_gain_mol_s == a.inventory_mol == 0
    assert_balanced(a, 0, 1)


def test_zero_sticking_does_not_hide_stored_evaporation():
    a = allocation(E=4, R=1, N=2, s=0)
    assert a.dish_gain_mol_s == -1
    assert_balanced(a, 2, 1)


def test_closed_reflecting_geometry_refuses_singular_steady_state():
    with pytest.raises(CrownStationRefusal, match="singular"):
        allocation(F=1, r=1, s=0, u=0)


def test_empty_closed_return_loop_cannot_emit_absent_material():
    a = allocation(E=0, R=100, N=0, F=1, r=1, s=1, u=0)
    assert a.gross_mol_s == a.reemission_mol_s == a.inventory_mol == 0


@pytest.mark.parametrize("s,u", [(0,0), (0,1), (.5,.5), (1,0), (1,1)])
def test_finite_competing_sinks_with_return_conserve(s, u):
    a = allocation(E=4, R=100, N=1, dt=2, r=.5, s=s, u=u, Q=10)
    assert_balanced(a, 1, 2)


@pytest.mark.parametrize("species", ["Mg", "Ca"])
def test_actual_temperatures_and_fixed_pressure_hkl_parity(species):
    p = {species: .1}
    cold = crown_station(p, {species: .2}, {species: LAWS[species]}, {}, **GEOMETRY)
    mass = parse_formula(species).molar_mass_kg_per_mol()
    assert cold.allocations[species].forward_mol_s == langmuir_molar_flux(
        2000, .1, 0, .2, molar_mass_kg_mol=mass)
    warm = crown_station(p, {species: .2}, {species: LAWS[species]}, {},
                         **(GEOMETRY | {"collector_temperature_K": 600.}))
    assert warm.allocations[species].reemission_mol_s > cold.allocations[species].reemission_mol_s
    assert cold.collector_temperature_K == 500
    assert warm.source_temperature_K == 2000


def test_station_area_scaling_preserves_reciprocity_and_rates():
    small = crown_station({"Mg": .1}, ALPHA, LAWS, {}, **GEOMETRY)
    large = crown_station({"Mg": .1}, ALPHA, LAWS, {},
                          **(GEOMETRY | {"melt_area_m2": 2., "collector_area_m2": 2.}))
    assert small.return_view_factor == large.return_view_factor == .9
    a, b = small.allocations["Mg"], large.allocations["Mg"]
    for field in ("forward_mol_s", "incidence_mol_s", "reemission_mol_s",
                  "net_source_mol_s", "dish_gain_mol_s", "sky_mol_s", "inventory_mol"):
        assert getattr(b, field) == pytest.approx(2*getattr(a, field))


def test_oxygen_vents_after_reflection_without_cold_capture():
    station = crown_station({"O2": 1}, {"O2": 1}, {"O2": LAWS["O2"]}, {}, **GEOMETRY)
    a = station.allocations["O2"]
    assert a.return_mol_s > 0
    assert a.dish_gain_mol_s == 0
    assert a.net_source_mol_s == pytest.approx(a.sky_mol_s)
    assert a.sky_mol_s < a.forward_mol_s
    dense = crown_station({"O2": 1000}, {"O2": 1}, {"O2": LAWS["O2"]}, {}, **GEOMETRY)
    assert "loaded_plume_outside_collisionless_band" in dense.statuses
    assert dense.knudsen_upper_bounds["O2"] < station.knudsen_upper_bounds["O2"]


def test_missing_trace_and_surface_laws_refuse():
    with pytest.raises(CrownStationRefusal, match="Sr.*unavailable"):
        crown_station({"Sr": 1}, {"Sr": 1}, {}, {"Sr": 1}, **GEOMETRY)
    with pytest.raises(CrownStationRefusal, match="missing surface"):
        crown_station({"Mg": 1}, {"Mg": .2}, {}, {}, **GEOMETRY)
    with pytest.raises(CrownStationRefusal, match="reciprocity"):
        crown_station({"Mg": 1}, ALPHA, LAWS, {}, **(GEOMETRY | {"collector_area_m2": .1}))


def test_empty_warm_major_surface_never_creates_negative_stock():
    result = crown_station({"Mg": 1e-7}, ALPHA, LAWS, {},
                           **(GEOMETRY | {"collector_temperature_K": 600.}))
    a = result.allocations["Mg"]
    assert a.inventory_mol == pytest.approx(0, abs=1e-20)
    assert a.reemission_mol_s == pytest.approx(a.incidence_mol_s)
    assert "no_oxidation_screen_missing_surface_chemistry" in result.statuses
    assert "Mg:self_collision_only_Kn_upper_bound" in result.statuses


def test_shared_mg_ca_oxygen_root_and_activity_dependence():
    r = solve_melt_vaporization(2000, ACTIVITIES, ALPHA)
    n = r.net_flux_mol_m2_s
    assert n["O"]+2*n["O2"] == pytest.approx(n["Mg"]+n["Ca"], rel=1e-10)
    # Independent screening calculation in crown-scope, not a saved campaign.
    assert n["Mg"]*parse_formula("Mg").molar_mass_kg_per_mol()*3600 == pytest.approx(.11710, rel=1e-4)
    reduced = solve_melt_vaporization(2000, ACTIVITIES | {"MgO": .01}, ALPHA)
    assert reduced.forward_flux_mol_m2_s["Mg"] < n["Mg"]
    assert reduced.surface_pO2_pa < r.surface_pO2_pa
    assert r.surface_pO2_pa > 0  # positive surface root without an ambient floor


def test_oxide_and_dimer_carriers_close_actual_atoms():
    alpha = ALPHA | {"MgO": 1., "Mg2": 1., "CaO": 1., "Ca2": 1.}
    r = solve_melt_vaporization(2200, ACTIVITIES, alpha)
    atoms = {e: sum(parse_formula(s).elements.get(e, 0)*n for s, n in r.net_flux_mol_m2_s.items())
             for e in ("Mg", "Ca", "O")}
    assert atoms["O"] == pytest.approx(atoms["Mg"]+atoms["Ca"], rel=1e-9)
    assert r.oxide_debit_mol_m2_s["MgO"] == pytest.approx(atoms["Mg"])
    assert not any("carrier_omitted" in s for s in r.statuses)


def test_joint_metal_and_oxygen_return_closes_net_not_gross():
    def boundary(pressures, forward):
        station = crown_station(pressures, ALPHA, LAWS, {"Mg": 1., "Ca": 1.},
                                **(GEOMETRY | {"source_temperature_K": 2100.}))
        return {s: a.net_source_mol_s for s, a in station.allocations.items()}

    r = solve_melt_vaporization(2100, ACTIVITIES, ALPHA, net_flux_boundary=boundary)
    net, gross = r.net_flux_mol_m2_s, r.forward_flux_mol_m2_s
    assert net["O"]+2*net["O2"] == pytest.approx(net["Mg"]+net["Ca"], rel=1e-9)
    assert net["Mg"] < gross["Mg"]
    assert net["O2"] < gross["O2"]
    # Poison old closure: gross oxygen is not the released amount once metal
    # AND oxygen return. Using gross here would fail the atom balance.
    assert abs(gross["O"]+2*gross["O2"]-gross["Mg"]-gross["Ca"]) > .1*gross["Mg"]
    no_return = solve_melt_vaporization(2100, ACTIVITIES, ALPHA)
    assert r.surface_pO2_pa != pytest.approx(no_return.surface_pO2_pa, rel=.01)


def test_invalid_source_activities_and_unowned_redox_basis_refuse():
    with pytest.raises(CongruentVaporizationError, match="supplied MgO/CaO"):
        solve_melt_vaporization(2000, {"SrO": 1}, ALPHA)
    with pytest.raises(CongruentVaporizationError, match="gas-basis"):
        solve_melt_vaporization(1000, {"CaO": 1}, {"Ca": 1, "O": 1, "O2": 1})
    with pytest.raises(CongruentVaporizationError, match="inconsistent return boundary"):
        solve_melt_vaporization(2000, ACTIVITIES, ALPHA,
                               net_flux_boundary=lambda p, n: {s: 2*v for s,v in n.items()})


THERMAL = dict(source_temperature_K=2000., collector_temperature_K=500.,
               melt_area_m2=1., collector_area_m2=1., view_factor_from_melt=.9,
               absorptivity=.1, radiator_area_m2=30., radiator_emissivity=.9,
               sky_temperature_K=0., heat_pipe_capacity_W=0.,
               deposition_heat_W=100., reaction_heat_W=0.,
               crust_thickness_m=.001, crust_conductivity_W_m_K=100.)


def test_thermal_radiation_scale_conduction_and_conditional_cooling():
    r = crown_thermal_demand(**THERMAL)
    # Stefan-Boltzmann definition: 500 K black sky rejection ~3.544 kW/m2.
    assert r["sky_radiation_W"] == pytest.approx(30*.9*3544, rel=2e-4)
    assert 81000 < r["absorbed_melt_radiation_W"] < 82000
    assert 499 < r["required_backside_temperature_K"] < 500
    assert r["heat_removal_margin_W"] > 0
    assert r["setpoint_status"].startswith("conditional")
    insufficient = crown_thermal_demand(**(THERMAL | {"radiator_area_m2": 1}))
    assert insufficient["setpoint_status"] == "infeasible"
    disconnected = crown_thermal_demand(**(THERMAL | {"view_factor_from_melt": 0}))
    assert disconnected["absorbed_melt_radiation_W"] == 0


def test_missing_thermal_inputs_prevent_feasibility_claim():
    r = crown_thermal_demand(**(THERMAL | {"reaction_heat_W": None}))
    assert r["total_heat_load_W"] is None
    assert r["heat_removal_margin_W"] is None
    assert r["setpoint_status"] == "unavailable"
