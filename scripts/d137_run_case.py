#!/usr/bin/env python3
"""Run the README C2A hero against one tree, with an optional duct-wall override.

The runner CLI has no setpoints-path flag. ``--recipe`` refuses a schedule
list (``recipe values must be scalars or lists``), and ``default_C`` alone
leaves a matching campaign schedule in force. This script copies
``data/setpoints.yaml`` to a temp file, changes only the stage_0_to_stage_1
wall, and passes that path as ``PyrolysisRun.setpoints_path``. That is the
field ``load_config_bundle`` already reads. Do not edit the tree.

``engines/engines.local.toml`` must sit in the tree. The runner finds it from
the imported package root (``engine_local_config.config_path``).

Usage:
  python3 scripts/d137_run_case.py --tree PATH --output PATH [--duct-wall-C 1700]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

# overhead_headspace.pipe_segment_temperatures_C.segments.stage_0_to_stage_1::OverheadGasModel.resolve_pipe_segment_temperatures_C
_SEGMENT_HEADER = "      stage_0_to_stage_1:"
_NEXT_HEADER = "      stage_1_to_stage_2:"
_TEMPERATURE_KEYS = ("default_C", "start_C", "end_C")

HERO_ARGV = (
    "--feedstock",
    "lunar_mare_low_ti",
    "--campaign",
    "C2A",
    "--hours",
    "225",
    "--mass-kg",
    "1",
    "--backend",
    "internal-analytical",
    "--include-wall-deposit-rate-diagnostics",
)


def _render_c(temperature_c: float) -> str:
    if not math.isfinite(temperature_c):
        raise SystemExit("duct wall temperature must be finite")
    text = f"{float(temperature_c):.6f}".rstrip("0").rstrip(".")
    if "." not in text:
        text += ".0"
    return text


def patch_stage_0_to_stage_1_wall(text: str, temperature_c: float) -> str:
    """Replace only the stage_0_to_stage_1 duct-wall temperatures.

    The C2A schedule wins over default_C, so every start_C, end_C, and
    default_C inside that one segment is set. The next segment is untouched.
    """

    rendered = _render_c(temperature_c)
    lines = text.splitlines(keepends=True)
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(_SEGMENT_HEADER)),
        None,
    )
    if start is None:
        raise SystemExit("stage_0_to_stage_1 block not found")
    end = next(
        (
            index
            for index, line in enumerate(lines[start + 1 :], start + 1)
            if line.startswith(_NEXT_HEADER)
        ),
        None,
    )
    if end is None:
        raise SystemExit("stage_1_to_stage_2 block not found after stage_0_to_stage_1")
    changed = False
    for index in range(start, end):
        stripped = lines[index].lstrip()
        indent = lines[index][: len(lines[index]) - len(stripped)]
        for key in _TEMPERATURE_KEYS:
            if stripped.startswith(f"{key}:"):
                lines[index] = f"{indent}{key}: {rendered}\n"
                changed = True
                break
    if not changed:
        raise SystemExit("stage_0_to_stage_1 block has no wall temperature keys")
    return "".join(lines)


def write_setpoints_override(tree: Path, temperature_c: float) -> Path:
    source = tree / "data" / "setpoints.yaml"
    patched = patch_stage_0_to_stage_1_wall(
        source.read_text(encoding="utf-8"),
        temperature_c,
    )
    handle = tempfile.NamedTemporaryFile(
        prefix="d137-setpoints-",
        suffix=".yaml",
        delete=False,
        mode="w",
        encoding="utf-8",
    )
    with handle:
        handle.write(patched)
    return Path(handle.name)


def _species_kg(stage: Mapping[str, Any], species: str) -> float:
    total = 0.0
    for key in ("designated_species_kg", "coproduct_species_kg", "impurity_species_kg"):
        bucket = stage.get(key) or {}
        if isinstance(bucket, Mapping):
            total += float(bucket.get(species, 0.0) or 0.0)
    return total


def _nested_kg(document: Mapping[str, Any], *path: str) -> Mapping[str, Any]:
    value: Any = document
    for key in path:
        if not isinstance(value, Mapping):
            return {}
        value = value.get(key)
    return value if isinstance(value, Mapping) else {}


def print_case_summary(document: Mapping[str, Any]) -> None:
    """Print the element split, duct and Stage-1 Fe/SiO, and closure."""

    status = document.get("status")
    metadata = document.get("run_metadata") or {}
    print(f"status {status}")
    print(
        "hours_completed "
        f"{metadata.get('hours_completed')} requested {metadata.get('hours_requested')}"
    )
    closure = _nested_kg(document, "yield_disposition", "closure")
    print(
        "closure maximum_residual_fraction "
        f"{closure.get('maximum_residual_fraction')} "
        f"limit_fraction {closure.get('limit_fraction')}"
    )
    rows = (
        ((document.get("yield_disposition") or {}).get("fraction_table") or {}).get("rows")
        or []
    )
    print("per_element_split_mol")
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        destinations = row.get("destination_mol_atoms") or {}
        nonzero = {
            name: float(amount)
            for name, amount in destinations.items()
            if abs(float(amount or 0.0)) > 0.0
        }
        print(
            f"  {row.get('element')} input_mol {row.get('feedstock_input_mol_atoms')} "
            f"melt {destinations.get('melt_retained', 0.0)} "
            f"condensed {destinations.get('product_condensed', 0.0)} "
            f"vented {destinations.get('offgas_vented', 0.0)} "
            f"wall {destinations.get('wall_deposit', 0.0)} "
            f"nonzero {nonzero}"
        )
    purity = document.get("stage_purity_report") or {}
    print("condensed_kg_by_stage")
    if isinstance(purity, Mapping):
        for stage_key, stage in purity.items():
            if not isinstance(stage, Mapping):
                continue
            species = {}
            for key in ("designated_species_kg", "coproduct_species_kg", "impurity_species_kg"):
                bucket = stage.get(key) or {}
                if isinstance(bucket, Mapping):
                    species.update({name: float(amount) for name, amount in bucket.items()})
            print(
                f"  {stage_key} number {stage.get('stage_number')} "
                f"total_kg {stage.get('total_kg')} species_kg {species}"
            )
    surfaces = _nested_kg(document, "final", "deposit_by_surface_species_kg")
    duct = surfaces.get("stage_0_to_stage_1") or {}
    if not isinstance(duct, Mapping):
        duct = {}
    duct_fe = float(duct.get("Fe", 0.0) or 0.0)
    duct_sio = float(duct.get("SiO", 0.0) or 0.0)
    evaporated_fe = 0.0
    for hour in document.get("per_hour_summary") or []:
        if not isinstance(hour, Mapping):
            continue
        vapor = hour.get("vapor_species_kg_hr") or {}
        if isinstance(vapor, Mapping):
            evaporated_fe += float(vapor.get("Fe", 0.0) or 0.0)
    stage_1 = purity.get("stage_1_fe_condenser") if isinstance(purity, Mapping) else {}
    stage_1 = stage_1 if isinstance(stage_1, Mapping) else {}
    stage_1_fe = _species_kg(stage_1, "Fe")
    stage_1_sio = _species_kg(stage_1, "SiO")
    fe_row = next(
        (
            row
            for row in rows
            if isinstance(row, Mapping) and row.get("element") == "Fe"
        ),
        {},
    )
    vented_fe = float(
        ((fe_row or {}).get("destination_mol_atoms") or {}).get("offgas_vented", 0.0) or 0.0
    )
    share = (duct_fe / evaporated_fe) if evaporated_fe else float("nan")
    print(f"duct_Fe_kg {duct_fe}")
    print(f"evaporated_Fe_kg {evaporated_fe}")
    print(f"duct_Fe_fraction_of_evaporated_Fe {share}")
    print(f"stage_1_Fe_kg {stage_1_fe}")
    print(f"vented_Fe_mol {vented_fe}")
    print(f"duct_SiO_kg {duct_sio}")
    print(f"stage_1_SiO_kg {stage_1_sio}")
    print(f"duct_surfaces {dict(duct)}")


def run_hero(tree: Path, output: Path, setpoints_path: Path | None) -> int:
    """Run the README hero via PyrolysisRun, importing simulator from ``tree``."""

    sys.path.insert(0, str(tree))
    os.chdir(tree)
    from simulator.run_executor import _json_safe
    from simulator.runner import PyrolysisRun

    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=225,
        mass_kg=1.0,
        backend_name="internal-analytical",
        include_wall_deposit_rate_diagnostics=True,
        setpoints_path=setpoints_path,
    )
    document = run.run()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(_json_safe(document), handle, indent=2, sort_keys=False, allow_nan=False)
        handle.write("\n")
    print_case_summary(document)
    return 0 if document.get("status") == "ok" else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True, help="Checkout to import and run")
    parser.add_argument("--output", type=Path, required=True, help="Hero JSON output path")
    parser.add_argument(
        "--duct-wall-C",
        type=float,
        default=None,
        help=(
            "Constant stage_0_to_stage_1 wall temperature in C. "
            "Omit it to keep the tree schedule."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tree = args.tree.resolve()
    if not (tree / "simulator" / "runner" / "__init__.py").is_file():
        raise SystemExit(f"tree has no simulator.runner: {tree}")
    if not (tree / "engines" / "engines.local.toml").is_file():
        raise SystemExit(
            f"tree has no engines/engines.local.toml: {tree} "
            "(the README command refuses without it)"
        )
    setpoints_path = None
    if args.duct_wall_C is not None:
        setpoints_path = write_setpoints_override(tree, float(args.duct_wall_C))
        print(
            "runner CLI has no setpoints override; "
            f"PyrolysisRun.setpoints_path={setpoints_path}"
        )
        print("hero " + " ".join(HERO_ARGV) + f" --output {args.output}")
    else:
        print("no duct override; using the tree data/setpoints.yaml")
        print("hero " + " ".join(HERO_ARGV) + f" --output {args.output}")
    return run_hero(tree, args.output, setpoints_path)


if __name__ == "__main__":
    raise SystemExit(main())
