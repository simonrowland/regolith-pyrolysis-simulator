from __future__ import annotations

import hashlib
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import RefusalReason, VaporizationMode
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
    if shape in {"point", "point-wrapper"}:
        return {"kind": "point", "point": point}
    if shape == "located-state":
        return _located_point(point, table="shape", page=7)
    if shape == "series":
        return {
            "kind": "series",
            "series": [["1", point], ["2", str(Decimal(point) * 2)]],
        }
    if shape == "interval":
        return {
            "kind": "interval",
            "interval_low": point,
            "interval_high": str(Decimal(point) * 2),
        }
    if shape == "bound":
        return {"kind": "bound", "bound_operator": ">", "bound_value": point}
    if shape == "inference-with-parameters":
        located_point = _located_point(point, table="unit-seed", page=7)
        located_point["inference"] = {
            "method": "fixture",
            "relation": "identity",
            "inputs": ["source value"],
            "output_unit": "fraction",
            "parameters": [
                [
                    "scale",
                    {
                        "state": {"tag": "value", "value": "1"},
                        "locator": {"table": "unit-parameter", "page": 8},
                    },
                ]
            ],
        }
        return located_point
    if shape == "wrapped-inference":
        return {
            "value": {"kind": "point", "point": point},
            "inference": {
                "method": "fixture",
                "relation": "identity",
                "inputs": ["source"],
                "output_unit": "g",
            },
        }
    if shape == "inner-inference":
        return {
            "value": {
                "state": {
                    "tag": "value",
                    "value": {"kind": "point", "point": point},
                },
                "inference": {
                    "method": "fixture",
                    "relation": "identity",
                    "inputs": ["source"],
                    "output_unit": "kg",
                },
            },
            "locator": {"table": "inner-inference", "page": 13},
        }
    if shape == "as-published-point":
        stored = Decimal(point)
        printed = stored * Decimal(1000)
        return {
            "state": {
                "tag": "value",
                "value": {"kind": "point", "point": str(stored)},
            },
            "as_published": {"value": str(printed), "units": "g"},
            "locator": {"table": "as-published", "page": 9},
        }
    if shape == "as-published-interval":
        low = Decimal(point)
        high = low * 2
        return {
            "state": {
                "tag": "value",
                "value": {
                    "kind": "interval",
                    "interval_low": str(low),
                    "interval_high": str(high),
                },
            },
            "as_published": {
                "low": str(low * 1000),
                "high": str(high * 1000),
                "low_units": "g",
                "high_units": "g",
            },
            "locator": {"table": "as-published-interval", "page": 10},
        }
    if shape == "nested-combination":
        stored = Decimal(point)
        printed = stored * Decimal(1000)
        located_point = _located_point(str(stored), table="nested-seed", page=11)
        located_point["as_published"] = {"value": str(printed), "units": "g"}
        located_point["inference"] = {
            "method": "fixture",
            "relation": "g_to_kg",
            "inputs": ["m_kg = m_g / 1000", "original_unit=g"],
            "output_unit": "kg",
            "parameters": [
                [
                    "printed_mass_g",
                    {
                        "state": {"tag": "value", "value": str(printed)},
                        "locator": {"table": "nested-parameter", "page": 12},
                    },
                ]
            ],
        }
        return located_point
    if shape == "misplaced-as-published":
        stored = Decimal(point)
        return {
            "state": {
                "tag": "value",
                "value": {
                    "kind": "point",
                    "point": str(stored),
                    "as_published": {
                        "value": str(stored * Decimal(1000)),
                        "units": "g",
                    },
                },
            },
        }
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
        elif value_shape in {"wrapped-inference", "inner-inference"}:
            observation["equipment"] = {field: field_value}
        elif isinstance(raw_value, Mapping) and any(
            "unit" in str(key).casefold() for key in raw_value
        ):
            observation["equipment"] = {
                field: field_value
                if "value" in raw_value
                else {"value": field_value, "locator": locator}
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


_EQUIPMENT_SAMPLE_MASS_HOMES = tuple(
    home for home in RUN_METADATA_FIELD_HOMES["mass_kg"][1] if ".equipment." in home
)
_EQUIPMENT_FINAL_MASS_HOMES = tuple(
    home
    for home in RUN_METADATA_FIELD_HOMES["final_mass_kg"][1]
    if ".equipment." in home
)
_MASS_UNIT_CASES = tuple(
    (field, home, units, expected)
    for field, homes in (
        ("mass_kg", _EQUIPMENT_SAMPLE_MASS_HOMES),
        ("final_mass_kg", _EQUIPMENT_FINAL_MASS_HOMES),
    )
    for home in homes
    for units, expected in (("g", "0.001"), ("kg", "1"))
)


@pytest.mark.parametrize(
    ("field", "home", "units", "expected"),
    _MASS_UNIT_CASES,
    ids=[
        f"{field}-{home}-{units}"
        for field, home, units, _expected in _MASS_UNIT_CASES
    ],
)
def test_equipment_sample_mass_units_convert_before_range_checks(
    tmp_path: Path,
    field: str,
    home: str,
    units: str,
    expected: str,
) -> None:
    unit_locator = {"table": "unit-source", "page": 4}
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        field,
        home,
        "scalar",
        raw_value={"value": "1", "units": units, "locator": unit_locator},
    )

    assert validate_extract_document(extract) == []
    migrate(root, write=True)
    _works, experiments, observations = load_migrated_store(root)
    experiment = experiments[next(iter(observations.values())).experiment_id]
    located = getattr(experiment.sample, field)

    assert located is not None and located.state.is_value
    assert located.state.value == Value.point_of(expected)
    assert located.locator == Locator(table="unit-source", page=4)


_NONFINITE_EQUIPMENT_MASSES = tuple(
    (home, value)
    for home in _EQUIPMENT_SAMPLE_MASS_HOMES
    for value in ("NaN", "Infinity", "-Infinity")
)


