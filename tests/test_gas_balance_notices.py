"""F3 applicability follows mean-mixture removal through the pressure paths."""
from types import SimpleNamespace

import pytest

from simulator import transport_regime as transport
from simulator.accounting import resolve_species_formula
from simulator.capacity_coupling import CapacityShadowResult, solve_capacity_shadow
from simulator.environment import _headspace_moles_at_pressure_pa
from simulator.gas_balance import FiniteCapacity, NoColdTrain
from tests.test_overhead_accounting import _gas_train_sim

REASON = "molecular_mixture_routing_approximation"


def test_pipe_notice_uses_c2_predicate(monkeypatch):
    seen = []
    def predicate(kn):
        seen.append(kn)
        return True
    monkeypatch.setattr(transport, "mean_mixture_bulk_fraction_routing_is_approximation", predicate)
    assert REASON in transport.pipe_conductance_authority_notice(0.12, 0.04, 1.8e-5, 300.0, 1000.0, 0.0)
    assert len(seen) == 1


@pytest.mark.parametrize("configured", [None, 1e-5])
@pytest.mark.parametrize("equal_pressures", [False, True])
def test_headspace_notice_including_unresolved_equal_total_exchange(configured, equal_pressures):
    sim = _gas_train_sim()
    sim._overhead_headspace_config["conductance_kg_s"] = configured
    sim._overhead_headspace_config.pop("conductance_kg_s_per_bar", None)
    capacity = sim._headspace_bleed_conductance_kg_s(
        species_kg_for_M_avg={"O2": 1e-8, "Kr": 1e-8},
        p_upstream_bar=1e-11, p_downstream_bar=1e-11 if equal_pressures else 0.0)
    assert REASON in sim.overhead_model.last_pipe_conductance_authority_notice
    assert capacity == 0.0 if equal_pressures else capacity > 0.0


@pytest.mark.parametrize("wall_diagnostic", [None, {}, {"status": "predicted", "warnings": ()}])
def test_sweep_notice_reaches_plan_provider_boundary_and_snapshot(monkeypatch, wall_diagnostic):
    sim = _gas_train_sim()
    sim.melt.background_gas_species = "Kr"
    sim.melt.p_total_mbar = 1e-8
    sim.melt.sweep_mol_per_s = 1e-9
    sim._overhead_headspace_config.update(enabled=True, conductance_kg_s=1e-5)
    monkeypatch.setattr(sim, "_cold_train_capacity_policy", lambda: (NoColdTrain(), None))
    target = _headspace_moles_at_pressure_pa(
        sim.melt.p_total_mbar * 100.0, sim._headspace_volume_m3(), sim._headspace_temperature_K())
    initial = {"O2": target / 2, "Kr": target / 2}
    plan = sim._continuous_sweep_plan(initial, {}, dt_s=1.0)
    assert any(REASON in n for n in plan["notices"])
    sim._admit_process_pressure()
    sim.atom_ledger.load_external_mol("process.overhead_gas", initial,
        source="F3 mixture", material_origin="feedstock")
    sim.atom_ledger.load_external_mol("reservoir.reagent.sweep_gas", plan["inlet_species_mol"],
        source="F3 inlet", material_origin="reagent")
    result = sim._dispatch_overhead_bleed(
        sweep_outflow_species_mol=plan["outflow_species_mol"],
        sweep_external_o2_outflow_mol=plan["external_o2_outflow_mol"],
        sweep_pressure_bar=plan["pressure_bar"], sweep_capacity_integrated=True,
        transport_notices=plan["notices"])
    assert result.status == "ok"
    assert any(REASON in n for n in result.diagnostic["notices"])
    boundary = sim._physical_sweep_boundary()
    assert any(n["code"] == REASON for n in boundary["notices"])
    sim._admit_process_pressure(actual_pressure_pa=sum(boundary["partials_bar"].values()) * 1e5)
    assert any(REASON in n for n in sim._process_pressure_diagnostic["transport_notices"])
    sim._condensation_model = (None if wall_diagnostic is None else
                               SimpleNamespace(last_knudsen_regime_diagnostic=wall_diagnostic))
    assert any(REASON in n for n in sim._latest_knudsen_summary()["warnings"])
    sim.melt.temperature_C = 100.0
    source = SimpleNamespace(warnings=[])
    sim._refresh_vapor_pressures_from_kernel(source)
    assert any(REASON in n for n in source.warnings)


def test_capacity_shadow_retains_molecular_routing_notice():
    masses = {s: resolve_species_formula(s).molar_mass_kg_per_mol() for s in ("O2", "Kr")}
    result = solve_capacity_shadow(
        pre_holdup_mol={"O2": 1e-8, "Kr": 1e-8}, molar_mass_kg_mol=masses,
        flux_kg_hr_at_partials=lambda _: {}, capacity=FiniteCapacity(1e-15),
        head_bled_species_mol={}, external_o2_holdup_mol=0.0,
        temperature_K=300.0, volume_m3=1.0, dt_hr=0.137,
        bleed_conductance_kg_s=1e-20, pipe_diameter_m=0.12, pipe_length_m=1.0,
        pipe_temperature_K=300.0, downstream_pressure_Pa=0.0,
        k_relief_kg_hr_Pa=1e-30, p_open_Pa=1e9, vessel_rating_Pa=1e12)
    assert isinstance(result, CapacityShadowResult)
    assert any(REASON in n for n in result.notices)



def test_passive_pressure_admission_preserves_accepted_routing_notice():
    sim = _gas_train_sim()
    notice = transport.pipe_conductance_authority_notice(0.12, 0.04, 1.8e-5, 300.0, 1e-6, 0.0)
    assert REASON in notice
    sim._process_pressure_diagnostic["transport_notices"] = [notice]
    sim._admit_process_pressure()
    assert sim._process_pressure_diagnostic["transport_notices"] == [notice]
    assert notice in sim._latest_knudsen_summary()["warnings"]


def test_mars_startup_before_first_pipe_evaluation():
    from tests.test_mars_process_pressure import mars_sim

    sim = mars_sim(sweep=0.0)
    boundary = sim._physical_sweep_boundary()
    assert boundary["partials_bar"].get("O2", 0.0) == 0.0



def test_legacy_bleed_notice_reaches_source_without_co2_projection():
    sim = _gas_train_sim()
    sim._headspace_bleed_conductance_kg_s(
        species_kg_for_M_avg={"O2": 1e-8, "Kr": 1e-8},
        p_upstream_bar=1e-11, p_downstream_bar=0.0)
    sim._headspace_co2_dissociation_diagnostic = {}
    sim.melt.temperature_C = 100.0
    source = SimpleNamespace(warnings=[])
    sim._refresh_vapor_pressures_from_kernel(source)
    assert any(REASON in n for n in source.warnings)
