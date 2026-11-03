"""One unparseable composition component refuses that row only."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from simulator.battery.enums import Engine, Rail, RefusalReason, ResidualStatus
from simulator.battery.records import State
from simulator.battery.score import ScoreContext, score_store
from tests.battery.factories import migrate_one_extract


def test_score_store_refuses_one_unparseable_component_and_scores_the_other_row():
    root = Path(__file__).resolve().parents[2]
    extract = root / "data/literature/extracts/borisov-2018-compilation.yaml"
    migrator = migrate_one_extract(root, extract)
    migrator.finalize()
    result = migrator.result
    good = result.observations[f"{extract.stem}::borisov_2018_table2_row_4"]
    composition = good.identity.composition.value
    bad_components = (("FeO1.5", composition.components[0][1]),) + tuple(
        composition.components[1:]
    )
    bad_id = f"{good.observation_id}::unparseable-feo1.5"
    bad = replace(
        good,
        observation_id=bad_id,
        identity=replace(
            good.identity,
            composition=State.of(replace(composition, components=bad_components)),
        ),
    )
    context = ScoreContext(
        result.works,
        result.experiments,
        {good.observation_id: good, bad_id: bad},
        origins={
            good.observation_id: extract.name,
            bad_id: extract.name,
        },
        benches=result.benches,
    )
    residuals, _candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.REDOX,
        include_diagnostics=False,
    )
    by_reference: dict[str, list] = {}
    for residual in residuals:
        by_reference.setdefault(residual.reference, []).append(residual)
    assert set(by_reference) == {good.observation_id, bad_id}

    good_residuals = by_reference[good.observation_id]
    assert good_residuals
    assert any(residual.numeric is not None for residual in good_residuals)
    assert all(
        residual.refusal is None
        or residual.refusal.detail.get("component") != "FeO1.5"
        for residual in good_residuals
    )

    bad_residuals = by_reference[bad_id]
    assert bad_residuals
    assert all(residual.status is ResidualStatus.REFUSED for residual in bad_residuals)
    assert all(residual.numeric is None for residual in bad_residuals)
    assert any(
        residual.refusal is not None
        and residual.refusal.reason is RefusalReason.INVALID_SOURCE
        and residual.refusal.detail.get("reason") == "unparseable_composition_component"
        and residual.refusal.detail.get("component") == "FeO1.5"
        for residual in bad_residuals
    )
