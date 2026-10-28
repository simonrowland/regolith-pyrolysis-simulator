"""Product consumers must retain missing transport refusals."""

from types import SimpleNamespace

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

pytestmark = pytest.mark.xdist_group("serial")


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


def test_unqualified_stage3_capture_stays_numeric_with_partial_class(
    two_hour_run, monkeypatch
):
    stage3 = two_hour_run.train.stages[3]
    monkeypatch.setattr(stage3, "collected_kg", {"SiO2": 4.5})
    monkeypatch.setattr(
        two_hour_run.record,
        "snapshots",
        [
            SimpleNamespace(
                evap_flux=snapshot.evap_flux,
                c2a_staged_gas={},
                condensed_by_stage_species_delta={},
            )
            for snapshot in two_hour_run.record.snapshots
        ],
    )

    silica = classify_products(two_hour_run)["pure_silica_glass"]

    assert silica["status"] == "partial"
    assert silica["class_total_kg"] == 0.0
    assert silica["stage_3_capture_kg"] == 4.5
    assert silica["stage_3_kg_by_species"] == {"SiO2": 4.5}
    assert [item["species"] for item in silica["excluded"]] == ["Si"]


def test_zero_oxygen_partition_is_kept_without_refusal(two_hour_run, monkeypatch):
    monkeypatch.setattr(two_hour_run, "_last_evaporation_flux_diagnostic", {})
    monkeypatch.setattr(two_hour_run.record, "snapshots", [])
    monkeypatch.setattr(
        two_hour_run,
        "_oxygen_terminal_partition_kg",
        lambda: {"stored": 1.0, "vented": 0.0, "total": 1.0},
    )

    products = classify_products(two_hour_run)

    assert products["oxygen"]["partition_kg"] == {
        "stored": 1.0,
        "vented": 0.0,
        "total": 1.0,
    }
    assert _metric_value(
        "oxygen_vented_kg", two_hour_run, {}, products
    ) == 0.0


def test_product_class_with_only_refused_parts_is_unavailable(two_hour_run):
    unavailable = product_class_availability(
        two_hour_run,
        ("SiO",),
        {"class_total_kg": 0.0},
        has_computed_part=False,
    )
    assert unavailable["status"] == "unavailable"
    assert unavailable["class_total_kg"] is None


def test_classifier_marks_refused_only_class_unavailable(two_hour_run, monkeypatch):
    monkeypatch.setattr(two_hour_run, "product_ledger", lambda: {})
    products = classify_products(two_hour_run)
    assert products["ingots_metals"]["status"] == "unavailable"
    assert products["ingots_metals"]["class_total_kg"] is None


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
        sim=two_hour_run,
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
    expected_excluded = classify_products(two_hour_run)["metals_plus_O2"][
        "excluded"
    ]
    for entry in diagnostic["product_costs"].values():
        assert entry["status"] == "partial"
        assert entry["flag"]["status"] == "partial"
        assert len(entry["excluded"]) == 1
        assert entry["excluded"][0]["species"] == "Si"
        assert isinstance(entry["quantity_kg"], (int, float))
        assert not is_unavailable_quantity(entry["owner_ratify_money_projection"])
        assert not is_unavailable_quantity(entry["accumulated_cost"])
        assert entry["excluded"] == expected_excluded


def test_product_class_availability_reports_unbounded_without_full_window_ledger():
    refused_snapshot = SimpleNamespace(
        hour=5,
        evap_flux=SimpleNamespace(
            carrier_authority_by_species={
                "Si": {
                    "policy": "fail_loud_missing_transport_parameters",
                    "refusal_type": "EvaporationFluxConfigurationError",
                    "reason": "missing row",
                    "parent_oxide": "SiO2",
                }
            }
        ),
    )
    result = product_class_availability(
        SimpleNamespace(
            _last_evaporation_flux_diagnostic={},
            species_formula_registry={},
        ),
        ("SiO",),
        {"computed": 3.0},
        has_computed_part=True,
        snapshots=(refused_snapshot,),
    )
    assert result["status"] == "partial"
    assert result["class_total_kg"] == 3.0
    assert result["excluded"][0]["upper_bound_kg"] == "unbounded"
    assert "upper_bound_basis" not in result["excluded"][0]


def test_product_class_availability_bounds_from_ledger_gross_flows():
    class Ledger:
        def atom_moles_by_account(self, account):
            return {
                "process.cleaned_melt": {"Si": 10.0},
                "process.spent_reductant_residue": {"Si": 4.0},
            }[account]

        def gross_account_flows(self):
            return {
                "withdrawals": {
                    "material_origin_atom_moles_by_account": {
                        "process.cleaned_melt": {
                            "Si": {"feedstock": 2.0},
                        },
                        "process.spent_reductant_residue": {},
                    },
                    "origin_unattributed_atom_moles_by_account": {
                        "process.cleaned_melt": {"Si": 1.0},
                        "process.spent_reductant_residue": {"Si": 3.0},
                    },
                },
            }

    refused_snapshot = SimpleNamespace(
        evap_flux=SimpleNamespace(
            carrier_authority_by_species={
                "Si": {
                    "policy": "fail_loud_missing_transport_parameters",
                    "refusal_type": "EvaporationFluxConfigurationError",
                    "reason": "missing row",
                    "parent_oxide": "SiO2",
                }
            }
        ),
    )
    result = product_class_availability(
        SimpleNamespace(
            atom_ledger=Ledger(),
            _last_evaporation_flux_diagnostic={},
            species_formula_registry={},
        ),
        ("SiO",),
        {"computed": 3.0},
        has_computed_part=True,
        snapshots=(refused_snapshot,),
    )

    # 20 mol Si at 28.085 g/mol, converted independently to kilograms.
    assert result["excluded"][0]["upper_bound_kg"] == pytest.approx(0.5617)
    assert result["excluded"][0]["upper_bound_basis"] == (
        "conservative_ledger_bound"
    )


def test_cost_refusal_matches_declared_targets_without_si_product(two_hour_run):
    diagnostic = build_cost_rollup_diagnostic(
        sim=two_hour_run,
        cost_ledger=two_hour_run.cost_ledger,
        per_hour=tuple({"T_C": 1500.0} for _ in two_hour_run.record.snapshots),
        products_kg={"Fe": 1.0},
        snapshots=tuple(two_hour_run.record.snapshots),
    )
    assert diagnostic["run_input_cost"]["allocation_status"] == (
        "partial_by_available_product_mass"
    )
    assert diagnostic["run_input_cost"]["excluded_products"][0]["species"] == "Si"


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
