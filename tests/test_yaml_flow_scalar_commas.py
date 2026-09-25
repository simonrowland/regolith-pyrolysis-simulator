"""Regression guard for SC-289 YAML flow-scalar comma truncation."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import yaml

from simulator.battery import migrate
from simulator.battery.migrate import discover_extracts, load_yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACTS = REPO_ROOT / "data" / "literature" / "extracts"

# Compatibility view used by the independent corpus audit for the one
# intentional block-style OCR continuation; the guard below is source-driven.
ALLOWED_INTENTIONAL_NULL_KEYS = {
    (
        "kems-035-sauerborn-2005.yaml",
        (
            "species",
            "O2",
            "observations",
            0,
            "values",
            "SiO2_mass_loss_locator",
            "1 Gew.%",
        ),
    ),
}


def _walk_null_prose(value: Any, path: tuple[object, ...] = ()):
    """Expose intentional prose-like nulls to the independent audit only."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + (key,)
            if child is None and isinstance(key, str) and any(
                char.isspace() for char in key
            ):
                yield child_path
            yield from _walk_null_prose(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_null_prose(child, path + (index,))


def _keys_match(loaded_key: object, source_key: str) -> bool:
    if loaded_key == source_key or str(loaded_key) == source_key:
        return True
    return (
        isinstance(loaded_key, bool)
        and str(loaded_key).lower() == source_key.lower()
    )


def _mapping_value(mapping: object, source_key: str) -> object:
    if not isinstance(mapping, dict):
        return None
    for loaded_key, child in mapping.items():
        if _keys_match(loaded_key, source_key):
            return child
    return None


def _merged_source_keys(node: object) -> set[str]:
    if isinstance(node, yaml.MappingNode):
        keys = {
            key_node.value
            for key_node, _ in node.value
            if key_node.value != "<<"
        }
        for key_node, value_node in node.value:
            if key_node.value == "<<":
                keys.update(_merged_source_keys(value_node))
        return keys
    if isinstance(node, yaml.SequenceNode):
        keys: set[str] = set()
        for value_node in node.value:
            keys.update(_merged_source_keys(value_node))
        return keys
    return set()


def _walk_flow_mapping_key_offenders(
    node: object,
    loaded: object,
    lines: list[str],
    path: tuple[object, ...] = (),
):
    if isinstance(node, yaml.MappingNode):
        if node.flow_style and node.start_mark.line == node.end_mark.line:
            line = lines[node.start_mark.line]
            source_keys: dict[str, bool] = {}
            for key_node, _ in node.value:
                source_keys.setdefault(key_node.value, False)
                if re.match(r"\s*:", line[key_node.end_mark.column:]):
                    source_keys[key_node.value] = True
            inherited_keys = set()
            for key_node, value_node in node.value:
                if key_node.value == "<<":
                    inherited_keys.update(_merged_source_keys(value_node))
            if isinstance(loaded, dict):
                # load_yaml expands YAML merges; inherited keys have no token
                # on this source line and are not candidates for this check.
                for loaded_key in loaded:
                    matching_keys = [
                        present
                        for source_key, present in source_keys.items()
                        if _keys_match(loaded_key, source_key)
                    ]
                    inherited = any(
                        _keys_match(loaded_key, source_key)
                        for source_key in inherited_keys
                    )
                    if (matching_keys and not any(matching_keys)) or (
                        not matching_keys and not inherited
                    ):
                        yield path + (loaded_key,), line
        for key_node, value_node in node.value:
            child = _mapping_value(loaded, key_node.value)
            yield from _walk_flow_mapping_key_offenders(
                value_node,
                child,
                lines,
                path + (key_node.value,),
            )
    elif isinstance(node, yaml.SequenceNode):
        for index, value_node in enumerate(node.value):
            child = (
                loaded[index]
                if isinstance(loaded, list) and index < len(loaded)
                else None
            )
            yield from _walk_flow_mapping_key_offenders(
                value_node,
                child,
                lines,
                path + (index,),
            )


def _format_path(path: tuple[object, ...]) -> str:
    result = ""
    for part in path:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += f".{part}" if result else str(part)
    return result


def test_battery_loader_rejects_comma_split_flow_keys():
    """Load every source through the battery loader and reject silent splits.

    Null hypothesis: suppose my change is wrong in the way that matters most
    — what shows it? A source line with a comma-split fragment would load a
    mapping key that has no corresponding ``key:`` token and fail this guard.
    """

    flow_mapping_offenders = []
    paths = discover_extracts(EXTRACTS)
    assert paths
    for path in paths:
        source = path.read_text(encoding="utf-8")
        document = load_yaml(path)
        source_tree = yaml.compose(source, Loader=migrate._YAML_LOADER)
        lines = source.splitlines()
        for key_path, line in _walk_flow_mapping_key_offenders(
            source_tree, document, lines
        ):
            flow_mapping_offenders.append(
                f"{path.relative_to(REPO_ROOT)}:{_format_path(key_path)}: {line}"
            )
    assert flow_mapping_offenders == [], (
        "loaded flow-mapping keys missing a source key token:\n"
        + "\n".join(flow_mapping_offenders)
    )
