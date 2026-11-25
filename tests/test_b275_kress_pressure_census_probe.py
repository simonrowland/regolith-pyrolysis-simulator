from __future__ import annotations

import pytest

from simulator.accounting.exceptions import UnparseableCompositionComponent
from simulator.battery import score
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.enums import Engine, Quantity
from simulator.battery.score import load_score_context


def test_print_kress_dp001_pressure_value_moves() -> None:
    context = load_score_context()
    moved_rows: list[tuple[str, str, str, float, str]] = []
    unparseable_rows: list[tuple[str, str]] = []

    for observation in context.observations.values():
        identity = observation.identity
        if not isinstance(identity, Identity):
            continue
        quantity = quantity_token(identity)
        if quantity is not Quantity.FE3_FE2_RATIO and not (
            quantity is Quantity.VALENCE_RATIO
            and identity.species is not None
            and identity.species.formula == "Fe"
        ):
            continue

        experiment = context.experiments.get(observation.experiment_id)
        bench = (
            None
            if experiment is None or experiment.bench_id is None
            else context.benches.get(experiment.bench_id)
        )
        try:
            material = score._fe_redox_material(observation, identity, experiment)
        except UnparseableCompositionComponent:
            unparseable_rows.append(
                (observation.source_id, observation.observation_id)
            )
            continue
        if not isinstance(material, score._FeRedoxMaterial):
            continue
        pressure_bar, _pressure_state, notice, invalid = (
            score._resolve_score_total_pressure(
                identity,
                quantity,
                observation=observation,
                experiment=experiment,
                bench=bench,
                engine=Engine.INTERNAL_ANALYTICAL,
            )
        )
        if (
            invalid is not None
            or pressure_bar is None
            or notice is None
            or notice.policy is None
            or notice.policy.id != "DP-001"
        ):
            continue
        moved_rows.append(
            (
                observation.source_id,
                observation.observation_id,
                quantity.value,
                pressure_bar,
                notice.reason,
            )
        )

    by_source: dict[str, list[str]] = {}
    for source_id, observation_id, quantity, _pressure, _reason in sorted(moved_rows):
        by_source.setdefault(source_id, []).append(
            f"{observation_id} ({quantity})"
        )
    rows = "\n".join(
        f"{source_id} | {', '.join(observation_ids)}"
        for source_id, observation_ids in sorted(by_source.items())
    )
    pytest.fail(
        "Kress pressure value-move census; former 1.0 bar rows now use DP-001 1.01325 bar:\n"
        f"count={len(moved_rows)} sources={len(by_source)}\n{rows}"
        f"\nunparseable_composition_rows={unparseable_rows!r}"
    )
