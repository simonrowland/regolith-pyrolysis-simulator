#!/usr/bin/env python3
"""Validate the generated VapoRock 0004 gas table against its two source tables.

The engine checks run the pinned VapoRock package in an isolated subprocess:
Vapor(database="JANAF") followed by eval_gibbs_species(np.array([T])) evaluates
the entire species set through the simulator-configured selector. JANAF grid
values are read from the simulator's hash-verified compilation.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

FIELDS = (
    "species_name",
    "state",
    "T_interval",
    "cation",
    "cat_num",
    "oxy_num",
    "T_min",
    "T_max",
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
    "G",
    "H",
    "Ref",
)
GAS_CSV_PATH = "src/vaporock/data/JANAF-vapor-data-full.csv"
PATCH_PATH = Path(__file__).resolve().parents[1] / "0004-gas-rows-from-openimcc-janaf.patch"
EXCLUDED = {"Na2O(g)", "K2O(g)"}
GAP_CHOICES = {
    "CaO(g)": "base",
    "Fe(g)": "openimcc",
    "FeO(g)": "base",
    "MgO(g)": "base",
    "Si(g)": "openimcc",
    "SiO2(g)": "base",
    "Ti(g)": "base",
    "TiO(g)": "openimcc",
    "TiO2(g)": "openimcc",
}
EPSILON_K = 1e-6
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
ENGINE_PROGRAM = r"""
import json
import sys
import numpy as np
from vaporock.equil import Vapor

temperatures = json.loads(sys.stdin.read())
vapor = Vapor(database="JANAF")
payload = {"species": [str(name) for name in vapor.species_comps.index], "rows": []}
for temperature in temperatures:
    try:
        frame = vapor.eval_gibbs_species(np.array([float(temperature)]))
        payload["rows"].append({
            "temperature": float(temperature),
            "shape": list(frame.shape),
            "species": [str(name) for name in frame.index],
            "values": [float(value) for value in frame.iloc[:, 0].to_numpy()],
        })
    except Exception as exc:
        payload["rows"].append({
            "temperature": float(temperature),
            "error": f"{type(exc).__name__}: {exc}",
        })
