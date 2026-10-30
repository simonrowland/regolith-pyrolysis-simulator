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
        exclusion = next(
            (
                entry
                for entry in bucket["excluded"]
                if entry.get("species") == "Si"
                and entry.get("account") == "process.cleaned_melt"
                and "EvaporationFluxConfigurationError"
                in entry.get("reason", "")
            ),
            None,
        )
        assert exclusion is not None
        assert exclusion["species"] == "Si"
        assert exclusion["account"] == "process.cleaned_melt"
        assert "EvaporationFluxConfigurationError" in exclusion["reason"]
        assert isinstance(exclusion["upper_bound_kg"], (int, float))
        assert exclusion["upper_bound_kg"] > 0.0
        assert exclusion["upper_bound_basis"] == "conservative_ledger_bound"

    silica = products["pure_silica_glass"]
    assert silica["stage_3_capture_kg"] == 0.0
    assert silica["stage_3_kg_by_species"] == {}
    sio_estimate = next(
        item
        for item in silica["estimated_contributions"]
        if item["species"] == "SiO"
    )
    assert sio_estimate["account"] == "process.cleaned_melt"
    assert sio_estimate["authority"] in {"status_bearing", "extrapolated"}
    assert sio_estimate["reason"]
    # C0 has no oxygen-hold qualification route: retain raw Stage-3 capture,
    # while the qualified product contribution remains an explicit zero.
    assert silica["class_total_kg"] == 0.0
    glass = products["glass"]
    assert glass["class_total_kg"] == 0.0
    assert glass["pure_silica_glass_kg"] == 0.0
    assert glass["species_kg"] == {}
    assert glass["excluded"] == silica["excluded"]
    assert glass["estimated_contributions"] == silica[
        "estimated_contributions"
    ]
    rump = products["refractory_ceramic_rump"]
    rump_by_species = sim._terminal_rump_by_species()
    rump_by_class = sim._terminal_rump_by_class()
    assert rump["class_total_kg"] == pytest.approx(
        rump_by_class["refractory_oxides"]
    )
    assert rump["rump_total_kg"] == pytest.approx(
        sum(rump_by_species.values())
    )
    assert rump["rump_silicate_residual_kg"] == pytest.approx(
        rump_by_class["silicate_residual"]
    )

    diagnostic = build_cost_rollup_diagnostic(
        sim=sim,
        cost_ledger=sim.cost_ledger,
        per_hour=execution.per_hour,
        products_kg=sim.product_ledger(),
        snapshots=tuple(sim.record.snapshots),
    )
    allocations = diagnostic["product_costs"]
    for allocation in allocations.values():
        assert allocation["status"] == "partial"
        assert allocation["flag"]["status"] == "partial"
        assert isinstance(allocation["quantity_kg"], (int, float))
        assert math.isfinite(allocation["quantity_kg"])
        assert isinstance(allocation["owner_ratify_money_projection"], float)
        assert not is_unavailable_quantity(
            allocation["owner_ratify_money_projection"]
        )
        assert not is_unavailable_quantity(allocation["accumulated_cost"])
        assert allocation["excluded"] == products["metals_plus_O2"]["excluded"]