@pytest.mark.parametrize(
    ("home", "value"),
    _NONFINITE_EQUIPMENT_MASSES,
    ids=[f"{home}-{value}" for home, value in _NONFINITE_EQUIPMENT_MASSES],
)
def test_nonfinite_equipment_initial_masses_return_typed_migration_issues(
    tmp_path: Path,
    home: str,
    value: str,
) -> None:
    root, extract = _run_metadata_shape_extract(
        tmp_path, "mass_kg", home, "scalar", raw_value=value
    )

    assert any(
        _run_metadata_shape_home_path(home) in error
        and "[run-metadata-shape]" in error
        for error in validate_extract_document(extract)
    )
    result = migrate(root, write=False)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.mass_kg")
        for issue in result.validation.issues
    ), result.validation.issues


@pytest.mark.parametrize(
    "home",
    _EQUIPMENT_FINAL_MASS_HOMES,
    ids=_EQUIPMENT_FINAL_MASS_HOMES,
)
def test_unsupported_equipment_final_mass_units_are_typed_errors(
    tmp_path: Path,
    home: str,
) -> None:
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        "final_mass_kg",
        home,
        "scalar",
        raw_value={"value": "1", "units": "lb", "locator": {"page": 4}},
    )

    assert any(
        _run_metadata_shape_home_path(home) in error
        and "[run-metadata-shape]" in error
        for error in validate_extract_document(extract)
    )
    result = migrate(root, write=False)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.final_mass_kg")
        for issue in result.validation.issues
    ), result.validation.issues


_FINAL_MASS_UNIT_ANNOTATION_CASES = tuple(
    (home, unit_key)
    for home in _EQUIPMENT_FINAL_MASS_HOMES
    for unit_key in ("unit", "units")
)


@pytest.mark.parametrize(
    ("home", "unit_key"),
    _FINAL_MASS_UNIT_ANNOTATION_CASES,
    ids=[
        f"{home}-{unit_key}"
        for home, unit_key in _FINAL_MASS_UNIT_ANNOTATION_CASES
    ],
)
def test_equipment_final_mass_point_unit_annotations_convert_and_keep_locator(
    tmp_path: Path,
    home: str,
    unit_key: str,
) -> None:
    unit_locator = {"table": "point-unit-source", "page": 7}
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        "final_mass_kg",
        home,
        "scalar",
        raw_value={
            "kind": "point",
            "point": "0.5",
            unit_key: "g",
            "locator": unit_locator,
        },
    )

    assert validate_extract_document(extract) == []
    migrate(root, write=True)
    _works, experiments, observations = load_migrated_store(root)
    experiment = experiments[next(iter(observations.values())).experiment_id]
    located = experiment.sample.final_mass_kg

    assert located is not None and located.state.is_value
    assert located.state.value == Value.point_of("0.0005")
    assert located.locator == Locator(table="point-unit-source", page=7)


_UNKNOWN_EQUIPMENT_MASS_UNIT_KEYS = tuple(
    (home, unit_key)
    for home in _EQUIPMENT_FINAL_MASS_HOMES
    for unit_key in ("unit_code", "mass_unit")
)


@pytest.mark.parametrize(
    ("home", "unit_key"),
    _UNKNOWN_EQUIPMENT_MASS_UNIT_KEYS,
    ids=[
        f"{home}-{unit_key}"
        for home, unit_key in _UNKNOWN_EQUIPMENT_MASS_UNIT_KEYS
    ],
)
def test_unknown_equipment_mass_unit_keys_are_typed_errors(
    tmp_path: Path,
    home: str,
    unit_key: str,
) -> None:
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        "final_mass_kg",
        home,
        "scalar",
        raw_value={"kind": "point", "point": "0.5", unit_key: "g"},
    )

    assert any(
        _run_metadata_shape_home_path(home) in error
        and "[run-metadata-shape]" in error
        for error in validate_extract_document(extract)
    )
    result = migrate(root, write=False)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.final_mass_kg")
        for issue in result.validation.issues
    ), result.validation.issues


_MASS_ANNOTATION_CASES = tuple(
    (field, home, shape, unit_key)
    for field in ("mass_kg", "final_mass_kg")
    for home in RUN_METADATA_FIELD_HOMES[field][1]
    for shape, unit_key in (
        ("wrapper", "unit"),
        ("point-value", "units"),
        ("located-state-value", "unit"),
    )
)


def _mass_unit_annotation_payload(shape: str, unit_key: str) -> dict:
    unit_locator = {"table": f"r7-{shape}", "page": 9}
    point = {"kind": "point", "point": "0.5"}
    if shape == "wrapper":
        return {"value": point, unit_key: "g", "locator": unit_locator}
    if shape == "point-value":
        return {**point, unit_key: "g", "locator": unit_locator}
    if shape == "located-state-value":
        point[unit_key] = "g"
        return {
            "state": {"tag": "value", "value": point},
            "locator": unit_locator,
        }
    raise AssertionError(f"unexpected unit annotation shape: {shape}")


@pytest.mark.parametrize(
    ("field", "home", "shape", "unit_key"),
    _MASS_ANNOTATION_CASES,
    ids=[
        f"{field}-{home}-{shape}"
        for field, home, shape, _unit_key in _MASS_ANNOTATION_CASES
    ],
)
def test_mass_unit_annotations_normalize_at_every_home_and_value_layer(
    tmp_path: Path,
    field: str,
    home: str,
    shape: str,
    unit_key: str,
) -> None:
    unit_locator = Locator(table=f"r7-{shape}", page=9)
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        field,
        home,
        "scalar",
        raw_value=_mass_unit_annotation_payload(shape, unit_key),
    )

    assert validate_extract_document(extract) == []
    migrate(root, write=True)
    _works, experiments, observations = load_migrated_store(root)
    experiment = experiments[next(iter(observations.values())).experiment_id]
    located = getattr(experiment.sample, field)

    assert located is not None and located.state.is_value
    assert located.state.value == Value.point_of("0.0005")
    assert located.locator == unit_locator


