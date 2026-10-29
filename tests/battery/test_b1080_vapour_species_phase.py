"""b-1080: vapour-species quantities use gas as their phase."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from simulator.battery.migrate import REPO_ROOT

_NON_VAPOUR_QUANTITIES = {
    "ab_initio_enthalpy_of_formation",
    # Sossi Table 4 values are liquid-oxide activity coefficients; gas_species
    # names the vaporization reaction partner, not the measured species.
    "activity_coefficient_this_work",
    "calibration_reference_gas_composition",
    "condensed_sample_mass",
    "deposit_edx_wt_pct_ranges",
    "equilibrium_molecular_oxygen_yield",
    "gibbs_table",
    "major_gas_speciation_by_environment",
    "not_reported_among_detected_species",
    "one_percent_evaporation_temperature",
    "open_furnace_residue_composition_vs_time",
    "reduced_partition_function_ratio_prefactor",
    "relation",
    "residue_component_composition",
    "residue_composition_vs_time",
    "solar_wind_helium_inventory",
    "free_evap_sio2_loss_with_vf",
}


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


def _has_quantity(row: dict[str, object], values: dict[str, object]) -> bool:
    return any(
        isinstance(quantity, ScalarNode) and _has_value(quantity)
        for quantity in (
            values.get("quantity"),
            row.get("quantity"),
            row.get("type"),
        )
    )


def _is_vapour_measurement(row: dict[str, object], values: dict[str, object]) -> bool:
    quantity = next(
        (
            candidate
            for candidate in (
                values.get("quantity"),
                row.get("quantity"),
                row.get("type"),
            )
            if isinstance(candidate, ScalarNode) and _has_value(candidate)
        ),
        None,
    )
    if not isinstance(quantity, ScalarNode):
        return False
    name = quantity.value.strip().casefold().replace("-", "_")
    # These quantities describe condensed samples, reference-gas fixtures, or
    # thermochemical/model records; gas_species is context in those rows.
    return name not in _NON_VAPOUR_QUANTITIES


def _has_gas_phase_identity(node: object) -> bool:
    if isinstance(node, ScalarNode):
        return re.search(r"\(\s*g\s*\)", node.value, re.IGNORECASE) is not None
    if isinstance(node, SequenceNode):
        return any(_has_gas_phase_identity(item) for item in node.value)
    if isinstance(node, MappingNode):
        return any(_has_gas_phase_identity(value) for _, value in node.value)
    return False


def _has_vapour_species(row: dict[str, object], values: dict[str, object]) -> bool:
    for owner in (values, row):
        if _has_value(owner.get("gas_species")):
            return True
        if any(
            _has_gas_phase_identity(owner.get(field))
            for field in ("species", "formula", "pressure_species")
        ):
            return True
    return False


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
                phase_name = (
                    phase.value.strip().casefold()
                    if isinstance(phase, ScalarNode)
                    else None
                )
                values = _mapping(row.get("values"))
                if (
                    isinstance(phase, ScalarNode)
                    # highT_compiled is a compilation/temperature-range tag,
                    # not a sample, apparatus, or condensed-phase context.
                    and phase_name not in {"gas", "g", "hight_compiled"}
                    and _has_quantity(row, values)
                    and _is_vapour_measurement(row, values)
                    and _has_vapour_species(row, values)
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


def test_vapour_species_quantity_phase_is_gas() -> None:
    offending = _offending_vapour_phase_rows()
    assert not offending, (
        "vapour-species quantities must use phase: gas; offending extract:line:\n"
        + "\n".join(offending)
    )
