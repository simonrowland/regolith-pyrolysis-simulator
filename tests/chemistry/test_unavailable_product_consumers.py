"""Product consumers must retain missing transport refusals."""

import pytest

from simulator.chemistry.kernel import ChemistryIntent
from simulator.condensation import _LENNARD_JONES_PARAMS
from simulator.cost_energy import is_unavailable_quantity
from simulator.cost_ledger import build_cost_rollup_diagnostic
from simulator.optimize.objective import ObjectiveComputationError, _metric_value
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


def test_oxygen_partitions_inherit_unavailable_product_total(two_hour_run, monkeypatch):
    monkeypatch.setattr(
        two_hour_run,
        "_oxygen_terminal_partition_kg",
        lambda: {"stored": 1.0, "total": 1.0},
    )
    products = classify_products(two_hour_run)
    assert products["oxygen"]["class_total_kg"] is None
    for bucket, key in (
        ("oxygen", "partition_kg"),
        ("metals_plus_O2", "O2_partition_kg"),
    ):
        assert products[bucket][key] == {"stored": None, "total": None}
        assert "EvaporationFluxConfigurationError" in products[bucket]["flag"]["reason"]


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


def test_cost_allocation_refuses_partial_product_denominator(two_hour_run):
    diagnostic = build_cost_rollup_diagnostic(
        cost_ledger=two_hour_run.cost_ledger,
        per_hour=tuple({"T_C": 1500.0} for _ in two_hour_run.record.snapshots),
        products_kg=two_hour_run.product_ledger(),
        snapshots=tuple(two_hour_run.record.snapshots),
    )
    run_input = diagnostic["run_input_cost"]
    assert run_input["allocation_status"] == "incomplete_unavailable_products"
    assert "EvaporationFluxConfigurationError" in run_input["unavailable_reason"]
    assert isinstance(run_input["owner_ratify_money_projection"], float)
    assert diagnostic["product_costs"]
    for entry in diagnostic["product_costs"].values():
        assert is_unavailable_quantity(entry["owner_ratify_money_projection"])
        assert is_unavailable_quantity(entry["accumulated_cost"])


@pytest.mark.parametrize("metric", ("oxygen_stored_kg", "oxygen_vented_kg"))
def test_oxygen_objectives_refuse_unavailable_partitions(
    two_hour_run, monkeypatch, metric
):
    monkeypatch.setattr(
        two_hour_run,
        "_oxygen_terminal_partition_kg",
        lambda: {"stored": 1.0, "vented": 1.0, "total": 2.0},
    )
    with pytest.raises(
        ObjectiveComputationError,
        match="EvaporationFluxConfigurationError",
    ):
        _metric_value(metric, two_hour_run, {}, classify_products(two_hour_run))
