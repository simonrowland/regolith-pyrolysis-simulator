"""C5 binary64 pins captured from the production balance/removal owners."""
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from engines.builtin.overhead_bleed import BuiltinOverheadBleedProvider, controlled_flow_capacity
from simulator.accounting import resolve_species_formula
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.overhead import continuous_sweep_balance
from simulator.thermal_train import FiniteCapacity
from tests.test_pressure_projection_pins import _hex


def balance_outputs():
    output = {}
    for capacity in (None, 0.0, 0.007):
        plan = continuous_sweep_balance(
            {"O2": 0.75, "Ar": 1.23456789}, {"O2": 0.13, "CO2": 0.27},
            carrier="Ar", sweep_mol_per_s=0.0037, dt_s=37.0,
            pressure_bar=0.0061, volume_m3=0.137, temperature_K=1873.15,
            initial_external_o2_mol=0.21, generated_external_o2_mol=0.07,
            outlet_capacity_mol_per_s=(None if capacity is None else lambda *_: capacity),
        )
        output[f"mars_{capacity}"] = plan
        result = BuiltinOverheadBleedProvider().dispatch(IntentRequest(
            intent=ChemistryIntent.OVERHEAD_BLEED,
            account_view=ProviderAccountView(accounts={
                "process.overhead_gas": {"O2": 0.88, "Ar": 1.23456789, "CO2": 0.27},
                "reservoir.reagent.sweep_gas": plan["inlet_species_mol"]},
                species_formula_registry={}),
            temperature_C=1600.0, pressure_bar=plan["pressure_bar"], control_inputs={
                "dt_hr": 37.0 / 3600.0, "p_total_bar": plan["pressure_bar"],
                "bleed_conductance_kg_s": 0.0,
                "headspace_volume_m3": 0.137, "headspace_temperature_K": 1873.15,
                "external_o2_in_overhead_mol": 0.28,
                "sweep_outflow_species_mol": plan["outflow_species_mol"],
                "sweep_external_o2_outflow_mol": plan["external_o2_outflow_mol"],
                "sweep_capacity_integrated": True,
                "sweep_capacity_shortfall": plan["capacity_shortfall"],
            }))
        assert result.status == "ok"
        output[f"mars_commit_{capacity}"] = {
            "diagnostic": dict(result.diagnostic),
            "debits": {k: dict(v) for k, v in result.transition.debits.items()} if result.transition else None,
            "credits": {k: dict(v) for k, v in result.transition.credits.items()} if result.transition else None,
        }
    for capacity in (0.0, 0.123456789):
        flow = controlled_flow_capacity(
            pipe_capacity_kg_hr=0.27, equipment_capacity_kg_hr=capacity,
            evolved_flux_kg_hr=0.13, retained_holdup_kg=0.19, dt_hr=0.137,
            upstream_pressure_bar=0.0061, pipe_diameter_m=0.12, pipe_length_m=1.0,
            gas_temperature_K=1873.15, gas_molar_mass_kg_mol=0.037,
        )
        output[f"flow_{capacity}"] = asdict(flow)
    for force, conductance, cold in ((False, 0.0, False), (False, 1e-5, False),
                                    (True, 0.0, False), (False, 1e-5, True),
                                    (True, 0.0, True)):
        controls = {"dt_hr": 0.137, "force_drain_all": force,
                    "bleed_conductance_kg_s": conductance, "p_total_bar": 0.0061,
                    "p_downstream_bar": 0.0, "external_o2_in_overhead_mol": 0.21}
        if cold:
            controls.update(cold_train_capacity=FiniteCapacity(0.00123),
                            p_ref_Pa=610.0, k_relief_kg_hr_Pa=1e-5,
                            p_open_Pa=400.0, vessel_rating_Pa=1000.0)
        inventory = {"O2": 0.75, "Ar": 1.23456789}
        preview = BuiltinOverheadBleedProvider._bled_species_mol(
            inventory, total_mol=sum(inventory.values()), total_kg=sum(n * resolve_species_formula(s).molar_mass_kg_per_mol()
                         for s, n in inventory.items()),
            controls=controls)
        result = BuiltinOverheadBleedProvider().dispatch(IntentRequest(
            intent=ChemistryIntent.OVERHEAD_BLEED,
            account_view=ProviderAccountView(accounts={"process.overhead_gas": inventory},
                                             species_formula_registry={}),
            temperature_C=1600.0, pressure_bar=0.0061, control_inputs=controls))
        assert result.status == "ok"
        output[f"bleed_{force}_{conductance}_{cold}"] = {
            "preview": preview, "diagnostic": dict(result.diagnostic),
            "debits": {k: dict(v) for k, v in result.transition.debits.items()} if result.transition else None,
            "credits": {k: dict(v) for k, v in result.transition.credits.items()} if result.transition else None,
        }
    return _hex(output)


def test_balance_and_removal_binary64_pins():
    expected = json.loads((Path(__file__).parent / "fixtures/gas_balance_pin.json").read_text())
    assert balance_outputs() == expected


def legacy_preview_output():
    from simulator.state import Atmosphere
    from tests.test_overhead_accounting import _gas_train_sim

    sim = _gas_train_sim()
    sim.melt.atmosphere = Atmosphere.PN2_SWEEP
    sim.melt.sweep_mol_per_s = None
    sim._overhead_headspace_config.update(enabled=True, conductance_kg_s=1e-5)
    sim._headspace_volume_m3 = lambda: 0.137
    sim._headspace_temperature_K = lambda: 1873.15
    sim.atom_ledger.load_external_mol("process.overhead_gas", {"O2": 0.75, "Ar": 1.23456789},
                                  source="C5 retained load", material_origin="feedstock")
    return sim._inert_sweep_transport_pO2_bar(0.75).hex()


def test_legacy_core_preview_binary64_pin():
    assert legacy_preview_output() == LEGACY_PREVIEW_HEX

LEGACY_PREVIEW_HEX = '0x1.bc5ff135cc51ep-2'
