"""Product consumers must retain missing transport refusals."""

import pytest

from simulator.chemistry.kernel import ChemistryIntent
from simulator.condensation import _LENNARD_JONES_PARAMS
from simulator.cost_energy import is_unavailable_quantity
from simulator.cost_ledger import build_cost_rollup_diagnostic
from simulator.accounting.completeness import product_class_availability
from simulator.optimize.objective import _metric_value
from simulator.optimize.physics import (
    PhysicsConstraintSet,
    ThresholdSpec,
    extraction_completeness_report,
)
from simulator.state import CampaignPhase
from simulator.three_product_report import classify_products
from simulator.trace import PhysicsTrace
from tests.chemistry.conftest import _build_sim
from tests.chemistry.test_builtin_evaporation_flux_provider import (
    _w3_result_with_controls,
)


@pytest.fixture(scope="module")
def two_hour_run(request, vapor_pressure_data, feedstocks_data, setpoints_data):
    monkeypatch = pytest.MonkeyPatch()
    request.addfinalizer(monkeypatch.undo)
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    original_dispatch = sim._dispatch_only
    monkeypatch.setattr(sim, "_freeze_gate_enabled", lambda: False)
    with monkeypatch.context() as missing:
        missing.delitem(_LENNARD_JONES_PARAMS, "Si", raising=False)
        result = _w3_result_with_controls(
            1.0,
            overhead_pressure_pa=1000.0,
            vapour_batch_flux_pressures_Pa={"Na": 100.0, "Si": 100.0},
            molar_mass_kg_mol={"Na": 0.023, "Si": 0.028085},
            stoich_by_species={
                "Na": {"parent_oxide": "Na2O", "oxide_per_product_kg": 1.347},
                "Si": {"parent_oxide": "SiO2", "oxide_per_product_kg": 2.139},
            },
            available_oxide_kg={"Na": 10.0, "Si": 10.0},
            alpha={"Na": 0.5, "Si": 0.5},
        )
    assert result.status == "ok"
    refusal = result.diagnostic["missing_transport_parameters"]["Si"]
    assert refusal["status"] == "unavailable"
    assert refusal["refusal_type"] == "EvaporationFluxConfigurationError"
    assert refusal["parent_oxide"] == "SiO2"

    def dispatch(intent, *args, **kwargs):
        if intent is ChemistryIntent.EVAPORATION_FLUX and not sim.record.snapshots:
            return result
        return original_dispatch(intent, *args, **kwargs)

    monkeypatch.setattr(sim, "_dispatch_only", dispatch)
    for _ in range(2):
        sim.melt.campaign = CampaignPhase.C4
        sim.melt.temperature_C = 1500.0
        sim.step()
    assert len(sim.record.snapshots) == 2
    assert "Si" in sim.record.snapshots[0].evap_flux.carrier_authority_by_species
    sim._last_evaporation_flux_diagnostic = {}
    sim._last_extraction_completeness_diagnostic = {
        "detail_by_target_species": {"Al": {"reason": "later Al diagnostic"}},
        "completeness_by_target_species": {"Al": None},
    }
    return sim


def test_oxygen_partitions_inherit_partial_product_flag(two_hour_run, monkeypatch):
    monkeypatch.setattr(
        two_hour_run,
        "_oxygen_terminal_partition_kg",
        lambda: {"stored": 1.0, "total": 1.0},
    )
    products = classify_products(two_hour_run)
    assert products["oxygen"]["class_total_kg"] == 1.0
    assert products["oxygen"]["status"] == "partial"
    assert products["oxygen"]["excluded"] == products["oxygen"]["flag"]["excluded"]
    assert products["oxygen"]["partition_kg"] == {"stored": 1.0, "total": 1.0}
    metals_partition = products["metals_plus_O2"]["O2_partition_kg"]
    assert all(value is not None for value in metals_partition.values())
    for bucket in ("oxygen", "metals_plus_O2"):
        assert products[bucket]["status"] == "partial"
        assert "EvaporationFluxConfigurationError" in products[bucket]["flag"]["reason"]


