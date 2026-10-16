#!/usr/bin/env python3
"""Generate the refractory-vapor registry from the held JANAF tables."""

from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.reference_data.janaf import load_table_document  # noqa: E402
from simulator.yaml_cache import load_cached_safe_yaml  # noqa: E402

OUTPUT_PATH = ROOT / "data" / "refractory_vapor_species.yaml"
OVERLAY_PATH = ROOT / "data" / "refractory_vapor_species_overlay.yaml"
TABLES_DIR = ROOT / "data" / "literature" / "compilations" / "janaf" / "tables"
TEMPERATURE_GRID_K = tuple(range(1500, 3501, 100))

GAS_TABLES = {
    "O": "O-001",
    "O2": "O-029",
    "Al": "Al-005",
    "Al2": "Al-080",
    "AlO": "Al-074",
    "AlO2": "Al-077",
    "Al2O": "Al-092",
    "Al2O2": "Al-094",
    "Ca": "Ca-006",
    "CaO": "Ca-030",
    "Ca2": "Ca-033",
    "Mg": "Mg-005",
    "MgO": "Mg-011",
    "Mg2": "Mg-027",
    "Si": "Si-005",
    "Si2": "Si-008",
    "Si3": "Si-009",
    "SiO": "O-012",
    "SiO2": "O-040",
    "Ti": "Ti-006",
    "TiO": "O-022",
    "TiO2": "O-046",
    "NaO": "Na-008",
    "KO": "K-008",
}

CONDENSED_TABLES = {
    "Al2O3": "Al-101",
    "CaO": "Ca-029",
    "MgO": "Mg-010",
    "SiO2": "O-039",
    "TiO2": "O-045",
    "MgAl2O4": "Al-089",
}

CONDENSED_FORMULAS = {"MgAl2O4": "MgAl2O4"}
LIQUID_TABLES = {"Al2O3": "Al-101", "CaO": "Ca-029", "MgO": "Mg-010"}

# The requested numerical carrier set is the existing JANAF-backed set plus
# NaO and KO. Al2O3(g) remains an explicitly unmodeled carrier in the metadata
# overlay: its NASA citation identifies the species but it is outside this
# registry's compiled JANAF log-Kf rows.

# Premise -> algebra -> units -> sanity check:
# JANAF's log Kf column is for formation from the table's elemental reference
# states. ΔfG° = -R T ln(Kf), hence log10(Kf) = -ΔfG°/(R T ln(10)). JANAF's
# ΔfG° values are kJ/mol, so the algebraic check converts them by 1000 J/kJ;
# R is J/(mol·K), T is K, and log10(Kf) is dimensionless. The generated value
# is the source's printed log Kf token, not a recomputation or fitted value.
# The freshness test compares every selected JANAF species' printed values at
# both 2000 K and 2500 K; those checks are the per-species source sanity anchors.


class RefractoryGenerationError(ValueError):
    """A requested source row is absent, incomplete, or phase-ambiguous."""


def _load_overlay() -> dict[str, Any]:
    payload = load_cached_safe_yaml(OVERLAY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise RefractoryGenerationError(f"{OVERLAY_PATH}: expected a mapping")
    return dict(payload)


def _load_janaf_table(table_id: str) -> dict[str, Any]:
    path = TABLES_DIR / f"{table_id}.yaml"
    if not path.is_file():
        raise RefractoryGenerationError(f"missing JANAF table {table_id}: {path}")
    document = load_table_document(path)
    if document.get("source_id") != "nist-janaf-4th":
        raise RefractoryGenerationError(f"{path}: unexpected source_id")
    table = document.get("table")
    if not isinstance(table, Mapping) or table.get("table_id") != table_id:
        raise RefractoryGenerationError(f"{path}: table id does not match {table_id}")
    return dict(table)


def _printed_log_kf(table: Mapping[str, Any]) -> tuple[dict[int, float], list[float]]:
    values: dict[int, float] = {}
    all_temperatures: list[float] = []
    for row in table.get("values") or []:
        if not isinstance(row, Mapping):
            continue
        temperature = (row.get("temperature") or {}).get("value")
        cell = row.get("log10_formation_equilibrium_constant")
        if temperature is None or not isinstance(cell, Mapping) or cell.get("value") is None:
            continue
        temp = float(temperature)
        if temp.is_integer():
            temp_key = int(temp)
        else:
            continue
        value = float(cell["value"])
        if temp_key in values and not math.isclose(values[temp_key], value, abs_tol=1e-12):
            raise RefractoryGenerationError(
                f"{table.get('table_id')} has conflicting printed log Kf cells at {temp_key} K"
            )
        values[temp_key] = value
        all_temperatures.append(temp)
    if not values:
        raise RefractoryGenerationError(f"{table.get('table_id')} has no printed log Kf cells")
    return values, all_temperatures


def _phase_transitions(table: Mapping[str, Any]) -> list[float]:
    transitions: set[float] = set()
    for ambiguity in table.get("parse_ambiguities") or []:
        raw_line = str(ambiguity.get("raw_line") or "")
        if "<-->" not in raw_line or "LIQUID" not in raw_line.upper():
            continue
        match = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)\s*\t", raw_line)
        if match:
            transitions.add(float(match.group(1)))
    return sorted(transitions)


