import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.chemistry.kernel import ChemistryIntent, LedgerTransitionProposal
from simulator.cost_energy import (
    ELECTRICAL_USD_PER_KWH,
    FURNACE_USD_PER_H,
    THERMAL_USD_PER_FLUX_H,
    furnace_thermal_flux_hours,
    owner_ratify_cost_placeholders,
)
from simulator.cost_ledger import CostImportContext, CostLedger, CostVector
from simulator.cost_ledger import build_cost_rollup_diagnostic
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.runner import PyrolysisRun


ROOT = Path(__file__).resolve().parent.parent
MASS_BALANCE_HARD_GATE_PCT = 5.0e-12


def _cost(summary: dict, key: str) -> CostVector:
    return CostVector(**summary["product_costs"][key]["accumulated_cost"])


def test_cost_ledger_mass_allocates_normal_coproducts_by_product_mass():
    ledger = CostLedger()

    transition = ledger.apply_mass_allocated_event(
        process_step="toy_mass_split",
        outputs_kg={"metal": 2.0, "glass": 3.0},
        processing_cost=CostVector(electrical_kWh=10.0, thermal_flux_h=5.0),
    )

    transition.validate_balance()
    summary = ledger.summary()
    metal = _cost(summary, "product:metal")
    glass = _cost(summary, "product:glass")
    assert metal.electrical_kWh == pytest.approx(4.0)
    assert glass.electrical_kWh == pytest.approx(6.0)
    assert metal.thermal_flux_h == pytest.approx(2.0)
    assert glass.thermal_flux_h == pytest.approx(3.0)
    assert summary["transition_balance_max_abs"] <= 1e-12


def test_reagent_full_cost_cascades_mg_to_al_to_ca_without_atom_coupling():
    ledger = CostLedger()
    ledger.seed_external_material(
        account="process.reagent_inventory",
        species="Mg",
        quantity_kg=1.0,
        cost=CostVector(electrical_kWh=30.0),
    )

    ledger.apply_reagent_full_cost_event(
        process_step="C6_MG",
        reagent_account="process.reagent_inventory",
        reagent_species="Mg",
        reagent_quantity_kg=1.0,
        beneficiary_outputs_kg={("process.metal_phase", "Al"): 2.0},
        coproduct_outputs_kg={("terminal.slag", "MgO"): 3.0},
        processing_cost=CostVector(thermal_flux_h=10.0),
    )
    ledger.apply_reagent_full_cost_event(
        process_step="C7_CA",
        reagent_account="process.metal_phase",
        reagent_species="Al",
        reagent_quantity_kg=2.0,
        beneficiary_outputs_kg={("terminal.product", "Ca"): 1.0},
        coproduct_outputs_kg={("terminal.slag", "calcium_aluminate"): 9.0},
        processing_cost=CostVector(thermal_flux_h=100.0),
    )

    summary = ledger.summary()
    ca = _cost(summary, "terminal.product:Ca")
    cement = _cost(summary, "terminal.slag:calcium_aluminate")
    mgo = _cost(summary, "terminal.slag:MgO")
    assert "process.metal_phase:Al" not in summary["product_costs"]
    assert ca.electrical_kWh == pytest.approx(30.0)
    assert ca.thermal_flux_h == pytest.approx(14.0)
    assert cement.electrical_kWh == pytest.approx(0.0)
    assert cement.thermal_flux_h == pytest.approx(90.0)
    assert mgo.thermal_flux_h == pytest.approx(6.0)
    assert summary["transition_count"] == 2
    assert summary["transition_balance_max_abs"] <= 1e-12


