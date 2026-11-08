from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from simulator.battery.enums import RefusalReason
from simulator.battery.migrate import load_migrated_store, migrate
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree


def _extract_with_experiments(root: Path, experiments: list[dict]) -> Path:
    extract = deepcopy(FIXTURE_EXTRACT)
    extract["experiments"] = experiments
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
    )

    migrate(extract, write=True)
    _works, experiments, _observations = load_migrated_store(extract)
    experiment = experiments["10.1234/fixture::experiment::run-2"]

    from simulator.battery.migrate import to_plain

    assert to_plain(experiment.sample.final_mass_kg) == sample["final_mass_kg"]
    assert to_plain(experiment.sample.species_loss_fraction) == sample[
        "species_loss_fraction"
    ]
    assert to_plain(experiment.charge_sequence) == {
        "charge_id": "charge-a",
        "run_index": 2,
        "locator": {"table": "1", "page": 2},
    }
    assert to_plain(experiment.vaporization_statement) == _located_token(
        "congruent", table="2", page=3
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
