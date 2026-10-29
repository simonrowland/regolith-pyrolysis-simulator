"""b-1080: pressure-bearing vapour species use gas as their phase."""

from __future__ import annotations

from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from simulator.battery.migrate import REPO_ROOT


def _mapping(node: object) -> dict[str, object]:
    if not isinstance(node, MappingNode):
        return {}
    return {
        key.value: value
        for key, value in node.value
        if isinstance(key, ScalarNode)
    }


def _has_value(node: object) -> bool:
    if isinstance(node, ScalarNode):
        return node.value.strip().casefold() not in {"", "null", "none", "~"}
    if isinstance(node, SequenceNode):
        return any(_has_value(item) for item in node.value)
    return isinstance(node, MappingNode) and bool(node.value)


def _is_pressure_quantity(row: dict[str, object], values: dict[str, object]) -> bool:
    quantity = values.get("quantity") or row.get("type")
    if not isinstance(quantity, ScalarNode):
        return False
    name = quantity.value.casefold().replace("-", "_")
    return "pressure" in name or name in {"p_sat", "psat_series", "p_partial"}


def _offending_vapour_phase_rows() -> list[str]:
    findings: list[str] = []
    extracts = REPO_ROOT / "data/literature/extracts"
    for path in sorted(extracts.iterdir()):
        if path.suffix != ".yaml":
            continue
        root = yaml.compose(path.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)
        if root is None:
            continue

        def visit(node: object) -> None:
            if isinstance(node, MappingNode):
                row = _mapping(node)
                phase = row.get("phase")
                values = _mapping(row.get("values"))
                gas_species = values.get("gas_species") or row.get("gas_species")
                if (
                    isinstance(phase, ScalarNode)
                    and phase.value.strip().casefold() not in {"gas", "g"}
                    and _has_value(gas_species)
                    and _is_pressure_quantity(row, values)
                ):
                    findings.append(
                        f"{path.relative_to(REPO_ROOT).as_posix()}:{phase.start_mark.line + 1}"
                    )
                for _, child in node.value:
                    visit(child)
            elif isinstance(node, SequenceNode):
                for child in node.value:
                    visit(child)

        visit(root)
    return findings


def test_pressure_bearing_vapour_species_phase_is_gas() -> None:
    offending = _offending_vapour_phase_rows()
    assert not offending, (
        "vapour-species pressure rows must use phase: gas; offending extract:line:\n"
        + "\n".join(offending)
    )
