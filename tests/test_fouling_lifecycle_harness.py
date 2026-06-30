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
from simulator.runner import _wall_fouling_report


def _artifact(
    deposit,
    *,
    ledger=None,
    mass_balance_error_pct: float = 0.0,
    authority=None,
    campaigns_total=None,
    result_document=None,
    c4b_state=None,
) -> FoulingRunArtifact:
    trace = SimpleNamespace(
        wall_deposit_by_segment_species_kg=deposit,
        wall_deposit_sticking_authority=(
            {"authoritative_for_resinter": True} if authority is None else authority
        ),
        snapshots=(SimpleNamespace(mass_balance_error_pct=mass_balance_error_pct),),
    )
    return FoulingRunArtifact(
        trace=trace,
        simulator=SimpleNamespace(atom_ledger=ledger) if ledger is not None else None,
        snapshots=trace.snapshots,
        c4b_binding_substrate_state=c4b_state,
        campaigns_to_resinter_total=campaigns_total,
        result_document=result_document,
    )


def test_harness_runs_n_iterations_without_ledger_reuse_or_closure_breach() -> None:
    ledgers = [object(), object()]
    deposits = [
        {("duct_a", "SiO"): 0.1},
        {("duct_a", "SiO"): 0.3, ("duct_b", "Si"): 0.05},
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
    assert (
        result.run_records[1].per_run_net_deposit_by_segment_species_kg["duct_a"]["SiO"]
        == pytest.approx(0.2)
    )
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


def test_harness_namespaces_live_runner_total_parity_from_lifecycle_projection() -> None:
    runner_verdict = _wall_fouling_report({"SiO": 0.3})

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
        "value": runner_verdict["campaigns_to_resinter"],
        "authoritative_for_resinter": runner_verdict["authoritative_for_resinter"],
    }
    assert result.lifecycle_projection.to_dict()["service_life_campaigns"] is None
    assert result.lifecycle_projection.to_dict()["service_life_authoritative"] is False


def test_harness_derived_total_fails_closed_when_runner_authority_absent() -> None:
    def run_campaign(_index: int) -> FoulingRunArtifact:
        return _artifact(
            {("duct_a", "SiO"): 0.3},
            ledger=object(),
            authority={},
        )

    result = FoulingLifecycleHarness(
        run_campaign,
        segment_area_m2={"duct_a": 1.0},
        resinter_threshold_kg=None,
    ).run((0,))

    assert result.campaigns_to_resinter_total.to_dict() == {
        "value": "resinter_threshold_kg / 0.3",
        "authoritative_for_resinter": False,
    }


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


def test_worst_segment_projection_stays_separate_from_total_resinter_basis() -> None:
    def run_campaign(_index: int) -> FoulingRunArtifact:
        return _artifact(
            {("duct_a", "SiO"): 0.2, ("duct_b", "SiO"): 0.1},
            ledger=object(),
        )

    result = FoulingLifecycleHarness(
        run_campaign,
        segment_area_m2={"duct_a": 1.0, "duct_b": 1.0},
        rho_deposit_kg_m3=1000.0,
        thickness_limit_m=0.001,
        resinter_threshold_kg=1.0,
    ).run((0,))

    assert result.campaigns_to_resinter_total.value == pytest.approx(1.0 / 0.3)
    assert (
        result.lifecycle_projection.worst_segment_campaigns_provisional
        == pytest.approx(5.0)
    )
    assert result.campaigns_to_resinter_total.value != pytest.approx(
        result.lifecycle_projection.worst_segment_campaigns_provisional
    )


def test_cascade_knee_is_reported_as_provisional_shape_indicator() -> None:
    deposits = [
        {("duct_a", "SiO"): 0.1},
        {("duct_a", "SiO"): 0.2},
        {("duct_a", "SiO"): 0.5},
    ]

    def run_campaign(index: int) -> FoulingRunArtifact:
        return _artifact(deposits[index], ledger=object())

    result = FoulingLifecycleHarness(
        run_campaign,
        segment_area_m2={"duct_a": 1.0},
        rho_deposit_kg_m3=1000.0,
        thickness_limit_m=0.01,
    ).run((0, 1, 2))

    assert result.lifecycle_projection.cascade_knee_provisional == 3
    assert result.lifecycle_projection.service_life_authoritative is False


def test_harness_carries_explicit_c4b_deferred_seam_without_live_harvest() -> None:
    c4b_state = {"hot_wall": {"available_si_mol": [0.25]}}

    def run_campaign(_index: int) -> FoulingRunArtifact:
        return _artifact(
            {("duct_a", "SiO"): 0.1},
            ledger=object(),
            c4b_state=c4b_state,
        )

    result = FoulingLifecycleHarness(
        run_campaign,
        segment_area_m2={"duct_a": 1.0},
    ).run((0,))

    c4b_state["hot_wall"]["available_si_mol"].append(9.0)
    snapshot = result.run_records[0].snapshot
    assert tuple(
        snapshot.c4b_binding_substrate_state["hot_wall"]["available_si_mol"]
    ) == (0.25,)


def test_projection_result_is_deterministic_for_same_run_order() -> None:
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
    first = FoulingLifecycleHarness(run_campaign, **kwargs).run(("low", "high"))
    second = FoulingLifecycleHarness(run_campaign, **kwargs).run(("low", "high"))

    assert first.lifecycle_projection.to_dict() == second.lifecycle_projection.to_dict()
    assert first.fouling_state_trajectory[-1]["duct_a"]["SiO"] == pytest.approx(
        second.fouling_state_trajectory[-1]["duct_a"]["SiO"]
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


def test_harness_module_has_no_commit_batch_or_atomledger_dependency() -> None:
    tree = ast.parse(Path("simulator/optimize/fouling_lifecycle.py").read_text())
    calls = [
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "commit_batch"
    ]
    names = [
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "AtomLedger"
    ]

    assert calls == []
    assert names == []
