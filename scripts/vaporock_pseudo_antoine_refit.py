#!/usr/bin/env python3
"""Dense VapoRock pseudo-Antoine refit for builtin fallback rows.

This script fits the fallback standard term used by
``data/vapor_pressures.yaml``. It does not edit files; copy the reported
coefficients only after reviewing the residual table.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import vaporock
import yaml
from scipy.optimize import linprog, minimize_scalar

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from engines.builtin.vapor_pressure import _ELLINGHAM_THERMO
from simulator.state import GAS_CONSTANT


SUPPORTED_VAPOROCK_OXIDES = {
    "Al2O3",
    "CaO",
    "Cr2O3",
    "FeO",
    "K2O",
    "MgO",
    "MnO",
    "Na2O",
    "P2O5",
    "SiO2",
    "TiO2",
}
DEFAULT_FEEDSTOCKS = (
    "lunar_mare_low_ti",
    "lunar_mare_high_ti",
    "lunar_highland",
    "lunar_pkt_kreep_average",
    "lunar_spa_kreep_influenced",
    "mars_basalt",
    "mars_sulfate_rich",
)
DEFAULT_SPECIES = ("Na", "K", "Mg", "Fe", "SiO")
OLD_METADATA_RESIDUALS = {
    "Na": 0.121,
    "K": 1.469,
    "Mg": 0.203,
    "Fe": 0.023,
    "SiO": 0.113,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fit pseudo-Antoine fallback rows against dense VapoRock IW samples."
    )
    parser.add_argument(
        "--feedstocks",
        nargs="+",
        default=list(DEFAULT_FEEDSTOCKS),
        help="feedstock IDs from data/feedstocks.yaml",
    )
    parser.add_argument("--t-min", type=float, default=1350.0)
    parser.add_argument("--t-max", type=float, default=1950.0)
    parser.add_argument("--t-step", type=float, default=20.0)
    parser.add_argument(
        "--species",
        nargs="+",
        default=list(DEFAULT_SPECIES),
        choices=list(DEFAULT_SPECIES),
    )
    parser.add_argument(
        "--c-bound",
        type=float,
        default=1000.0,
        help="symmetric Antoine C bound in K; prevents singular/pathological fits",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    return parser.parse_args()


def load_yaml(path: Path) -> Any:
    with path.open() as handle:
        return yaml.safe_load(handle) or {}


def clean_silicate_composition(composition_wt_pct: dict[str, Any]) -> dict[str, float]:
    """Project a feedstock into VapoRock's silicate oxide basis and renormalize."""

    cleaned = {
        oxide: float(value)
        for oxide, value in composition_wt_pct.items()
        if oxide in SUPPORTED_VAPOROCK_OXIDES and float(value) > 0.0
    }
    total = sum(cleaned.values())
    if total <= 0.0:
        raise ValueError("no VapoRock-supported oxides in composition")
    return {oxide: value * 100.0 / total for oxide, value in cleaned.items()}


def temperature_grid(t_min: float, t_max: float, t_step: float) -> list[float]:
    if t_step <= 0.0:
        raise ValueError("--t-step must be positive")
    count = int(round((t_max - t_min) / t_step))
    return [round(t_min + i * t_step, 10) for i in range(count + 1)]


def vaporock_pressures_pa(
    composition_wt_pct: dict[str, float],
    temperature_k: float,
    species: tuple[str, ...],
) -> tuple[float, dict[str, float]]:
    system = vaporock.System()
    system.set_melt_comp(composition_wt_pct)
    logfo2 = float(vaporock.redox_buffer(temperature_k, "IW"))
    result = system.eval_gas_abundances(temperature_k, logfo2)
    pressures: dict[str, float] = {}
    for name in species:
        try:
            log10_bar = float(result.loc[f"{name}(g)"].iloc[0])
        except Exception:  # noqa: BLE001 - upstream table boundary
            continue
        pressure_pa = 10.0**log10_bar * 1.0e5
        if math.isfinite(pressure_pa) and pressure_pa > 0.0:
            pressures[name] = pressure_pa
    return logfo2, pressures