def test_registry_initial_mass_point_unit_annotation_witness_converts_from_grams(
    tmp_path: Path,
) -> None:
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        "mass_kg",
        "experiments[].sample.mass_kg",
        "point-wrapper",
        raw_value={
            "kind": "point",
            "point": "1",
            "units": "g",
            "locator": {"page": 1},
        },
    )

    assert validate_extract_document(extract) == []
    migrate(root, write=True)
    _works, experiments, _observations = load_migrated_store(root)
    located = experiments["10.1234/fixture::experiment::run-1"].sample.mass_kg

    assert located is not None and located.state.is_value
    assert located.state.value == Value.point_of("0.001")
    assert located.locator == Locator(page=1)


def test_registry_initial_mass_unit_alias_is_typed_instead_of_ignored(
    tmp_path: Path,
) -> None:
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        "mass_kg",
        "experiments[].sample.mass_kg",
        "point-wrapper",
        raw_value={
            "kind": "point",
            "point": "1",
            "mass_unit": "g",
        },
    )

    assert any(
        "experiments[0].sample.mass_kg" in error
        and "[run-metadata-shape]" in error
        for error in validate_extract_document(extract)
    )
    result = migrate(root, write=False)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.mass_kg")
        for issue in result.validation.issues
    ), result.validation.issues


def test_registry_species_loss_fraction_unit_annotation_is_typed(
    tmp_path: Path,
) -> None:
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        "species_loss_fraction",
        "experiments[].sample.species_loss_fraction",
        "scalar",
        raw_value={
            "Na": {"kind": "point", "point": "0.25", "units": "g"},
        },
    )

    assert any(
        "experiments[0].sample.species_loss_fraction" in error
        and "[run-metadata-shape]" in error
        for error in validate_extract_document(extract)
    )
    result = migrate(root, write=False)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.species_loss_fraction[Na]")
        for issue in result.validation.issues
    ), result.validation.issues


_MASS_UNIT_ERROR_CASES = tuple(
    (field, home, error_case, annotations)
    for field in ("mass_kg", "final_mass_kg")
    for home in RUN_METADATA_FIELD_HOMES[field][1]
    for error_case, annotations in (
        ("unknown-unit", {"unit": "lb"}),
        ("duplicate-unit", {"unit": "g", "units": "g"}),
        ("conflicting-units", {"unit": "g", "units": "kg"}),
        ("unknown-unit-key", {"mass_unit": "g"}),
    )
)


@pytest.mark.parametrize(
    ("field", "home", "error_case", "annotations"),
    _MASS_UNIT_ERROR_CASES,
    ids=[
        f"{field}-{home}-{error_case}"
        for field, home, error_case, _annotations in _MASS_UNIT_ERROR_CASES
    ],
)
def test_invalid_mass_unit_annotations_are_typed_at_every_home(
    tmp_path: Path,
    field: str,
    home: str,
    error_case: str,
    annotations: dict,
) -> None:
    del error_case
    point = "1" if field == "mass_kg" else "0.5"
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        field,
        home,
        "scalar",
        raw_value={"kind": "point", "point": point, **annotations},
    )

    assert any(
        _run_metadata_shape_home_path(home) in error
        and "[run-metadata-shape]" in error
        for error in validate_extract_document(extract)
    )
    result = migrate(root, write=False)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith(f"sample.{field}")
        for issue in result.validation.issues
    ), result.validation.issues


_MASS_COMPOSITE_UNIT_CASES = tuple(
    (field, home, kind)
    for field in ("mass_kg", "final_mass_kg")
    for home in RUN_METADATA_FIELD_HOMES[field][1]
    for kind in ("series", "interval")
)


@pytest.mark.parametrize(
    ("field", "home", "kind"),
    _MASS_COMPOSITE_UNIT_CASES,
    ids=[
        f"{field}-{home}-{kind}"
        for field, home, kind in _MASS_COMPOSITE_UNIT_CASES
    ],
)
def test_mass_units_on_series_and_interval_values_are_typed_at_every_home(
    tmp_path: Path,
    field: str,
    home: str,
    kind: str,
) -> None:
    raw_value = (
        {"kind": kind, "series": [["1", "0.5"]], "unit": "g"}
        if kind == "series"
        else {
            "kind": kind,
            "interval_low": "0.5",
            "interval_high": "1",
            "units": "g",
        }
    )
    root, extract = _run_metadata_shape_extract(
        tmp_path, field, home, "scalar", raw_value=raw_value
    )

    assert any(
        _run_metadata_shape_home_path(home) in error
        and "[run-metadata-shape]" in error
        for error in validate_extract_document(extract)
    )
    result = migrate(root, write=False)

    assert result.validation is not None
    issue_suffix = f"sample.{field}"
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith(issue_suffix)
        for issue in result.validation.issues
    ), result.validation.issues


@pytest.mark.parametrize(
    "home", RUN_METADATA_FIELD_HOMES["species_loss_fraction"][1],
    ids=RUN_METADATA_FIELD_HOMES["species_loss_fraction"][1],
)
def test_species_loss_fraction_rejects_units_at_every_home(
    tmp_path: Path,
    home: str,
) -> None:
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        "species_loss_fraction",
        home,
        "scalar",
        raw_value={
            "Na": {
                "state": {
                    "tag": "value",
                    "value": {"kind": "point", "point": "0.25", "unit": "g"},
                },
                "locator": {"table": "r7-loss", "page": 3},
            },
        },
    )

    assert any(
        _run_metadata_shape_home_path(home) in error
        and "[run-metadata-shape]" in error
        for error in validate_extract_document(extract)
    )
    result = migrate(root, write=False)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.species_loss_fraction[Na]")
        for issue in result.validation.issues
    ), result.validation.issues


