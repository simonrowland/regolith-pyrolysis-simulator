import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.chemistry.kernel import (
    ChemistryIntent,
    IntentResult,
    LedgerTransitionProposal,
)
from simulator.accounting.ledger import LedgerTransition
from simulator.cost_energy import (
    ELECTRICAL_USD_PER_KWH,
    FURNACE_USD_PER_H,
    THERMAL_USD_PER_FLUX_H,
    furnace_thermal_flux_hours,
    is_unavailable_quantity,
    owner_ratify_cost_placeholders,
    unavailable_reason_of,
)
from simulator.cost_ledger import CostImportContext, CostLedger, CostVector
from simulator.cost_ledger import build_cost_rollup_diagnostic, run_pumping_input_cost
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.runner import PyrolysisRun


ROOT = Path(__file__).resolve().parent.parent
MASS_BALANCE_HARD_GATE_PCT = 5.0e-12


def _cost(summary: dict, key: str) -> CostVector:
    return CostVector(**summary["product_costs"][key]["accumulated_cost"])


@pytest.mark.parametrize("diagnostic", [
    {"reason_refused": "uncertified_multi_oxide_current_partition"},
    {"reason_refused": "uncertified_multi_oxide_current_partition", "energy_kWh": 0.0},
    {},
    {"energy_kWh": None},
])
def test_unavailable_electrolysis_energy_skips_cost_allocation(diagnostic):
    ledger = CostLedger()
    ledger.seed_external_material(
        account="process.metal_phase", species="Fe", quantity_kg=1.0,
        cost=CostVector(electrical_kWh=3.0),
    )
    before = ledger.summary()
    result = ledger.observe_transition(
        intent=ChemistryIntent.ELECTROLYSIS_STEP,
        transition=LedgerTransition.move(
            "mre", "process.metal_phase", "terminal.product", {"Fe": 1.0}
        ),
        diagnostic=diagnostic,
    )
    after = ledger.summary()
    assert result is None
    assert after["transition_count"] == before["transition_count"]
    assert after["active_inventory_costs"] == before["active_inventory_costs"]
    assert after["product_costs"] == before["product_costs"]
    reason = diagnostic.get("reason_refused") or "missing_energy_kWh"
    assert any(reason in warning for warning in after["warnings"])


@pytest.mark.parametrize(("intent", "diagnostic", "energy"), [
    (ChemistryIntent.ELECTROLYSIS_STEP, {"energy_kWh": 0.0}, 0.0),
    (ChemistryIntent.ELECTROLYSIS_STEP, {"energy_kWh": 1.25}, 1.25),
    (ChemistryIntent.CA_ALUMINOTHERMIC_STEP, {}, 0.0),
])
def test_computed_energy_and_non_electrical_cost_controls(intent, diagnostic, energy):
    ledger = CostLedger()
    ledger.seed_external_material(
        account="process.metal_phase", species="Fe", quantity_kg=1.0,
        cost=CostVector(electrical_kWh=3.0),
    )
    result = ledger.observe_transition(
        intent=intent,
        transition=LedgerTransition.move(
            "mre", "process.metal_phase", "terminal.product", {"Fe": 1.0}
        ),
        diagnostic=diagnostic,
    )
    assert result is not None
    assert result.processing_cost_added.electrical_kWh == pytest.approx(energy)
    assert ledger.summary()["warnings"] == []


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
    assert ELECTRICAL_USD_PER_KWH.value == 0.15
    assert THERMAL_USD_PER_FLUX_H.value == 1.0
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


def test_run_input_allocation_reports_terminal_mass_basis_not_cost_lot_mass():
    ledger = CostLedger()
    ledger.apply_mass_allocated_event(
        process_step="stage0",
        outputs_kg={("terminal.offgas", "CO"): 2.0},
        processing_cost=CostVector(external_reagent_kg=1.0),
    )

    diagnostic = build_cost_rollup_diagnostic(
        cost_ledger=ledger,
        per_hour=({"T_C": 1000.0},),
        products_kg={"CO": 10.0},
    )

    assert diagnostic["product_costs"]["terminal.offgas:CO"]["quantity_kg"] == pytest.approx(10.0)


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
                    "validated_line_conductance_m3_s": 100.0,
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
    expected_pumping_kWh = 0.0735236524284681 / 0.90
    assert pumping["pumping_electrical_kWh"] == pytest.approx(expected_pumping_kWh)
    assert pumping["rows"][0]["regime"] == "pump"
    components = diagnostic["auxiliary_electrical_diagnostic"]["components_kWh"]
    assert components["pumping"] == pytest.approx(expected_pumping_kWh)
    assert components["turbine"] == pytest.approx(0.0)
    assert components["condenser"] == pytest.approx(0.0)
    o2 = CostVector(
        **diagnostic["product_costs"]["terminal.product:O2"]["accumulated_cost"]
    )
    assert o2.electrical_kWh == pytest.approx(expected_pumping_kWh)


