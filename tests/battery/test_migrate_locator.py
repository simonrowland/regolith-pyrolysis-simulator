"""Locator aliases and typed refusals at the extract migration boundary."""

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import RefusalReason
from simulator.battery.migrate import (
    Migrator,
    _bind_same_run_initial_composition,
    _same_run_initial_melts,
    migrate,
    observation_from_plain,
    to_plain,
    wt_pct_to_mole_fraction,
)
from simulator.battery.records import Locator
from tests.battery.test_migrate import FIXTURE_EXTRACT, _write_min_tree


def test_row_and_column_aliases_survive_extract_migration(tmp_path: Path) -> None:
    assert Locator(row="R-7").has_location()
    assert Locator(column="Na").has_location()

    extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    locator = extract["species"]["Na"]["observations"][0]["locator"]
    locator.update(
        {
            "table_row": "R-7",
            "table_column": "Na",
        }
    )

    from tools.validate_literature_extracts import _locator_ok

    assert _locator_ok({"table_row": "R-7"})
    assert _locator_ok({"table_column": "Na"})

    result = migrate(
        _write_min_tree(tmp_path / "row-column", extract), write=False
    )

    observation = next(iter(result.observations.values()))
    assert observation.locator is not None
    assert observation.locator.row == "R-7"
    assert observation.locator.column == "Na"


@pytest.mark.parametrize(
    ("baseline_locator", "promoted_locator"),
    [
        ({}, {}),
        (
            {},
            {"table_row": "R-7"},
        ),
        (
            {},
            {"table_column": "Na"},
        ),
        (
            {},
            {"table_row": "R-7", "table_column": "Na"},
        ),
    ],
    ids=("no-coordinates", "row-only", "column-only", "both"),
)
def test_locator_alias_promotion_does_not_rekey_observations(
    tmp_path: Path,
    baseline_locator: dict[str, str],
    promoted_locator: dict[str, str],
) -> None:
    baseline_extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    alias_extract = yaml.safe_load(yaml.safe_dump(FIXTURE_EXTRACT))
    baseline_extract["species"]["Na"]["observations"][0]["locator"].update(
        baseline_locator
    )
    alias_extract["species"]["Na"]["observations"][0]["locator"].update(
        promoted_locator
    )

    baseline = migrate(
        _write_min_tree(tmp_path / "baseline", baseline_extract), write=False
    )
    promoted = migrate(
        _write_min_tree(tmp_path / "promoted", alias_extract), write=False
    )

    assert baseline.observations
    assert promoted.observations
    assert set(promoted.observations) == set(baseline.observations)


def test_row_label_aliases_bind_initial_composition_to_final_phase_row(
    tmp_path: Path,
) -> None:
    initial_rows = [
        (
            formula,
            {
                "observation_id": f"initial melt {formula}",
                "experiment": "run-1",
                "type": "equilibrium_phase_composition",
                "units": "wt%",
                "values": {
                    "quantity": "equilibrium_phase_composition",
                    "value": amount,
                },
                "locator": {"table": "3", "row_label": "initial melt"},
            },
        )
        for formula, amount in (("SiO2", "60"), ("MgO", "40"))
    ]
    final_row = {
        "observation_id": "final phase analysis",
        "experiment": "run-1",
        "type": "equilibrium_phase_composition",
        "units": "wt%",
        "values": {
            "quantity": "equilibrium_phase_composition",
            "value": "12",
        },
        "locator": {"table": "3", "row_label": "final phase analysis"},
    }
    document = {
        "species": {
            "SiO2": {"observations": [initial_rows[0][1]]},
            "MgO": {"observations": [initial_rows[1][1]]},
            "FeO": {"observations": [final_row]},
        }
    }
    migrator = Migrator(root=tmp_path, index={}, aliases={})
    migrator._validate_extract_locators(document, source_key="phase-alias.yaml")
    rows = [*initial_rows, ("FeO", final_row)]

    assert all("row" in row["locator"] for _formula, row in rows)
    assert all("row_label" not in row["locator"] for _formula, row in rows)
    initial_melts = _same_run_initial_melts(rows)
    bound = _bind_same_run_initial_composition(
        final_row, {"same_run_initial_melts": initial_melts}
    )

    assert bound is not None
    expected = wt_pct_to_mole_fraction(
        {"SiO2": Decimal("60"), "MgO": Decimal("40")}
    )
    assert dict(bound.components) == dict(expected.components)
    assert migrator.result.registry_issues == []


def test_canonical_and_alias_page_indexes_normalize_before_conflict_check() -> None:
    from simulator.battery.enums import RefusalReason
    from simulator.battery.migrate import _locator_from_plain, locator_from_mapping

    canonical = locator_from_mapping(
        {"pdf_page_index": "1", "section": "Methods"}
    )
    same_value_alias = locator_from_mapping(
        {
            "pdf_page_index": "1",
            "continuation_pdf_page_index": 1,
            "section": "Methods",
        }
    )
    invalid = locator_from_mapping(
        {"pdf_page_index": "page one", "section": "Methods"}
    )
    invalid_alias = locator_from_mapping(
        {"continuation_pdf_page_index": "page one", "section": "Methods"}
    )
    legacy_range = locator_from_mapping(
        {"pdf_page_index": "14–15", "section": "Methods"}
    )

    assert canonical is not None and canonical.pdf_page_index == 1
    assert same_value_alias == canonical
    assert invalid is None
    assert invalid_alias is None
    assert legacy_range is not None and legacy_range.pdf_page_index == "14–15"
    for bad_locator in (
        {"pdf_page_index": "page one", "section": "Methods"},
        {"continuation_pdf_page_index": "page one", "section": "Methods"},
    ):
        with pytest.raises(ValueError) as exc_info:
            _locator_from_plain(bad_locator, refuse_invalid=True)
        assert exc_info.value.reason is RefusalReason.INVALID_SOURCE


def test_persisted_observation_refuses_unknown_locator_keys(tmp_path: Path) -> None:
    result = migrate(_write_min_tree(tmp_path), write=False)
    payload = to_plain(next(iter(result.observations.values())))
    assert isinstance(payload, dict)
    payload["locator"]["unreviewed_locator_axis"] = "axis"

    with pytest.raises(ValueError) as exc_info:
        observation_from_plain(payload)

    assert getattr(exc_info.value, "reason", None) is RefusalReason.INVALID_SOURCE
    assert "unreviewed_locator_axis" in str(exc_info.value)


def test_compilation_refuses_unknown_locator_keys_with_invalid_source(
    tmp_path: Path,
) -> None:
    root = _write_min_tree(tmp_path)
    path = (
        root
        / "data"
        / "literature"
        / "compilations"
        / "fixture-compilation"
        / "records"
        / "record.yaml"
    )
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_compilation.v1",
                "source_id": "fixture-compilation",
                "source": {"citation": "Fixture compilation"},
                "record_id": "record-1",
                "formula": "Fe",
                "source_locator": {
                    "page": 2,
                    "unreviewed_locator_axis": "axis",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    migrator = Migrator(root=root, index={}, aliases={})

    migrator._migrate_compilation_file(path)

    assert migrator.result.observations == {}
    assert len(migrator.result.registry_issues) == 1
    issue = migrator.result.registry_issues[0]
    assert issue.reason is RefusalReason.INVALID_SOURCE
    assert issue.path.endswith("record.yaml].source_locator")
    assert "unreviewed_locator_axis" in issue.detail


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
