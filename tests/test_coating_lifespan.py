from __future__ import annotations

from collections.abc import Mapping
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
        "vapour_carrier_authority_by_species": {
            species: {
                "species_id": species,
                "pressure": {"kind": "value", "pa": 1.0},
                "flux": {"kind": "eligible"},
                "verdict_status": "authoritative",
                "certification_ceiling": "validated_point",
                "validation_status": "validated",
                "is_union_flux_eligible": True,
                "is_flux_active": True,
            }
        },
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


def _wall_pressure_refusal_notice(
    species: str = "Mg",
    *,
    segment: str = "hot_wall",
) -> dict[str, object]:
    return {
        "wall_saturation_pressure_refusals_by_species": {
            species: {
                segment: {
                    "status": "refused",
                    "reason": "wall_saturation_pressure_out_of_domain",
                    "output_status": "status_bearing",
                    "wall_temperature_K": 1800.0,
                    "wall_saturation_pressure_pa": None,
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


def test_merge_run_snapshot_preserves_refusal_without_deposit() -> None:
    run_export = FoulingTerminalSnapshot.from_trace(
        _trace({}, _wall_pressure_refusal_notice())
    )

    post_merge, per_run_net = merge_run_snapshot(None, run_export)

    assert dict(per_run_net) == {}
    authority = post_merge.wall_deposit_sticking_authority
    assert authority["authoritative_for_resinter"] is False
    assert authority["deposited_species"] == ()
    assert authority["wall_saturation_pressure_refused_species"] == ("Mg",)
    assert authority["status_bearing_refusal_count"] == 1


def test_merge_run_snapshot_preserves_carried_positive_and_new_refusal() -> None:
    carried = FoulingTerminalSnapshot.from_trace(
        _trace(
            {("hot_wall", "SiO"): 1.0},
            _alpha_notice("SiO", cited=True),
        )
    )
    run_export = FoulingTerminalSnapshot.from_trace(
        _trace({}, _wall_pressure_refusal_notice())
    )

    post_merge, _per_run_net = merge_run_snapshot(carried, run_export)

    assert post_merge.wall_deposit_by_segment_species_kg["hot_wall"][
        "SiO"
    ] == pytest.approx(1.0)
    authority = post_merge.wall_deposit_sticking_authority
    assert authority["authoritative_for_resinter"] is False
    assert authority["deposited_species"] == ("SiO",)
    assert authority["wall_saturation_pressure_refused_species"] == ("Mg",)
    assert "Mg" in authority["wall_saturation_pressure_refusals_by_species"]


def test_merge_run_snapshot_preserves_carried_refusal_before_later_deposit(
) -> None:
    carried = FoulingTerminalSnapshot.from_trace(
        _trace({}, _wall_pressure_refusal_notice())
    )
    run_export = FoulingTerminalSnapshot.from_trace(
        _trace(
            {("hot_wall", "SiO"): 1.0},
            _alpha_notice("SiO", cited=True),
        )
    )

    post_merge, _per_run_net = merge_run_snapshot(carried, run_export)

    authority = post_merge.wall_deposit_sticking_authority
    assert authority["authoritative_for_resinter"] is False
    assert authority["deposited_species"] == ("SiO",)
    assert authority["wall_saturation_pressure_refused_species"] == ("Mg",)
    assert "Mg" in authority["wall_saturation_pressure_refusals_by_species"]


def test_merge_run_snapshot_preserves_status_bearing_carrier_authority() -> None:
    notice = _alpha_notice("Fe", cited=True)
    carrier = notice["vapour_carrier_authority_by_species"]["Fe"]
    carrier.update({
        "verdict_status": "status_bearing_non_authoritative",
        "certification_ceiling": "never",
        "validation_status": "modeled-PENDING",
    })
    run_export = FoulingTerminalSnapshot.from_trace(
        _trace({("hot_wall", "Fe"): 0.25}, notice)
    )

    post_merge, _per_run_net = merge_run_snapshot(None, run_export)

    authority = post_merge.wall_deposit_sticking_authority
    assert authority["authoritative_for_resinter"] is False
    assert authority["non_authoritative_carrier_species"] == ("Fe",)
    assert authority["vapour_carrier_authority_by_species"]["Fe"] == carrier


def test_merge_cannot_overwrite_carried_status_bearing_carrier() -> None:
    carried_notice = _alpha_notice("Fe", cited=True)
    carried_carrier = carried_notice["vapour_carrier_authority_by_species"][
        "Fe"
    ]
    carried_carrier.update({
        "verdict_status": "status_bearing_non_authoritative",
        "certification_ceiling": "never",
        "validation_status": "modeled-PENDING",
    })
    carried = FoulingTerminalSnapshot.from_trace(
        _trace({("hot_wall", "Fe"): 0.25}, carried_notice)
    )
    run_export = FoulingTerminalSnapshot.from_trace(
        _trace({("hot_wall", "Fe"): 0.10}, _alpha_notice("Fe", cited=True))
    )

    post_merge, _per_run_net = merge_run_snapshot(carried, run_export)

    authority = post_merge.wall_deposit_sticking_authority
    assert authority["authoritative_for_resinter"] is False
    assert authority["non_authoritative_carrier_species"] == ("Fe",)
    assert authority["vapour_carrier_authority_by_species"]["Fe"] == (
        carried_carrier
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("first_temperature", [None, 1500.0])
def test_merge_equal_severity_carriers_preserves_source_extrapolation_notices(
    reverse, first_temperature,
) -> None:
    snapshots = []
    expected = []
    for temperature in (first_temperature, 500.0):
        authority = _alpha_notice("Mg")
        carrier = authority["vapour_carrier_authority_by_species"]["Mg"]
        carrier.update({
            "verdict_status": "status_bearing_non_authoritative",
            "certification_ceiling": "never",
            "validation_status": "modeled-PENDING",
        })
        if temperature is not None:
            notice = {
                "authority_level": "extrapolated", "temperature_K": temperature,
                "valid_range_K": [701.0, 1361.0], "reason": "outside source band",
            }
            carrier["extra"] = {"extrapolation_notice": notice}
            expected.append(notice)
        snapshots.append(FoulingTerminalSnapshot.from_trace(
            _trace({("hot_wall", "Mg"): 0.1}, authority)
        ))
    if reverse:
        snapshots.reverse()
    merged, _ = merge_run_snapshot(*snapshots)
    assert merged.wall_deposit_by_segment_species_kg["hot_wall"]["Mg"] == 0.2
    assert merged.wall_deposit_sticking_authority["authoritative_for_resinter"] is False
    extra = merged.wall_deposit_sticking_authority["vapour_carrier_authority_by_species"]["Mg"]["extra"]
    assert extra["extrapolation_notice"]["reason"] == "outside source band"
    assert extra["extrapolation_notice"]["valid_range_K"] == (701.0, 1361.0)
    temperatures = [notice["temperature_K"] for notice in expected]
    assert len(extra["extrapolation_notices"]) == 1
    group = extra["extrapolation_notices"][0]
    low = min(temperatures)
    high = max(temperatures)
    assert group["temperature_K"] == (low if low == high else {"min": low, "max": high})
    assert group["occurrences"] == len(temperatures)
    assert group["probe_ids_first"] == "evaporation"
    assert group["probe_ids_last"] == (
        "evaporation" if len(temperatures) == 1 else f"evaporation#{len(temperatures)}"
    )
    assert group["reason"] == "outside source band"
    assert group["valid_range_K"] == (701.0, 1361.0)
    assert group["authority_level"] == "extrapolated"
    repeated, _ = merge_run_snapshot(merged, snapshots[1])
    repeated_group = repeated.wall_deposit_sticking_authority[
        "vapour_carrier_authority_by_species"
    ]["Mg"]["extra"]["extrapolation_notices"][0]
    repeated_notice = snapshots[1].wall_deposit_sticking_authority[
        "vapour_carrier_authority_by_species"
    ]["Mg"].get("extra", {}).get("extrapolation_notice")
    added = 1 if isinstance(repeated_notice, Mapping) else 0
    assert repeated_group["occurrences"] == group["occurrences"] + added
    assert repeated_group["temperature_K"] == group["temperature_K"]
    assert repeated_group["reason"] == "outside source band"


@pytest.mark.parametrize("later_status", ("proven_zero", "refused"))
def test_merge_promotes_later_worse_typed_carrier(later_status: str) -> None:
    carried_notice = _alpha_notice("Fe", cited=True)
    carried_notice["vapour_carrier_authority_by_species"]["Fe"].update({
        "verdict_status": "status_bearing_non_authoritative",
        "certification_ceiling": "never",
        "validation_status": "modeled-PENDING",
    })
    later_notice = _alpha_notice("Fe", cited=True)
    later_carrier = later_notice["vapour_carrier_authority_by_species"]["Fe"]
    later_carrier.update({
        "pressure": (
            {"kind": "refusal", "code": "test_refusal"}
            if later_status == "refused"
            else {"kind": "zero_by_physics", "evidence_ref": "test:zero"}
        ),
        "flux": (
            {"kind": "refusal", "code": "test_refusal"}
            if later_status == "refused"
            else {"kind": "eligible", "alpha_ref": "test:alpha"}
        ),
        "is_refused": later_status == "refused",
        "is_union_flux_eligible": False,
        "is_flux_active": False,
        "verdict_status": "status_bearing_non_authoritative",
        "certification_ceiling": "never",
        "validation_status": "modeled-PENDING",
    })
    carried = FoulingTerminalSnapshot.from_trace(
        _trace({("hot_wall", "Fe"): 0.25}, carried_notice)
    )
    run_export = FoulingTerminalSnapshot.from_trace(
        _trace({}, later_notice)
    )

    post_merge, _per_run_net = merge_run_snapshot(carried, run_export)

    authority = post_merge.wall_deposit_sticking_authority
    assert authority["authoritative_for_resinter"] is False
    assert authority[f"{later_status}_carrier_species"] == ("Fe",)
    assert authority["vapour_carrier_authority_by_species"]["Fe"] == (
        later_carrier
    )


def test_merge_cannot_overwrite_carried_out_of_domain_alpha() -> None:
    carried_notice = _alpha_notice("Fe", cited=True)
    carried_alpha = carried_notice["alpha_s_provenance_by_species"]["Fe"][
        "hot_wall"
    ]
    carried_alpha.update({
        "alpha_s_extrapolated": True,
        "alpha_s_domain_status": "out_of_domain",
        "alpha_s_authoritative_at_temperature": False,
        "output_status": "status_bearing",
    })
    carried = FoulingTerminalSnapshot.from_trace(
        _trace({("hot_wall", "Fe"): 0.25}, carried_notice)
    )
    run_export = FoulingTerminalSnapshot.from_trace(
        _trace({("hot_wall", "Fe"): 0.10}, _alpha_notice("Fe", cited=True))
    )

    post_merge, _per_run_net = merge_run_snapshot(carried, run_export)

    authority = post_merge.wall_deposit_sticking_authority
    assert authority["authoritative_for_resinter"] is False
    assert authority["out_of_domain_alpha_species"] == ("Fe",)
    assert authority["alpha_s_provenance_by_species"]["Fe"]["hot_wall"] == (
        carried_alpha
    )


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


def _transport_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "status": "extrapolated",
        "authority_level": "extrapolated",
        "model_domain": "Kn < 0.01 certified",
        "doctrine_category": 2,
        "affected_species": ("Na",),
        "detail": "continuum continuation",
        "knudsen_number": 0.02,
        "overhead_pressure_pa": 10.0,
        "commanded_pressure_pa": 20.0,
    }
    record.update(overrides)
    return record


def _record_transport(model: SimpleNamespace, record: dict[str, object], *, species: str = "Na") -> None:
    from simulator.wall_deposition import _record_wall_pressure_notice

    _record_wall_pressure_notice(
        model,
        "evaporation_transport_notices_by_species",
        species,
        "evaporation",
        record,
    )


def test_collapse_probed_notices_keeps_float_span_and_probe_range() -> None:
    from simulator.wall_deposition import collapse_probed_notices

    def notice(temperature_K: float, *, reason: str = "outside source band") -> dict:
        return {
            "authority_level": "extrapolated",
            "temperature_K": temperature_K,
            "valid_range_K": [701.0, 1361.0],
            "reason": reason,
        }

    collapsed = collapse_probed_notices([
        notice(500.0),
        notice(500.0),
        notice(900.0),
    ])
    assert len(collapsed) == 1
    group = collapsed[0]
    assert group["temperature_K"] == {"min": 500.0, "max": 900.0}
    assert group["occurrences"] == 3
    assert group["probe_ids_first"] == "evaporation"
    assert group["probe_ids_last"] == "evaporation#3"
    assert group["valid_range_K"] == [701.0, 1361.0]

    again = collapse_probed_notices(collapsed)
    assert again == collapsed

    widened = collapse_probed_notices([*collapsed, notice(400.0)])
    assert widened[0]["temperature_K"] == {"min": 400.0, "max": 900.0}
    assert widened[0]["occurrences"] == 4
    assert widened[0]["probe_ids_last"] == "evaporation#4"

    split = collapse_probed_notices([notice(500.0), notice(500.0, reason="other")])
    assert len(split) == 2
    assert split[0]["temperature_K"] == 500.0
    assert split[0]["occurrences"] == 1
    assert split[1]["reason"] == "other"
    assert split[1]["probe_ids_first"] == "evaporation#2"


def test_evaporation_transport_notice_collapses_probe_floats() -> None:
    model = SimpleNamespace(
        last_sticking_alpha_provenance_notice={},
        _evaporation_transport_notice_hour=1,
    )
    _record_transport(model, _transport_record())
    _record_transport(
        model,
        _transport_record(knudsen_number=0.08, overhead_pressure_pa=30.0),
    )
    stored = model.last_sticking_alpha_provenance_notice[
        "evaporation_transport_notices_by_species"
    ]["Na"]
    assert list(stored) == ["evaporation"]
    group = stored["evaporation"]
    assert group["occurrences"] == 2
    assert group["probe_ids_first"] == "evaporation"
    assert group["probe_ids_last"] == "evaporation#2"
    assert group["knudsen_number"] == {"min": 0.02, "max": 0.08}
    assert group["overhead_pressure_pa"] == {"min": 10.0, "max": 30.0}
    assert group["commanded_pressure_pa"] == 20.0
    assert group["authority_level"] == "extrapolated"
    assert group["affected_species"] == ("Na",)

    _record_transport(
        model,
        _transport_record(
            affected_species=("K",),
            knudsen_number=0.05,
            overhead_pressure_pa=12.0,
        ),
    )
    assert list(stored) == ["evaporation", "evaporation#3"]
    other = stored["evaporation#3"]
    assert other["occurrences"] == 1
    assert other["knudsen_number"] == 0.05
    assert other["probe_ids_first"] == "evaporation#3"
    assert other["affected_species"] == ("K",)


def test_equal_probe_floats_stay_a_scalar_and_a_new_hour_resets() -> None:
    model = SimpleNamespace(
        last_sticking_alpha_provenance_notice={},
        _evaporation_transport_notice_hour=1,
    )
    _record_transport(model, _transport_record())
    _record_transport(model, _transport_record(affected_species=["Na"]))
    stored = model.last_sticking_alpha_provenance_notice[
        "evaporation_transport_notices_by_species"
    ]["Na"]["evaporation"]
    assert stored["occurrences"] == 2
    assert stored["knudsen_number"] == 0.02
    assert stored["affected_species"] == ("Na",)

    model._evaporation_transport_notice_hour = 2
    _record_transport(model, _transport_record(knudsen_number=0.04))
    by_species = model.last_sticking_alpha_provenance_notice[
        "evaporation_transport_notices_by_species"
    ]
    assert list(by_species) == ["Na"]
    assert list(by_species["Na"]) == ["evaporation"]
    assert by_species["Na"]["evaporation"]["occurrences"] == 1
    assert by_species["Na"]["evaporation"]["knudsen_number"] == 0.04


def test_empty_notice_after_reset_restarts_probe_labels() -> None:
    model = SimpleNamespace(
        last_sticking_alpha_provenance_notice={},
        _evaporation_transport_notice_hour=1,
    )
    _record_transport(model, _transport_record())
    _record_transport(model, _transport_record(knudsen_number=0.09))
    model.last_sticking_alpha_provenance_notice = {}
    _record_transport(model, _transport_record(knudsen_number=0.03))
    stored = model.last_sticking_alpha_provenance_notice[
        "evaporation_transport_notices_by_species"
    ]["Na"]
    assert list(stored) == ["evaporation"]
    assert stored["evaporation"]["occurrences"] == 1
    assert stored["evaporation"]["probe_ids_last"] == "evaporation"


def test_wall_saturation_notices_stay_on_exact_record_identity() -> None:
    from simulator.wall_deposition import _record_wall_pressure_notice

    model = SimpleNamespace(
        last_sticking_alpha_provenance_notice={},
        _evaporation_transport_notice_hour=1,
    )
    first = {"status": "extrapolated", "temperature_K": 3200.0, "reason": "above range"}
    second = {"status": "extrapolated", "temperature_K": 3300.0, "reason": "above range"}
    _record_wall_pressure_notice(
        model, "wall_saturation_pressure_extrapolations_by_species",
        "Fe", "stage", first,
    )
    _record_wall_pressure_notice(
        model, "wall_saturation_pressure_extrapolations_by_species",
        "Fe", "stage", dict(first),
    )
    _record_wall_pressure_notice(
        model, "wall_saturation_pressure_extrapolations_by_species",
        "Fe", "stage", second,
    )
    stored = model.last_sticking_alpha_provenance_notice[
        "wall_saturation_pressure_extrapolations_by_species"
    ]["Fe"]
    assert list(stored) == ["stage", "stage#2"]
    assert "occurrences" not in stored["stage"]
    assert stored["stage"]["temperature_K"] == 3200.0
    assert stored["stage#2"]["temperature_K"] == 3300.0


def test_coating_merge_widens_collapsed_transport_ranges() -> None:
    def authority(species: str, record: dict[str, object], extra: dict | None = None) -> dict:
        by_probe = {"evaporation": record}
        if extra:
            by_probe.update(extra)
        return {
            "evaporation_transport_notices_by_species": {species: by_probe},
        }

    carried = FoulingTerminalSnapshot.from_trace(_trace(
        {("hot_wall", "Al"): 0.2},
        authority("Al", {
            "status": "extrapolated",
            "authority_level": "extrapolated",
            "model_domain": "Kn < 0.01 certified",
            "doctrine_category": 2,
            "affected_species": ["Al"],
            "detail": "continuum continuation",
            "knudsen_number": {"min": 0.02, "max": 0.08},
            "overhead_pressure_pa": {"min": 10.0, "max": 30.0},
            "commanded_pressure_pa": 20.0,
            "occurrences": 4,
            "probe_ids_first": "evaporation",
            "probe_ids_last": "evaporation#4",
        }),
    ))
    run_export = FoulingTerminalSnapshot.from_trace(_trace(
        {("hot_wall", "Al"): 0.1},
        authority("Al", {
            "status": "extrapolated",
            "authority_level": "extrapolated",
            "model_domain": "Kn < 0.01 certified",
            "doctrine_category": 2,
            "affected_species": ["Al"],
            "detail": "continuum continuation",
            "knudsen_number": {"min": 0.01, "max": 0.05},
            "overhead_pressure_pa": {"min": 8.0, "max": 40.0},
            "commanded_pressure_pa": 20.0,
            "occurrences": 2,
            "probe_ids_first": "evaporation",
            "probe_ids_last": "evaporation#2",
        }, extra={
            "evaporation#3": {
                "status": "extrapolated",
                "authority_level": "extrapolated",
                "model_domain": "Kn < 0.01 certified",
                "doctrine_category": 2,
                "affected_species": ["K"],
                "detail": "continuum continuation",
                "knudsen_number": 0.04,
                "overhead_pressure_pa": 11.0,
                "commanded_pressure_pa": 20.0,
                "occurrences": 1,
                "probe_ids_first": "evaporation#3",
                "probe_ids_last": "evaporation#3",
            },
        }),
    ))
    post_merge, _per_run_net = merge_run_snapshot(carried, run_export)
    stored = post_merge.wall_deposit_sticking_authority[
        "evaporation_transport_notices_by_species"
    ]["Al"]
    assert set(stored) == {"evaporation", "evaporation#3"}
    group = stored["evaporation"]
    assert group["occurrences"] == 6
    assert group["knudsen_number"] == {"min": 0.01, "max": 0.08}
    assert group["overhead_pressure_pa"] == {"min": 8.0, "max": 40.0}
    assert group["commanded_pressure_pa"] == 20.0
    assert group["probe_ids_first"] == "evaporation"
    assert group["probe_ids_last"] == "evaporation#4"
    other = stored["evaporation#3"]
    assert other["occurrences"] == 1
    assert other["knudsen_number"] == 0.04
    assert other["affected_species"] == ("K",)