print(json.dumps(payload, allow_nan=True))
"""


class Validation:
    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.lines: list[str] = []

    def check(self, code: str, ok: bool, message: str) -> None:
        self.lines.append(f"{code} {'PASS' if ok else 'FAIL'}: {message}")
        if not ok:
            self.errors.append((code, message))

    def fail(self, code: str, message: str) -> None:
        self.check(code, False, message)


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    payload = path.read_bytes()
    if b"\r" in payload:
        raise ValueError(f"{path}: CSV must use clean LF endings")
    text = payload.decode("utf-8")
    physical_lines = text.splitlines()
    parsed = list(csv.reader(io.StringIO(text, newline="")))
    if not parsed or parsed[0] != list(FIELDS):
        raise ValueError(f"{path}: unexpected gas CSV header")
    if len(parsed) != len(physical_lines):
        raise ValueError(f"{path}: multiline CSV records are unsupported")
    rows: list[dict[str, str]] = []
    raw_lines: list[str] = []
    for offset, fields in enumerate(parsed[1:]):
        if len(fields) != len(FIELDS):
            raise ValueError(f"{path}: line {offset + 2} has {len(fields)} columns")
        rows.append(dict(zip(FIELDS, fields)))
        raw_lines.append(physical_lines[offset + 1])
    return rows, raw_lines


def group_rows(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["species_name"]].append(row)
    return dict(grouped)


def patch_paths(path: Path) -> list[str]:
    paths = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) != 4 or not parts[2].startswith("a/") or not parts[3].startswith("b/"):
            raise ValueError(f"{path}: malformed diff header: {line}")
        before, after = parts[2][2:], parts[3][2:]
        if before != after:
            raise ValueError(f"{path}: diff changes path {before} to {after}")
        paths.append(after)
    return paths


def numeric(row: dict[str, str], field: str) -> Decimal:
    return Decimal(row[field])


def check_i2(groups: dict[str, list[dict[str, str]]]) -> list[str]:
    problems: list[str] = []
    for species, rows in sorted(groups.items()):
        intervals: list[tuple[Decimal, Decimal, int]] = []
        for index, row in enumerate(rows, 1):
            try:
                lower = numeric(row, "T_min")
                upper = numeric(row, "T_max")
            except Exception as exc:
                problems.append(f"{species}: invalid bound: {exc}")
                continue
            if lower >= upper:
                problems.append(f"{species}: row {index} is not strict ({lower}..{upper})")
            intervals.append((lower, upper, index))
        if len(intervals) != len(rows):
            continue
        if intervals != sorted(intervals, key=lambda item: (item[0], item[1])):
            problems.append(f"{species}: rows are not sorted by T_min/T_max")
        for left, right in zip(intervals, intervals[1:]):
            if left[1] > right[0]:
                problems.append(
                    f"{species}: overlap rows {left[2]}/{right[2]} at {right[0]} K"
                )
            elif left[1] < right[0]:
                problems.append(
                    f"{species}: gap rows {left[2]}/{right[2]} ({left[1]}..{right[0]} K)"
                )
        minimum = min(item[0] for item in intervals)
        maximum = max(item[1] for item in intervals)
        if sum(item[0] == minimum for item in intervals) != 1:
            problems.append(f"{species}: minimum T_min is not unique ({minimum} K)")
        if sum(item[1] == maximum for item in intervals) != 1:
            problems.append(f"{species}: maximum T_max is not unique ({maximum} K)")
    return problems


def raw_species_lines(
    rows: list[dict[str, str]],
    raw_lines: list[str],
    species: str,
) -> list[str]:
    return [
        raw
        for row, raw in zip(rows, raw_lines)
        if row["species_name"] == species
    ]


def build_probes(
    base_groups: dict[str, list[dict[str, str]]],
    candidate_groups: dict[str, list[dict[str, str]]],
    source_groups: dict[str, list[dict[str, str]]],
    janaf_points: dict[str, dict[float, float]],
) -> list[float]:
    probes: set[float] = {float(temperature) for temperature in range(300, 6001, 50)}
    for groups in (base_groups, candidate_groups):
        for rows in groups.values():
            for row in rows:
                lower = float(row["T_min"])
                upper = float(row["T_max"])
                probes.add((lower + upper) / 2)
                for boundary in (lower, upper):
                    probes.add(boundary)
                    if boundary - EPSILON_K > 0:
                        probes.add(boundary - EPSILON_K)
                    if boundary + EPSILON_K > 0:
                        probes.add(boundary + EPSILON_K)
    for rows in source_groups.values():
        for row in rows:
            lower, upper = float(row["T_min"]), float(row["T_max"])
            width = upper - lower
            for fraction in (0.25, 0.5, 0.75, 1.0):
                probes.add(lower + width * fraction)
            probes.add(lower + min(EPSILON_K, width / 1000.0))
    for points in janaf_points.values():
        probes.update(points)
    return sorted(value for value in probes if value > 0 and math.isfinite(value))


def vaporock_package(source: Path) -> Path:
    source = source.resolve()
    candidates = (source, source / "vaporock", source / "src" / "vaporock")
    for candidate in candidates:
        if (candidate / "equil.py").is_file() and (candidate / "data").is_dir():
            return candidate
    raise ValueError(f"{source}: cannot locate VapoRock's vaporock package")


def evaluate_engine(
    label: str,
    vaporock_src: Path,
    csv_path: Path,
    temperatures: list[float],
    expected_species: set[str],
) -> tuple[dict[float, dict[str, float]], list[tuple[float, str]]]:
    package = vaporock_package(vaporock_src)
    values: dict[float, dict[str, float]] = {}
    problems: list[tuple[float, str]] = []
    with tempfile.TemporaryDirectory(prefix="validate-0004-") as temporary:
        temp_root = Path(temporary)
        site = temp_root / "site"
        staged_package = site / "vaporock"
        site.mkdir()
        shutil.copytree(package, staged_package)
        shutil.copyfile(csv_path, staged_package / "data" / "JANAF-vapor-data-full.csv")
        mplconfig = Path(tempfile.gettempdir()) / "vaporock-gas-0004-mplconfig"
        mplconfig.mkdir(exist_ok=True)
        env = os.environ.copy()
        env["MPLCONFIGDIR"] = str(mplconfig)
        prior_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(site) + (os.pathsep + prior_pythonpath if prior_pythonpath else "")
        process = subprocess.run(
            [sys.executable, "-c", ENGINE_PROGRAM],
            input=json.dumps(temperatures),
            text=True,
            capture_output=True,
            env=env,
            cwd=REPO_ROOT,
            check=False,
        )
        if process.returncode:
            detail = " ".join(process.stderr.strip().splitlines()[-4:])
            return {}, [
                (
                    math.nan,
                    f"{label} engine subprocess exited {process.returncode}: {detail}",
                )
            ]
        try:
            payload = json.loads(process.stdout.strip().splitlines()[-1])
        except Exception as exc:
            return {}, [
                (temperature, f"{label} engine returned invalid output: {exc}")
                for temperature in temperatures
            ]
        engine_species = payload.get("species", [])
        if set(engine_species) != expected_species or len(engine_species) != len(expected_species):
            problems.append(
                (math.nan, f"{label} engine species do not match CSV "
                 f"(missing={sorted(expected_species - set(engine_species))}, "
                 f"extra={sorted(set(engine_species) - expected_species)})")
            )
        for item in payload.get("rows", []):
            temperature = float(item["temperature"])
            if "error" in item:
                problems.append((temperature, str(item["error"])))
                continue
            index = item.get("species", [])
            shape = item.get("shape", [])
            row_values = item.get("values", [])
            if shape != [len(expected_species), 1] or len(index) != len(expected_species) or len(set(index)) != len(expected_species):
                problems.append(
                    (temperature, f"{label} returned shape {shape} / {len(index)} species rows")
                )
                continue
            if set(index) != expected_species or len(row_values) != len(index):
                problems.append((temperature, f"{label} returned an incomplete species result"))
                continue
            mapping = dict(zip(index, row_values))
            nonfinite = [name for name, value in mapping.items() if not math.isfinite(float(value))]
            if nonfinite:
                problems.append((temperature, f"{label} returned non-finite values for {nonfinite}"))
                continue
            values[temperature] = {name: float(value) for name, value in mapping.items()}
    return values, problems


def load_janaf_points(
    base_groups: dict[str, list[dict[str, str]]],
    source_groups: dict[str, list[dict[str, str]]],
    tables_dir: Path,
) -> tuple[dict[str, dict[float, float]], list[str]]:
    from simulator.melt_backend.pure_phase_janaf_score import janaf_values_at
    from simulator.reference_data.janaf import load_table_document

    refs_by_species: dict[str, set[str]] = defaultdict(set)
    for species, rows in source_groups.items():
        if species not in EXCLUDED:
            refs_by_species[species].update(row["Ref"] for row in rows)
    for species, rows in base_groups.items():
        if species in refs_by_species:
            continue
        refs_by_species[species].update(
            row["Ref"]
            for row in rows
            if (tables_dir / f"{row['Ref']}.yaml").is_file()
        )

    documents: dict[str, Any] = {}
    errors: list[str] = []
    for refs in refs_by_species.values():
        for reference in refs:
            path = tables_dir / f"{reference}.yaml"
            if not path.is_file():
                errors.append(f"missing hash-verified JANAF table {path}")
                continue
            try:
                documents[reference] = load_table_document(path)
            except Exception as exc:
                errors.append(f"cannot load JANAF table {path}: {exc}")

    points_by_species: dict[str, dict[float, float]] = {}
    for species, refs in refs_by_species.items():
        points: dict[float, float] = {}
        for reference in sorted(refs):
            document = documents.get(reference)
            if document is None:
                continue
            reference_298 = janaf_values_at(document, 298.15)
            if (
                reference_298 is None
                or reference_298.formation_enthalpy_kJ_mol is None
            ):
                errors.append(f"{species}: {reference} has no JANAF ΔfH°(298.15 K)")
                continue
            formation_enthalpy_298 = reference_298.formation_enthalpy_kJ_mol
            for row in document.get("table", {}).get("values", []):
                temperature_value = (row.get("temperature") or {}).get("value")
                if temperature_value is None:
                    continue
                temperature = float(temperature_value)
                if not 300 <= temperature <= 6000:
                    continue
                values = janaf_values_at(document, temperature)
                if values is None:
                    continue
                fields = (values.enthalpy_increment_kJ_mol, values.S_J_K_mol)
                if any(value is None for value in fields):
                    continue
                gibbs = (
                    (formation_enthalpy_298 + values.enthalpy_increment_kJ_mol)
                    * 1000.0
                    - temperature * values.S_J_K_mol
                )
                if temperature in points and points[temperature] != gibbs:
                    errors.append(
                        f"{species}: JANAF references disagree at {temperature:g} K"
                    )
                points[temperature] = gibbs
        if points:
            points_by_species[species] = points
    return points_by_species, errors


def gap_ranges(
    base_groups: dict[str, list[dict[str, str]]],
    source_groups: dict[str, list[dict[str, str]]],
) -> dict[str, tuple[float, float]]:
    result: dict[str, tuple[float, float]] = {}
    for species in set(GAP_CHOICES) & set(base_groups) & set(source_groups):
        source_top = max(float(row["T_max"]) for row in source_groups[species])
        high_starts = [
            float(row["T_min"])
            for row in base_groups[species]
            if float(row["T_min"]) > source_top
        ]
        if len(high_starts) == 1:
            result[species] = (source_top, high_starts[0])
    return result


def outside_imported_area(
    species: str,
    temperature: float,
    source_groups: dict[str, list[dict[str, str]]],
    gaps: dict[str, tuple[float, float]],
) -> bool:
    rows = source_groups.get(species)
    if rows:
        low = min(float(row["T_min"]) for row in rows)
        high = max(float(row["T_max"]) for row in rows)
        if low <= temperature <= high:
            return False
    gap = gaps.get(species)
    if gap:
        gap_low, gap_high = gap
        gap_choice = GAP_CHOICES[species]
        if gap_low < temperature < gap_high:
            return False
        if gap_choice == "openimcc" and gap_low < temperature <= gap_high:
            return False
    return True


def pointwise_identity(
    base_values: dict[float, dict[str, float]],
    candidate_values: dict[float, dict[str, float]],
    base_species: set[str],
    candidate_species: set[str],
    source_groups: dict[str, list[dict[str, str]]],
    gaps: dict[str, tuple[float, float]],
) -> tuple[int, list[str]]:
    count = 0
    differences: list[str] = []
    for temperature in sorted(set(base_values) & set(candidate_values)):
        for species in sorted(base_species & candidate_species):
            if not outside_imported_area(species, temperature, source_groups, gaps):
                continue
            before = base_values[temperature][species]
            after = candidate_values[temperature][species]
            count += 1
            if struct.pack(">d", before) != struct.pack(">d", after):
                differences.append(
                    f"{species} at {temperature:.9g} K: base={before:.17g}, patched={after:.17g}"
                )
    return count, differences


def imported_identity(
    candidate_values: dict[float, dict[str, float]],
    source_groups: dict[str, list[dict[str, str]]],
    openimcc_src: Path,
) -> tuple[int, float, list[str]]:
    sys.path.insert(0, str(openimcc_src.resolve()))
    try:
        from openimcc.gas import _janaf_gibbs
    except Exception as exc:
        return 0, math.inf, [f"cannot import openimcc G_app: {exc}"]
    checked = 0
    max_delta = 0.0
    differences: list[str] = []
    for species, rows in sorted(source_groups.items()):
        if species in EXCLUDED:
            continue
        for row in rows:
            lower, upper = float(row["T_min"]), float(row["T_max"])
            width = upper - lower
            temperatures = [
                lower + min(EPSILON_K, width / 1000.0),
                lower + width * 0.25,
                lower + width * 0.5,
                lower + width * 0.75,
                upper,
            ]
            for temperature in dict.fromkeys(temperatures):
                actual = candidate_values.get(temperature, {}).get(species)
                if actual is None:
                    differences.append(f"{species} at {temperature:g} K has no engine value")
                    continue
                coefficients = {
                    name: float(row[name]) for name in ("A", "B", "C", "D", "E", "F", "G", "H")
                }
                expected = float(_janaf_gibbs(temperature, coefficients))
                delta = abs(actual - expected)
                max_delta = max(max_delta, delta)
                checked += 1
                if delta > 1e-6:
                    differences.append(
                        f"{species} at {temperature:g} K: |Δ|={delta:.12g} J/mol"
                    )
    return checked, max_delta, differences


def fidelity_report(
    reference_points: dict[str, dict[float, float]],
    base_values: dict[float, dict[str, float]],
    candidate_values: dict[float, dict[str, float]],
    base_species: set[str],
    candidate_species: set[str],
) -> tuple[list[str], list[str], int, list[str]]:
    summaries: list[str] = []
    worsenings: list[str] = []
    problems: list[str] = []
    compared_points = 0
    zones = (
        ("300–<500 K", lambda temperature: 300 <= temperature < 500),
        ("500–3000 K", lambda temperature: 500 <= temperature <= 3000),
        (">3000–6000 K", lambda temperature: 3000 < temperature <= 6000),
    )
    for species in sorted(candidate_species):
        points = reference_points.get(species, {})
        if not points:
            if species in base_species:
                summaries.append(
                    f"{species}: no table id in base Ref; unchanged identity is covered by I4"
                )
            continue
        if species not in base_species:
            after_by_zone: dict[str, list[float]] = {name: [] for name, _ in zones}
            for temperature, janaf_g in sorted(points.items()):
                after = candidate_values.get(temperature, {}).get(species)
                if after is None:
                    problems.append(
                        f"{species} at {temperature:g} K lacks a real engine evaluation"
                    )
                    continue
                error = abs(after - janaf_g)
                for name, belongs in zones:
                    if belongs(temperature):
                        after_by_zone[name].append(error)
                        break
            for zone, _ in zones:
                entries = after_by_zone[zone]
                if entries:
                    summaries.append(
                        f"{species} {zone}: no base→{max(entries):.6f} J/mol "
                        f"({len(entries)} points; new species)"
                    )
            continue
        species_values: dict[str, list[tuple[float, float, float]]] = {
            name: [] for name, _ in zones
        }
        for temperature, janaf_g in sorted(points.items()):
            before = base_values.get(temperature, {}).get(species)
            after = candidate_values.get(temperature, {}).get(species)
            if before is None or after is None:
                problems.append(f"{species} at {temperature:g} K lacks a real engine evaluation")
                continue
            before_error = abs(before - janaf_g)
            after_error = abs(after - janaf_g)
            compared_points += 1
            for name, belongs in zones:
                if belongs(temperature):
                    species_values[name].append((temperature, before_error, after_error))
                    if after_error > before_error:
                        worsenings.append(
                            f"{species} {temperature:g} K: {before_error:.6f}→{after_error:.6f} "
                            f"J/mol (+{after_error - before_error:.6f})"
                        )
                    break
        for zone, _ in zones:
            entries = species_values[zone]
            if not entries:
                continue
            before_max = max(item[1] for item in entries)
            after_max = max(item[2] for item in entries)
            summaries.append(
                f"{species} {zone}: {before_max:.6f}→{after_max:.6f} J/mol "
                f"({len(entries)} points)"
            )
            if after_max > before_max + 1e-9:
                problems.append(
                    f"{species} {zone} JANAF maximum worsened "
                    f"{before_max:.6f}→{after_max:.6f} J/mol"
                )
    return summaries, worsenings, compared_points, problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-csv", required=True, type=Path)
    parser.add_argument("--candidate-csv", required=True, type=Path)
    parser.add_argument("--openimcc-csv", required=True, type=Path)
    parser.add_argument("--vaporock-src", required=True, type=Path)
    parser.add_argument(
        "--candidate-vaporock-src",
        type=Path,
        help="patched VapoRock source tree; defaults to --vaporock-src",
    )
    parser.add_argument("--openimcc-src", required=True, type=Path)
    parser.add_argument(
        "--janaf-tables",
        type=Path,
        default=REPO_ROOT / "data/literature/compilations/janaf/tables",
    )
    args = parser.parse_args()
    report = Validation()
    try:
        base_rows, base_raw = read_csv(args.base_csv)
        candidate_rows, candidate_raw = read_csv(args.candidate_csv)
        source_rows, _ = read_csv(args.openimcc_csv)
        touched_paths = patch_paths(PATCH_PATH)
    except (OSError, UnicodeError, ValueError, csv.Error) as exc:
        print(f"FAIL INPUT: {exc}")
        return 2
    base_groups = group_rows(base_rows)
    candidate_groups = group_rows(candidate_rows)
    source_groups = group_rows(source_rows)
    base_species = set(base_groups)
    imported_groups = {
        species: rows
        for species, rows in source_groups.items()
        if species not in EXCLUDED and species in base_species
    }
    candidate_species = set(candidate_groups)
    report.check(
        "I7",
        touched_paths == [GAS_CSV_PATH] and candidate_species == base_species,
        f"patch files={touched_paths}, expected={[GAS_CSV_PATH]}; "
        f"base species={len(base_species)}, candidate species={len(candidate_species)}, "
        f"added={sorted(candidate_species - base_species)}, "
        f"deleted={sorted(base_species - candidate_species)}",
    )
    missing = sorted(base_species - candidate_species)
    untouched = sorted(base_species - set(imported_groups))
    untouched_differences = [
        species
        for species in untouched
        if raw_species_lines(base_rows, base_raw, species)
        != raw_species_lines(candidate_rows, candidate_raw, species)
    ]
    report.check(
        "I1",
        not missing and not untouched_differences,
        f"base species={len(base_species)}, patched species={len(candidate_species)}, "
        f"missing={missing}, byte-changed untouched={untouched_differences}",
    )

    i2_problems = check_i2(candidate_groups)
    report.check("I2", not i2_problems, f"species={len(candidate_species)}, structural errors={len(i2_problems)}")
    for problem in i2_problems:
        report.lines.append(f"  I2 detail: {problem}")

    janaf_points, reference_errors = load_janaf_points(
        base_groups, imported_groups, args.janaf_tables.resolve()
    )
    for error in reference_errors:
        report.fail("I6", error)
    probes = build_probes(base_groups, candidate_groups, imported_groups, janaf_points)
    base_values, base_engine_errors = evaluate_engine(
        "base",
        args.vaporock_src,
        args.base_csv,
        probes,
        base_species,
    )
    candidate_values, candidate_engine_errors = evaluate_engine(
        "patched",
        args.candidate_vaporock_src or args.vaporock_src,
        args.candidate_csv,
        probes,
        candidate_species,
    )
    engine_errors = [("base", *failure) for failure in base_engine_errors]
    engine_errors.extend(("patched", *failure) for failure in candidate_engine_errors)
    report.check(
        "I3",
        not engine_errors,
        f"whole-species Vapor.eval_gibbs_species calls={2 * len(probes)} "
        f"({len(probes)} temperatures per table), probes={len(probes)}, "
        f"base failures={len(base_engine_errors)}, patched failures={len(candidate_engine_errors)}",
    )
    for label, temperature, error in engine_errors[:12]:
        temp_text = "species-set" if math.isnan(temperature) else f"{temperature:.9g} K"
        report.lines.append(f"  I3 detail: {label} {temp_text}: {error}")
    if len(engine_errors) > 12:
        report.lines.append(f"  I3 detail: {len(engine_errors) - 12} additional failures omitted")

    gaps = gap_ranges(base_groups, imported_groups)
    identity_count, identity_errors = pointwise_identity(
        base_values,
        candidate_values,
        base_species,
        candidate_species,
        imported_groups,
        gaps,
    )
    report.check(
        "I4",
        not identity_errors,
        f"bit-identical comparisons={identity_count}, mismatches={len(identity_errors)}, "
        f"excluded declared gaps={len(gaps)}",
    )
    for difference in identity_errors[:12]:
        report.lines.append(f"  I4 detail: {difference}")
    if len(identity_errors) > 12:
        report.lines.append(f"  I4 detail: {len(identity_errors) - 12} additional mismatches omitted")

    imported_count, imported_max_delta, imported_errors = imported_identity(
        candidate_values,
        imported_groups,
        args.openimcc_src,
    )
    report.check(
        "I5",
        not imported_errors and imported_count > 0,
        f"imported-row G_app comparisons={imported_count}, max |Δ|={imported_max_delta:.12g} J/mol",
    )
    for difference in imported_errors[:12]:
        report.lines.append(f"  I5 detail: {difference}")
    if len(imported_errors) > 12:
        report.lines.append(f"  I5 detail: {len(imported_errors) - 12} additional mismatches omitted")

    try:
        fidelity_summaries, worsenings, janaf_count, fidelity_errors = fidelity_report(
            janaf_points, base_values, candidate_values, base_species, candidate_species
        )
        report.check(
            "I6",
            not fidelity_errors and bool(janaf_points),
            f"JANAF grid comparisons={janaf_count}, species with references="
            f"{sum(bool(points) for points in janaf_points.values())}, "
            f"pointwise worsenings={len(worsenings)}",
        )
        for summary in fidelity_summaries:
            report.lines.append(f"  I6 zone: {summary}")
        for worsening in worsenings:
            report.lines.append(f"  I6 worsening: {worsening}")
        for error in fidelity_errors[:12]:
            report.lines.append(f"  I6 detail: {error}")
        if len(fidelity_errors) > 12:
            report.lines.append(
                f"  I6 detail: {len(fidelity_errors) - 12} additional failures omitted"
            )
    except Exception as exc:
        report.fail("I6", f"JANAF fidelity calculation failed: {type(exc).__name__}: {exc}")

    print("\n".join(report.lines))
    if report.errors:
        print(f"FAIL: {len(report.errors)} validation condition(s) failed")
        return 1
    changed = sorted(set(imported_groups))
    new_species = sorted(set(candidate_species) - base_species)
    declared_gains = []
    for species in changed:
        source_low = min(float(row["T_min"]) for row in imported_groups[species])
        source_high = max(float(row["T_max"]) for row in imported_groups[species])
        overlaps = any(
            float(row["T_min"]) < source_high and float(row["T_max"]) > source_low
            for row in base_groups.get(species, [])
        )
        if not overlaps:
            declared_gains.append(species)
    print(
        f"Species: changed={len(changed)}, untouched={len(untouched)}, "
        f"new-to-table={len(new_species)}, declared-window coverage gains={len(declared_gains)}"
    )
    print(f"  changed: {', '.join(changed)}")
    print(f"  untouched: {', '.join(untouched)}")
    print(f"  new-to-table: {', '.join(new_species)}")
    print(f"  declared-window coverage gains: {', '.join(declared_gains)}")
    print(
        f"PASS: {len(candidate_species)} species / {2 * len(probes)} whole-set evaluations; "
        "I1–I7 satisfied"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
