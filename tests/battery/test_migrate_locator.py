"""Locator aliases and typed refusals at the extract migration boundary."""

from pathlib import Path

import yaml

from simulator.battery.enums import RefusalReason
from simulator.battery.migrate import migrate
from simulator.battery.records import Locator
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree


def test_row_and_column_aliases_survive_extract_migration(tmp_path: Path) -> None:
    assert Locator(row="R-7").has_location()
    assert Locator(column="Na").has_location()

    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    locator = extract["species"]["Na"]["observations"][0]["locator"]
    locator.update(
        {
            "row_label": "R-7",
            "table_row_number": "R-7",
            "column_header": "Na",
            "table_column_number": "Na",
        }
    )

    result = migrate(
        _write_min_tree(tmp_path / "row-column", extract), write=False
    )

    observation = next(iter(result.observations.values()))
    assert observation.locator is not None
    assert observation.locator.row == "R-7"
    assert observation.locator.column == "Na"


def test_conflicting_locator_aliases_are_listed_as_invalid_source(
    tmp_path: Path,
) -> None:
    baseline = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    locator = extract["species"]["Na"]["observations"][0]["locator"]
    locator.update(
        {
            "row": "R-7",
            "row_label": "R-8",
            "column": "Na",
            "column_header": "MgO",
        }
    )

    result = migrate(
        _write_min_tree(tmp_path / "conflicting-locator", extract), write=False
    )

    assert result.validation is not None
    issues = [
        issue
        for issue in result.validation.hard_issues
        if "conflicting locator" in issue.detail
    ]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.INVALID_SOURCE
    assert all(
        key in issues[0].detail
        for key in ("row", "row_label", "column", "column_header")
    )
    expected = next(
        iter(
            migrate(
                _write_min_tree(tmp_path / "baseline", baseline), write=False
            ).observations.values()
        )
    )
    migrated = next(iter(result.observations.values()))
    assert migrated.locator is None
    assert migrated.value == expected.value


def test_unknown_locator_key_is_a_typed_validation_issue(tmp_path: Path) -> None:
    baseline = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    locator = extract["species"]["Na"]["observations"][0]["locator"]
    locator["unreviewed_locator_axis"] = "value"

    result = migrate(
        _write_min_tree(tmp_path / "unknown-locator", extract), write=False
    )

    assert result.validation is not None
    issues = [
        issue
        for issue in result.validation.hard_issues
        if "unreviewed_locator_axis" in issue.detail
    ]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.INVALID_SOURCE
    assert issues[0].path.endswith(".locator")
    expected = next(
        iter(
            migrate(
                _write_min_tree(tmp_path / "baseline", baseline), write=False
            ).observations.values()
        )
    )
    migrated = next(iter(result.observations.values()))
    assert migrated.locator is None
    assert migrated.value == expected.value


def test_conflicting_registry_locator_does_not_drop_observation_values(
    tmp_path: Path,
) -> None:
    baseline = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    extract["experiments"] = [
        {
            "experiment_id": "conflicting-run",
            "locator": {
                "page": 2,
                "row_label": "E",
                "table_row_number": "1",
            },
        }
    ]

    result = migrate(
        _write_min_tree(tmp_path / "registry-conflict", extract), write=False
    )
    expected = next(
        iter(
            migrate(
                _write_min_tree(tmp_path / "baseline", baseline), write=False
            ).observations.values()
        )
    )

    assert result.validation is not None
    issues = [
        issue
        for issue in result.validation.hard_issues
        if "conflicting locator" in issue.detail
    ]
    assert len(issues) == 1
    assert issues[0].reason is RefusalReason.INVALID_SOURCE
    assert "row_label" in issues[0].detail
    assert "table_row_number" in issues[0].detail
    migrated = next(iter(result.observations.values()))
    assert migrated.value == expected.value
