from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from simulator.battery.enums import AdmissionStatus, EvidenceClass, RefusalReason
from simulator.battery.migrate import (
    experiment_from_plain,
    iter_observation_store_paths,
    load_yaml,
    observation_from_plain,
)
from simulator.battery.records import Experiment, Observation
from simulator.battery.validity import (
    comparison_method_cell_constant_cancels,
    run_validity_gates,
)
from tests.battery import factories as F


@pytest.mark.parametrize(
    "pairing_kind",
    (None, "not_printed", "not_reported", "unknown", "same_cell"),
)
def test_comparison_pairing_cancellation_golden_pin(pairing_kind: str | None) -> None:
    provenance = {
        "comparison_method": {"kind": "comparison_ratio"},
        "common_knudsen_cell_constant": {"cancels": True},
        "melt_reference_pairing": {"kind": pairing_kind},
    }

    assert comparison_method_cell_constant_cancels(provenance) is True


@pytest.mark.parametrize("comparison_kind", ("ratio", "comparison_ratio"))
def test_comparison_ratio_kind_cancellation_golden_pin(
    comparison_kind: str,
) -> None:
    provenance = {
        "comparison_method": {"kind": comparison_kind},
        "common_knudsen_cell_constant": {"cancels": True},
        "melt_reference_pairing": {"kind": "same_cell"},
    }

    assert comparison_method_cell_constant_cancels(provenance) is True


_VALIDITY_BUCKET_COUNT = 8


def _validity_observation_paths(root: Path) -> list[Path]:
    literature = root / "data" / "literature"
    return sorted(
        (
            path
            for directory in (
                literature / "extracts-v2",
                literature / "observations-v2",
            )
            for path in iter_observation_store_paths(directory)
        ),
        key=lambda path: path.relative_to(literature).as_posix(),
    )


def _validity_observation_buckets(root: Path) -> tuple[tuple[Path, ...], ...]:
    """Assign sorted observation shards to deterministic size-balanced buckets."""

    buckets: list[list[Path]] = [[] for _ in range(_VALIDITY_BUCKET_COUNT)]
    sizes = [0] * _VALIDITY_BUCKET_COUNT
    for path in _validity_observation_paths(root):
        bucket = min(
            range(_VALIDITY_BUCKET_COUNT), key=lambda index: (sizes[index], index)
        )
        buckets[bucket].append(path)
        sizes[bucket] += path.stat().st_size
    return tuple(tuple(bucket) for bucket in buckets)


def _validity_context_for_paths(
    root: Path, paths: tuple[Path, ...]
) -> tuple[dict[str, Experiment], dict[str, Observation]]:
    literature = root / "data" / "literature"
    experiments: dict[str, Experiment] = {}
    for path in sorted((literature / "works").glob("*.yaml")):
        if path.name == "ALIASES.yaml":
            continue
        document = load_yaml(path)
        if isinstance(document, Mapping):
            for raw_experiment in document.get("experiments") or ():
                experiment = experiment_from_plain(raw_experiment)
                experiments[experiment.experiment_id] = experiment

    observations: dict[str, Observation] = {}
    for path in paths:
        document = load_yaml(path)
        if not isinstance(document, Mapping):
            continue
        for raw_observation in document.get("observations") or ():
            observation = observation_from_plain(raw_observation)
            observations[observation.observation_id] = observation
    return experiments, observations


def test_kems_gate_treats_missing_point_conditions_as_empty() -> None:
    experiment = F.kems_experiment(
        orifice_area=None, clausing=None, kn=None, calibrated=False
    )
    identity = F.psat_identity("Na")
    observation = F.observation(
        "missing-point-conditions",
        experiment.experiment_id,
        identity,
        "1",
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.ADMITTED,
    )
    missing = replace(observation, point_conditions=None)
    empty = replace(observation, point_conditions={})

    missing_outcome = run_validity_gates(experiment, missing)
    empty_outcome = run_validity_gates(experiment, empty)

    assert missing_outcome == empty_outcome
    assert missing_outcome.passed is False
    assert missing_outcome.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED


def test_kems_apparatus_gates_apply_to_measured_rows() -> None:
    experiment = F.kems_experiment(
        experiment_id="kems-missing-apparatus",
        orifice_area=None,
        clausing=None,
        kn=None,
        calibrated=False,
    )
    observation = F.observation(
        "measured-row",
        experiment.experiment_id,
        F.psat_identity("Na"),
        "1",
        evidence=EvidenceClass.MEASURED_DIRECT,
    )

    outcome = run_validity_gates(experiment, observation)

    assert outcome.passed is False
    assert outcome.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED
    assert any(
        check.name == "in_cell_partial_pressure_sum" and not check.passed
        for check in outcome.checks
    )
    assert any(
        check.name == "kems_calibration" and not check.passed
        for check in outcome.checks
    )


def test_kems_apparatus_gates_do_not_apply_to_model_derived_rows() -> None:
    experiment = F.kems_experiment(
        experiment_id="kems-missing-apparatus",
        orifice_area=None,
        clausing=None,
        kn=None,
        calibrated=False,
    )
    observation = F.observation(
        "model-derived-row",
        experiment.experiment_id,
        F.psat_identity("Na"),
        "1",
        evidence=EvidenceClass.MODEL_DERIVED,
    )

    outcome = run_validity_gates(experiment, observation)

    assert outcome.passed is True
    assert any(
        check.name == "apparatus_applicability"
        and check.passed
        and check.detail["reason"]
        == "apparatus gate not applicable: non-measured evidence"
        for check in outcome.checks
    )
    assert not any(
        check.name in {"in_cell_partial_pressure_sum", "kems_calibration"}
        for check in outcome.checks
    )


def test_validity_observation_buckets_partition_store() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = _validity_observation_paths(root)
    buckets = _validity_observation_buckets(root)
    flattened = [path for bucket in buckets for path in bucket]

    assert len(buckets) == _VALIDITY_BUCKET_COUNT
    assert sorted(flattened) == paths
    assert len(flattened) == len(set(flattened))


@pytest.mark.parametrize("bucket_index", range(_VALIDITY_BUCKET_COUNT))
def test_full_store_validity_gates_do_not_raise(bucket_index: int) -> None:
    root = Path(__file__).resolve().parents[2]
    paths = _validity_observation_buckets(root)[bucket_index]
    experiments, observations = _validity_context_for_paths(root, paths)

    failures: list[str] = []
    for observation in observations.values():
        try:
            experiment = experiments[observation.experiment_id]
            run_validity_gates(experiment, observation)
        except Exception as exc:
            failures.append(f"{observation.observation_id}: {exc}")
    assert not failures, "\n".join(failures)
