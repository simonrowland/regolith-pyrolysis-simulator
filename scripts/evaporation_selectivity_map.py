#!/usr/bin/env python3
"""Emit a read-only evaporation-plane selectivity map over temperature.

Fe rows are redox-inert in this model: a_FeO is static until SSO-R.
"""

from __future__ import annotations

import argparse
import copy
import csv
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.core import PyrolysisSimulator
from simulator.state import Atmosphere


DEFAULT_SPECIES = ("Na", "K", "Fe", "SiO", "Cr", "CrO2", "Mg")
FE_LOW_CONFIDENCE_NOTE = (
    "LOW_CONFIDENCE: residual_dex=0.418; "
    "fit_target=pseudo_psat_backsolved_from_vaporock; "
    "REDOX-INERT: a_FeO static, Fe VP insensitive to fO2 in current model "
    "(see sso-r-fe-redox-design.md)"
)


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _parse_additive(value: str) -> tuple[str, float]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("additive must be SPECIES=KG")
    species, raw_kg = value.split("=", 1)
    species = species.strip()
    if not species:
        raise argparse.ArgumentTypeError("additive species must be non-empty")
    try:
        kg = float(raw_kg)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"additive kg must be numeric: {raw_kg!r}"
        ) from exc
    if kg < 0.0:
        raise argparse.ArgumentTypeError("additive kg must be non-negative")
    return species, kg


def _temperature_grid(start_c: float, stop_c: float, step_c: float) -> list[float]:
    if step_c <= 0.0:
        raise ValueError("--step-C must be > 0")
    if stop_c < start_c:
        raise ValueError("--stop-C must be >= --start-C")
    values: list[float] = []
    current = start_c
    while current <= stop_c + 1.0e-9:
        values.append(round(current, 10))
        current += step_c
    return values


def _build_sim(args: argparse.Namespace) -> PyrolysisSimulator:
    setpoints = _load_yaml(REPO_ROOT / "data" / "setpoints.yaml")
    feedstocks = _load_yaml(REPO_ROOT / "data" / "feedstocks.yaml")
    vapor_pressures = _load_yaml(REPO_ROOT / "data" / "vapor_pressures.yaml")
    additives = dict(args.additive or ())
    sim = PyrolysisSimulator(None, setpoints, feedstocks, vapor_pressures)
    sim.load_batch(args.feedstock, mass_kg=args.mass_kg, additives_kg=additives)
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.pO2_mbar = float(args.p_o2_mbar)
    sim.melt.p_total_mbar = float(args.p_total_mbar)
    return sim


def _rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    sim = _build_sim(args)
    species_order = tuple(dict.fromkeys(args.species or DEFAULT_SPECIES))
    rows: list[dict[str, Any]] = []
    original_temperature_C = sim.melt.temperature_C
    original_reservoir = copy.deepcopy(sim.melt.oxygen_reservoir)
    try:
        for temperature_c in _temperature_grid(args.start_C, args.stop_C, args.step_C):
            sim.melt.temperature_C = temperature_c
            sim._re_reference_melt_fO2_to_temperature(temperature_c + 273.15)
            equilibrium = sim._get_equilibrium()
            flux = sim._calculate_evaporation(equilibrium)
            total_flux = sum(
                max(0.0, float(value))
                for value in (flux.species_kg_hr or {}).values()
            )
            emitted_species = set(species_order) | set(flux.species_kg_hr or {})
            for species in sorted(emitted_species):
                flux_kg_hr = max(
                    0.0,
                    float((flux.species_kg_hr or {}).get(species, 0.0)),
                )
                rows.append({
                    "temperature_C": temperature_c,
                    "pO2_mbar": float(args.p_o2_mbar),
                    "p_total_mbar": float(args.p_total_mbar),
                    "species": species,
                    "flux_kg_hr": flux_kg_hr,
                    "fraction": flux_kg_hr / total_flux if total_flux > 0.0 else 0.0,
                    "vp_confidence_note": (
                        FE_LOW_CONFIDENCE_NOTE if species == "Fe" else ""
                    ),
                })
    finally:
        sim.melt.temperature_C = original_temperature_C
        sim.melt.oxygen_reservoir = original_reservoir
        sim._sync_oxygen_reservoir_mirror()
    return rows


def _write_csv(rows: list[dict[str, Any]], out_path: Path | None) -> None:
    fieldnames = [
        "temperature_C",
        "pO2_mbar",
        "p_total_mbar",
        "species",
        "flux_kg_hr",
        "fraction",
        "vp_confidence_note",
    ]
    if out_path is None:
        handle = sys.stdout
        close = False
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        handle = out_path.open("w", newline="", encoding="utf-8")
        close = True
    try:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if close:
            handle.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feedstock", default="lunar_mare_low_ti")
    parser.add_argument("--mass-kg", type=float, default=1000.0)
    parser.add_argument("--start-C", type=float, default=1000.0)
    parser.add_argument("--stop-C", type=float, default=2000.0)
    parser.add_argument("--step-C", type=float, default=50.0)
    parser.add_argument("--pO2-mbar", dest="p_o2_mbar", type=float, default=0.0)
    parser.add_argument("--p-total-mbar", type=float, default=10.0)
    parser.add_argument(
        "--p-neutral-mbar",
        type=float,
        default=None,
        help="Neutral carrier pressure; when set, p_total = pO2 + p_neutral.",
    )
    parser.add_argument("--species", action="append", default=None)
    parser.add_argument("--additive", action="append", type=_parse_additive)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.p_neutral_mbar is not None:
        args.p_total_mbar = float(args.p_o2_mbar) + float(args.p_neutral_mbar)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _write_csv(_rows(args), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