_UNITLESS_RUN_METADATA_CASES = tuple(
    (field, home)
    for field in ("charge_sequence", "vaporization_statement")
    for home in RUN_METADATA_FIELD_HOMES[field][1]
)


def _unitless_run_metadata_extract(
    root: Path,
    field: str,
    home: str,
    payload: dict,
) -> tuple[Path, dict]:
    extract = deepcopy(FIXTURE_EXTRACT)
    experiment = {"experiment_id": "run-1", field: payload}
    observation = extract["species"]["Na"]["observations"][0]
    observation["experiment"] = "run-1"
    if home.startswith("experiments[]."):
        extract["experiments"] = [experiment]
    else:
        extract["experiments"] = {"run-1": experiment}
    return _write_min_tree(root, extract), extract


@pytest.mark.parametrize(
    ("field", "home"),
    _UNITLESS_RUN_METADATA_CASES,
    ids=[f"{field}-{home}" for field, home in _UNITLESS_RUN_METADATA_CASES],
)
def test_run_metadata_without_unit_semantics_rejects_unit_annotations(
    tmp_path: Path,
    field: str,
    home: str,
) -> None:
    payload = (
        {"charge_id": "charge-a", "run_index": 1, "unit": "g"}
        if field == "charge_sequence"
        else {
            "state": {"tag": "value", "value": "congruent"},
            "unit": "g",
            "locator": {"table": "r7-vapor", "page": 4},
        }
    )
    root, extract = _unitless_run_metadata_extract(
        tmp_path, field, home, payload
    )

    assert any(
        _run_metadata_shape_home_path(home) in error
        and "[run-metadata-shape]" in error
        for error in validate_extract_document(extract)
    )
    result = migrate(root, write=False)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith(field)
        for issue in result.validation.issues
    ), result.validation.issues


_RUN_METADATA_NESTED_UNIT_ANNOTATION_CASES = tuple(
    (field, home, location, unit_key)
    for field in ("mass_kg", "final_mass_kg", "species_loss_fraction")
    for home in RUN_METADATA_FIELD_HOMES[field][1]
    for location, unit_key in (
        ("locator", "unit"),
        ("locator", "unit_code"),
        ("inference", "unit"),
    )
)


@pytest.mark.parametrize(
    ("field", "home", "location", "unit_key"),
    _RUN_METADATA_NESTED_UNIT_ANNOTATION_CASES,
    ids=[
        f"{field}-{home}-{location}-{unit_key}"
        for field, home, location, unit_key in _RUN_METADATA_NESTED_UNIT_ANNOTATION_CASES
    ],
)
def test_nested_unit_annotations_are_typed_at_every_sample_home(
    tmp_path: Path,
    field: str,
    home: str,
    location: str,
    unit_key: str,
) -> None:
    point = "1" if field == "mass_kg" else "0.5"
    raw_value = {"kind": "point", "point": point}
    if location == "locator":
        raw_value["locator"] = {"page": 1, unit_key: "g"}
    else:
        raw_value["inference"] = {
            "method": "fixture",
            "relation": "identity",
            "inputs": ["source"],
            unit_key: "g",
        }
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
    issue_suffix = (
        f"sample.{field}[Na]"
        if field == "species_loss_fraction"
        else f"sample.{field}"
    )
    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith(issue_suffix)
        for issue in result.validation.issues
    ), result.validation.issues


_UNITLESS_LOCATOR_UNIT_CASES = tuple(
    (field, home)
    for field in ("charge_sequence", "vaporization_statement")
    for home in RUN_METADATA_FIELD_HOMES[field][1]
)


@pytest.mark.parametrize(
    ("field", "home"),
    _UNITLESS_LOCATOR_UNIT_CASES,
    ids=[f"{field}-{home}" for field, home in _UNITLESS_LOCATOR_UNIT_CASES],
)
def test_locator_unit_annotations_are_typed_at_unitless_registry_homes(
    tmp_path: Path,
    field: str,
    home: str,
) -> None:
    payload = (
        {
            "charge_id": "charge-a",
            "run_index": 1,
            "locator": {"page": 1, "unit": "g"},
        }
        if field == "charge_sequence"
        else {
            "state": {"tag": "value", "value": "congruent"},
            "locator": {"table": "r8-vapor", "page": 1, "unit": "g"},
        }
    )
    root, extract = _unitless_run_metadata_extract(tmp_path, field, home, payload)

    errors = validate_extract_document(extract)
    assert any(
        _run_metadata_shape_home_path(home) in error
        and "[run-metadata-shape]" in error
        for error in errors
    ), errors

    result = migrate(root, write=True)
    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith(field)
        for issue in result.validation.issues
    ), result.validation.issues
    _works, experiments, _observations = load_migrated_store(root)
    experiment = experiments["10.1234/fixture::experiment::run-1"]
    assert getattr(experiment, field) is None


def test_nested_locator_unit_refusal_survives_store_write_and_load(
    tmp_path: Path,
) -> None:
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        "mass_kg",
        "experiments[].sample.mass_kg",
        "scalar",
        raw_value={
            "kind": "point",
            "point": "1",
            "locator": {"table": "r8-mass", "page": 1, "unit": "g"},
        },
    )

    assert any(
        "experiments[0].sample.mass_kg" in error
        and "[run-metadata-shape]" in error
        for error in validate_extract_document(extract)
    )
    result = migrate(root, write=True)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.mass_kg")
        for issue in result.validation.issues
    ), result.validation.issues
    _works, experiments, _observations = load_migrated_store(root)
    experiment = experiments["10.1234/fixture::experiment::run-1"]
    assert experiment.sample.mass_kg is None