def _registry_row(
    species: str,
    table_id: str,
    *,
    condensed: bool,
    override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    table = _load_janaf_table(table_id)
    table_meta = table.get("index_entry") or {}
    phase = str(table_meta.get("state") or "")
    if not phase:
        raise RefractoryGenerationError(f"{table_id}: missing phase/state")

    log_kf, source_temperatures = _printed_log_kf(table)
    grid = list(TEMPERATURE_GRID_K)
    transitions = _phase_transitions(table) if condensed else []
    if len(transitions) > 1:
        raise RefractoryGenerationError(
            f"{table_id} has multiple crystal-liquid transitions: {transitions}"
        )
    transition = transitions[0] if transitions else None
    notice: dict[str, Any] | None = None
    phase_limited = condensed and phase not in {"cr,l", "l", "L", "liquid"}
    if phase_limited:
        if transition is None:
            raise RefractoryGenerationError(
                f"{table_id} is crystalline-only but has no compiled crystal-liquid transition"
            )
        grid = [temperature for temperature in grid if temperature < transition]
        if not grid:
            raise RefractoryGenerationError(f"{table_id}: no requested grid points below Tm")
        notice = {
            "type": "liquid_table_unavailable_after_phase_boundary",
            "temperature_K": transition,
            "source_table_id": table_id,
            "message": (
                "The JANAF crystal table ends at its crystal-liquid transition; "
                "no liquid table is available. The stored grid stops at the last "
                "source point below Tm, and out-of-range use remains typed."
            ),
        }

    missing = [temperature for temperature in grid if temperature not in log_kf]
    if missing:
        raise RefractoryGenerationError(
            f"{table_id} lacks printed log Kf grid cells for {species}: {missing}"
        )
    selected = {temperature: log_kf[temperature] for temperature in grid}
    used_source_temperatures = [
        temperature for temperature in source_temperatures
        if not phase_limited or transition is None or temperature < transition
    ]

    formula = CONDENSED_FORMULAS.get(species, species)
    source_title = str(table.get("title_as_published") or species)
    source_url = str((table.get("table") or {}).get("url") or table.get("url") or "")
    if not source_url:
        source_url = f"https://janaf.nist.gov/tables/{table_id}.html"

    row: dict[str, Any] = {
        "formula": formula,
        "source": f"NIST-JANAF {table_id}, {source_title}",
        "source_url": source_url,
        "valid_temperature_K": [float(grid[0]), float(grid[-1])],
        "provenance": {
            "table_id": table_id,
            "source_temperature_range_K": [
                float(min(used_source_temperatures)),
                float(max(used_source_temperatures)),
            ],
            "phase": phase,
            "derivation": (
                "Use JANAF's printed, dimensionless log10(Kf) for formation from "
                "the elements in their reference states. The equivalent identity is "
                "log10(Kf) = -ΔfG°/(R T ln 10); ΔfG° is kJ/mol in the source and "
                "requires 1000 J/kJ, R is J/(mol·K), and T is K. No fit or "
                "residual-based adjustment is applied."
            ),
        },
        "log10_kf": selected,
    }
    if transitions:
        row["phase_transitions_K"] = transitions
    if override:
        row.update(dict(override))
    if notice:
        row["out_of_band_notice"] = notice
    return row


def render_registry() -> str:
    """Return the stable generated registry text without writing it."""
    overlay = _load_overlay()
    output: dict[str, Any] = {
        "schema_version": int(overlay["schema_version"]),
        "standard_pressure_pa": float(overlay["standard_pressure_pa"]),
        "interpolation": overlay["interpolation"],
        "out_of_range_policy": overlay["out_of_range_policy"],
        "extension_policy": overlay["extension_policy"],
        "kinetic_model": overlay["kinetic_model"],
        "provenance": {
            "database": "NIST-JANAF Thermochemical Tables, Fourth Edition, SRD 13",
            "doi": "10.18434/T42S31",
            "note": (
                "Every gas and condensed log10(Kf) value is copied from a compiled "
                "JANAF as_published cell on the 100 K grid from 1500 through 3500 K. "
                "The crystalline-only MgAl2O4 row stops below its 2408 K transition; "
                "its typed out-of-band notice records the missing liquid table."
            ),
        },
        "overlay_provenance": overlay["overlay_citation"],
        "gas_species": {
            species: _registry_row(species, table_id, condensed=False)
            for species, table_id in GAS_TABLES.items()
        },
        "condensed_phases": {
            species: _registry_row(
                species,
                table_id,
                condensed=True,
                override=(overlay.get("condensed_row_overrides") or {}).get(species),
            )
            for species, table_id in CONDENSED_TABLES.items()
        },
        "materials": overlay["materials"],
    }
    return yaml.safe_dump(
        output,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=110,
    )


def _parse_registry(text: str) -> dict[str, Any]:
    payload = load_cached_safe_yaml(text)
    if not isinstance(payload, Mapping):
        raise RefractoryGenerationError("registry is not a mapping")
    return dict(payload)


def render_change_report(previous_text: str, generated_text: str) -> str:
    """Report every changed or newly populated species/temperature value."""
    previous = _parse_registry(previous_text)
    generated = _parse_registry(generated_text)
    lines = [
        "# Refractory vapor species generation report",
        "",
        "The generated values below come from compiled JANAF printed log Kf cells.",
        "No row was selected or adjusted by residual score.",
        "",
    ]
    changed_row_count = 0
    changed_cell_count = 0
    for section in ("gas_species", "condensed_phases"):
        lines.extend([f"## {section}", ""])
        old_rows = previous.get(section) or {}
        new_rows = generated.get(section) or {}
        for species, new_entry in new_rows.items():
            old_entry = old_rows.get(species) or {}
            old_values = {int(key): float(value) for key, value in (old_entry.get("log10_kf") or {}).items()}
            new_values = {int(key): float(value) for key, value in (new_entry.get("log10_kf") or {}).items()}
            changes = [
                (temperature, old_values.get(temperature), new_values.get(temperature))
                for temperature in sorted(set(old_values) | set(new_values))
                if old_values.get(temperature) != new_values.get(temperature)
            ]
            if not changes:
                continue
            changed_row_count += 1
            changed_cell_count += len(changes)
            old_source_match = re.search(r"JANAF ([A-Z][a-z]?-[0-9]+)", str(old_entry.get("source", "")))
            old_table_id = old_source_match.group(1) if old_source_match else "none"
            provenance = new_entry["provenance"]
            new_table_id = provenance["table_id"]
            if old_entry:
                reason = f"source {old_table_id} → {new_table_id}; source-grounded grid coverage and phase handling"
            else:
                reason = f"new JANAF carrier from {new_table_id}, required by the registry coverage ruling"
            if section == "condensed_phases" and species in LIQUID_TABLES:
                reason = (
                    f"use the ruled crystal/liquid table {new_table_id} across Tm; "
                    "the previous row was crystalline-only"
                )
            if species == "MgAl2O4":
                reason = (
                    "include the JANAF source point below Tm=2408 K and exclude the "
                    "crystalline-only continuation above Tm; retain a typed notice"
                )
            deltas = "; ".join(
                f"{temperature} K: "
                f"{'—' if old_value is None else f'{old_value:.3f}'} → "
                f"{'—' if new_value is None else f'{new_value:.3f}'}"
                for temperature, old_value, new_value in changes
            )
            valid = new_entry["valid_temperature_K"]
            lines.append(
                f"- **{species}**: {old_table_id} → {new_table_id}; "
                f"{old_entry.get('valid_temperature_K', 'absent')} → {valid} K. "
                f"{reason}. Values: {deltas}."
            )
        lines.append("")
    lines.extend(
        [
            "## Summary",
            "",
            f"Changed species rows: {changed_row_count}; changed/new temperature cells: {changed_cell_count}.",
            "Any changed shared-grid cells are listed with their old and new values above. NaO and KO were added from Na-008 and K-008. The Al2O3(g) gap remains an explicitly unmodeled, cited carrier; no NASA-only numeric row was in the requested registry set.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check freshness without writing")
    mode.add_argument("--report", type=Path, help="write the old-to-new value report")
    args = parser.parse_args(argv)

    generated = render_registry()
    if args.report:
        report = render_change_report(OUTPUT_PATH.read_text(encoding="utf-8"), generated)
        args.report.write_text(report, encoding="utf-8")
        print(f"Wrote {args.report}")
        return 0
    if args.check:
        if not OUTPUT_PATH.is_file() or OUTPUT_PATH.read_text(encoding="utf-8") != generated:
            print(f"stale generated registry: run {Path(__file__).relative_to(ROOT)}")
            return 1
        print(f"fresh: {OUTPUT_PATH.relative_to(ROOT)}")
        return 0
    OUTPUT_PATH.write_text(generated, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
