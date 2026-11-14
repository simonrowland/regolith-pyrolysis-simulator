from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import simulator.battery.validate as validate
from simulator.battery.enums import Quantity
from tests.battery import factories as F


def test_validation_indexes_reuse_store_sets_aliases_tables_and_lineage(
    monkeypatch,
) -> None:
    build_indexes = getattr(validate, "_build_validation_indexes", None)
    assert callable(build_indexes), "validation index builder is missing"

    work = F.work("overhead-index-work")
    experiment = F.tabulation_experiment(
        "overhead-index-exp", work.work_id
    )
    delta = F.observation(
        "overhead-index-delta",
        experiment.experiment_id,
        F.o2_identity(),
        Decimal("-0.1"),
    )
    log_identity = replace(delta.identity, quantity=Quantity.LOG10_KF)
    log_k = F.observation(
        "overhead-index-logk",
        experiment.experiment_id,
        log_identity,
        Decimal("0.2"),
    )
    works = {work.work_id: work}
    experiments = {experiment.experiment_id: experiment}
    observations = {row.observation_id: row for row in (delta, log_k)}

    indexes = build_indexes(works, observations, experiments)

    assert indexes.table_ids == {"nasa-cea-thermo"}
    assert indexes.asset_ids == {"pdf-1", "nasa-cea-thermo"}
    assert indexes.work_by_alias[work.work_id] is work
    assert indexes.work_by_alias["janaf-4th"] is work
    assert indexes.work_by_alias["pdf-1"] is work
    assert validate._table_payloads(
        delta, observations, indexes.printed_thermo_index
    ) == validate._table_payloads(delta, observations)

    lineage_walks = 0
    original_lineage = validate._observation_lineage

    def counted_lineage(*args, **kwargs):
        nonlocal lineage_walks
        lineage_walks += 1
        return original_lineage(*args, **kwargs)

    monkeypatch.setattr(validate, "_observation_lineage", counted_lineage)
    first = validate._resolve_coefficient_sources(
        (work.work_id,), observations, works, experiments, _indexes=indexes
    )
    first_walks = lineage_walks
    second = validate._resolve_coefficient_sources(
        (work.work_id,), observations, works, experiments, _indexes=indexes
    )

    assert first.complete and first.ids is not None
    assert first.ids == frozenset(
        {"nasa-cea-thermo", delta.observation_id, log_k.observation_id}
    )
    assert second == first
    assert first_walks > 0
    assert lineage_walks == first_walks
