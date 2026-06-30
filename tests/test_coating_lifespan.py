from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
import ast

import pytest

from simulator.coating_lifespan import (
    GROUNDING_PROVISIONAL,
    GROUNDING_UNGROUNDED,
    THICKNESS_PROXY_LIMITER,
    FoulingTerminalSnapshot,
    campaigns_to_resinter_total,
    merge_run_snapshot,
    merge_snapshot_sequence,
    project_lifecycle,
)


def _trace(deposit, authority=None):
    return SimpleNamespace(
        wall_deposit_by_segment_species_kg=deposit,
        wall_deposit_sticking_authority=authority or {},
    )


def test_snapshot_is_frozen_and_deep_copied_after_export() -> None:
    deposit = {("duct_a", "SiO"): 0.25}
    authority = {"deposited_species": ["SiO"], "authoritative_for_resinter": True}

    snapshot = FoulingTerminalSnapshot.from_trace(
        _trace(deposit, authority),
        threshold_params={"thickness_limit_m": None},
    )

    deposit[("duct_a", "SiO")] = 9.0
    authority["deposited_species"].append("Na")

    assert snapshot.wall_deposit_by_segment_species_kg["duct_a"]["SiO"] == pytest.approx(0.25)
    assert tuple(snapshot.wall_deposit_sticking_authority["deposited_species"]) == ("SiO",)
    with pytest.raises(FrozenInstanceError):
        snapshot.grounding_status = "mutated"  # type: ignore[misc]
    with pytest.raises(TypeError):
        snapshot.wall_deposit_by_segment_species_kg["duct_a"]["SiO"] = 1.0  # type: ignore[index]


def test_incremental_merge_uses_phase_a_net_export_not_gross_rederived() -> None:
    first = FoulingTerminalSnapshot.from_trace(_trace({("duct_a", "SiO"): 0.7}))
    second = FoulingTerminalSnapshot.from_trace(
        _trace({("duct_a", "SiO"): 0.4, ("duct_b", "Na"): 0.2})
    )

    merged = merge_snapshot_sequence((first, second))

    assert merged.per_run_net_deposit_by_segment_species_kg[0]["duct_a"]["SiO"] == pytest.approx(0.7)
    assert merged.per_run_net_deposit_by_segment_species_kg[1]["duct_a"]["SiO"] == pytest.approx(0.4)
    assert merged.trajectory[-1].wall_deposit_by_segment_species_kg["duct_a"]["SiO"] == pytest.approx(1.1)
    assert merged.trajectory[-1].wall_deposit_by_segment_species_kg["duct_b"]["Na"] == pytest.approx(0.2)


def test_seeded_export_mode_diffs_terminal_export_from_carried_projection() -> None:
    carried = FoulingTerminalSnapshot.from_trace(_trace({("duct_a", "SiO"): 1.0}))
    seeded_terminal = FoulingTerminalSnapshot.from_trace(_trace({("duct_a", "SiO"): 1.25}))

    post_merge, per_run_net = merge_run_snapshot(
        carried,
        seeded_terminal,
        export_includes_carried=True,
    )

    assert per_run_net["duct_a"]["SiO"] == pytest.approx(0.25)
    assert post_merge.wall_deposit_by_segment_species_kg["duct_a"]["SiO"] == pytest.approx(1.25)


def test_limiter_stack_provisional_verdict_and_threshold_parametric_motion() -> None:
    snapshots = merge_snapshot_sequence((
        FoulingTerminalSnapshot.from_trace(_trace({("duct_a", "SiO"): 0.10})),
        FoulingTerminalSnapshot.from_trace(_trace({("duct_a", "SiO"): 0.10})),
        FoulingTerminalSnapshot.from_trace(_trace({("duct_a", "SiO"): 0.10})),
    )).trajectory

    ungrounded = project_lifecycle(
        snapshots,
        segment_area_m2={"duct_a": 1.0},
        rho_deposit_kg_m3=None,
        thickness_limit_m=None,
    )
    assert ungrounded.service_life_campaigns is None
    assert ungrounded.service_life_authoritative is False
    assert ungrounded.grounding_status == GROUNDING_UNGROUNDED

    placeholder = project_lifecycle(
        snapshots,
        segment_area_m2={"duct_a": 1.0},
        rho_deposit_kg_m3=1000.0,
        thickness_limit_m=0.00025,
    )
    moved = project_lifecycle(
        snapshots,
        segment_area_m2={"duct_a": 1.0},
        rho_deposit_kg_m3=1000.0,
        thickness_limit_m=0.00050,
    )

    assert placeholder.end_condition_stack == (THICKNESS_PROXY_LIMITER,)
    assert placeholder.limiter_fired == THICKNESS_PROXY_LIMITER
    assert placeholder.service_life_campaigns == pytest.approx(3.0)
    assert placeholder.worst_segment_campaigns_provisional == pytest.approx(2.5)
    assert placeholder.service_life_authoritative is False
    assert placeholder.grounding_status == GROUNDING_PROVISIONAL
    assert moved.service_life_campaigns == pytest.approx(5.0)


def test_campaigns_to_resinter_total_mirrors_runner_null_threshold_string_basis() -> None:
    total = campaigns_to_resinter_total(
        {"duct_a": {"SiO": 0.25}, "duct_b": {"Si": 0.05}},
        resinter_threshold_kg=None,
        authoritative_for_resinter=True,
    )

    assert total.value == "resinter_threshold_kg / 0.3"
    assert total.authoritative_for_resinter is True


def test_c4b_binding_state_is_not_inferred_from_elemental_alkali_deposits() -> None:
    snapshot = FoulingTerminalSnapshot.from_trace(
        _trace({("duct_a", "Na"): 0.1, ("duct_a", "K"): 0.2})
    )

    assert snapshot.c4b_binding_substrate_state is None


def test_overlay_module_has_no_commit_batch_or_atomledger_dependency() -> None:
    tree = ast.parse(Path("simulator/coating_lifespan.py").read_text())
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
