"""Whole-store census for row analytical methods and experiment splitting."""

from __future__ import annotations

import gc
import json
from collections.abc import Mapping
from pathlib import Path

import simulator.battery.migrate as migration_module
from simulator.battery.migrate import Migrator, REPO_ROOT


SOSS_SOURCE = "sossi-2020-magma-ocean-redox"
AITHALA_SOURCE = "aithala-2026-magmatic-iron-redox-to-2100c"
AITHALA_XANES_RUN_IDS = {
    "aithala-2026-1800-30",
    "aithala-2026-1900-30",
    "aithala-2026-2000-10",
    "aithala-2026-2000-120",
    "aithala-2026-2000-30",
    "aithala-2026-2100-30",
    "aithala-2026-vf231",
    "aithala-2026-vf268",
    "aithala-2026-vf270",
    "aithala-2026-vf271",
    "aithala-2026-vf274",
}
AITHALA_DECLARED_EXPERIMENT_PREFIX = (
    "10.7185/geochemlet.2617::experiment::"
)
AITHALA_SPLIT_SUFFIX = "::method:xanes_spectroscopy"
CURRENT_EXPERIMENT_COUNT = 8211
LEGACY_EXPERIMENT_COUNT = 8234


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
    work_ids = {
        source_id: {
            work_id
            for work_id, work in result.works.items()
            if source_id in work.source_ids
        }
        for source_id in source_ids
    }
    return {
        source_id: {
            experiment_id
            for experiment_id, experiment in result.experiments.items()
            if experiment.work_id in source_work_ids
        }
        for source_id, source_work_ids in work_ids.items()
    }


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
        legacy_aithala_observation_ids = {
            observation.observation_id
            for observation in legacy.result.observations.values()
            if observation.source_id == AITHALA_SOURCE
        }

    del legacy
    gc.collect()

    current = migration_module.migrate(REPO_ROOT, write=False)
    current_ids = _experiment_ids_by_source(current, source_ids)
    current_total = len(current.experiments)
    current_aithala_observations = [
        observation
        for observation in current.observations.values()
        if observation.source_id == AITHALA_SOURCE
    ]
    current_aithala_observation_ids = {
        observation.observation_id
        for observation in current_aithala_observations
    }
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

    assert set(changes) == {SOSS_SOURCE, AITHALA_SOURCE}, changes
    assert changes[SOSS_SOURCE]["before"] == 24
    assert changes[SOSS_SOURCE]["after"] == 12
    assert len(changes[SOSS_SOURCE]["removed"]) == 12
    assert not changes[SOSS_SOURCE]["added"]

    aithala_change = changes[AITHALA_SOURCE]
    assert aithala_change["before"] == 84
    assert aithala_change["after"] == 73
    assert len(aithala_change["removed"]) == 11
    assert not aithala_change["added"]

    aithala_document = migration_module.load_yaml(source_paths[AITHALA_SOURCE])
    declared_runs = {
        str(experiment["experiment_id"])
        for experiment in aithala_document["experiments"]
    }
    analytical_methods_by_run: dict[str, set[str]] = {}
    for _formula, observation in migration_module.iter_extract_observations(
        aithala_document
    ):
        run_id = str(observation["experiment"])
        values = migration_module._plain_observation_values(observation)
        analytical_method = (
            migration_module._method_text_value(
                observation.get("analytical_method")
            )
            or migration_module._method_text_value(
                values.get("analytical_method")
            )
        )
        if analytical_method is not None:
            analytical_methods_by_run.setdefault(run_id, set()).add(
                analytical_method
            )

    aithala_parent_ids = {
        split_id.split("::declared:", 1)[1].removesuffix(
            AITHALA_SPLIT_SUFFIX
        )
        for split_id in aithala_change["removed"]
    }
    expected_aithala_parent_ids = {
        AITHALA_DECLARED_EXPERIMENT_PREFIX + run_id
        for run_id in AITHALA_XANES_RUN_IDS
    }
    assert aithala_parent_ids == expected_aithala_parent_ids
    assert AITHALA_XANES_RUN_IDS <= declared_runs
    assert all(
        {"XANES", "WDS EPMA"} <= analytical_methods_by_run[run_id]
        for run_id in AITHALA_XANES_RUN_IDS
    )

    aithala_work_ids = {
        work.work_id
        for work in current.works.values()
        if AITHALA_SOURCE in work.source_ids
    }
    aithala_experiment_ids = {
        experiment_id
        for experiment_id, experiment in current.experiments.items()
        if experiment.work_id in aithala_work_ids
    }
    assert expected_aithala_parent_ids <= aithala_experiment_ids
    assert len(current_aithala_observations) == 89
    assert current_aithala_observation_ids == legacy_aithala_observation_ids
    assert all(
        observation.experiment_id in aithala_experiment_ids
        for observation in current_aithala_observations
    )

    assert current_total == CURRENT_EXPERIMENT_COUNT
    assert legacy_store_total == LEGACY_EXPERIMENT_COUNT