def test_recovered_condensate_can_carry_prior_cost_back_to_reagent_inventory():
    ledger = CostLedger()
    ledger.seed_external_material(
        account="process.reagent_inventory",
        species="Na",
        quantity_kg=1.0,
        cost=CostVector(electrical_kWh=12.0),
    )
    ledger.apply_reagent_full_cost_event(
        process_step="C3_NA_RECOVERY",
        reagent_account="process.reagent_inventory",
        reagent_species="Na",
        reagent_quantity_kg=1.0,
        beneficiary_outputs_kg={("process.condensation_train", "Na"): 1.0},
    )
    ledger.move_inventory_lots(
        source_account="process.condensation_train",
        destination_account="process.reagent_inventory",
        species="Na",
        quantity_kg=1.0,
        reason="recovered Na condensate transfer",
    )
    ledger.apply_reagent_full_cost_event(
        process_step="C3_NA_REUSE",
        reagent_account="process.reagent_inventory",
        reagent_species="Na",
        reagent_quantity_kg=1.0,
        beneficiary_outputs_kg={("terminal.product", "Fe"): 2.0},
    )

    summary = ledger.summary()
    fe = _cost(summary, "terminal.product:Fe")
    assert fe.electrical_kWh == pytest.approx(12.0)
    assert summary["transition_balance_max_abs"] <= 1e-12


def test_import_modes_are_reporting_only_and_apply_launch_penalty_when_enabled():
    mature = CostImportContext.mature()
    bootstrap = CostImportContext.bootstrap_narrative(available_supplier_species=("Na",))

    assert mature.classify("Mg") == "isru_local"
    assert bootstrap.classify("Na") == "isru_local"
    assert bootstrap.classify("Mg") == "import_penalty"
    assert bootstrap.route_option_visible("Mg") is True

    mature_ledger = CostLedger(import_context=mature)
    bootstrap_ledger = CostLedger(import_context=bootstrap)
    mature_lot = mature_ledger.seed_external_material(
        account="reservoir.reagent.Mg",
        species="Mg",
        quantity_kg=2.0,
    )
    bootstrap_lot = bootstrap_ledger.seed_external_material(
        account="reservoir.reagent.Mg",
        species="Mg",
        quantity_kg=2.0,
    )

    assert mature_lot is not None
    assert bootstrap_lot is not None
    assert mature_lot.accumulated_cost.launch_penalty_kg == pytest.approx(0.0)
    assert bootstrap_lot.accumulated_cost.launch_penalty_kg == pytest.approx(2.0)


def test_furnace_flux_tracks_absolute_temperature_times_time():
    cool = furnace_thermal_flux_hours(1000.0, 2.0)
    hot = furnace_thermal_flux_hours(1500.0, 2.0)

    assert cool == pytest.approx((1000.0 + 273.15) * 2.0)
    assert hot > cool
    assert ELECTRICAL_USD_PER_KWH.value > THERMAL_USD_PER_FLUX_H.value
    assert len(owner_ratify_cost_placeholders()) == 5


def test_cost_rollup_leaves_run_input_unallocated_when_no_product_mass():
    diagnostic = build_cost_rollup_diagnostic(
        cost_ledger=CostLedger(),
        per_hour=({"T_C": 1000.0},),
        products_kg={},
    )

    assert diagnostic["run_input_cost"]["allocation_status"] == "unallocated_no_product_mass"
    assert "run_input_cost_unallocated_no_product_mass" in diagnostic["warnings"]
    assert diagnostic["product_costs"] == {}


def test_run_input_allocation_uses_existing_species_product_row():
    ledger = CostLedger()
    ledger.apply_mass_allocated_event(
        process_step="stage0",
        outputs_kg={("terminal.offgas", "CO"): 2.0},
        processing_cost=CostVector(external_reagent_kg=1.0),
    )

    diagnostic = build_cost_rollup_diagnostic(
        cost_ledger=ledger,
        per_hour=({"T_C": 1000.0},),
        products_kg={"CO": 2.0},
    )

    assert "terminal.offgas:CO" in diagnostic["product_costs"]
    assert "terminal.product:CO" not in diagnostic["product_costs"]
    co = CostVector(**diagnostic["product_costs"]["terminal.offgas:CO"]["accumulated_cost"])
    assert co.external_reagent_kg == pytest.approx(1.0)
    assert co.thermal_flux_h == pytest.approx(1273.15)