def test_cost_rollup_prefers_pumping_context_over_per_hour_breakdown():
    reviewer_reproduction_kWh = 0.0735236524284681
    diagnostic = build_cost_rollup_diagnostic(
        cost_ledger=CostLedger(),
        per_hour=(
            {
                "T_C": 1000.0,
                "energy_electrical_breakdown_kWh": {
                    "pumping_kWh": reviewer_reproduction_kWh,
                },
            },
        ),
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
                    "validated_line_conductance_m3_s": 100.0,
                },
            ),
        },
    )

    expected_context_kWh = reviewer_reproduction_kWh / 0.90
    components = diagnostic["auxiliary_electrical_diagnostic"]["components_kWh"]
    o2 = CostVector(
        **diagnostic["product_costs"]["terminal.product:O2"]["accumulated_cost"]
    )
    assert components["pumping"] == pytest.approx(expected_context_kWh)
    assert o2.electrical_kWh == pytest.approx(expected_context_kWh)
    # Reviewer reproduced 0.0735 + 0.0735 = 0.1470 kWh before the authority rule.
    assert o2.electrical_kWh != pytest.approx(2.0 * reviewer_reproduction_kWh)


def test_cost_rollup_marks_pumping_feasibility_unresolved_without_conductance():
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

    pumping = diagnostic["pumping_diagnostic"]
    assert pumping["status"] == "pumping_feasibility_unresolved"
    assert pumping["feasible"] is None
    assert pumping["rows"][0]["status"] == "missing-validated-line-conductance"
    assert pumping["pumping_electrical_kWh"] == pytest.approx(
        0.0735236524284681 / 0.90
    )


