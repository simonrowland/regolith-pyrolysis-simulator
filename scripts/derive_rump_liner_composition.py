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
from simulator.run_executor import completed_runner_status  # noqa: E402


DEFAULT_FEEDSTOCK = "lunar_mare_low_ti"
DEFAULT_RECIPE = ROOT / "data" / "recipes" / "canonical_lunar_full_yield.yaml"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "generated" / "liner_compositions"
FULL_SEQUENCE_RUN_HOURS = 400  # Canonical runner recipe invocation in data/recipes/README.md.
BAKEOUT_TEMPERATURE_TOLERANCE_C = 5.0


class GeneratorRefusal(ValueError):
    """Typed refusal that preserves the runner's complete stop identity."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = str(reason)
        self.detail = str(detail)
        super().__init__(f"{self.reason}: {self.detail}")


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


def _terminal_rump_composition(document: Mapping[str, Any]) -> dict[str, Any]:
    """Separate unassigned species and normalize only reported oxide masses."""

    product_report = document.get("product_classification")
    if not isinstance(product_report, Mapping):
        raise GeneratorRefusal(
            "terminal_rump_composition_missing",
            "product_classification report is unavailable",
        )
    classification = product_report.get("classification")
    rump = (
        classification.get("refractory_ceramic_rump")
        if isinstance(classification, Mapping)
        else None
    )
    raw_oxide_kg = rump.get("rump_kg_by_species") if isinstance(rump, Mapping) else None
    if not isinstance(raw_oxide_kg, Mapping) or not raw_oxide_kg:
        raise GeneratorRefusal(
            "terminal_rump_composition_missing",
            "terminal refractory-rump species report is unavailable",
        )

    oxide_kg: dict[str, float] = {}
    unassigned_trace_kg: dict[str, float] = {}
    for raw_species, raw_mass in raw_oxide_kg.items():
        species = str(raw_species).strip()
        mass = _positive_finite(raw_mass, f"rump_kg_by_species[{species!r}]")
        try:
            formula = parse_formula(species)
        except Exception as exc:  # noqa: BLE001 - preserve unassigned identity
            unassigned_trace_kg[species] = mass
            continue
        if "O" not in formula.elements or len(formula.elements) != 2:
            unassigned_trace_kg[species] = mass
        else:
            oxide_kg[species] = oxide_kg.get(species, 0.0) + mass
    total_kg = math.fsum(oxide_kg.values())
    if total_kg <= 0.0:
        raise GeneratorRefusal("terminal_rump_oxide_inventory_empty", "no oxide mass")
    unassigned_total_kg = math.fsum(unassigned_trace_kg.values())
    rump_total_kg = total_kg + unassigned_total_kg
    return {
        "composition_wt_pct": {
            oxide: mass_kg / total_kg * 100.0
            for oxide, mass_kg in sorted(oxide_kg.items())
        },
        "unassigned_trace_species": {
            "species_kg": dict(sorted(unassigned_trace_kg.items())),
            "total_mass_kg": unassigned_total_kg,
            "fraction_of_reported_rump_mass": (
                unassigned_total_kg / rump_total_kg if rump_total_kg > 0.0 else 0.0
            ),
            "excluded_from_oxide_normalization": True,
        },
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
    status = document.get("status") if isinstance(document, Mapping) else None
    reason = document.get("reason") if isinstance(document, Mapping) else None
    stop_reason = str(reason or "")
    execution = getattr(run, "_last_execution", None)
    simulator = getattr(execution, "simulator", None)
    is_complete = getattr(simulator, "is_complete", None)
    sequence_complete = bool(is_complete()) if callable(is_complete) else False
    completed_status = completed_runner_status(
        status,
        reason,
        sequence_complete=sequence_complete,
    )
    if not completed_status:
        status_label = str(status or "status_missing")
        reason_label = stop_reason or "reason_missing"
        refusal_reason = (
            f"run_refused:{reason_label}"
            if status == "refused"
            else f"run_{status_label}:{reason_label}"
        )
        detail = f"runner status={status_label!r}; reason={reason_label!r}"
        if status == "refused" and stop_reason == "c4_preheat_wall_clock_exhausted":
            detail += "; blocked on b-1049; no campaign-sequence workaround applied"
        raise GeneratorRefusal(refusal_reason, detail)

    if execution is None:
        raise GeneratorRefusal(
            "bakeout_operating_history_missing",
            "completed runner did not expose its execution snapshots",
        )
    from simulator.diagnostics import achieved_melt_bakeout

    achieved = achieved_melt_bakeout(
        execution.snapshots,
        setpoint_C=temperature_C,
        tolerance_C=BAKEOUT_TEMPERATURE_TOLERANCE_C,
    )
    if achieved["hold_duration_h"] + 1.0e-9 < hold_duration_h:
        raise GeneratorRefusal(
            "bakeout_hold_shortfall",
            (
                f"runner status={status!r}; reason={stop_reason or 'none'!r}; "
                f"requested_hold_h={hold_duration_h:.9g}; "
                f"achieved_hold_h={achieved['hold_duration_h']:.9g}; "
                f"hold_threshold_C={achieved['hold_threshold_C']:.9g}; "
                f"achieved_peak_melt_C={achieved['peak_melt_temperature_C']:.9g}"
            ),
        )
    rump = _terminal_rump_composition(document)
    recipe_label = str(recipe_path.relative_to(ROOT)) if recipe_path.is_relative_to(ROOT) else str(recipe_path)
    output = args.output or (
        DEFAULT_OUTPUT_DIR / f"rump_liner_{args.feedstock}.yaml"
    )
    if not output.is_absolute():
        output = ROOT / output
    generated = {
        "schema": "liner-composition-v1",
        "composition": rump["composition_wt_pct"],
        "unassigned_trace_species": rump["unassigned_trace_species"],
        "provenance": {
            "feedstock_id": str(args.feedstock),
            "recipe": recipe_label,
            "campaign_sequence": "standard full campaign sequence from C0",
            "starting_campaign": "C0",
            "repo_sha": _repo_sha(),
            "generator_command": shlex.join(sys.argv),
            "requested_bakeout_temperature_C": temperature_C,
            "requested_hold_duration_h": hold_duration_h,
            "achieved_peak_melt_temperature_C": achieved[
                "peak_melt_temperature_C"
            ],
            "achieved_hold_duration_h": achieved["hold_duration_h"],
            "hold_temperature_tolerance_C": BAKEOUT_TEMPERATURE_TOLERANCE_C,
            "achieved_hold_threshold_C": achieved["hold_threshold_C"],
            "runner_campaign_hours": FULL_SEQUENCE_RUN_HOURS,
            "source_report": (
                "product_classification.classification."
                "refractory_ceramic_rump.rump_kg_by_species"
            ),
            "normalization": "reported terminal-rump oxide kg normalized to 100 wt%",
            "unassigned_trace_species_policy": (
                "Bare non-oxides are disclosed by species and mass and excluded "
                "from oxide normalization; no oxidation state or mass-fraction "
                "threshold is inferred from the runner's species-only report."
            ),
        },
    }
    _atomic_write_yaml(output, generated)
    print(output.relative_to(ROOT) if output.is_relative_to(ROOT) else output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GeneratorRefusal as exc:
        print(
            json.dumps(
                {"status": "refused", "reason": exc.reason, "detail": exc.detail},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
