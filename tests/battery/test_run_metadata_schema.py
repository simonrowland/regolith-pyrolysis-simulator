from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from simulator.battery.enums import RefusalReason
from simulator.battery.migrate import load_migrated_store, migrate, to_plain
from simulator.battery.records import (
    RUN_METADATA_FIELD_HOMES,
    Located,
    Locator,
    State,
    Value,
)
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree
from tools.validate_literature_extracts import validate_extract_document


_RUN_METADATA_VALUE_HOMES = tuple(
    (field, home)
    for field, (shape, homes) in RUN_METADATA_FIELD_HOMES.items()
    if shape in {"located_value", "species_located_values"}
    for home in homes
)
_RUN_METADATA_SPECIES_LOSS_HOMES = tuple(
    home for field, home in _RUN_METADATA_VALUE_HOMES if field == "species_loss_fraction"
)
_SAMPLE_MASS_HOMES = (
    "experiments[].sample.mass_kg",
    "experiments.*.sample.mass_kg",
    "experiments[].charge.mass_kg",
    "experiments.*.charge.mass_kg",
    "species.*.observations[].equipment.mass_kg",
    "species.*.observations[].equipment.sample.value.mass_kg",
)
_RAW_VALUE_UNSET = object()

_RUN_METADATA_VALUE_SHAPES = (
    ("scalar", True),
    ("point-wrapper", True),
    ("located-state", True),
    ("malformed-point", False),
    ("unknown-kind", False),
    ("nonnumeric-scalar", False),
)


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


def _record_located_point(point: str, *, table: str, page: int) -> Located:
    return Located(
        State.of(Value.point_of(point)),
        locator=Locator(table=table, page=page),
    )


def _run_metadata_value_shape(shape: str, point: str) -> object:
    if shape == "empty-species-map":
        return {}
    if shape == "scalar":
        return point
    if shape == "point-wrapper":
        return {"kind": "point", "point": point}
    if shape == "located-state":
        return _located_point(point, table="shape", page=7)
    if shape == "malformed-point":
        return {"kind": "point"}
    if shape == "unknown-kind":
        return {"kind": "unsupported", "point": point}
    if shape == "nonnumeric-scalar":
        return "not-numeric"
    raise AssertionError(f"unexpected run metadata shape: {shape}")


def _run_metadata_shape_extract(
    root: Path,
    field: str,
    home: str,
    value_shape: str,
    *,
    raw_value: object = _RAW_VALUE_UNSET,
) -> tuple[Path, dict]:
    extract = deepcopy(FIXTURE_EXTRACT)
    point = "0.0008" if field == "final_mass_kg" else "0.25"
    leaf = (
        _run_metadata_value_shape(value_shape, point)
        if raw_value is _RAW_VALUE_UNSET
        else raw_value
    )
    field_value = (
        {}
        if field == "species_loss_fraction" and value_shape == "empty-species-map"
        else {"Na": leaf}
        if field == "species_loss_fraction"
        else leaf
    )
    experiment = {"experiment_id": "run-1", "sample": {}}
    observation = extract["species"]["Na"]["observations"][0]
    observation["experiment"] = "run-1"

    if home.startswith("experiments[].") or home.startswith("experiments.*."):
        registry_field = (
            home.split(".", 2)[1]
            if home.startswith("experiments[].")
            else home.split(".", 2)[2].split(".", 1)[0]
        )
        experiment[registry_field] = {field: field_value}
        if home.startswith("experiments[]."):
            extract["experiments"] = [experiment]
        else:
            extract["experiments"] = {"run-1": experiment}
    else:
        extract["experiments"] = [experiment]
        locator = {"table": "equipment", "page": 1}
        if ".equipment.sample.value." in home:
            observation["equipment"] = {
                "sample": {
                    "value": {field: field_value},
                    "locator": locator,
                }
            }
        else:
            observation["equipment"] = {
                field: {"value": field_value, "locator": locator}
            }

    return _write_min_tree(root, extract), extract