def test_valid_inference_output_unit_is_preserved(
    tmp_path: Path,
) -> None:
    root, extract = _run_metadata_shape_extract(
        tmp_path,
        "mass_kg",
        "experiments[].sample.mass_kg",
        "scalar",
        raw_value={
            "kind": "point",
            "point": "1",
            "inference": {
                "method": "fixture",
                "relation": "identity",
                "inputs": ["source"],
                "output_unit": "kg",
            },
        },
    )

    assert validate_extract_document(extract) == []
    migrate(root, write=True)
    _works, experiments, _observations = load_migrated_store(root)
    experiment = experiments["10.1234/fixture::experiment::run-1"]

    assert experiment.sample.mass_kg is not None
    assert experiment.sample.mass_kg.inference is not None
    assert experiment.sample.mass_kg.inference.output_unit == "kg"


_BUSEMANN_PRECONVERTED_MASS_ROWS = (
    ("cold_bokkeveld_csse", "9.30", "0.0093"),
    ("grosnaja_csse", "5.68", "0.00568"),
    ("lance_csse", "6.78", "0.00678"),
    ("isna_csse", "9.87", "0.00987"),
    ("chainpur_csse", "6.05", "0.00605"),
    ("dimmitt_csse", "25.84", "0.02584"),
)


def test_busemann_preconverted_mass_rows_keep_their_units_and_store_bytes(
    tmp_path: Path,
) -> None:
    experiments = []
    for experiment_id, printed_grams, stored_kg in _BUSEMANN_PRECONVERTED_MASS_ROWS:
        note = (
            f"Derived unit conversion only: printed {printed_grams} g × 10^-3 kg/g; "
            "no physical quantity was recomputed."
        )
        experiments.append(
            {
                "experiment_id": experiment_id,
                "sample": {
                    "mass_kg": {
                        "state": {
                            "tag": "value",
                            "value": {"kind": "point", "point": stored_kg},
                        },
                        "as_published": {"value": printed_grams, "units": "g"},
                        "locator": {
                            "page": 3,
                            "published_page": 951,
                            "pdf_page_index": 2,
                            "table": "2",
                            "record": "sample mass column",
                            "note": note,
                        },
                    }
                },
            }
        )
    root = _extract_with_experiments(
        tmp_path,
        experiments,
        observation_experiment=_BUSEMANN_PRECONVERTED_MASS_ROWS[0][0],
    )

    assert validate_extract_document(
        yaml.safe_load((root / "data/literature/extracts/fixture-source.yaml").read_text())
    ) == []
    migrate(root, write=True)
    _works, loaded_experiments, _observations = load_migrated_store(root)

    for experiment_id, printed_grams, stored_kg in _BUSEMANN_PRECONVERTED_MASS_ROWS:
        mass = loaded_experiments[
            f"10.1234/fixture::experiment::{experiment_id}"
        ].sample.mass_kg
        assert mass is not None and mass.state.is_value
        assert mass.state.value == Value.point_of(stored_kg)
        assert mass.locator is not None
        assert mass.inference is not None
        assert mass.inference.relation == "g_to_kg"
        assert mass.inference.inputs == (
            "m_kg = m_g / 1000",
            "original_unit=g",
        )
        assert mass.inference.output_unit == "kg"
        assert mass.inference.parameters[0][1].state.value == Decimal(printed_grams)

    work_yaml = root / "data/literature/works/10.1234_fixture.yaml"
    first_bytes = work_yaml.read_bytes()
    migrate(root, write=True)
    assert work_yaml.read_bytes() == first_bytes


_RUN_METADATA_UNIT_KEY_BASES = (
    "unit",
    "units",
    "unit_code",
    "mass_unit",
    "output_unit",
    "low_units",
    "high_units",
)
_RUN_METADATA_UNIT_KEYS = tuple(
    spelling
    for base in _RUN_METADATA_UNIT_KEY_BASES
    for spelling in dict.fromkeys((base, base.upper(), base.title()))
)


def _run_metadata_field_payload(extract: dict, field: str, home: str) -> object:
    if home.startswith("experiments[]."):
        experiment = extract["experiments"][0]
    elif home.startswith("experiments.*."):
        experiment = extract["experiments"]["run-1"]
    elif home.startswith("species.*.observations[].equipment."):
        equipment = extract["species"]["Na"]["observations"][0]["equipment"]
        if ".equipment.sample.value." in home:
            return equipment["sample"]["value"][field]
        return equipment[field]
    else:
        raise AssertionError(f"unexpected run-metadata home: {home}")

    if ".sample." in home:
        return experiment["sample"][field]
    if ".charge." in home:
        return experiment["charge"][field]
    return experiment[field]


def _run_metadata_mapping_paths(
    value: object,
    path: tuple[str | int, ...] = (),
) -> tuple[tuple[str | int, ...], ...]:
    paths: list[tuple[str | int, ...]] = []
    if isinstance(value, Mapping):
        paths.append(path)
        for key, child in value.items():
            paths.extend(_run_metadata_mapping_paths(child, (*path, str(key))))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            paths.extend(_run_metadata_mapping_paths(child, (*path, index)))
    return tuple(paths)


def _run_metadata_mapping_at(
    value: object,
    path: tuple[str | int, ...],
) -> dict:
    current = value
    for part in path:
        current = current[part]
    assert isinstance(current, dict)
    return current


def _loaded_run_metadata_value(experiment, field: str):
    if field in {"charge_sequence", "vaporization_statement"}:
        return getattr(experiment, field)
    return getattr(experiment.sample, field)


def _loaded_run_metadata_inference(experiment, field: str):
    if field == "species_loss_fraction":
        values = experiment.sample.species_loss_fraction
        return None if values is None else values["Na"].inference
    value = _loaded_run_metadata_value(experiment, field)
    return None if value is None else value.inference