def test_cost_rollup_preserves_typed_pumping_context_refusal():
    diagnostic = build_cost_rollup_diagnostic(
        cost_ledger=CostLedger(),
        per_hour=({"T_C": 1000.0},),
        products_kg={"O2": 1.0},
        pumping_context={
            "schema_version": "pumping-context-v1",
            "status": "refused",
            "reason": "missing-ambient-pressure",
            "body": "mars",
            "ambient_pressure_pa": float("nan"),
            "rows": (),
        },
    )

    pumping = diagnostic["pumping_diagnostic"]
    assert pumping["status"] == "refused"
    assert pumping["reason"] == "missing-ambient-pressure"
    assert pumping["feasible"] is False
    assert is_unavailable_quantity(pumping["pumping_electrical_kWh"])
    assert unavailable_reason_of(pumping["pumping_electrical_kWh"]) == "missing-ambient-pressure"
    assert diagnostic["run_input_cost"]["allocation_status"] == "incomplete_unavailable_pumping"
    o2_money = diagnostic["product_costs"]["terminal.product:O2"]["owner_ratify_money_projection"]
    assert is_unavailable_quantity(o2_money)
    assert unavailable_reason_of(o2_money) == "missing-ambient-pressure"
    assert diagnostic["product_costs"]["terminal.product:O2"]["completeness"] == "incomplete"


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
    # Fixture regenerated 2026-08-02 under REPAIRED MAGEMin config per the
    # train13 adjudication; prior value was generated against the
    # broken-liquidus job tree. docs-private/research/2026-08-02-train13-adjudication.md
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=24,
        additives_kg={},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
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
    assert actual["run_metadata"]["cost_rollup_diagnostic"]["price_basis"] == (
        "legacy_placeholder_awaiting_owner_ratification"
    )
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
    intent = ChemistryIntent.CA_ALUMINOTHERMIC_STEP
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
    provider = sim._chem_registry.authoritative_for(intent)
    assert provider is not None

    def dispatch_cost_isolation_proposal(request):
        return IntentResult(
            intent=request.intent,
            status="ok",
            transition=LedgerTransitionProposal(
                debits={"process.cleaned_melt": {"SiO2": move_mol}},
                credits={"terminal.slag": {"SiO2": move_mol}},
                reason="cost_exception_isolation_smoke",
            ),
        )

    monkeypatch.setattr(provider, "dispatch", dispatch_cost_isolation_proposal)
    result = sim._dispatch_only(intent, control_inputs={})
    assert result.transition is not None
    transition = sim._commit_proposal(
        intent,
        result.transition,
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


def _assert_unavailable(value, reason: str, units: str) -> None:
    assert is_unavailable_quantity(value)
    assert value["value"] is None
    assert unavailable_reason_of(value) == reason
    assert value["units"] == units


def _rh84_subambient_context(offgas_mol_per_s):
    row = {
        "hour": 12,
        "target_pressure_pa": 500.0,
        "duration_s": 3600.0,
        "gas_temperature_K": 300.0,
        "validated_line_conductance_m3_s": 1.0,
    }
    if offgas_mol_per_s != "ABSENT":
        row["offgas_mol_per_s"] = offgas_mol_per_s
    return {
        "status": "ok",
        "feedstock_id": "mars_basalt",
        "body": "mars",
        "ambient_pressure_pa": 610.0,
        "rows": [row],
    }


def test_missing_offgas_row_is_unavailable_energy_and_money() -> None:
    cost, diagnostic = run_pumping_input_cost(_rh84_subambient_context("ABSENT"))
    assert cost is None
    assert diagnostic["status"] == "refused"
    _assert_unavailable(
        diagnostic["pumping_electrical_kWh"], "invalid-offgas-rate", "kWh"
    )
    rollup = build_cost_rollup_diagnostic(
        cost_ledger=CostLedger(),
        per_hour=({"T_C": 1000.0},),
        products_kg={"O2": 1.0},
        pumping_context=_rh84_subambient_context("ABSENT"),
    )
    assert rollup["run_input_cost"]["allocation_status"] == (
        "incomplete_unavailable_pumping"
    )
    product = rollup["product_costs"]["terminal.product:O2"]
    _assert_unavailable(
        product["owner_ratify_money_projection"], "invalid-offgas-rate", "USD"
    )
    _assert_unavailable(
        product["accumulated_cost"]["electrical_kWh"], "invalid-offgas-rate", "kWh"
    )
    assert product["completeness"] == "incomplete"
    _assert_unavailable(
        rollup["auxiliary_electrical_diagnostic"]["auxiliary_electrical_kWh"],
        "invalid-offgas-rate",
        "kWh",
    )


def test_invalid_negative_offgas_is_typed_refusal_not_zero() -> None:
    cost, diagnostic = run_pumping_input_cost(_rh84_subambient_context(-0.01))
    assert cost is None
    assert diagnostic["reason"] == "invalid-offgas-rate"
    _assert_unavailable(
        diagnostic["pumping_electrical_kWh"], "invalid-offgas-rate", "kWh"
    )


def test_measured_zero_offgas_row_stays_numeric_zero() -> None:
    cost, diagnostic = run_pumping_input_cost(_rh84_subambient_context(0.0))
    assert cost is not None
    assert cost.electrical_kWh == 0.0
    assert diagnostic["status"] == "ok"
    assert diagnostic["pumping_electrical_kWh"] == 0.0
    assert not is_unavailable_quantity(diagnostic["pumping_electrical_kWh"])
    rollup = build_cost_rollup_diagnostic(
        cost_ledger=CostLedger(),
        per_hour=({"T_C": 1000.0},),
        products_kg={"O2": 1.0},
        pumping_context=_rh84_subambient_context(0.0),
    )
    assert rollup["run_input_cost"]["allocation_status"] == "allocated_by_product_mass"
    money = rollup["product_costs"]["terminal.product:O2"][
        "owner_ratify_money_projection"
    ]
    assert isinstance(money, float)
    assert not is_unavailable_quantity(money)


def test_refused_context_money_is_unavailable_not_silently_short() -> None:
    rollup = build_cost_rollup_diagnostic(
        cost_ledger=CostLedger(),
        per_hour=({"T_C": 1000.0},),
        products_kg={"O2": 1.0},
        pumping_context={
            "schema_version": "pumping-context-v1",
            "status": "refused",
            "reason": "missing-o2-vented-flow",
            "feedstock_id": "mars_basalt",
            "body": "mars",
            "ambient_pressure_pa": 610.0,
            "rows": (),
        },
    )
    money = rollup["product_costs"]["terminal.product:O2"][
        "owner_ratify_money_projection"
    ]
    _assert_unavailable(money, "missing-o2-vented-flow", "USD")
    with pytest.raises(TypeError):
        sum(
            entry["owner_ratify_money_projection"]
            for entry in rollup["product_costs"].values()
        )


def test_cost_rollup_unavailable_money_mutation_fails_then_restores(monkeypatch) -> None:
    from simulator import cost_ledger as ledger_mod
    from simulator.cost_energy import unavailable_quantity

    original = ledger_mod.run_pumping_input_cost

    def mutated(context):
        cost, diagnostic = original(context)
        diagnostic = dict(diagnostic)
        diagnostic["pumping_electrical_kWh"] = 0.0
        return CostVector(), diagnostic

    context = {
        "schema_version": "pumping-context-v1",
        "status": "refused",
        "reason": "missing-o2-vented-flow",
        "body": "mars",
        "rows": (),
    }
    kwargs = dict(
        cost_ledger=CostLedger(),
        per_hour=({"T_C": 1000.0},),
        products_kg={"O2": 1.0},
        pumping_context=context,
    )
    monkeypatch.setattr(ledger_mod, "run_pumping_input_cost", mutated)
    with pytest.raises(AssertionError):
        money = ledger_mod.build_cost_rollup_diagnostic(**kwargs)["product_costs"][
            "terminal.product:O2"
        ]["owner_ratify_money_projection"]
        assert is_unavailable_quantity(money)
    monkeypatch.setattr(ledger_mod, "run_pumping_input_cost", original)
    restored = ledger_mod.build_cost_rollup_diagnostic(**kwargs)
    _assert_unavailable(
        restored["product_costs"]["terminal.product:O2"][
            "owner_ratify_money_projection"
        ],
        "missing-o2-vented-flow",
        "USD",
    )
    assert restored["run_input_cost"]["allocation_status"] == (
        "incomplete_unavailable_pumping"
    )
    assert isinstance(unavailable_quantity(reason="x", units="USD"), dict)


def test_energy_and_money_consumers_do_not_default_unavailable_to_zero() -> None:
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    consumer_files = (
        root / "simulator/cost_ledger.py",
        root / "simulator/cost_energy.py",
        root / "simulator/pumping_cost.py",
        root / "simulator/optimize/evaluate.py",
        root / "simulator/optimize/objective.py",
        root / "simulator/accounting/run_artifact.py",
        root / "scripts/sso2_owner_recipe_report.py",
    )
    energy_names = {
        "pumping_electrical_kWh",
        "pumping_electrical_energy_kWh",
        "owner_ratify_money_projection",
    }

    def _name(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    violations = []
    for path in consumer_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            func_name = _name(func)
            if func_name != "_finite":
                continue
            if len(node.args) < 1:
                continue
            target = _name(node.args[0])
            if target not in energy_names:
                continue
            default = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                default = node.args[1].value
            for keyword in node.keywords:
                if keyword.arg == "default" and isinstance(keyword.value, ast.Constant):
                    default = keyword.value.value
            if default == 0.0:
                violations.append(f"{path.name}:{node.lineno} _finite({target}, 0.0)")
    js_files = (
        root / "web/report_viewer/panels/p8-cost-rollup.js",
        root / "web/report_viewer/report-viewer.js",
    )
    for path in js_files:
        text = path.read_text(encoding="utf-8")
        assert "isUnavailableQuantity" in text, path.name
        assert "unavailable" in text
    owner_report = (root / "scripts/sso2_owner_recipe_report.py").read_text(encoding="utf-8")
    assert "pumping_electrical_kWh" not in owner_report
    assert "owner_ratify_money_projection" not in owner_report
    assert violations == []
