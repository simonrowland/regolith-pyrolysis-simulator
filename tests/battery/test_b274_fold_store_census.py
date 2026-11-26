"""Temporary census. Not a pin. Deleted after the remote receipt."""

from __future__ import annotations

from collections import Counter

from simulator.battery.migrate import load_migrated_store, migrate
from simulator.battery.store_validation import (
    _ALLOWED_HARD_ISSUE_PATH_SUFFIXES,
    validate_migrated_store,
)

_SOURCES = (
    "hastie-bonnell-1980-nbsir-80-2169-molecular-chemistry",
    "floss-1998-corundum-hibonite-evaporation-residues",
    "fortin-2014-sulfur-saturation-natural-melts-thesis",
    "britt-2019-asteroid-simulants",
    "hendrix-2024-reactivity-reduced-simulants",
    "wood-wade-2013-trace-activities",
)


def _source_experiment_counts(observations) -> dict[str, int]:
    linked = {source_id: set() for source_id in _SOURCES}
    for observation in observations.values():
        source_id = observation.source_id
        if source_id in linked:
            linked[source_id].add(observation.experiment_id)
    return {source_id: len(experiment_ids) for source_id, experiment_ids in linked.items()}


def _method_split_count(experiment_ids) -> int:
    return sum("::method:" in experiment_id for experiment_id in experiment_ids)


def _forbidden(hard_issues) -> tuple[int, dict[str, int]]:
    by_kind: Counter[str] = Counter()
    forbidden = 0
    for issue in hard_issues:
        kind = issue.reason.value
        by_kind[kind] += 1
        allowed = kind == "conditional_field" and issue.path.endswith(
            _ALLOWED_HARD_ISSUE_PATH_SUFFIXES
        )
        if not allowed:
            forbidden += 1
    return forbidden, dict(sorted(by_kind.items()))


def test_b274_fold_store_census() -> None:
    """Fail with the census text. The remote profile refuses pytest -s."""

    committed_works, committed_experiments, committed_observations = load_migrated_store()
    fresh = migrate(write=False)
    try:
        committed_validation = validate_migrated_store()
    except Exception as exc:  # noqa: BLE001 — the receipt is the message
        committed_validation = f"{type(exc).__name__}: {exc}"
    fresh_hard = () if fresh.validation is None else fresh.validation.hard_issues
    fresh_forbidden, fresh_by_kind = _forbidden(fresh_hard)
    committed_by_source = _source_experiment_counts(committed_observations)
    fresh_by_source = _source_experiment_counts(fresh.observations)
    lines = [
        f"CENSUS committed_experiments {len(committed_experiments)}",
        f"CENSUS committed_observations {len(committed_observations)}",
        f"CENSUS committed_works {len(committed_works)}",
        f"CENSUS committed_method_ids {_method_split_count(committed_experiments)}",
        f"CENSUS fresh_experiments {len(fresh.experiments)}",
        f"CENSUS fresh_observations {len(fresh.observations)}",
        f"CENSUS fresh_works {len(fresh.works)}",
        f"CENSUS fresh_method_ids {_method_split_count(fresh.experiments)}",
        f"CENSUS fresh_hard {len(fresh_hard)}",
        f"CENSUS fresh_forbidden {fresh_forbidden}",
        f"CENSUS fresh_by_kind {fresh_by_kind}",
        f"CENSUS committed_store_validation {committed_validation}",
    ]
    for source_id in _SOURCES:
        lines.append(
            "CENSUS source "
            f"{source_id} committed {committed_by_source[source_id]} "
            f"fresh {fresh_by_source[source_id]}"
        )
    raise AssertionError("\n".join(lines))