def test_run_input_allocation_excludes_reagent_bookkeeping_products():
    diagnostic = build_cost_rollup_diagnostic(
        cost_ledger=CostLedger(),
        per_hour=({"T_C": 1000.0},),
        products_kg={
            "Fe": 5.0,
            "unspent_Mg_reagent": 50.0,
            "consumed_C_reagent": 5.0,
        },
    )

    assert "terminal.product:unspent_Mg_reagent" not in diagnostic["product_costs"]
    assert "terminal.product:consumed_C_reagent" not in diagnostic["product_costs"]
    fe = CostVector(
        **diagnostic["product_costs"]["terminal.product:Fe"]["accumulated_cost"]
    )
    assert fe.thermal_flux_h == pytest.approx(1273.15)
    assert fe.furnace_h == pytest.approx(1.0)


def test_cost_rollup_allocates_pumping_sidecar_without_costing_run_input():
    diagnostic = build_cost_rollup_diagnostic(
        cost_ledger=CostLedger(),
        per_hour=({"T_C": 1000.0},),
        products_kg={"O2": 1.0},
        pumping_context={
            "body": "mars",
            "ambient_pressure_pa": 610.0,
            "ambient_pressure_source": "test.mars_datum",
            "rows": (
                {
                    "hour": 1,
                    "target_pressure_pa": 100.0,
                    "offgas_mol_per_s": 0.01,
                    "duration_s": 3600.0,
                    "gas_temperature_K": 300.0,
                },
            ),
        },
    )

    run_input = diagnostic["run_input_cost"]
    physical = run_input["physical_cost"]
    assert physical["thermal_flux_h"] == pytest.approx(1273.15)
    assert physical["furnace_h"] == pytest.approx(1.0)
    assert physical["electrical_kWh"] == pytest.approx(0.0)
    assert run_input["owner_ratify_money_projection"] == pytest.approx(
        1273.15 * THERMAL_USD_PER_FLUX_H.value
        + 1.0 * FURNACE_USD_PER_H.value
    )
    pumping = diagnostic["pumping_diagnostic"]
    assert pumping["status"] == "ok"
    assert pumping["pumping_electrical_kWh"] == pytest.approx(0.3006989878103832)
    assert pumping["rows"][0]["regime"] == "pump"
    components = diagnostic["auxiliary_electrical_diagnostic"]["components_kWh"]
    assert components["pumping"] == pytest.approx(0.3006989878103832)
    assert components["turbine"] == pytest.approx(0.0)
    assert components["condenser"] == pytest.approx(0.0)
    o2 = CostVector(
        **diagnostic["product_costs"]["terminal.product:O2"]["accumulated_cost"]
    )
    assert o2.electrical_kWh == pytest.approx(0.3006989878103832)


def test_cost_rollup_allocates_non_mre_electrical_to_real_products():
    diagnostic = build_cost_rollup_diagnostic(
        cost_ledger=CostLedger(),
        per_hour=(
            {
                "campaign": "C2A",
                "T_C": 1000.0,
                "energy_electrical_kWh": 9.0,
            },
        ),
        products_kg={"Fe": 2.0, "unspent_Mg_reagent": 8.0},
    )

    run_input = diagnostic["run_input_cost"]
    assert run_input["physical_cost"]["electrical_kWh"] == pytest.approx(0.0)
    assert run_input["owner_ratify_money_projection"] == pytest.approx(
        1273.15 * THERMAL_USD_PER_FLUX_H.value
        + 1.0 * FURNACE_USD_PER_H.value
    )
    assert "terminal.product:unspent_Mg_reagent" not in diagnostic["product_costs"]
    fe = CostVector(
        **diagnostic["product_costs"]["terminal.product:Fe"]["accumulated_cost"]
    )
    assert fe.electrical_kWh == pytest.approx(9.0)
    assert fe.thermal_flux_h == pytest.approx(1273.15)


