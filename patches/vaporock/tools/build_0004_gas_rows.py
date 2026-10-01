#!/usr/bin/env python3
"""Build patch 0004's gas CSV from the series-0001--0003 base and openimcc.

The imported window is the union of each in-scope species' openimcc intervals:
22 species have 500--1500 K and 1500--3000 K rows, 18 have only a 1500--3000 K
row, and Cr(g) has one 1500--2900 K row. Source intervals are sorted by their
temperature bounds and renumbered from one. A base interval crossing an
imported bound is split there: retain its coefficients below the first source
bound and above the last source bound, and use the openimcc coefficients only
on each imported interval. If a one-row base fit lies on both sides of the
import, it becomes distinct low and high rows; this avoids a zero-width row
while preserving the old effective fit outside the imported interval.

Nine species have a declared high-row start above the imported top and thus a
gap before that row. Fill each gap with whichever endpoint fit has the smaller
maximum absolute JANAF error on the published grid strictly inside the gap.
These choices and the measured errors (old high row / imported fit, J/mol) are:
CaO 105.097069 / 1310.101024 (old); Fe 1.138733 / 0.486690 (imported);
FeO 6.318010 / 39.982709 (old); MgO 3.110035 / 552.588095 (old);
Si 1.923349 / 1.334765 (imported); SiO2 2.264821 / 4.384827 (old);
Ti 1.482656 / 2.020629 (old); TiO 43.295557 / 30.000200 (imported);
TiO2 3.131162 / 1.880397 (imported). The values were evaluated against the
simulator's hash-verified NIST-JANAF compilation; seam size and battery
residuals did not select the fit. Na2O(g) and K2O(g) are excluded because their
openimcc provenance is LH84 plus NASA-Cp, not JANAF.

VapoRock's configured selector replaces every row at a species' minimum
T_min with 0 K and every row at its maximum T_max with 1e8 K. Generated rows
have strict, contiguous intervals with unique extrema, so only the first
interval receives the lower extension and only the last interval receives
the upper extension. Base-only species and their CSV lines are copied
byte-for-byte. This script reads exactly two data inputs: the base CSV and the
openimcc d2a7516 gas-shomate.csv; all gap choices above are fixed evidence.
"""

from __future__ import annotations

import argparse
import csv
import io
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

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
HORIZON_K = Decimal("6000")


@dataclass(frozen=True)
class CsvRow:
    values: dict[str, str]
    raw_line: str | None
    position: int


def parse_csv(path: Path) -> tuple[list[str], list[CsvRow]]:
    payload = path.read_bytes()
    if b"\r" in payload:
        raise ValueError(f"{path}: input must use LF line endings")
    text = payload.decode("utf-8")
    physical_lines = text.splitlines()
    parsed = list(csv.reader(io.StringIO(text, newline="")))
    if not parsed or parsed[0] != list(FIELDS):
        raise ValueError(f"{path}: unexpected gas CSV header")
    if len(parsed) != len(physical_lines):
        raise ValueError(f"{path}: multiline CSV records are not supported")
    rows = []
    for position, fields in enumerate(parsed[1:]):
        if len(fields) != len(FIELDS):
            raise ValueError(f"{path}: row {position + 2} has {len(fields)} columns")
        rows.append(CsvRow(dict(zip(FIELDS, fields)), physical_lines[position + 1], position))
    return list(FIELDS), rows


def decimal(row: CsvRow, key: str) -> Decimal:
    return Decimal(row.values[key])


def grouped(rows: list[CsvRow]) -> dict[str, list[CsvRow]]:
    result: dict[str, list[CsvRow]] = {}
    for row in rows:
        result.setdefault(row.values["species_name"], []).append(row)
    return result


def sorted_intervals(rows: list[CsvRow]) -> list[CsvRow]:
    return sorted(rows, key=lambda row: (decimal(row, "T_min"), decimal(row, "T_max"), row.position))


def check_contiguous(species: str, rows: list[CsvRow]) -> list[CsvRow]:
    ordered = sorted_intervals(rows)
    for row in ordered:
        if decimal(row, "T_min") >= decimal(row, "T_max"):
            raise ValueError(f"{species}: source interval is not strictly increasing")
    for left, right in zip(ordered, ordered[1:]):
        if decimal(left, "T_max") != decimal(right, "T_min"):
            raise ValueError(f"{species}: input intervals are not contiguous")
    return ordered


def effective_base_row(rows: list[CsvRow], temperature: Decimal, species: str) -> CsvRow:
    ordered = sorted_intervals(rows)
    minimum = min(decimal(row, "T_min") for row in ordered)
    maximum = max(decimal(row, "T_max") for row in ordered)
    selected = [
        row
        for row in ordered
        if temperature > (Decimal(0) if decimal(row, "T_min") == minimum else decimal(row, "T_min"))
        and temperature <= (
            Decimal("1e8") if decimal(row, "T_max") == maximum else decimal(row, "T_max")
        )
    ]
    if len(selected) != 1:
        raise ValueError(
            f"{species}: base selector chooses {len(selected)} rows at {temperature} K"
        )
    return selected[0]


def format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return str(int(normalized))
    return format(normalized, "f")


def serialize(values: dict[str, str], fields: list[str]) -> str:
    stream = io.StringIO(newline="")
    csv.writer(stream, lineterminator="\n").writerow([values[name] for name in fields])
    return stream.getvalue().removesuffix("\n")


