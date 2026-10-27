from __future__ import annotations

import math

import pytest

from simulator import condensation
from simulator.cost_energy import is_unavailable_quantity
from simulator.cost_ledger import build_cost_rollup_diagnostic
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun
from simulator.three_product_report import classify_products


@pytest.mark.xdist_group("serial")
def test_b858_lunar_c0_si_transport_refusal_keeps_computed_products_partial(
    monkeypatch,
):
    # Removing the Si transport row drives the production evaporation refusal
    # path used by b-858; the runner and ledger still compute other products.
    monkeypatch.delitem(condensation._LENNARD_JONES_PARAMS, "Si", raising=False)
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti", campaign="C0", hours=24,
        additives_kg={}, allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    execution = RunExecutor().execute(run._session_config())
    assert execution.status == "ok", execution.error_message
    assert len(execution.snapshots) == 24

    sim = execution.session.simulator
    products = classify_products(sim)
    for name in (
        "metals_plus_O2",
        "ingots_metals",
        "oxygen",
        "pure_silica_glass",
        "glass",
        "refractory_ceramic_rump",
        "captured_volatiles",
    ):
        bucket = products[name]
        assert bucket["status"] == "partial"
        assert isinstance(bucket.get("class_total_kg"), (int, float))
        assert math.isfinite(bucket["class_total_kg"])
        assert len(bucket["excluded"]) == 1
        exclusion = bucket["excluded"][0]
        assert exclusion["species"] == "Si"
        assert exclusion["account"] == "process.cleaned_melt"
        assert "EvaporationFluxConfigurationError" in exclusion["reason"]
        assert exclusion["upper_bound_kg"] == "unbounded"

    silica = products["pure_silica_glass"]
    assert silica["stage_3_capture_kg"] > 0.0
    assert silica["stage_3_kg_by_species"]
    rump = products["refractory_ceramic_rump"]
    assert rump["class_total_kg"] == pytest.approx(542.0)
    assert rump["rump_total_kg"] == pytest.approx(999.0)
    assert rump["rump_silicate_residual_kg"] == pytest.approx(457.0)

    diagnostic = build_cost_rollup_diagnostic(
        sim=sim,
        cost_ledger=sim.cost_ledger,
        per_hour=execution.per_hour,
        products_kg=sim.product_ledger(),
        snapshots=tuple(sim.record.snapshots),
    )
    allocations = diagnostic["product_costs"]
    assert len(allocations) == 21
    for allocation in allocations.values():
        assert allocation["status"] == "partial"
        assert isinstance(allocation["quantity_kg"], (int, float))
        assert math.isfinite(allocation["quantity_kg"])
        assert isinstance(allocation["owner_ratify_money_projection"], float)
        assert not is_unavailable_quantity(
            allocation["owner_ratify_money_projection"]
        )
        assert not is_unavailable_quantity(allocation["accumulated_cost"])
        assert len(allocation["excluded"]) == 1
        assert allocation["excluded"][0]["species"] == "Si"
        assert allocation["excluded"] == products["metals_plus_O2"]["excluded"]