def test_real_si_refusal_keeps_product_classes_numeric_and_partial(two_hour_run):
    products = classify_products(two_hour_run)
    for name in (
        "metals_plus_O2",
        "ingots_metals",
        "pure_silica_glass",
        "glass",
        "refractory_ceramic_rump",
        "captured_volatiles",
    ):
        bucket = products[name]
        assert bucket["status"] == "partial"
        assert isinstance(bucket.get("class_total_kg"), (int, float))
        assert len(bucket["excluded"]) == 1
        assert bucket["excluded"][0]["species"] == "Si"
        assert bucket["excluded"][0]["upper_bound_kg"] != 0


def test_product_class_with_only_refused_parts_is_unavailable(two_hour_run):
    unavailable = product_class_availability(
        two_hour_run, ("SiO",), {}
    )
    assert unavailable["status"] == "unavailable"
    assert unavailable["class_total_kg"] is None


def test_extraction_gate_does_not_use_partial_products(two_hour_run):
    constraints = PhysicsConstraintSet(
        target_species=("SiO",),
        extraction_min_fraction=ThresholdSpec(
            id="extraction_completeness_min",
            value=0.0001,
            units="fraction",
            source="code_default",
            source_ref="b-858 refused-channel regression",
        ),
    )
    trace = PhysicsTrace.from_simulator(two_hour_run)
    report = extraction_completeness_report(trace, constraints)
    assert report["targets"]["SiO"]["completeness_fraction"] is None
    assert "EvaporationFluxConfigurationError" in report["reason"]
    margin = constraints.extraction_completeness(trace)
    assert not margin.feasible
    assert margin.status == "unavailable"
    assert not margin.authoritative


def test_cost_allocations_keep_numeric_mass_and_partial_flag(two_hour_run):
    diagnostic = build_cost_rollup_diagnostic(
        cost_ledger=two_hour_run.cost_ledger,
        per_hour=tuple({"T_C": 1500.0} for _ in two_hour_run.record.snapshots),
        products_kg=two_hour_run.product_ledger(),
        snapshots=tuple(two_hour_run.record.snapshots),
    )
    run_input = diagnostic["run_input_cost"]
    assert run_input["allocation_status"] == "partial_by_available_product_mass"
    assert len(run_input["excluded_products"]) == 1
    assert run_input["excluded_products"][0]["species"] == "Si"
    assert isinstance(run_input["owner_ratify_money_projection"], float)
    assert diagnostic["product_costs"]
    assert len(diagnostic["product_costs"]) == 21
    for entry in diagnostic["product_costs"].values():
        assert entry["status"] == "partial"
        assert len(entry["excluded"]) == 1
        assert not is_unavailable_quantity(entry["owner_ratify_money_projection"])
        assert not is_unavailable_quantity(entry["accumulated_cost"])


def test_product_class_availability_reports_ledger_bound_and_unbounded():
    class Ledger:
        def atom_moles_by_account(self, account):
            assert account == "process.cleaned_melt"
            return {"Si": 2.0}

    class Sim:
        _last_evaporation_flux_diagnostic = {
            "missing_transport_parameters": {
                "Si": {
                    "policy": "fail_loud_missing_transport_parameters",
                    "refusal_type": "EvaporationFluxConfigurationError",
                    "reason": "missing row",
                    "parent_oxide": "SiO2",
                }
            }
        }
        species_formula_registry = {}
        atom_ledger = Ledger()

    bounded = product_class_availability(Sim(), ("SiO",), {"computed": 3.0})
    assert bounded["status"] == "partial"
    assert bounded["class_total_kg"] == 3.0
    assert bounded["excluded"][0]["upper_bound_kg"] == pytest.approx(0.05617)

    class NoLedger:
        _last_evaporation_flux_diagnostic = Sim._last_evaporation_flux_diagnostic
        species_formula_registry = {}

    unbounded = product_class_availability(
        NoLedger(), ("SiO",), {"computed": 3.0}
    )
    assert unbounded["excluded"][0]["upper_bound_kg"] == "unbounded"


@pytest.mark.parametrize("metric", ("oxygen_stored_kg", "oxygen_vented_kg"))
def test_oxygen_objectives_use_available_partial_partitions(
    two_hour_run, monkeypatch, metric
):
    monkeypatch.setattr(
        two_hour_run,
        "_oxygen_terminal_partition_kg",
        lambda: {"stored": 1.0, "vented": 1.0, "total": 2.0},
    )
    assert _metric_value(
        metric, two_hour_run, {}, classify_products(two_hour_run)
    ) == 1.0
