from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import ast

import pytest

from simulator.coating_lifespan import CampaignsToResinterTotal, FoulingProjectionError
from simulator.optimize.fouling_lifecycle import (
    FoulingLifecycleHarness,
    FoulingRunArtifact,
)


def _artifact(
    deposit,
    *,
    ledger=None,
    mass_balance_error_pct: float = 0.0,
    authority=None,
    campaigns_total=None,
    result_document=None,
) -> FoulingRunArtifact:
    trace = SimpleNamespace(
        wall_deposit_by_segment_species_kg=deposit,
        wall_deposit_sticking_authority=authority
        or {"authoritative_for_resinter": True},
        snapshots=(SimpleNamespace(mass_balance_error_pct=mass_balance_error_pct),),
    )
    return FoulingRunArtifact(
        trace=trace,
        simulator=SimpleNamespace(atom_ledger=ledger) if ledger is not None else None,
        snapshots=trace.snapshots,
        campaigns_to_resinter_total=campaigns_total,
        result_document=result_document,
    )


def test_harness_runs_n_iterations_without_ledger_reuse_or_closure_breach() -> None:
    ledgers = [object(), object()]
    deposits = [
        {("duct_a", "SiO"): 0.1},
        {("duct_a", "SiO"): 0.2, ("duct_b", "Si"): 0.05},
    ]

    def run_campaign(index: int) -> FoulingRunArtifact:
        return _artifact(deposits[index], ledger=ledgers[index])

    result = FoulingLifecycleHarness(
        run_campaign,
        segment_area_m2={"duct_a": 1.0, "duct_b": 2.0},
        rho_deposit_kg_m3={"SiO": 1000.0, "Si": 2000.0},
        thickness_limit_m=0.00050,
    ).run((0, 1))

    assert len(result.run_records) == 2
    assert result.run_records[0].atom_ledger is not result.run_records[1].atom_ledger
    assert result.run_records[0].mass_balance_error_pct == pytest.approx(0.0)
    assert result.fouling_state_trajectory[-1]["duct_a"]["SiO"] == pytest.approx(0.3)
    assert result.fouling_state_trajectory[-1]["duct_b"]["Si"] == pytest.approx(0.05)
    assert result.lifecycle_projection.service_life_authoritative is False
    assert result.lifecycle_projection.service_life_campaigns == pytest.approx(10 / 3)


def test_harness_rejects_warm_worker_reused_atom_ledger() -> None:
    ledger = object()

    def run_campaign(_index: int) -> FoulingRunArtifact:
        return _artifact({("duct_a", "SiO"): 0.1}, ledger=ledger)

    harness = FoulingLifecycleHarness(
        run_campaign,
        segment_area_m2={"duct_a": 1.0},
        rho_deposit_kg_m3=1000.0,
        thickness_limit_m=0.001,
    )

    with pytest.raises(FoulingProjectionError, match="reused atom_ledger"):
        harness.run((0, 1))


def test_harness_rejects_constituent_mass_balance_regression() -> None:
    def run_campaign(_index: int) -> FoulingRunArtifact:
        return _artifact(
            {("duct_a", "SiO"): 0.1},
            ledger=object(),
            mass_balance_error_pct=6e-12,
        )

    harness = FoulingLifecycleHarness(
        run_campaign,
        segment_area_m2={"duct_a": 1.0},
        rho_deposit_kg_m3=1000.0,
        thickness_limit_m=0.001,
    )

    with pytest.raises(FoulingProjectionError, match="mass balance closure"):
        harness.run((0,))


def test_harness_namespaces_runner_total_parity_from_lifecycle_projection() -> None:
    runner_verdict = {
        "campaigns_to_resinter": "resinter_threshold_kg / 0.3",
        "authoritative_for_resinter": True,
    }

    def run_campaign(_index: int) -> FoulingRunArtifact:
        return _artifact(
            {("duct_a", "SiO"): 0.3},
            ledger=object(),
            result_document={"fouling_rate": runner_verdict},
        )

    result = FoulingLifecycleHarness(
        run_campaign,
        segment_area_m2={"duct_a": 1.0},
        rho_deposit_kg_m3=None,
        thickness_limit_m=None,
        resinter_threshold_kg=None,
    ).run((0,))

    assert result.campaigns_to_resinter_total.to_dict() == {
        "value": "resinter_threshold_kg / 0.3",
        "authoritative_for_resinter": True,
    }
    assert result.lifecycle_projection.to_dict()["service_life_campaigns"] is None
    assert result.lifecycle_projection.to_dict()["service_life_authoritative"] is False


def test_harness_accepts_explicit_total_struct_and_preserves_authority_namespace() -> None:
    total = CampaignsToResinterTotal(value=7.5, authoritative_for_resinter=True)

    def run_campaign(_index: int) -> FoulingRunArtifact:
        return _artifact(
            {("duct_a", "SiO"): 0.1},
            ledger=object(),
            campaigns_total=total,
        )

    result = FoulingLifecycleHarness(
        run_campaign,
        segment_area_m2={"duct_a": 1.0},
        rho_deposit_kg_m3=1000.0,
        thickness_limit_m=0.001,
    ).run((0,))

    assert result.campaigns_to_resinter_total is total
    assert result.lifecycle_projection.service_life_authoritative is False


def test_projection_result_is_order_independent_for_non_feedback_runs() -> None:
    deposits = {
        "low": {("duct_a", "SiO"): 0.1},
        "high": {("duct_a", "SiO"): 0.2},
    }

    def run_campaign(name: str) -> FoulingRunArtifact:
        return _artifact(deposits[name], ledger=object())

    kwargs = {
        "segment_area_m2": {"duct_a": 1.0},
        "rho_deposit_kg_m3": 1000.0,
        "thickness_limit_m": 0.001,
    }
    forward = FoulingLifecycleHarness(run_campaign, **kwargs).run(("low", "high"))
    reverse = FoulingLifecycleHarness(run_campaign, **kwargs).run(("high", "low"))

    assert forward.lifecycle_projection.to_dict() == reverse.lifecycle_projection.to_dict()
    assert forward.fouling_state_trajectory[-1]["duct_a"]["SiO"] == pytest.approx(
        reverse.fouling_state_trajectory[-1]["duct_a"]["SiO"]
    )


def test_import_isolation_from_runner_core_and_optimizer_boundaries() -> None:
    forbidden_modules = {
        "simulator.coating_lifespan",
        "simulator.optimize.fouling_lifecycle",
    }
    scanned = (
        Path("simulator/runner.py"),
        Path("simulator/core.py"),
        Path("simulator/optimize/evaluate.py"),
        Path("simulator/optimize/objective.py"),
    )
    offenders: list[str] = []
    for path in scanned:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_modules:
                        offenders.append(f"{path}:{alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                offenders.append(f"{path}:{node.module}")

    assert offenders == []
