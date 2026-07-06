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
    FoulingProjectionError,
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


def _alpha_notice(species: str, *, cited: bool = True) -> dict[str, object]:
    return {
        "alpha_s_provenance_by_species": {
            species: {
                "hot_wall": {
                    "segment": "hot_wall",
                    "species": species,
                    "alpha_s": 0.02 if cited else 1.0,
                    "citation_status": "CITED" if cited else "UNCERTIFIED",
                    "status": "sourced" if cited else "proxy",
                    "output_status": (
                        "sourced_with_surface_proxy"
                        if cited
                        else "status_bearing"
                    ),
                }
            }
        }
    }


def test_snapshot_is_frozen_and_deep_copied_after_export() -> None:
    deposit = {("duct_a", "SiO"): 0.25}
    authority = {"deposited_species": ["SiO"], "authoritative_for_resinter": True}
    c4b_state = {"hot_wall": {"available_sio2_mol": [1.0]}}

    snapshot = FoulingTerminalSnapshot.from_trace(
        _trace(deposit, authority),
        threshold_params={"thickness_limit_m": None},
        c4b_binding_substrate_state=c4b_state,
    )

    deposit[("duct_a", "SiO")] = 9.0
    authority["deposited_species"].append("Na")
    c4b_state["hot_wall"]["available_sio2_mol"].append(2.0)

    assert snapshot.wall_deposit_by_segment_species_kg["duct_a"]["SiO"] == pytest.approx(0.25)
    assert tuple(snapshot.wall_deposit_sticking_authority["deposited_species"]) == ("SiO",)
    assert tuple(
        snapshot.c4b_binding_substrate_state["hot_wall"]["available_sio2_mol"]
    ) == (1.0,)
    with pytest.raises(FrozenInstanceError):
        snapshot.grounding_status = "mutated"  # type: ignore[misc]
    with pytest.raises(TypeError):
        snapshot.wall_deposit_by_segment_species_kg["duct_a"]["SiO"] = 1.0  # type: ignore[index]


def test_from_trace_requires_existing_wall_deposit_export() -> None:
    with pytest.raises(FoulingProjectionError, match="wall_deposit_by_segment_species_kg"):
        FoulingTerminalSnapshot.from_trace(SimpleNamespace())


def test_independent_run_exports_accumulate_by_default() -> None:
    first = FoulingTerminalSnapshot.from_trace(_trace({("duct_a", "SiO"): 0.10}))
    second = FoulingTerminalSnapshot.from_trace(_trace({("duct_a", "SiO"): 0.20}))
    third = FoulingTerminalSnapshot.from_trace(_trace({("duct_a", "SiO"): 0.15}))

    merged = merge_snapshot_sequence((first, second, third))

    assert merged.per_run_net_deposit_by_segment_species_kg[0]["duct_a"]["SiO"] == pytest.approx(0.10)
    assert merged.per_run_net_deposit_by_segment_species_kg[1]["duct_a"]["SiO"] == pytest.approx(0.20)
    assert merged.per_run_net_deposit_by_segment_species_kg[2]["duct_a"]["SiO"] == pytest.approx(0.15)
    assert merged.trajectory[0].wall_deposit_by_segment_species_kg["duct_a"]["SiO"] == pytest.approx(0.10)
    assert merged.trajectory[1].wall_deposit_by_segment_species_kg["duct_a"]["SiO"] == pytest.approx(0.30)
    assert merged.trajectory[2].wall_deposit_by_segment_species_kg["duct_a"]["SiO"] == pytest.approx(0.45)


def test_default_merge_treats_terminal_export_as_per_run_net() -> None:
    carried = FoulingTerminalSnapshot.from_trace(_trace({("duct_a", "SiO"): 1.0}))
    independent_terminal = FoulingTerminalSnapshot.from_trace(_trace({("duct_a", "SiO"): 0.25}))

    post_merge, per_run_net = merge_run_snapshot(carried, independent_terminal)

    assert per_run_net["duct_a"]["SiO"] == pytest.approx(0.25)
    assert post_merge.wall_deposit_by_segment_species_kg["duct_a"]["SiO"] == pytest.approx(1.25)


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


def test_merge_run_snapshot_rederives_authority_for_cumulative_species() -> None:
    carried = FoulingTerminalSnapshot.from_trace(
        _trace({("hot_wall", "SiO"): 1.0}, _alpha_notice("SiO", cited=True))
    )
    run_export = FoulingTerminalSnapshot.from_trace(
        _trace(
            {("hot_wall", "Na"): 0.25},
            {
                "deposited_species": ["Na"],
                "authoritative_for_resinter": True,
                "authoritative_for_coating": True,
            },
        )
    )

    post_merge, _per_run_net = merge_run_snapshot(carried, run_export)

    authority = post_merge.wall_deposit_sticking_authority
    assert authority is not None
    assert authority["authoritative_for_resinter"] is False
    assert authority["deposited_species"] == ("Na", "SiO")


def test_limiter_stack_provisional_verdict_and_threshold_parametric_motion() -> None:
    snapshots = merge_snapshot_sequence((
        FoulingTerminalSnapshot.from_trace(_trace({("duct_a", "SiO"): 0.10})),
        FoulingTerminalSnapshot.from_trace(_trace({("duct_a", "SiO"): 0.20})),
        FoulingTerminalSnapshot.from_trace(_trace({("duct_a", "SiO"): 0.30})),
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
    assert placeholder.service_life_campaigns == pytest.approx(2.0)
    assert placeholder.worst_segment_campaigns_provisional == pytest.approx(1.25)
    assert placeholder.service_life_authoritative is False
    assert placeholder.grounding_status == GROUNDING_PROVISIONAL
    assert moved.service_life_campaigns == pytest.approx(3.0)


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