def generated_species_rows(
    species: str, base_rows: list[CsvRow], source_rows: list[CsvRow]
) -> list[dict[str, str]]:
    source = check_contiguous(species, source_rows)
    if not base_rows:
        return [dict(row.values, T_interval=str(index)) for index, row in enumerate(source, 1)]

    base = check_contiguous(species, base_rows)
    source_low = decimal(source[0], "T_min")
    source_top = decimal(source[-1], "T_max")
    horizon = max(HORIZON_K, *(decimal(row, "T_max") for row in base))
    adjusted_source = [dict(row.values) for row in source]
    gap_end: Decimal | None = None

    if species in GAP_CHOICES:
        high_rows = [row for row in base if decimal(row, "T_min") > source_top]
        if len(high_rows) != 1 or GAP_CHOICES[species] not in {"base", "openimcc"}:
            raise ValueError(f"{species}: accepted JANAF gap no longer matches the base CSV")
        gap_end = decimal(high_rows[0], "T_min")
        if gap_end <= source_top:
            raise ValueError(f"{species}: invalid declared high-row gap")
        if GAP_CHOICES[species] == "openimcc":
            adjusted_source[-1]["T_max"] = format_decimal(gap_end)

    boundaries = {Decimal(0), horizon}
    for row in base:
        for key in ("T_min", "T_max"):
            value = decimal(row, key)
            if Decimal(0) < value < horizon:
                boundaries.add(value)
    for row in adjusted_source:
        for key in ("T_min", "T_max"):
            value = Decimal(row[key])
            if Decimal(0) < value < horizon:
                boundaries.add(value)
    if gap_end is not None and Decimal(0) < gap_end < horizon:
        boundaries.add(gap_end)

    ordered_boundaries = sorted(boundaries)
    segments: list[tuple[Decimal, Decimal, str, int, dict[str, str]]] = []
    for start, end in zip(ordered_boundaries, ordered_boundaries[1:]):
        midpoint = (start + end) / 2
        source_matches = [
            (index, row)
            for index, row in enumerate(adjusted_source)
            if midpoint > Decimal(row["T_min"]) and midpoint <= Decimal(row["T_max"])
        ]
        if len(source_matches) > 1:
            raise ValueError(f"{species}: overlapping imported intervals near {midpoint} K")
        if source_matches:
            origin, row = source_matches[0]
            kind = "openimcc"
            values = row
        else:
            row = effective_base_row(base, midpoint, species)
            origin = row.position
            kind = "base"
            values = row.values

        if segments and segments[-1][2:4] == (kind, origin):
            previous = segments[-1]
            segments[-1] = (previous[0], end, kind, origin, values)
        else:
            segments.append((start, end, kind, origin, values))

    output = []
    for interval, (start, end, kind, origin, selected) in enumerate(segments, 1):
        values = dict(selected)
        values["T_interval"] = str(interval)
        values["T_min"] = format_decimal(start)
        values["T_max"] = format_decimal(end)
        output.append(values)
    return output


def build(base_path: Path, source_path: Path) -> str:
    fields, base_rows = parse_csv(base_path)
    source_fields, source_rows = parse_csv(source_path)
    if fields != source_fields:
        raise ValueError("base and openimcc CSV schemas differ")
    base_groups = grouped(base_rows)
    source_groups = grouped(source_rows)
    targets = {
        species: check_contiguous(species, rows)
        for species, rows in source_groups.items()
        if species not in EXCLUDED
    }
    generated = {
        species: generated_species_rows(species, base_groups.get(species, []), rows)
        for species, rows in targets.items()
    }

    output_lines = [",".join(fields)]
    emitted: set[str] = set()
    for base_row in base_rows:
        species = base_row.values["species_name"]
        if species in targets:
            if species not in emitted:
                output_lines.extend(serialize(row, fields) for row in generated[species])
                emitted.add(species)
        else:
            assert base_row.raw_line is not None
            output_lines.append(base_row.raw_line)
    for species in sorted(set(targets) - set(base_groups)):
        output_lines.extend(serialize(row, fields) for row in generated[species])

    output_groups: dict[str, list[dict[str, str]]] = {}
    for line in output_lines[1:]:
        values = next(csv.reader([line]))
        row = dict(zip(fields, values))
        output_groups.setdefault(row["species_name"], []).append(row)
    missing = set(base_groups) - set(output_groups)
    if missing:
        raise ValueError(f"generated output drops base species: {', '.join(sorted(missing))}")
    for species in set(base_groups) - set(targets):
        old_lines = [row.raw_line for row in base_groups[species]]
        if old_lines != [
            line
            for line in output_lines[1:]
            if next(csv.reader([line]))[0] == species
        ]:
            raise ValueError(f"{species}: untouched base rows changed")

    return "\n".join(output_lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-csv", required=True, type=Path, help="series-0001--0003 base CSV")
    parser.add_argument("--openimcc-csv", required=True, type=Path, help="openimcc d2a7516 gas CSV")
    parser.add_argument("--output", required=True, type=Path, help="generated VapoRock gas CSV")
    args = parser.parse_args()
    base_path = args.base_csv.resolve()
    source_path = args.openimcc_csv.resolve()
    output_path = args.output.resolve()
    if output_path in {base_path, source_path}:
        parser.error("output must not overwrite either input")
    try:
        payload = build(base_path, source_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(2, f"build_0004_gas_rows.py: {exc}\n")
    base_groups = grouped(parse_csv(base_path)[1])
    source_groups = grouped(parse_csv(source_path)[1])
    imported_species = len(set(source_groups) - EXCLUDED)
    imported_rows = sum(len(rows) for name, rows in source_groups.items() if name not in EXCLUDED)
    final_species = len(set(base_groups) | (set(source_groups) - EXCLUDED))
    print(
        f"generated {final_species} species; {imported_rows} rows across "
        f"{imported_species} imported species; "
        f"excluded {', '.join(sorted(EXCLUDED))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