def fallback_activity_term(
    *,
    species: str,
    composition_wt_pct: dict[str, float],
    temperature_k: float,
    logfo2: float,
    vapor_pressure_data: dict[str, Any],
) -> float | None:
    pO2_bar = max(10.0**logfo2, 1.0e-9)
    if species == "SiO":
        activity = max(float(composition_wt_pct.get("SiO2", 0.0)) / 100.0, 0.0)
        if activity <= 0.0:
            return None
        if pO2_bar > 1.0e-9:
            activity *= math.sqrt(1.0e-9 / pO2_bar)
        return activity

    row = vapor_pressure_data["metals"][species]
    parent_oxide = row["parent_oxide"]
    oxide_activity = max(float(composition_wt_pct.get(parent_oxide, 0.0)) / 100.0, 0.0)
    if oxide_activity <= 1.0e-10:
        return None

    dH_f, dS_f, n_metal, n_oxide = _ELLINGHAM_THERMO[species]
    dG_f_kJ = dH_f - temperature_k * dS_f
    k_decomp = math.exp(dG_f_kJ * 1000.0 / (GAS_CONSTANT * temperature_k))
    numerator = k_decomp * (oxide_activity**n_oxide) / pO2_bar
    if numerator <= 0.0:
        return None
    return min(numerator ** (1.0 / n_metal), 1.0)


def current_coefficients(vapor_pressure_data: dict[str, Any], species: str) -> tuple[float, float, float]:
    section = "oxide_vapors" if species == "SiO" else "metals"
    antoine = vapor_pressure_data[section][species]["antoine"]
    return float(antoine["A"]), float(antoine["B"]), float(antoine.get("C") or 0.0)


def predict_log10(coefficients: tuple[float, float, float], temperature_k: float) -> float:
    A, B, C = coefficients
    return A - B / (temperature_k + C)


def residual_stats(
    coefficients: tuple[float, float, float],
    samples: list[dict[str, Any]],
) -> dict[str, float]:
    residuals = np.array(
        [predict_log10(coefficients, sample["temperature_k"]) - sample["target_log10"] for sample in samples],
        dtype=float,
    )
    return {
        "max_abs_dex": float(np.max(np.abs(residuals))),
        "rmse_dex": float(np.sqrt(np.mean(residuals * residuals))),
        "min_dex": float(np.min(residuals)),
        "max_dex": float(np.max(residuals)),
    }


def minimax_for_c(
    samples: list[dict[str, Any]],
    c_value: float,
) -> tuple[float, tuple[float, float, float]] | None:
    denominators = np.array([sample["temperature_k"] + c_value for sample in samples], dtype=float)
    if np.any(denominators <= 0.0) or not np.all(np.isfinite(denominators)):
        return None

    x_values = 1.0 / denominators
    y_values = np.array([sample["target_log10"] for sample in samples], dtype=float)
    constraints: list[list[float]] = []
    limits: list[float] = []
    for x_value, y_value in zip(x_values, y_values, strict=True):
        constraints.append([1.0, x_value, -1.0])
        limits.append(float(y_value))
        constraints.append([-1.0, -x_value, -1.0])
        limits.append(float(-y_value))

    result = linprog(
        [0.0, 0.0, 1.0],
        A_ub=np.array(constraints),
        b_ub=np.array(limits),
        bounds=[(None, None), (None, None), (0.0, None)],
        method="highs",
    )
    if not result.success:
        return None
    A, beta, max_abs = result.x
    return float(max_abs), (float(A), float(-beta), float(c_value))


def fit_minimax(
    samples: list[dict[str, Any]],
    *,
    c_bound: float,
) -> tuple[float, tuple[float, float, float]]:
    if c_bound <= 0.0:
        raise ValueError("--c-bound must be positive")
    low = -float(c_bound)
    high = float(c_bound)
    best: tuple[float, tuple[float, float, float]] | None = None
    for c_value in np.linspace(low, high, 801):
        candidate = minimax_for_c(samples, float(c_value))
        if candidate is not None and (best is None or candidate[0] < best[0]):
            best = candidate
    if best is None:
        raise RuntimeError("no feasible minimax fit")

    c0 = best[1][2]

    def objective(c_value: float) -> float:
        candidate = minimax_for_c(samples, float(c_value))
        return candidate[0] if candidate is not None else 1.0e9

    for width in (20.0, 100.0, c_bound):
        result = minimize_scalar(
            objective,
            bounds=(max(low, c0 - width), min(high, c0 + width)),
            method="bounded",
            options={"xatol": 1.0e-8, "maxiter": 300},
        )
        if not result.success:
            continue
        candidate = minimax_for_c(samples, float(result.x))
        if candidate is not None and candidate[0] < best[0]:
            best = candidate
            c0 = best[1][2]
    return best