_AS_PUBLISHED_MASS_BYPASS_SHAPES = (
    (
        "nested-inside-value",
        {
            "state": {
                "tag": "value",
                "value": {
                    "kind": "point",
                    "point": "0.5",
                    "as_published": {"units": "g"},
                },
            }
        },
    ),
    (
        "explicit-identity",
        {
            "state": {
                "tag": "value",
                "value": {"kind": "point", "point": "0.5"},
            },
            "as_published": {"value": "0.5", "units": "g"},
            "inference": {
                "method": "fixture",
                "relation": "identity",
                "inputs": ["source"],
                "output_unit": "kg",
            },
        },
    ),
)
_AS_PUBLISHED_MASS_BYPASS_CASES = tuple(
    (home, name, raw_value)
    for home in RUN_METADATA_FIELD_HOMES["mass_kg"][1]
    for name, raw_value in _AS_PUBLISHED_MASS_BYPASS_SHAPES
)
_UNCONSUMED_AS_PUBLISHED_UNIT_CASES = tuple(
    (field, home, unit_key)
    for field in (
        "mass_kg",
        "final_mass_kg",
        "species_loss_fraction",
        "charge_sequence",
        "vaporization_statement",
    )
    for home in RUN_METADATA_FIELD_HOMES[field][1]
    for unit_key in ("unit_code",)
) + tuple(
    ("mass_kg", home, "unit")
    for home in RUN_METADATA_FIELD_HOMES["mass_kg"][1]
)


def test_run_metadata_unit_keys_are_consumed_or_typed_at_every_home(
    tmp_path: Path,
) -> None:
    mutations = 0
    seeds = 0
    fields = (
        "mass_kg",
        "final_mass_kg",
        "species_loss_fraction",
        "charge_sequence",
        "vaporization_statement",
    )
    located_value_shapes = (
        "scalar",
        "point",
        "located-state",
        "series",
        "interval",
        "bound",
        "inference-with-parameters",
        "wrapped-inference",
        "inner-inference",
        "as-published-point",
        "as-published-interval",
        "nested-combination",
        "misplaced-as-published",
    )
    shapes_by_field = {
        "mass_kg": located_value_shapes,
        "final_mass_kg": located_value_shapes,
        "species_loss_fraction": (
            "scalar",
            "point",
            "located-state",
            "series",
            "interval",
            "bound",
            "inference-with-parameters",
        ),
        "charge_sequence": ("unitless", "unitless-with-inference"),
        "vaporization_statement": ("unitless", "unitless-with-inference"),
    }
    cases = [
        (field, home, shape)
        for field in fields
        for home in RUN_METADATA_FIELD_HOMES[field][1]
        for shape in shapes_by_field[field]
    ]
    priority_cases = [
        case
        for case in cases
        if (
            case[0] in {"mass_kg", "final_mass_kg"}
            and case[2] in {"wrapped-inference", "inner-inference"}
        )
        or (
            case[0] in {"charge_sequence", "vaporization_statement"}
            and case[2] == "unitless-with-inference"
        )
    ]
    priority_case_set = set(priority_cases)
    ordered_cases = priority_cases + [
        case for case in cases if case not in priority_case_set
    ]
    for field, home, shape in ordered_cases:
        seed_root = tmp_path / f"seed-{seeds}"
        seeds += 1
        if shape.startswith("unitless"):
            payload = (
                {"charge_id": "charge-a", "run_index": 1,
                 "locator": {"table": "unit-matrix", "page": 1}}
                if field == "charge_sequence"
                else {
                    "state": {"tag": "value", "value": "congruent"},
                    "locator": {"table": "unit-matrix", "page": 1},
                }
            )
            if shape == "unitless-with-inference":
                payload["inference"] = {
                    "method": "fixture",
                    "relation": "identity",
                    "inputs": ["source"],
                    "output_unit": "g",
                }
            _seed, base_extract = _unitless_run_metadata_extract(
                seed_root, field, home, payload
            )
        else:
            _seed, base_extract = _run_metadata_shape_extract(
                seed_root, field, home, shape
            )

        base_payload = _run_metadata_field_payload(
            base_extract, field, home
        )
        for node_path in _run_metadata_mapping_paths(base_payload):
            for unit_key in _RUN_METADATA_UNIT_KEYS:
                extract = deepcopy(base_extract)
                field_payload = _run_metadata_field_payload(
                    extract, field, home
                )
                _run_metadata_mapping_at(field_payload, node_path)[unit_key] = "g"
                root = _write_min_tree(
                    tmp_path / f"mutation-{mutations}", extract
                )
                result = migrate(root, write=True)
                assert result.validation is not None
                experiment_id = "10.1234/fixture::experiment::run-1"
                _works, experiments, _observations = load_migrated_store(root)
                loaded = _loaded_run_metadata_value(
                    experiments[experiment_id], field
                )
                field_issues = [
                    issue
                    for issue in result.validation.issues
                    if issue.reason is RefusalReason.INVALID_SOURCE
                    and field in issue.path
                ]
                context = (
                    f"field={field}, home={home}, shape={shape}, "
                    f"node={node_path}, key={unit_key}"
                )
                if field_issues:
                    assert loaded is None, context
                    if shape == "misplaced-as-published":
                        assert any(
                            "as_published is only valid on the Located value"
                            in issue.detail
                            for issue in field_issues
                        ), context
                else:
                    if unit_key.casefold() == "output_unit":
                        inference = _loaded_run_metadata_inference(
                            experiments[experiment_id], field
                        )
                        assert inference is not None, context
                        assert inference.output_unit == "g", context
                    elif (
                        field in {"mass_kg", "final_mass_kg"}
                        and node_path == ("as_published",)
                        and unit_key in {"units", "low_units", "high_units"}
                    ):
                        assert loaded is not None and loaded.state.is_value, context
                        assert loaded.inference is not None, context
                        assert loaded.inference.relation == "g_to_kg", context
                    elif (
                        field in {"mass_kg", "final_mass_kg"}
                        and node_path
                        in {
                            (),
                            ("value",),
                            ("state", "value"),
                            ("value", "state", "value"),
                        }
                        and unit_key in {"unit", "units"}
                    ):
                        assert loaded is not None and loaded.state.is_value, context
                        printed = Decimal(
                            "0.0008" if field == "final_mass_kg" else "0.25"
                        )
                        assert loaded.state.value == Value.point_of(
                            printed / Decimal(1000)
                        ), context
                    else:
                        pytest.fail(
                            "unit-like key was accepted without a consuming reader: "
                            f"{context}"
                        )
                mutations += 1


