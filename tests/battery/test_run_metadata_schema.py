from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from simulator.battery.enums import RefusalReason
from simulator.battery.migrate import load_migrated_store, migrate, to_plain
from simulator.battery.records import Located, Locator, State, Value
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree


def _extract_with_experiments(
    root: Path,
    experiments: list[dict],
    *,
    observation_experiment: str | None = None,
    observation_equipment: dict | None = None,
) -> Path:
    extract = deepcopy(FIXTURE_EXTRACT)
    extract["experiments"] = experiments
    if observation_experiment is not None:
        observation = extract["species"]["Na"]["observations"][0]
        observation["experiment"] = observation_experiment
        if observation_equipment is not None:
            observation["equipment"] = observation_equipment
    return _write_min_tree(root, extract)


def _located_point(point: str, *, table: str, page: int) -> dict:
    return {
        "state": {
            "tag": "value",
            "value": {"kind": "point", "point": point},
        },
        "locator": {"table": table, "page": page},
    }


def _located_token(token: str, *, table: str, page: int) -> dict:
    return {
        "state": {"tag": "value", "value": token},
        "locator": {"table": table, "page": page},
    }


def _plain_located_point(point: str, *, table: str, page: int) -> dict:
    return to_plain(
        Located(
            State.of(Value.point_of(point)),
            locator=Locator(table=table, page=page),
        )
    )


def test_run_metadata_round_trips_from_extract_through_migrated_store(
    tmp_path: Path,
) -> None:
    sample = {
        "final_mass_kg": _located_point("0.0008", table="1", page=2),
        "species_loss_fraction": {
            "Na": _located_point("0.25", table="2", page=3),
        },
    }
    extract = _extract_with_experiments(
        tmp_path,
        [
            {
                "experiment_id": "run-2",
                "sample": sample,
                "charge_sequence": {
                    "charge_id": "charge-a",
                    "run_index": 2,
                    "locator": {"table": "1", "page": 2},
                },
                "vaporization_statement": _located_token(
                    "congruent", table="2", page=3
                ),
                "locator": {"table": "1", "page": 2},
            }
        ],
        observation_experiment="run-2",
    )

    migrate(extract, write=True)
    _works, experiments, _observations = load_migrated_store(extract)
    experiment = experiments["10.1234/fixture::experiment::run-2"]

    assert to_plain(experiment.sample.final_mass_kg) == _plain_located_point(
        "0.0008", table="1", page=2
    )
    assert to_plain(experiment.sample.species_loss_fraction) == {
        "Na": _plain_located_point("0.25", table="2", page=3),
    }
    assert to_plain(experiment.charge_sequence) == {
        "charge_id": "charge-a",
        "run_index": 2,
        "locator": {"table": "1", "page": 2},
    }
    assert to_plain(experiment.vaporization_statement) == _located_token(
        "congruent", table="2", page=3
    )


def test_equipment_sample_metadata_round_trips_with_locators(
    tmp_path: Path,
) -> None:
    extract = deepcopy(FIXTURE_EXTRACT)
    extract["species"]["Na"]["observations"][0]["equipment"] = {
        "sample": {
            "final_mass_kg": {
                "value": 0.0008,
                "locator": {"table": "1", "page": 2},
            },
            "species_loss_fraction": {
                "Na": {
                    "value": 0.25,
                    "locator": {"table": "2", "page": 3},
                },
            },
        },
    }
    root = _write_min_tree(tmp_path, extract)

    migrate(root, write=True)
    _works, experiments, observations = load_migrated_store(root)
    observation = next(iter(observations.values()))
    experiment = experiments[observation.experiment_id]

    final_mass = experiment.sample.final_mass_kg
    assert final_mass is not None and final_mass.state.is_value
    assert final_mass.state.value == Value.point_of("0.0008")
    assert final_mass.locator == Locator(table="1", page=2)
    species_loss = experiment.sample.species_loss_fraction
    assert species_loss is not None and species_loss["Na"].state.is_value
    assert species_loss["Na"].state.value == Value.point_of("0.25")
    assert species_loss["Na"].locator == Locator(table="2", page=3)