def _run_metadata_shape_home_path(home: str) -> str:
    return (
        home.replace("experiments[]", "experiments[0]")
        .replace("experiments.*", "experiments.run-1")
        .replace("species.*", "species.Na")
        .replace("observations[]", "observations[0]")
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
            "value": {
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
            "locator": {"table": "1", "page": 2},
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
            "value": {
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
            "locator": {"table": "3", "page": 4},
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


def test_legacy_charge_alias_run_metadata_is_loaded_alongside_sample(
    tmp_path: Path,
) -> None:
    extract = _extract_with_experiments(
        tmp_path,
        [
            {
                "experiment_id": "run-2",
                "sample": {
                    "mass_kg": _located_point("0.001", table="1", page=1),
                },
                "charge": {
                    "final_mass_kg": _located_point(
                        "0.0008", table="2", page=2
                    ),
                },
            }
        ],
    )

    migrate(extract, write=True)
    _works, experiments, _observations = load_migrated_store(extract)
    experiment = experiments["10.1234/fixture::experiment::run-2"]

    assert experiment.sample.mass_kg is not None
    assert experiment.sample.mass_kg.state.value == Value.point_of("0.001")
    assert experiment.sample.final_mass_kg is not None
    assert experiment.sample.final_mass_kg.state.value == Value.point_of("0.0008")
    assert experiment.sample.final_mass_kg.locator == Locator(table="2", page=2)


def test_legacy_charge_alias_conflicts_are_typed_validation_errors(
    tmp_path: Path,
) -> None:
    extract = _extract_with_experiments(
        tmp_path,
        [
            {
                "experiment_id": "run-2",
                "sample": {
                    "final_mass_kg": _located_point(
                        "0.0009", table="1", page=1
                    ),
                },
                "charge": {
                    "final_mass_kg": _located_point(
                        "0.0008", table="2", page=2
                    ),
                },
            }
        ],
    )

    result = migrate(extract, write=False)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.final_mass_kg")
        and "source_disagreement" in issue.detail
        for issue in result.validation.issues
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
    extract = deepcopy(FIXTURE_EXTRACT)
    extract["experiments"] = experiments
    root = _write_min_tree(tmp_path, extract)

    extract_errors = validate_extract_document(extract)
    assert any(
        "experiments[0].charge_sequence" in error
        and "[run-metadata-shape]" in error
        for error in extract_errors
    ), extract_errors
    assert any(
        "experiments[2].vaporization_statement" in error
        and "[run-metadata-shape]" in error
        for error in extract_errors
    ), extract_errors

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


@pytest.mark.parametrize(
    ("field", "home"),
    _RUN_METADATA_VALUE_HOMES,
    ids=[f"{field}-{home}" for field, home in _RUN_METADATA_VALUE_HOMES],
)
@pytest.mark.parametrize(
    ("value_shape", "accepted"),
    _RUN_METADATA_VALUE_SHAPES,
    ids=[shape for shape, _accepted in _RUN_METADATA_VALUE_SHAPES],
)
def test_run_metadata_value_shapes_validate_and_round_trip_at_every_home(
    tmp_path: Path,
    field: str,
    home: str,
    value_shape: str,
    accepted: bool,
) -> None:
    root, extract = _run_metadata_shape_extract(
        tmp_path, field, home, value_shape
    )
    errors = validate_extract_document(extract)
    issue_suffix = (
        f"sample.{field}[Na]"
        if field == "species_loss_fraction"
        else f"sample.{field}"
    )
    point = "0.0008" if field == "final_mass_kg" else "0.25"
    raw_value = _run_metadata_value_shape(value_shape, point)

    if not accepted:
        assert any(
            _run_metadata_shape_home_path(home) in error
            and "[run-metadata-shape]" in error
            for error in errors
        ), errors
        result = migrate(root, write=False)
        assert result.validation is not None
        assert any(
            issue.reason is RefusalReason.INVALID_SOURCE
            and issue.path.endswith(issue_suffix)
            and repr(raw_value) in issue.detail
            for issue in result.validation.issues
        ), result.validation.issues
        return

    assert errors == []
    migrate(root, write=True)
    _works, experiments, _observations = load_migrated_store(root)
    experiment = experiments["10.1234/fixture::experiment::run-1"]
    expected_point = point
    if field == "final_mass_kg":
        located = experiment.sample.final_mass_kg
    else:
        assert experiment.sample.species_loss_fraction is not None
        located = experiment.sample.species_loss_fraction["Na"]
    assert located is not None and located.state.is_value
    assert located.state.value == Value.point_of(expected_point)

    if value_shape == "located-state":
        assert located.locator == Locator(table="shape", page=7)
    elif ".equipment." in home:
        assert located.locator == Locator(table="equipment", page=1)
    else:
        assert located.locator is None


@pytest.mark.parametrize("home", _RUN_METADATA_SPECIES_LOSS_HOMES)
def test_empty_species_loss_mapping_is_rejected_at_every_home(
    tmp_path: Path,
    home: str,
) -> None:
    root, extract = _run_metadata_shape_extract(
        tmp_path, "species_loss_fraction", home, "empty-species-map"
    )

    errors = validate_extract_document(extract)
    assert any(
        _run_metadata_shape_home_path(home) in error
        and "[run-metadata-shape]" in error
        for error in errors
    ), errors

    result = migrate(root, write=False)
    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.species_loss_fraction")
        and "{}" in issue.detail
        for issue in result.validation.issues
    ), result.validation.issues


_RANGE_INVALID_VALUES = {
    "mass_kg": (
        ("zero-point", "0"),
        ("negative-point", "-0.1"),
        ("zero-series-value", {"kind": "series", "series": [[1, "0"]]}),
        (
            "zero-interval-endpoint",
            {"kind": "interval", "interval_low": "0", "interval_high": "1"},
        ),
    ),
    "final_mass_kg": (
        ("negative-point", "-0.1"),
        ("negative-series-value", {"kind": "series", "series": [[1, "-0.1"]]}),
        (
            "negative-interval-endpoint",
            {"kind": "interval", "interval_low": "-0.1", "interval_high": "0.5"},
        ),
    ),
    "species_loss_fraction": (
        ("negative-point", "-0.1"),
        ("above-one-point", "1.1"),
        ("negative-series-value", {"kind": "series", "series": [[1, "-0.1"]]}),
        ("above-one-series-value", {"kind": "series", "series": [[1, "1.1"]]}),
        (
            "negative-interval-endpoint",
            {"kind": "interval", "interval_low": "-0.1", "interval_high": "0.5"},
        ),
        (
            "above-one-interval-endpoint",
            {"kind": "interval", "interval_low": "0.5", "interval_high": "1.1"},
        ),
    ),
}

_RANGE_HOMES = tuple(("mass_kg", home) for home in _SAMPLE_MASS_HOMES) + tuple(
    (field, home)
    for field in ("final_mass_kg", "species_loss_fraction")
    for home in RUN_METADATA_FIELD_HOMES[field][1]
)
_OUT_OF_RANGE_CASES = tuple(
    (field, home, label, raw_value)
    for field, home in _RANGE_HOMES
    for label, raw_value in _RANGE_INVALID_VALUES[field]
)


@pytest.mark.parametrize(
    ("field", "home", "case", "raw_value"),
    _OUT_OF_RANGE_CASES,
    ids=[f"{field}-{home}-{case}" for field, home, case, _ in _OUT_OF_RANGE_CASES],
)
def test_out_of_range_sample_values_are_typed_at_every_home(
    tmp_path: Path,
    field: str,
    home: str,
    case: str,
    raw_value: object,
) -> None:
    del case
    root, extract = _run_metadata_shape_extract(
        tmp_path, field, home, "scalar", raw_value=raw_value
    )

    errors = validate_extract_document(extract)
    assert any(
        _run_metadata_shape_home_path(home) in error
        and "[run-metadata-shape]" in error
        for error in errors
    ), errors

    result = migrate(root, write=False)
    assert result.validation is not None
    issue_suffix = (
        f"sample.{field}[Na]"
        if field == "species_loss_fraction"
        else f"sample.{field}"
    )
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith(issue_suffix)
        for issue in result.validation.issues
    ), result.validation.issues


_RANGE_BOUNDARIES = tuple(
    ("mass_kg", home, "1") for home in _SAMPLE_MASS_HOMES
) + tuple(
    ("final_mass_kg", home, "0")
    for home in RUN_METADATA_FIELD_HOMES["final_mass_kg"][1]
) + tuple(
    ("species_loss_fraction", home, value)
    for home in _RUN_METADATA_SPECIES_LOSS_HOMES
    for value in ("0", "1")
)


@pytest.mark.parametrize(
    ("field", "home", "value"),
    _RANGE_BOUNDARIES,
    ids=[f"{field}-{home}-{value}" for field, home, value in _RANGE_BOUNDARIES],
)
def test_physical_range_boundaries_are_accepted_at_every_home(
    tmp_path: Path,
    field: str,
    home: str,
    value: str,
) -> None:
    root, extract = _run_metadata_shape_extract(
        tmp_path, field, home, "scalar", raw_value=value
    )

    assert validate_extract_document(extract) == []
    migrate(root, write=True)
    _works, experiments, _observations = load_migrated_store(root)
    sample = experiments["10.1234/fixture::experiment::run-1"].sample
    if field == "species_loss_fraction":
        assert sample.species_loss_fraction is not None
        located = sample.species_loss_fraction["Na"]
    else:
        located = getattr(sample, field)
    assert located is not None and located.state.is_value
    assert located.state.value == Value.point_of(value)


def test_record_validation_rejects_out_of_range_sample_values(
    tmp_path: Path,
) -> None:
    from simulator.battery.validate import validate_corpus

    root = _extract_with_experiments(
        tmp_path,
        [{"experiment_id": "run-1", "sample": {}}],
    )
    result = migrate(root, write=False)
    experiment_id = "10.1234/fixture::experiment::run-1"
    experiment = result.experiments[experiment_id]
    invalid_sample = replace(
        experiment.sample,
        mass_kg=_record_located_point("0", table="1", page=2),
        final_mass_kg=_record_located_point("-0.1", table="1", page=2),
        species_loss_fraction={
            "Na": _record_located_point("1.1", table="2", page=3)
        },
    )
    invalid_experiment = replace(experiment, sample=invalid_sample)
    experiments = dict(result.experiments)
    experiments[experiment_id] = invalid_experiment

    report = validate_corpus(result.works, experiments, result.observations)

    for suffix in (
        "sample.mass_kg",
        "sample.final_mass_kg",
        "sample.species_loss_fraction[Na]",
    ):
        assert any(
            issue.reason is RefusalReason.INVALID_SOURCE
            and issue.path.endswith(suffix)
            for issue in report.issues
        ), report.issues