def test_cost_rollup_allocates_c5_mre_auxiliary_electrical_by_component():
    diagnostic = build_cost_rollup_diagnostic(
        cost_ledger=CostLedger(),
        per_hour=(
            {
                "campaign": "C5",
                "T_C": 1500.0,
                "energy_electrical_kWh": 1000.0,
                "energy_electrical_breakdown_kWh": {
                    "turbine_kWh": 2.0,
                    "condenser_kWh": 3.0,
                    "pumping_kWh": 0.5,
                    "mre_kWh": 994.5,
                },
            },
            {
                "campaign": "MRE_BASELINE",
                "T_C": 1600.0,
                "energy_electrical_kWh": 2000.0,
                "energy_electrical_breakdown_kWh": {
                    "turbine_kWh": 4.0,
                    "condenser_kWh": 1.0,
                    "pumping_electrical_kWh": 1.5,
                    "mre_kWh": 1993.5,
                },
            },
        ),
        products_kg={"O2": 1.0},
    )

    components = diagnostic["auxiliary_electrical_diagnostic"]["components_kWh"]
    assert components["turbine"] == pytest.approx(6.0)
    assert components["condenser"] == pytest.approx(4.0)
    assert components["pumping"] == pytest.approx(2.0)
    o2 = CostVector(
        **diagnostic["product_costs"]["terminal.product:O2"]["accumulated_cost"]
    )
    assert o2.electrical_kWh == pytest.approx(12.0)


def test_cost_rollup_uses_snapshot_electrical_breakdown_for_mre_auxiliary():
    diagnostic = build_cost_rollup_diagnostic(
        cost_ledger=CostLedger(),
        per_hour=(
            {
                "hour": 7,
                "campaign": "C5",
                "T_C": 1500.0,
                "energy_electrical_kWh": 100.0,
            },
        ),
        products_kg={"O2": 1.0},
        snapshots=(
            SimpleNamespace(
                hour=7,
                energy=SimpleNamespace(
                    turbine_kWh=2.0,
                    condenser_kWh=3.0,
                    mre_kWh=95.0,
                ),
            ),
        ),
    )

    components = diagnostic["auxiliary_electrical_diagnostic"]["components_kWh"]
    assert components["turbine"] == pytest.approx(2.0)
    assert components["condenser"] == pytest.approx(3.0)
    assert components["pumping"] == pytest.approx(0.0)
    o2 = CostVector(
        **diagnostic["product_costs"]["terminal.product:O2"]["accumulated_cost"]
    )
    assert o2.electrical_kWh == pytest.approx(5.0)


def test_cost_rollup_breakdown_aliases_do_not_double_sum():
    diagnostic = build_cost_rollup_diagnostic(
        cost_ledger=CostLedger(),
        per_hour=(
            {
                "campaign": "C5",
                "T_C": 1500.0,
                "energy_electrical_breakdown_kWh": {
                    "turbine_kWh": 2.0,
                    "turbine": 2.0,
                    "condenser_kWh": 3.0,
                    "condenser": 3.0,
                    "pumping_kWh": 0.5,
                    "pumping": 0.5,
                },
            },
        ),
        products_kg={"O2": 1.0},
    )

    components = diagnostic["auxiliary_electrical_diagnostic"]["components_kWh"]
    assert components["turbine"] == pytest.approx(2.0)
    assert components["condenser"] == pytest.approx(3.0)
    assert components["pumping"] == pytest.approx(0.5)
    o2 = CostVector(
        **diagnostic["product_costs"]["terminal.product:O2"]["accumulated_cost"]
    )
    assert o2.electrical_kWh == pytest.approx(5.5)


def test_cost_rollup_empty_electrical_breakdown_mapping_means_zero():
    diagnostic = build_cost_rollup_diagnostic(
        cost_ledger=CostLedger(),
        per_hour=(
            {
                "campaign": "C2A",
                "T_C": 1000.0,
                "energy_electrical_kWh": 99.0,
                "energy_electrical_breakdown_kWh": {},
            },
        ),
        products_kg={"Fe": 1.0},
    )

    components = diagnostic["auxiliary_electrical_diagnostic"]["components_kWh"]
    assert components == {"condenser": 0.0, "pumping": 0.0, "turbine": 0.0}
    fe = CostVector(
        **diagnostic["product_costs"]["terminal.product:Fe"]["accumulated_cost"]
    )
    assert fe.electrical_kWh == pytest.approx(0.0)
    assert fe.thermal_flux_h == pytest.approx(1273.15)


