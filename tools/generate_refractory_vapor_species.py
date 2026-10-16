#!/usr/bin/env python3
"""Generate the refractory-vapor registry from held thermochemical compilations."""

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
from simulator.reference_data.nasa_glenn import load_record_document  # noqa: E402
from simulator.vapour_rail.nasa_cea import Nasa9Segment  # noqa: E402
from simulator.yaml_cache import load_cached_safe_yaml  # noqa: E402

OUTPUT_PATH = ROOT / "data" / "refractory_vapor_species.yaml"
OVERLAY_PATH = ROOT / "data" / "refractory_vapor_species_overlay.yaml"
TABLES_DIR = ROOT / "data" / "literature" / "compilations" / "janaf" / "tables"
NASA_RECORDS_DIR = ROOT / "data" / "literature" / "compilations" / "nasa-glenn" / "records"
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

# JANAF has no Al2O3(g) formation table. Its numeric carrier is derived from
# NASA Glenn record NG-0071, using the same elemental reference phases as the
# JANAF rows. No other JANAF-missing species is included as a numeric carrier.

# Premise -> algebra -> units -> sanity check:
# JANAF's log Kf column is for formation from the table's elemental reference
# states. ΔfG° = -R T ln(Kf), hence log10(Kf) = -ΔfG°/(R T ln(10)). JANAF's
# ΔfG° values are kJ/mol, so the algebraic check converts them by 1000 J/kJ;
# R is J/(mol·K), T is K, and log10(Kf) is dimensionless. The generated value
# is the source's printed log Kf token, not a recomputation or fitted value.
# The freshness test compares every selected JANAF species' printed values at
# both 2000 K and 2500 K; those checks are the per-species source sanity anchors.

# NASA fallback derivation (premise -> algebra -> units -> sanity check):
# Premise: NG-0071 is Al2O3(g); NG-1273 is Al(l), and NG-0937 is O2(g). These
# are the same elemental reference phases used by JANAF for Al(g) formation
# above Al's melting point and O2(g). Check that alignment independently with
# NG-0005 (Al(g)): its NASA-9 log10(Kf) from Al(l) is -2.225958 at
# 2000 K and -0.657470 at 2500 K, versus JANAF Al-005's printed -2.211 and
# -0.644. Algebra: ΔG°/(RT) = G°(Al2O3,g)/(RT) - 2 G°(Al,l)/(RT)
#   - 3/2 G°(O2,g)/(RT); log10(Kf) = -ΔG°/(RT ln(10)). Units: each NASA-9
# G/RT and the resulting log10(Kf) are dimensionless; T is in K, so R and T
# cancel in the formation ratio. Sanity: source-derived Al2O3(g) log10(Kf) is
# 10.892022 at 2000 K.


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