_RUN_METADATA_INFERENCE_LOSS_CASES = tuple(
    ("mass_kg", home) for home in RUN_METADATA_FIELD_HOMES["mass_kg"][1]
) + tuple(
    ("charge_sequence", home)
    for home in RUN_METADATA_FIELD_HOMES["charge_sequence"][1]
)


@pytest.mark.parametrize(
    ("field", "home"),
    _RUN_METADATA_INFERENCE_LOSS_CASES,
    ids=[f"{field}-{home}" for field, home in _RUN_METADATA_INFERENCE_LOSS_CASES],
)
def test_run_metadata_inference_is_rejected_when_the_shape_reader_drops_it(
    tmp_path: Path,
    field: str,
    home: str,
) -> None:
    if field == "mass_kg":
        raw_value = _run_metadata_value_shape("wrapped-inference", "0.0005")
        root, extract = _run_metadata_shape_extract(
            tmp_path / field / home.replace("/", "_"),
            field,
            home,
            "wrapped-inference",
            raw_value=raw_value,
        )
        if home == "species.*.observations[].equipment.mass_kg":
            assert extract["species"]["Na"]["observations"][0]["equipment"][
                field
            ] == raw_value
    else:
        payload = {"charge_id": "charge-a", "run_index": 1}
        payload["inference"] = {
            "method": "fixture",
            "relation": "identity",
            "inputs": ["source"],
            "output_unit": "g",
        }
        root, _extract = _unitless_run_metadata_extract(
            tmp_path / field / home.replace("/", "_"), field, home, payload
        )

    result = migrate(root, write=True)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and field in issue.path
        and "inference" in issue.detail
        for issue in result.validation.issues
    ), result.validation.issues
    _works, experiments, _observations = load_migrated_store(root)
    experiment = experiments["10.1234/fixture::experiment::run-1"]
    assert _loaded_run_metadata_value(experiment, field) is None


@pytest.mark.parametrize(
    "witness",
    ("inner-located-value", "species-loss-fraction", "vaporization-statement"),
)
def test_consumed_inference_witnesses_round_trip(
    tmp_path: Path,
    witness: str,
) -> None:
    if witness == "inner-located-value":
        field = "mass_kg"
        raw_value = _run_metadata_value_shape("inner-inference", "0.0005")
        root, extract = _run_metadata_shape_extract(
            tmp_path / witness,
            field,
            "experiments[].sample.mass_kg",
            "inner-inference",
            raw_value=raw_value,
        )
        expected_unit = "kg"
        expected_table = "inner-inference"
        expected_page = 13
    elif witness == "species-loss-fraction":
        field = "species_loss_fraction"
        raw_value = {
            "state": {
                "tag": "value",
                "value": {"kind": "point", "point": "0.25"},
            },
            "locator": {"table": "grok-species-loss", "page": 6},
            "inference": {
                "method": "fixture",
                "relation": "identity",
                "inputs": ["source"],
                "output_unit": "fraction",
            },
        }
        root, extract = _run_metadata_shape_extract(
            tmp_path / witness,
            field,
            "experiments[].sample.species_loss_fraction",
            "scalar",
            raw_value=raw_value,
        )
        expected_unit = "fraction"
        expected_table = "grok-species-loss"
        expected_page = 6
    else:
        field = "vaporization_statement"
        payload = {
            "state": {"tag": "value", "value": "congruent"},
            "locator": {"table": "grok-vaporization", "page": 8},
            "inference": {
                "method": "fixture",
                "relation": "identity",
                "inputs": ["source"],
                "output_unit": "as_published",
            },
        }
        root, extract = _unitless_run_metadata_extract(
            tmp_path / witness,
            field,
            "experiments[].vaporization_statement",
            payload,
        )
        expected_unit = "as_published"
        expected_table = "grok-vaporization"
        expected_page = 8

    assert validate_extract_document(extract) == []
    migrate(root, write=True)
    _works, experiments, _observations = load_migrated_store(root)
    experiment = experiments["10.1234/fixture::experiment::run-1"]
    if field == "species_loss_fraction":
        assert experiment.sample.species_loss_fraction is not None
        located = experiment.sample.species_loss_fraction["Na"]
        assert located.state.value == Value.point_of("0.25")
    elif field == "vaporization_statement":
        located = experiment.vaporization_statement
        assert located is not None
        assert located.state.value is VaporizationMode.CONGRUENT
    else:
        located = experiment.sample.mass_kg
        assert located is not None
        assert located.state.value == Value.point_of("0.0005")

    assert located.locator == Locator(table=expected_table, page=expected_page)
    assert located.inference is not None
    assert located.inference.relation == "identity"
    assert located.inference.inputs == ("source",)
    assert located.inference.output_unit == expected_unit


