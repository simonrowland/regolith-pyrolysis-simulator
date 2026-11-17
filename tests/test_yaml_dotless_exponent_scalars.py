"""Keep the six newly added extracts free of untyped dotless exponents."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.yaml_cache import YAML12SafeLoader
from tools.validate_literature_extracts import _DOTLESS_EXPONENT_RE


REPO_ROOT = Path(__file__).resolve().parents[1]
NEW_EXTRACTS = (
    "data/literature/extracts/cohen-2006-iron-silicate-reduction-isotopes.yaml",
    "data/literature/extracts/floss-1998-corundum-hibonite-evaporation-residues.yaml",
    "data/literature/extracts/navrotsky-koryttseva-2023-oxide-acid-base-calorimetry.yaml",
    "data/literature/extracts/pretorius-muan-1992-chromium-oxide-activities.yaml",
    "data/literature/extracts/smythe-2013-cerium-thesis.yaml",
    "data/literature/extracts/van-westrenen-1999-garnet-melt-trace-partitioning.yaml",
)


def _source_node(source: str) -> yaml.Node | None:
    loader = YAML12SafeLoader(source)
    try:
        return loader.get_single_node()
    finally:
        loader.dispose()


def _find_untyped_exponents(node: yaml.Node, path: tuple[str, ...] = ()):
    if isinstance(node, yaml.ScalarNode):
        if (
            node.tag == "tag:yaml.org,2002:str"
            and node.style in (None, "")
            and _DOTLESS_EXPONENT_RE.fullmatch(node.value)
        ):
            yield ".".join(path), node.start_mark.line + 1, node.value
        return
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            yield from _find_untyped_exponents(
                value_node, path + (str(key_node.value),)
            )
    elif isinstance(node, yaml.SequenceNode):
        for index, value_node in enumerate(node.value):
            yield from _find_untyped_exponents(value_node, path + (str(index),))


@pytest.mark.parametrize("relative", NEW_EXTRACTS)
def test_new_extracts_have_no_untyped_dotless_exponents(relative: str) -> None:
    path = REPO_ROOT / relative
    node = _source_node(path.read_text(encoding="utf-8"))
    assert node is not None
    findings = list(_find_untyped_exponents(node))
    assert not findings, f"{relative} has untyped dotless exponents: {findings}"


@pytest.mark.parametrize(
    ("source", "expected_path"),
    [
        ("value: 3e-8\n", "value"),
        ("values: [3e-8]\n", "values.0"),
        ("values: {sample: 3e-8}\n", "values.sample"),
    ],
    ids=["block-mapping", "flow-sequence", "flow-mapping"],
)
def test_guard_detects_untyped_dotless_exponent_mutations(
    source: str, expected_path: str
) -> None:
    loaded = yaml.load(source, Loader=YAML12SafeLoader)
    assert loaded is not None
    value = (
        loaded["value"]
        if expected_path == "value"
        else loaded["values"][0]
        if expected_path == "values.0"
        else loaded["values"]["sample"]
    )
    assert value == "3e-8"
    assert isinstance(value, str)

    node = _source_node(source)
    assert node is not None
    findings = list(_find_untyped_exponents(node))
    assert [(path, token) for path, _line, token in findings] == [
        (expected_path, "3e-8")
    ]
