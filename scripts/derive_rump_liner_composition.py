#!/usr/bin/env python3
"""Generate a feedstock-specific oxide composition from the terminal-rump report."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from typing import Any, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.accounting.formulas import parse_formula  # noqa: E402
from simulator.recipe_io import load_recipe_patch  # noqa: E402
from simulator.runner import PyrolysisRun  # noqa: E402


DEFAULT_FEEDSTOCK = "lunar_mare_low_ti"
DEFAULT_RECIPE = ROOT / "data" / "recipes" / "canonical_lunar_full_yield.yaml"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "generated" / "liner_compositions"
FULL_SEQUENCE_RUN_HOURS = 400  # Canonical runner recipe invocation in data/recipes/README.md.


def _positive_finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite positive number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite positive number") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be a finite positive number")
    return result


def _repo_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _terminal_rump_oxide_wt_pct(document: Mapping[str, Any]) -> dict[str, float]:
    """Normalize the runner's existing oxide-only terminal-rump report."""

    product_report = document.get("product_classification")
    if not isinstance(product_report, Mapping):
        raise ValueError("product_classification report is unavailable")
    classification = product_report.get("classification")
    rump = (
        classification.get("refractory_ceramic_rump")
        if isinstance(classification, Mapping)
        else None
    )
    raw_oxide_kg = rump.get("rump_kg_by_species") if isinstance(rump, Mapping) else None
    if not isinstance(raw_oxide_kg, Mapping) or not raw_oxide_kg:
        raise ValueError("terminal refractory-rump species report is unavailable")

    oxide_kg: dict[str, float] = {}
    for raw_species, raw_mass in raw_oxide_kg.items():
        species = str(raw_species).strip()
        mass = _positive_finite(raw_mass, f"rump_kg_by_species[{species!r}]")
        formula = parse_formula(species)
        if "O" not in formula.elements or len(formula.elements) != 2:
            raise ValueError(
                f"terminal-rump report contains non-oxide {species!r}; refusing to invent oxidation"
            )
        oxide_kg[species] = oxide_kg.get(species, 0.0) + mass
    total_kg = math.fsum(oxide_kg.values())
    if total_kg <= 0.0:
        raise ValueError("terminal-rump oxide inventory is empty")
    return {
        oxide: mass_kg / total_kg * 100.0
        for oxide, mass_kg in sorted(oxide_kg.items())
    }


def _atomic_write_yaml(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            yaml.safe_dump(dict(payload), handle, sort_keys=False, allow_unicode=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feedstock", default=DEFAULT_FEEDSTOCK)
    parser.add_argument("--recipe", type=Path, default=DEFAULT_RECIPE)
    parser.add_argument("--bakeout-temperature-c", type=float, required=True)
    parser.add_argument("--hold-duration-h", type=float, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    temperature_C = _positive_finite(
        args.bakeout_temperature_c, "bakeout_temperature_C"
    )
    hold_duration_h = _positive_finite(args.hold_duration_h, "hold_duration_h")
    recipe_path = args.recipe if args.recipe.is_absolute() else ROOT / args.recipe
    recipe_patch = load_recipe_patch(recipe_path)
    campaigns = dict(recipe_patch.get("campaigns") or {})
    terminal_campaign = dict(campaigns.get("C2A_continuous") or {})
    terminal_campaign["temp_range_C"] = [1050.0, temperature_C]
    terminal_campaign["duration_h"] = hold_duration_h
    terminal_campaign["max_hold_hr"] = hold_duration_h
    campaigns["C2A_continuous"] = terminal_campaign
    recipe_patch["campaigns"] = campaigns
    recipe_patch["furnace_max_T_C"] = temperature_C

    # "Parking lot" equivalence: in vacuum, melt-side residue depends on the
    # melt's temperature/pO2 history, not on whether evolved vapor is captured.
    # An open-crucible boil-out without condensers leaves the same rump as a
    # furnace run with the same thermal history.
    run = PyrolysisRun(
        feedstock_id=str(args.feedstock),
        campaign="C0",
        hours=FULL_SEQUENCE_RUN_HOURS,
        setpoints_patch=recipe_patch,
    )
    document = run.run()
    if not isinstance(document, Mapping) or document.get("status") != "ok":
        raise RuntimeError(
            f"bake-out run did not complete: {json.dumps(document, default=str)[:1000]}"
        )
    composition = _terminal_rump_oxide_wt_pct(document)
    recipe_label = str(recipe_path.relative_to(ROOT)) if recipe_path.is_relative_to(ROOT) else str(recipe_path)
    output = args.output or (
        DEFAULT_OUTPUT_DIR / f"rump_liner_{args.feedstock}.yaml"
    )
    if not output.is_absolute():
        output = ROOT / output
    generated = {
        "schema": "liner-composition-v1",
        "composition": composition,
        "provenance": {
            "feedstock_id": str(args.feedstock),
            "recipe": recipe_label,
            "campaign_sequence": "standard full campaign sequence from C0",
            "starting_campaign": "C0",
            "repo_sha": _repo_sha(),
            "generator_command": shlex.join(sys.argv),
            "bakeout_temperature_C": temperature_C,
            "hold_duration_h": hold_duration_h,
            "runner_campaign_hours": FULL_SEQUENCE_RUN_HOURS,
            "source_report": (
                "product_classification.classification."
                "refractory_ceramic_rump.rump_kg_by_species"
            ),
            "normalization": "reported terminal-rump oxide kg normalized to 100 wt%",
        },
    }
    _atomic_write_yaml(output, generated)
    print(output.relative_to(ROOT) if output.is_relative_to(ROOT) else output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