def confidence_tier(residual_dex: float) -> str:
    if residual_dex <= 0.05:
        return "tight"
    if residual_dex <= 0.35:
        return "moderate"
    return "low"


def main() -> int:
    args = parse_args()
    species = tuple(args.species)
    temps = temperature_grid(args.t_min, args.t_max, args.t_step)
    vapor_pressure_data = load_yaml(REPO_ROOT / "data" / "vapor_pressures.yaml")
    feedstocks = load_yaml(REPO_ROOT / "data" / "feedstocks.yaml")

    samples_by_species: dict[str, list[dict[str, Any]]] = {name: [] for name in species}
    for feedstock_id in args.feedstocks:
        feedstock = feedstocks[feedstock_id]
        composition = clean_silicate_composition(feedstock["composition_wt_pct"])
        for temperature_k in temps:
            logfo2, pressures = vaporock_pressures_pa(composition, temperature_k, species)
            for name, pressure_pa in pressures.items():
                activity = fallback_activity_term(
                    species=name,
                    composition_wt_pct=composition,
                    temperature_k=temperature_k,
                    logfo2=logfo2,
                    vapor_pressure_data=vapor_pressure_data,
                )
                if activity is None or activity <= 0.0:
                    continue
                samples_by_species[name].append(
                    {
                        "feedstock": feedstock_id,
                        "temperature_k": temperature_k,
                        "logfo2": logfo2,
                        "vaporock_pa": pressure_pa,
                        "activity_term": activity,
                        "target_log10": math.log10(pressure_pa / activity),
                    }
                )

    rows = []
    for name in species:
        samples = samples_by_species[name]
        if not samples:
            raise RuntimeError(f"{name}: no VapoRock samples")
        old_coeff = current_coefficients(vapor_pressure_data, name)
        old_dense = residual_stats(old_coeff, samples)
        fit_residual, new_coeff = fit_minimax(samples, c_bound=args.c_bound)
        new_stats = residual_stats(new_coeff, samples)
        rows.append(
            {
                "species": name,
                "n_samples": len(samples),
                "old_metadata_residual_dex": OLD_METADATA_RESIDUALS.get(name),
                "old_dense_residual_dex": old_dense["max_abs_dex"],
                "new_residual_dex": new_stats["max_abs_dex"],
                "new_rmse_dex": new_stats["rmse_dex"],
                "A": new_coeff[0],
                "B": new_coeff[1],
                "C": new_coeff[2],
                "confidence_tier": confidence_tier(new_stats["max_abs_dex"]),
            }
        )

    payload = {
        "grid": {
            "feedstocks": list(args.feedstocks),
            "temperature_K": temps,
            "temperature_count": len(temps),
            "fO2_convention": "VapoRock redox_buffer(T, 'IW')",
            "activity_convention": "runtime fallback pO2 floor 1e-9 bar; cleaned VapoRock oxide basis renormalized to 100 wt%",
            "c_bound_K": args.c_bound,
        },
        "vaporock": {
            "module_file": getattr(vaporock, "__file__", "unknown"),
        },
        "rows": rows,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"grid: {len(args.feedstocks)} feedstocks x {len(temps)} temperatures = "
            f"{len(args.feedstocks) * len(temps)} cells; C bound +/-{args.c_bound:g} K"
        )
        print("| species | n | old metadata | old dense | new dense | rmse | A | B | C | tier |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
        for row in rows:
            print(
                "| {species} | {n_samples} | {old_metadata_residual_dex:.3f} | "
                "{old_dense_residual_dex:.3f} | {new_residual_dex:.3f} | "
                "{new_rmse_dex:.3f} | {A:.6f} | {B:.6f} | {C:.6f} | "
                "{confidence_tier} |".format(**row)
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