def test_top_level_equipment_sample_metadata_round_trips_with_locators(
    tmp_path: Path,
) -> None:
    extract = deepcopy(FIXTURE_EXTRACT)
    extract["species"]["Na"]["observations"][0]["equipment"] = {
        "final_mass_kg": {
            "value": 0.0008,
            "locator": {"table": "1", "page": 2},
        },
        "species_loss_fraction": {
            "value": {"Na": 0.25},
            "locator": {"table": "2", "page": 3},
        },
    }
    root = _write_min_tree(tmp_path, extract)

    migrate(root, write=True)
    _works, experiments, observations = load_migrated_store(root)
    observation = next(iter(observations.values()))
    experiment = experiments[observation.experiment_id]

    final_mass = experiment.sample.final_mass_kg
    assert final_mass is not None and final_mass.state.is_value
    assert final_mass.state.value == Value.point_of("0.0008")
    assert final_mass.locator == Locator(table="1", page=2)
    species_loss = experiment.sample.species_loss_fraction
    assert species_loss is not None and species_loss["Na"].state.is_value
    assert species_loss["Na"].state.value == Value.point_of("0.25")
    assert species_loss["Na"].locator == Locator(table="2", page=3)


def test_equipment_sample_metadata_conflicts_are_typed_validation_errors(
    tmp_path: Path,
) -> None:
    extract = deepcopy(FIXTURE_EXTRACT)
    extract["species"]["Na"]["observations"][0]["equipment"] = {
        "final_mass_kg": {
            "value": 0.0008,
            "locator": {"table": "1", "page": 2},
        },
        "species_loss_fraction": {
            "value": {"Na": 0.25},
            "locator": {"table": "2", "page": 3},
        },
        "sample": {
            "final_mass_kg": {
                "value": 0.0009,
                "locator": {"table": "3", "page": 4},
            },
            "species_loss_fraction": {
                "Na": {
                    "value": 0.5,
                    "locator": {"table": "4", "page": 5},
                },
            },
        },
    }

    result = migrate(_write_min_tree(tmp_path, extract), write=False)

    assert result.validation is not None
    issues = result.validation.issues
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.final_mass_kg")
        and "conflicting equipment values" in issue.detail
        for issue in issues
    )
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.species_loss_fraction[Na]")
        and "conflicting equipment values" in issue.detail
        for issue in issues
    )


def _registry_equipment_conflict_root(tmp_path: Path) -> Path:
    return _extract_with_experiments(
        tmp_path,
        [
            {
                "experiment_id": "run-2",
                "sample": {
                    "final_mass_kg": _located_point(
                        "0.0009", table="1", page=1
                    ),
                    "species_loss_fraction": {
                        "Na": _located_point("0.5", table="1", page=1),
                    },
                },
            }
        ],
        observation_experiment="run-2",
        observation_equipment={
            "final_mass_kg": {
                "value": 0.0008,
                "locator": {"table": "2", "page": 2},
            },
            "species_loss_fraction": {
                "value": {"Na": 0.25},
                "locator": {"table": "2", "page": 2},
            },
        },
    )


def test_registry_equipment_metadata_disagreements_are_typed_errors(
    tmp_path: Path,
) -> None:
    root = _registry_equipment_conflict_root(tmp_path)

    result = migrate(root, write=False)

    assert result.validation is not None
    issues = result.validation.issues
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.final_mass_kg")
        and "source_disagreement" in issue.detail
        for issue in issues
    )
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.species_loss_fraction[Na]")
        and "source_disagreement" in issue.detail
        for issue in issues
    )