def _load_nasa9_record(
    record_id: str,
) -> tuple[dict[str, Any], tuple[Nasa9Segment, ...]]:
    path = NASA_RECORDS_DIR / f"{record_id}.json"
    if not path.is_file():
        raise RefractoryGenerationError(f"missing NASA Glenn record {record_id}: {path}")
    document = load_record_document(path)
    if (
        document.get("source_id") != "nasa-glenn"
        or document.get("record_id") != record_id
    ):
        raise RefractoryGenerationError(f"{path}: NASA Glenn record identity mismatch")
    intervals = document.get("intervals") or []
    if not intervals or any(
        interval.get("coefficient_count") != 9 for interval in intervals
    ):
        raise RefractoryGenerationError(f"{path}: expected NASA-9 coefficients")
    if document.get("phase") not in {"gas", "L", "l", "liquid"}:
        raise RefractoryGenerationError(f"{path}: unsupported NASA reference phase")
    segments: list[Nasa9Segment] = []
    for interval in intervals:
        try:
            segments.append(
                Nasa9Segment(
                    float(interval["T_min_K"]["value"]),
                    float(interval["T_max_K"]["value"]),
                    tuple(float(item["value"]) for item in interval["a_coefficients"]),
                    float(interval["b1"]["value"]),
                    float(interval["b2"]["value"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RefractoryGenerationError(
                f"{path}: invalid NASA-9 interval: {exc}"
            ) from exc
    if not segments:
        raise RefractoryGenerationError(f"{path}: no NASA-9 intervals")
    return dict(document), tuple(segments)


def _source_temperature_range(document: Mapping[str, Any]) -> list[float]:
    intervals = document.get("intervals") or []
    if not intervals:
        raise RefractoryGenerationError(
            f"{document.get('record_id', 'NASA record')}: no source intervals"
        )
    return [
        min(float(interval["T_min_K"]["value"]) for interval in intervals),
        max(float(interval["T_max_K"]["value"]) for interval in intervals),
    ]


def _nasa9_g_over_rt(
    segments: tuple[Nasa9Segment, ...], temperature_K: int
) -> float:
    matching = [
        segment
        for segment in segments
        if segment.T_min_K <= temperature_K <= segment.T_max_K
    ]
    if len(matching) != 1:
        raise RefractoryGenerationError(
            f"NASA-9 intervals cover {temperature_K} K {len(matching)} times"
        )
    return matching[0].evaluate_ratios(float(temperature_K))[3]


def _nasa_al2o3_gas_row() -> dict[str, Any]:
    product_doc, product_segments = _load_nasa9_record("NG-0071")
    aluminum_doc, aluminum_segments = _load_nasa9_record("NG-1273")
    oxygen_doc, oxygen_segments = _load_nasa9_record("NG-0937")
    log_kf = {
        temperature: -(
            _nasa9_g_over_rt(product_segments, temperature)
            - 2.0 * _nasa9_g_over_rt(aluminum_segments, temperature)
            - 1.5 * _nasa9_g_over_rt(oxygen_segments, temperature)
        ) / math.log(10.0)
        for temperature in TEMPERATURE_GRID_K
    }
    intervals = product_doc["intervals"]
    return {
        "formula": str(product_doc["formula"]),
        "source": "NASA Glenn NG-0071, AL2O3 (NASA-9)",
        "source_url": str(product_doc["source"]["official_url"]),
        "valid_temperature_K": [
            float(TEMPERATURE_GRID_K[0]),
            float(TEMPERATURE_GRID_K[-1]),
        ],
        "provenance": {
            "source_tag": "nasa_glenn",
            "record_id": str(product_doc["record_id"]),
            "species_as_published": str(product_doc["name_as_published"]),
            "coefficient_set": "NASA-9",
            "coefficient_intervals_K": [
                [
                    float(interval["T_min_K"]["value"]),
                    float(interval["T_max_K"]["value"]),
                ]
                for interval in intervals
            ],
            "source_temperature_range_K": _source_temperature_range(product_doc),
            "phase": str(product_doc["phase"]),
            "element_reference_states": {
                "Al": {
                    "record_id": str(aluminum_doc["record_id"]),
                    "species_as_published": str(aluminum_doc["name_as_published"]),
                    "phase": str(aluminum_doc["phase"]),
                    "coefficient_set": "NASA-9",
                    "source_temperature_range_K": _source_temperature_range(aluminum_doc),
                },
                "O2": {
                    "record_id": str(oxygen_doc["record_id"]),
                    "species_as_published": str(oxygen_doc["name_as_published"]),
                    "phase": str(oxygen_doc["phase"]),
                    "coefficient_set": "NASA-9",
                    "source_temperature_range_K": _source_temperature_range(oxygen_doc),
                },
            },
            "derivation": (
                "For 2 Al(l) + 3/2 O2(g) -> Al2O3(g), evaluate the NASA-9 "
                "dimensionless G/RT for NG-0071, NG-1273 and NG-0937. Form "
                "ΔG°/(RT) = G°(Al2O3,g)/(RT) - 2 G°(Al,l)/(RT) - "
                "3/2 G°(O2,g)/(RT), then log10(Kf) = -ΔG°/(RT ln 10). "
                "All ratios and log10(Kf) are dimensionless; T is in K. The "
                "Al(l)+O2(g) reference phases match JANAF's elemental states."
            ),
        },
        "log10_kf": log_kf,
    }


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
            "source_tag": "nist_janaf",
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
            "database": (
                "NIST-JANAF Thermochemical Tables, Fourth Edition, SRD 13; "
                "NASA Glenn / CEA thermo.inp"
            ),
            "doi": "10.18434/T42S31",
            "note": (
                "JANAF rows copy compiled as_published log10(Kf) cells; Al2O3(g) "
                "is derived from NASA Glenn NASA-9 G/RT values against Al(l) and "
                "O2(g) elemental reference states. All rows use the 100 K grid "
                "from 1500 through 3500 K. "
                "The crystalline-only MgAl2O4 row stops below its 2408 K transition; "
                "its typed out-of-band notice records the missing liquid table."
            ),
        },
        "overlay_provenance": overlay["overlay_citation"],
        "gas_species": {
            **{
                species: _registry_row(species, table_id, condensed=False)
                for species, table_id in GAS_TABLES.items()
            },
            "Al2O3": _nasa_al2o3_gas_row(),
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
        "JANAF rows use printed log Kf cells; NASA Glenn rows use the tagged source derivation in their provenance.",
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
            old_source = old_source_match.group(1) if old_source_match else "none"
            old_provenance = old_entry.get("provenance") or {}
            old_source_tag = old_provenance.get("source_tag", "none")
            provenance = new_entry["provenance"]
            new_source_tag = provenance["source_tag"]
            new_source = provenance.get("table_id", provenance.get("record_id", "unknown"))
            if old_entry:
                reason = (
                    f"source {old_source_tag} {old_source} → "
                    f"{new_source_tag} {new_source}; source-grounded grid coverage and phase handling"
                )
            else:
                reason = (
                    f"new {new_source_tag} carrier from {new_source}, "
                    "required by the registry coverage ruling"
                )
            if section == "condensed_phases" and species in LIQUID_TABLES:
                reason = (
                    f"use the ruled crystal/liquid table {new_source} across Tm; "
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
                f"- **{species}** ({old_source_tag} → {new_source_tag}): "
                f"{old_source} → {new_source}; "
                f"{old_entry.get('valid_temperature_K', 'absent')} → {valid} K. "
                f"{reason}. Values: {deltas}."
            )
        lines.append("")
    lines.extend(
        [
            "## Summary",
            "",
            f"Changed species rows: {changed_row_count}; changed/new temperature cells: {changed_cell_count}.",
            "Any changed shared-grid cells are listed with their old and new values above. NaO and KO were added from JANAF Na-008 and K-008. Al2O3(g) was added from NASA Glenn NG-0071 because JANAF has no species table; no other JANAF-missing species was needed as a numeric row. Other material metadata gaps remain CaAlO, MgAlO and Si2O2, for which no coefficient source is currently included in this registry.",
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
