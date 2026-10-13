"""Regression guard for SC-289 YAML flow-scalar comma truncation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import discover_extracts
from simulator.yaml_cache import YAML12SafeLoader


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACTS = REPO_ROOT / "data" / "literature" / "extracts"


def _format_path(path: tuple[object, ...]) -> str:
    result = ""
    for part in path:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += f".{part}" if result else str(part)
    return result


def _walk_mapping_source_issues(
    node: object,
    loader: YAML12SafeLoader,
    source: str,
    path: tuple[object, ...] = (),
) -> list[tuple[str, int, str]]:
    """Report constructed duplicate keys and flow keys lacking a value token."""
    issues: list[tuple[str, int, str]] = []
    if isinstance(node, yaml.MappingNode):
        constructed_keys: dict[object, yaml.Mark] = {}
        merge_key_seen = False
        for key_node, value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                if merge_key_seen:
                    issues.append(
                        (
                            "duplicate explicit merge key",
                            key_node.start_mark.line + 1,
                            f"{_format_path(path)}: <<",
                        )
                    )
                merge_key_seen = True
            else:
                constructed_key = loader.construct_object(key_node, deep=True)
                try:
                    first_mark = constructed_keys.get(constructed_key)
                except TypeError:
                    # The production loader rejects unhashable mapping keys
                    # while constructing the document below.
                    first_mark = None
                if first_mark is not None:
                    issues.append(
                        (
                            "duplicate constructed mapping key",
                            key_node.start_mark.line + 1,
                            f"{_format_path(path)}: {key_node.value!r} constructs as "
                            f"{constructed_key!r}; first at line {first_mark.line + 1}",
                        )
                    )
                else:
                    try:
                        constructed_keys[constructed_key] = key_node.start_mark
                    except TypeError:
                        pass

            if (
                node.flow_style
                and isinstance(value_node, yaml.ScalarNode)
                and value_node.tag == "tag:yaml.org,2002:null"
                and value_node.value == ""
                and ":"
                not in source[
                    key_node.end_mark.index : value_node.start_mark.index
                ]
            ):
                issues.append(
                    (
                        "flow mapping key has no value indicator",
                        key_node.start_mark.line + 1,
                        f"{_format_path(path)}: {key_node.value!r}",
                    )
                )

            issues.extend(
                _walk_mapping_source_issues(
                    value_node,
                    loader,
                    source,
                    path + (key_node.value,),
                )
            )
    elif isinstance(node, yaml.SequenceNode):
        for index, value_node in enumerate(node.value):
            issues.extend(
                _walk_mapping_source_issues(
                    value_node,
                    loader,
                    source,
                    path + (index,),
                )
            )
    return issues


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
    loader = YAML12SafeLoader(source)
    try:
        node = loader.get_single_node()
        assert node is not None
        issues = _walk_mapping_source_issues(node, loader, source)
        loader.construct_document(node)
    finally:
        loader.dispose()

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
        loader = YAML12SafeLoader(source)
        try:
            source_tree = loader.get_single_node()
            if source_tree is None:
                continue
            issues = _walk_mapping_source_issues(source_tree, loader, source)
            # Construct the document with the same production loader instance;
            # its mapping construction defines key identity and validates keys.
            loader.construct_document(source_tree)
        finally:
            loader.dispose()
        for kind, line_number, detail in issues:
            source_line = lines[line_number - 1].strip()
            mapping_issues.append(
                f"{path.relative_to(REPO_ROOT)}:{line_number}: {kind}: "
                f"{detail} | {source_line}"
            )
    assert mapping_issues == [], "YAML mapping source issues:\n" + "\n".join(
        mapping_issues
    )