def test_registry_equipment_species_loss_conflict_does_not_write_invalid_entry(
    tmp_path: Path,
) -> None:
    root = _registry_equipment_conflict_root(tmp_path)

    migrate(root, write=True)
    _works, experiments, _observations = load_migrated_store(root)
    experiment = experiments["10.1234/fixture::experiment::run-2"]

    assert experiment.sample.species_loss_fraction is None or (
        "Na" not in experiment.sample.species_loss_fraction
    )


def test_run_metadata_invalid_extract_values_are_typed_validation_errors(
    tmp_path: Path,
) -> None:
    experiments = [
        {
            "experiment_id": "zero-index",
            "charge_sequence": {"charge_id": "charge-a", "run_index": 0},
        },
        {
            "experiment_id": "empty-charge",
            "charge_sequence": {"charge_id": "", "run_index": 1},
        },
        {
            "experiment_id": "unknown-vaporization",
            "vaporization_statement": _located_token(
                "unstated", table="1", page=2
            ),
        },
        {
            "experiment_id": "unknown-loss-formula",
            "sample": {
                "species_loss_fraction": {
                    "Xx": _located_point("0.25", table="1", page=2),
                }
            },
        },
    ]
    root = _extract_with_experiments(tmp_path, experiments)

    result = migrate(root, write=False)

    issues = result.validation.issues
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("charge_sequence")
        and "run_index" in issue.detail
        for issue in issues
    )
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("charge_sequence")
        and "charge_id" in issue.detail
        for issue in issues
    )
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("vaporization_statement")
        for issue in issues
    )
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("species_loss_fraction[Xx]")
        for issue in issues
    )


def test_validate_corpus_reports_invalid_run_metadata_as_typed_issues(
    tmp_path: Path,
) -> None:
    from simulator.battery.records import ChargeSequence, Located, Locator, State
    from simulator.battery.validate import validate_corpus

    root = _extract_with_experiments(
        tmp_path,
        [{"experiment_id": "run-1", "sample": {}}],
    )
    result = migrate(root, write=False)
    experiment_id = "10.1234/fixture::experiment::run-1"
    experiment = result.experiments[experiment_id]
    invalid_sequence = object.__new__(ChargeSequence)
    object.__setattr__(invalid_sequence, "charge_id", " ")
    object.__setattr__(invalid_sequence, "run_index", 0)
    object.__setattr__(invalid_sequence, "locator", None)
    invalid_experiment = replace(
        experiment,
        charge_sequence=invalid_sequence,
        vaporization_statement=Located(
            State.of("unstated"), locator=Locator(table="1")
        ),
    )
    experiments = dict(result.experiments)
    experiments[experiment_id] = invalid_experiment

    report = validate_corpus(result.works, experiments, result.observations)

    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path == f"experiment[{experiment_id}].charge_sequence.run_index"
        for issue in report.issues
    )
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path == f"experiment[{experiment_id}].charge_sequence.charge_id"
        for issue in report.issues
    )
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("vaporization_statement.state.value")
        for issue in report.issues
    )


def test_charge_sequence_constructor_rejects_empty_id_and_nonpositive_index() -> None:
    from simulator.battery.records import ChargeSequence

    with pytest.raises(ValueError, match="charge_id"):
        ChargeSequence(charge_id="", run_index=1, locator=None)
    with pytest.raises(ValueError, match="run_index"):
        ChargeSequence(charge_id="charge-a", run_index=0, locator=None)


def test_extract_without_run_metadata_keeps_legacy_store_bytes(
    tmp_path: Path,
) -> None:
    root = _extract_with_experiments(
        tmp_path,
        [
            {
                "experiment_id": "run-1",
                "sample": {},
                "locator": {"table": "1", "row": "1"},
            }
        ],
    )

    migrate(root, write=True)

    work_yaml = root / "data" / "literature" / "works" / "10.1234_fixture.yaml"
    assert hashlib.sha256(work_yaml.read_bytes()).hexdigest() == (
        "4f9553f197a2b2397e71bdee20e264b4061d21463c493179e3e65ac424e027e8"
    )
