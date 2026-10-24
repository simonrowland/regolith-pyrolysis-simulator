"""Regression guard for SC-289 YAML flow-scalar comma truncation."""

from __future__ import annotations

from pathlib import Path

import pytest

from simulator.battery.migrate import discover_extracts
from simulator.yaml_cache import yaml_mapping_source_issues


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACTS = REPO_ROOT / "data" / "literature" / "extracts"


@pytest.mark.parametrize(
    ("source", "expected_issue_kinds"),
    [
        pytest.param(
            "locator: {paragraph: Quartz SiO2}\n",
            set(),
            id="valid-comma-free-paragraph",
        ),
        pytest.param(
            "locator: {paragraph: Quartz, SiO2}\n",
            {"flow mapping key has no value indicator"},
            id="comma-without-whitespace",
        ),
        pytest.param(
            "values: {as_printed: 1,1}\n",
            {"flow mapping key has no value indicator"},
            id="decimal-comma",
        ),
        pytest.param(
            "locator: {paragraph: mass loss, 1 Gew.%}\n",
            {"flow mapping key has no value indicator"},
            id="comma-with-whitespace",
        ),
        pytest.param(
            "locator: {pdf_page_index: 3, table: '1', note: Max Temp is tabulated, but the sensor and measurement target are not stated}\n",
            {"flow mapping key has no value indicator"},
            id="cardiff-note-truncation",
        ),
        pytest.param(
            "locator: {page: 7, \"lab notes\": null}\n",
            set(),
            id="explicit-spaced-null-flow-key",
        ),
        pytest.param(
            "metadata:\n  \"lab notes\": null\n",
            set(),
            id="explicit-spaced-null-block-key",
        ),
        pytest.param(
            "locator: {paragraph: see section, page: 75}\n",
            set(),
            id="mapping-shaped-remainder-is-ambiguous",
        ),
        pytest.param(
            "values: {key: first, key: second}\n",
            {"duplicate constructed mapping key"},
            id="duplicate-string-key",
        ),
        pytest.param(
            "values:\n  pressure_Pa: 7\n  pressure_Pa: 8\n",
            {"duplicate constructed mapping key"},
            id="duplicate-block-key",
        ),
        pytest.param(
            "values: [{pressure_Pa: 7, pressure_Pa: 8}]\n",
            {"duplicate constructed mapping key"},
            id="duplicate-key-in-flow-sequence",
        ),
        pytest.param(
            "values: {1: first, 01: second}\n",
            {"duplicate constructed mapping key"},
            id="integer-spellings-construct-equal",
        ),
        pytest.param(
            "values: {true: first, True: second}\n",
            {"duplicate constructed mapping key"},
            id="boolean-spellings-construct-equal",
        ),
        pytest.param(
            "values: {true: first, 1: second}\n",
            {"duplicate constructed mapping key"},
            id="python-equal-constructed-keys",
        ),
        pytest.param(
            "base: &b {pressure_Pa: 7}\nvalues: {<<: *b, pressure_Pa: 8}\n",
            set(),
            id="single-merge-allows-inherited-override",
        ),
        pytest.param(
            "a: &a {pressure_Pa: 7}\nb: &b {pressure_Pa: 8}\nvalues: {<<: *a, <<: *b}\n",
            {"duplicate explicit merge key"},
            id="repeated-explicit-merge-key",
        ),
    ],
)
def test_mapping_source_guard_cases(
    tmp_path: Path,
    source: str,
    expected_issue_kinds: set[str],
) -> None:
    """Pin source-level comma splits and production-constructed key identity."""
    probe = tmp_path / "probe.yaml"
    probe.write_text(source, encoding="utf-8")
    source = probe.read_text(encoding="utf-8")
    issues = yaml_mapping_source_issues(source)

    assert {kind for kind, _line, _detail in issues} == expected_issue_kinds, issues


def test_battery_loader_rejects_comma_splits_and_duplicate_keys():
    """Use one production-loader parse for source syntax and key identity.

    A remainder such as ``page: 75`` is syntactically a valid mapping entry,
    so this guard cannot infer whether it was intended as prose without
    field/source expectations. A missing value indicator has no such colon.
    """

    mapping_issues = []
    paths = discover_extracts(EXTRACTS)
    assert paths
    for path in paths:
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        issues = yaml_mapping_source_issues(source)
        for kind, line_number, detail in issues:
            source_line = lines[line_number - 1].strip()
            mapping_issues.append(
                f"{path.relative_to(REPO_ROOT)}:{line_number}: {kind}: "
                f"{detail} | {source_line}"
            )
    assert mapping_issues == [], "YAML mapping source issues:\n" + "\n".join(
        mapping_issues
    )