@pytest.mark.parametrize(
    ("field", "home", "unit_key"),
    _UNCONSUMED_AS_PUBLISHED_UNIT_CASES,
    ids=[
        f"{field}-{home}-as-published-{unit_key}"
        for field, home, unit_key in _UNCONSUMED_AS_PUBLISHED_UNIT_CASES
    ],
)
def test_as_published_unit_aliases_are_refused_at_every_run_metadata_home(
    tmp_path: Path,
    field: str,
    home: str,
    unit_key: str,
) -> None:
    if field in {"charge_sequence", "vaporization_statement"}:
        payload = (
            {"charge_id": "charge-a", "run_index": 1}
            if field == "charge_sequence"
            else {"state": {"tag": "value", "value": "congruent"}}
        )
        root, extract = _unitless_run_metadata_extract(
            tmp_path / f"{field}-{home}", field, home, payload
        )
    else:
        root, extract = _run_metadata_shape_extract(
            tmp_path / f"{field}-{home}", field, home, "point-wrapper"
        )
    field_payload = _run_metadata_field_payload(extract, field, home)
    assert isinstance(field_payload, dict)
    field_payload["as_published"] = {unit_key: "g"}
    (root / "data/literature/extracts/fixture-source.yaml").write_text(
        yaml.safe_dump(extract, sort_keys=False), encoding="utf-8"
    )

    result = migrate(root, write=True)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE and field in issue.path
        for issue in result.validation.issues
    ), result.validation.issues
    _works, experiments, _observations = load_migrated_store(root)
    experiment = experiments["10.1234/fixture::experiment::run-1"]
    assert _loaded_run_metadata_value(experiment, field) is None


@pytest.mark.parametrize(
    ("home", "name", "raw_value"),
    _AS_PUBLISHED_MASS_BYPASS_CASES,
    ids=[
        f"{home}-{name}"
        for home, name, _raw_value in _AS_PUBLISHED_MASS_BYPASS_CASES
    ],
)
def test_run_metadata_as_published_conversion_bypasses_are_typed(
    tmp_path: Path,
    home: str,
    name: str,
    raw_value: dict,
) -> None:
    root, _extract = _run_metadata_shape_extract(
        tmp_path / f"{home.replace('.', '_').replace('[', '').replace(']', '').replace('*', 'star')}-{name}",
        "mass_kg",
        home,
        "scalar",
        raw_value=raw_value,
    )
    result = migrate(root, write=True)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and "sample.mass_kg" in issue.path
        for issue in result.validation.issues
    ), result.validation.issues
    _works, experiments, _observations = load_migrated_store(root)
    experiment = experiments["10.1234/fixture::experiment::run-1"]
    assert experiment.sample.mass_kg is None


_RUN_METADATA_UNIT_LOSS_WITNESSES = (
    (
        "as-published-high-units-alone",
        {
            "state": {
                "tag": "value",
                "value": {"kind": "point", "point": "0.0005"},
            },
            "as_published": {"high_units": "g"},
        },
    ),
    (
        "as-published-point-with-unused-high-units",
        {
            "state": {
                "tag": "value",
                "value": {"kind": "point", "point": "0.0005"},
            },
            "as_published": {
                "value": "0.5",
                "units": "g",
                "high_units": "kg",
            },
        },
    ),
    (
        "uppercase-output-unit-next-to-consumed-output-unit",
        {
            "state": {
                "tag": "value",
                "value": {"kind": "point", "point": "0.0005"},
            },
            "inference": {
                "method": "fixture",
                "relation": "identity",
                "inputs": ["source"],
                "output_unit": "kg",
                "OUTPUT_UNIT": "g",
            },
        },
    ),
    (
        "fake-inference-nested-in-value",
        {
            "state": {
                "tag": "value",
                "value": {
                    "kind": "point",
                    "point": "0.0005",
                    "ignored": {"inference": {"output_unit": "g"}},
                },
            },
        },
    ),
    (
        "uppercase-output-unit-with-nested-unit-alias",
        {
            "state": {
                "tag": "value",
                "value": {"kind": "point", "point": "0.0005"},
            },
            "inference": {
                "method": "fixture",
                "relation": "identity",
                "inputs": ["source"],
                "output_unit": "kg",
                "OUTPUT_UNIT": {"unit": "g"},
            },
        },
    ),
)
_RUN_METADATA_UNIT_LOSS_CASES = tuple(
    (home, name, raw_value)
    for home in RUN_METADATA_FIELD_HOMES["mass_kg"][1]
    for name, raw_value in _RUN_METADATA_UNIT_LOSS_WITNESSES
)


@pytest.mark.parametrize(
    ("home", "name", "raw_value"),
    _RUN_METADATA_UNIT_LOSS_CASES,
    ids=[
        f"{home}-{name}"
        for home, name, _raw_value in _RUN_METADATA_UNIT_LOSS_CASES
    ],
)
def test_run_metadata_unit_reader_rejects_unconsumed_members_at_every_mass_home(
    tmp_path: Path,
    home: str,
    name: str,
    raw_value: dict,
) -> None:
    root, _extract = _run_metadata_shape_extract(
        tmp_path / f"{home}-{name}",
        "mass_kg",
        home,
        "scalar",
        raw_value=raw_value,
    )

    result = migrate(root, write=True)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and "sample.mass_kg" in issue.path
        for issue in result.validation.issues
    ), result.validation.issues
    _works, experiments, _observations = load_migrated_store(root)
    experiment = experiments["10.1234/fixture::experiment::run-1"]
    assert experiment.sample.mass_kg is None


@pytest.mark.parametrize(
    ("name", "as_published"),
    (
        ("empty", {}),
    ),
)
def test_run_metadata_unconsumed_as_published_blocks_are_typed(
    tmp_path: Path,
    name: str,
    as_published: dict,
) -> None:
    root, _extract = _run_metadata_shape_extract(
        tmp_path / name,
        "mass_kg",
        "experiments[].sample.mass_kg",
        "scalar",
        raw_value={
            "state": {
                "tag": "value",
                "value": {"kind": "point", "point": "0.5"},
            },
            "as_published": as_published,
        },
    )
    result = migrate(root, write=True)

    assert result.validation is not None
    assert any(
        issue.reason is RefusalReason.INVALID_SOURCE
        and issue.path.endswith("sample.mass_kg")
        for issue in result.validation.issues
    ), result.validation.issues
    _works, experiments, _observations = load_migrated_store(root)
    experiment = experiments["10.1234/fixture::experiment::run-1"]
    assert experiment.sample.mass_kg is None