def test_cost_rollup_metadata_is_golden_neutral_for_runner_fixture():
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=24,
        additives_kg={},
        allow_fallback_vapor=True,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "goal-18-fixture",
        },
    )

    actual = run.run()
    expected = json.loads(
        (ROOT / "tests" / "fixtures" / "runner" / "lunar_mare_low_ti_C0_24h.json")
        .read_text(encoding="utf-8")
    )
    assert "cost_rollup_diagnostic" in actual["run_metadata"]
    stripped_actual = copy.deepcopy(actual)
    stripped_expected = copy.deepcopy(expected)
    stripped_actual["run_metadata"].pop("cost_rollup_diagnostic", None)
    stripped_expected["run_metadata"].pop("cost_rollup_diagnostic", None)
    assert stripped_actual == stripped_expected


def test_cost_observation_exception_does_not_abort_chemistry_commit(
    monkeypatch,
):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"sample": {"composition_wt_pct": {"SiO2": 100.0}}},
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("sample", mass_kg=1.0)
    before_cleaned_mol = sim.atom_ledger.mol_by_account("process.cleaned_melt")[
        "SiO2"
    ]
    before_snapshot = sim._make_snapshot()
    move_mol = before_cleaned_mol * 0.25

    def raise_injected_cost_failure(*args, **kwargs):
        raise RuntimeError("injected cost failure")

    monkeypatch.setattr(
        "simulator.cost_ledger._process_step",
        raise_injected_cost_failure,
    )
    transition = sim._commit_proposal(
        ChemistryIntent.CA_ALUMINOTHERMIC_STEP,
        LedgerTransitionProposal(
            debits={"process.cleaned_melt": {"SiO2": move_mol}},
            credits={"terminal.slag": {"SiO2": move_mol}},
            reason="cost_exception_isolation_smoke",
        ),
    )

    assert transition.name == "cost_exception_isolation_smoke"
    assert sim.atom_ledger.mol_by_account("process.cleaned_melt")[
        "SiO2"
    ] == pytest.approx(before_cleaned_mol - move_mol)
    assert sim.atom_ledger.mol_by_account("terminal.slag")[
        "SiO2"
    ] == pytest.approx(move_mol)
    after_snapshot = sim._make_snapshot()
    assert abs(before_snapshot.mass_balance_error_pct or 0.0) <= MASS_BALANCE_HARD_GATE_PCT
    assert abs(after_snapshot.mass_balance_error_pct or 0.0) <= MASS_BALANCE_HARD_GATE_PCT
    warnings = sim.cost_ledger.summary()["warnings"]
    assert any(
        "cost_observation_error: observe_transition: RuntimeError: "
        "injected cost failure" in warning
        for warning in warnings
    )


def test_cost_seed_exception_does_not_abort_additive_atom_load(
    monkeypatch,
):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"sample": {"composition_wt_pct": {"SiO2": 100.0}}},
        {"metals": {}, "oxide_vapors": {}},
    )

    def raise_injected_seed_failure(self, prefix):
        raise RuntimeError("injected seed failure")

    monkeypatch.setattr(CostLedger, "_next_id", raise_injected_seed_failure)
    sim.load_batch("sample", mass_kg=1.0, additives_kg={"Mg": 0.25})

    reagent_kg = sim.atom_ledger.kg_by_account("reservoir.reagent.Mg")
    assert reagent_kg["Mg"] == pytest.approx(0.25)
    snapshot = sim._make_snapshot()
    assert abs(snapshot.mass_balance_error_pct or 0.0) <= MASS_BALANCE_HARD_GATE_PCT
    summary = sim.cost_ledger.summary()
    assert "reservoir.reagent.Mg:Mg" not in summary["active_inventory_costs"]
    assert any(
        "cost_seed_error: seed_external_material: RuntimeError: "
        "injected seed failure" in warning
        for warning in summary["warnings"]
    )
