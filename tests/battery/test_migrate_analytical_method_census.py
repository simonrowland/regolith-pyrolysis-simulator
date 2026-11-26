"""Whole-store census for row analytical methods and experiment splitting."""

from __future__ import annotations

import gc
import json
from collections.abc import Mapping
from pathlib import Path

import simulator.battery.migrate as migration_module
from simulator.battery.migrate import Migrator, REPO_ROOT


SOSS_SOURCE = "sossi-2020-magma-ocean-redox"
ASSEMBLY_EXPERIMENT_COUNT = 8222


def _extracts_with_analytical_methods() -> dict[str, Path]:
    sources = {}
    for path in migration_module.discover_extracts(
        migration_module.EXTRACTS_DIR
    ):
        document = migration_module.load_yaml(path)
        if not isinstance(document, Mapping):
            continue
        source_id = str(document.get("source_id") or path.stem)
        has_analytical_method = any(
            migration_module._method_text_value(
                observation.get("analytical_method")
            )
            is not None
            or migration_module._method_text_value(
                migration_module._plain_observation_values(observation).get(
                    "analytical_method"
                )
            )
            is not None
            for _formula, observation in migration_module.iter_extract_observations(
                document
            )
        )
        if has_analytical_method:
            sources[source_id] = path
    return sources


def _experiment_ids_by_source(
    result, source_ids: set[str]
) -> dict[str, set[str]]:
    ids = {source_id: set() for source_id in source_ids}
    for observation in result.observations.values():
        if observation.source_id in ids:
            ids[observation.source_id].add(observation.experiment_id)
    return ids


def test_analytical_method_split_census_across_the_store(monkeypatch) -> None:
    source_paths = _extracts_with_analytical_methods()
    source_ids = set(source_paths)
    assert SOSS_SOURCE in source_ids

    method_claim_fields = migration_module._method_claim_fields

    def restore_legacy_assay_split(observation, values, **_kwargs):
        return method_claim_fields(
            observation, values, include_analytical_method=True
        )

    with monkeypatch.context() as mutation:
        mutation.setattr(
            migration_module,
            "_method_claim_fields",
            restore_legacy_assay_split,
        )
        legacy = Migrator(REPO_ROOT)
        for source_id in sorted(source_paths):
            legacy._migrate_extract(source_paths[source_id])
        legacy.finalize()
        legacy_ids = _experiment_ids_by_source(legacy.result, source_ids)

    del legacy
    gc.collect()

    current = migration_module.migrate(REPO_ROOT, write=False)
    current_ids = _experiment_ids_by_source(current, source_ids)
    current_total = len(current.experiments)
    changes = {
        source_id: {
            "before": len(legacy_ids[source_id]),
            "after": len(current_ids[source_id]),
            "removed": sorted(legacy_ids[source_id] - current_ids[source_id]),
            "added": sorted(current_ids[source_id] - legacy_ids[source_id]),
        }
        for source_id in sorted(source_ids)
        if legacy_ids[source_id] != current_ids[source_id]
    }
    legacy_store_total = current_total + sum(
        len(legacy_ids[source_id]) - len(current_ids[source_id])
        for source_id in source_ids
    )
    print(
        "ANALYTICAL_METHOD_CENSUS="
        + json.dumps(
            {
                "candidate_sources": len(source_ids),
                "changes": changes,
                "legacy_store_total": legacy_store_total,
                "current_store_total": current_total,
            },
            sort_keys=True,
        )
    )

    assert set(changes) == {SOSS_SOURCE}, changes
    assert changes[SOSS_SOURCE]["before"] == 24
    assert changes[SOSS_SOURCE]["after"] == 12
    assert len(changes[SOSS_SOURCE]["removed"]) == 12
    assert not changes[SOSS_SOURCE]["added"]
    assert current_total == ASSEMBLY_EXPERIMENT_COUNT
    assert legacy_store_total == ASSEMBLY_EXPERIMENT_COUNT + 12
