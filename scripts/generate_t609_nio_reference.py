#!/usr/bin/env python3
"""Reproduce the t-609 NiO liquid-reference Antoine fit and evidence grid.

Premise: NiO(l) -> Ni(g) + 0.5 O2(g), at unit NiO activity and 1 bar O2.
Algebra: log10(p_Ni/Pa) = 5 - delta_G_standard/(R*T*ln(10)).
Units: every Gibbs term is J/mol; R is J/(mol K); T is K; pressure is Pa.
Sanity: composing the result with Ni + 0.5 O2 -> NiO cancels O2 exactly.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import math
import platform
import sys
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy.optimize import minimize_scalar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXTRACT = ROOT / "data/literature/extracts/nasa-cea-thermo.yaml"
OUTPUT = (
    ROOT
    / "validation-data/pin-evidence/nio_liquid_reference_fit_grid_2026-08-10.yaml"
)
R_J_MOL_K = 8.314462618
LN10 = math.log(10.0)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed-as-distribution"


def _load_cea_gas(name: str) -> Any:
    from simulator.vapour_rail.nasa_cea import NasaCeaPolynomial, Nasa9Segment

    raw = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    expected_id = f"cea_{name}_gibbs"
    observations = raw["species"][name]["observations"]
    matches = [
        observation
        for observation in observations
        if observation.get("observation_id") == expected_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one {expected_id!r} observation for {name}; "
            f"found {len(matches)}"
        )
    observation = matches[0]
    if observation.get("type") != "gibbs_table":
        raise ValueError(f"{expected_id} must be a gibbs_table observation")
    if observation.get("phase") != "gas":
        raise ValueError(f"{expected_id} must describe the gas phase")
    values = observation["values"]
    segments = tuple(
        Nasa9Segment(
            float(segment["T_min_K"]),
            float(segment["T_max_K"]),
            tuple(float(value) for value in segment["a_coefficients"]),
            float(segment["b1"]),
            float(segment["b2"]),
        )
        for segment in values["segments"]
    )
    return NasaCeaPolynomial(
        name=name,
        family="nasa_cea_9",
        standard_state="gas",
        segments=segments,
        formula=str(values.get("formula", name)),
        delta_f_H_298_15_J_per_mol=values.get(
            "delta_f_H_298_15_J_per_mol"
        ),
        reference_pressure_Pa=float(values.get("reference_pressure_Pa", 1.0e5)),
        citation=values.get("citation"),
    )


def _liquid_nio_reference() -> tuple[Any, dict[str, Any]]:
    import vaporock

    system = vaporock.System(vapor_database="JANAF")
    liquid = system.liq_phs
    endmember_names = [str(name) for name in liquid.endmember_names]
    n_endmembers = len(endmember_names)
    oxide_names = [str(name) for name in system.OXIDES]
    transform = np.asarray(system.MOL_LIQ_OXIDES, dtype=float)
    transformed_oxide_names = oxide_names[: transform.shape[0]]
    if "NiO" not in transformed_oxide_names:
        raise RuntimeError("VapoRock liquid transform has no NiO oxide row")

    def mu0_nio(T_K: float, pressure_bar: float = 1.0) -> float:
        endmember_mu0 = np.array(
            [
                float(
                    np.squeeze(
                        liquid.gibbs_energy(
                            T_K,
                            pressure_bar,
                            mol=np.eye(n_endmembers)[index],
                        )
                    )
                )
                for index in range(n_endmembers)
            ]
        )
        oxide_mu0 = np.dot(transform, endmember_mu0)
        return float(
            dict(zip(transformed_oxide_names, oxide_mu0, strict=True))["NiO"]
        )

    identity = {
        "vaporock_database": "JANAF",
        "liquid_model_module": type(liquid).__module__,
        "liquid_model_class": type(liquid).__name__,
        "endmember_names": endmember_names,
        "oxide_basis": transformed_oxide_names,
        "nio_transform_row": [
            float(value)
            for value in transform[transformed_oxide_names.index("NiO")]
        ],
    }
    return mu0_nio, identity


def _log10_pref_pa(
    ni_gas: Any,
    o2_gas: Any,
    mu0_nio: Any,
    T_K: float,
) -> float:
    delta_g_J_mol = (
        ni_gas.evaluate(T_K).g_J_per_mol
        + 0.5 * o2_gas.evaluate(T_K).g_J_per_mol
        - mu0_nio(T_K)
    )
    return 5.0 - delta_g_J_mol / (R_J_MOL_K * T_K * LN10)


def _fit_antoine(
    temperatures_K: np.ndarray,
    log10_source_pa: np.ndarray,
) -> tuple[dict[str, float], np.ndarray]:
    def residual(C_K: float) -> tuple[float, float, float]:
        inverse_temperature = 1.0 / (temperatures_K + C_K)
        design = np.column_stack(
            [np.ones_like(temperatures_K), -inverse_temperature]
        )
        coefficients, _, _, _ = np.linalg.lstsq(
            design, log10_source_pa, rcond=None
        )
        predicted = coefficients[0] - coefficients[1] * inverse_temperature
        return (
            float(np.max(np.abs(predicted - log10_source_pa))),
            float(coefficients[0]),
            float(coefficients[1]),
        )

    optimization = minimize_scalar(
        lambda value: residual(float(value))[0],
        bounds=(-400.0, 400.0),
        method="bounded",
    )
    C_K = float(optimization.x)
    max_abs_dex, A, B_K = residual(C_K)
    predicted = A - B_K / (temperatures_K + C_K)
    fit = {
        "A": A,
        "B_K": B_K,
        "C_K": C_K,
        "max_abs_residual_dex": max_abs_dex,
        "rms_residual_dex": float(
            np.sqrt(np.mean((predicted - log10_source_pa) ** 2))
        ),
    }
    return fit, predicted


def main() -> int:
    ni_gas = _load_cea_gas("Ni")
    o2_gas = _load_cea_gas("O2")
    mu0_nio, liquid_identity = _liquid_nio_reference()
    # Preserve the regular 10 K grid and include the declared upper endpoint.
    temperatures_K = np.append(np.arange(1400.0, 2273.15, 10.0), 2273.15)
    source = np.array(
        [
            _log10_pref_pa(ni_gas, o2_gas, mu0_nio, float(T_K))
            for T_K in temperatures_K
        ]
    )
    fit, predicted = _fit_antoine(temperatures_K, source)

    rows = [
        {
            "temperature_K": float(T_K),
            "source_log10_pressure_Pa": float(observed),
            "fit_log10_pressure_Pa": float(modelled),
            "residual_dex": float(modelled - observed),
        }
        for T_K, observed, modelled in zip(
            temperatures_K, source, predicted, strict=True
        )
    ]
    payload = {
        "schema_version": 1,
        "kind": "vapour_rail_computational_fit_grid",
        "source_id": "t609-feo-nio-composition",
        "observation_id": "nio_liquid_reference_fit",
        "date": "2026-08-10",
        "validation_use": "structural_computational_provenance_only",
        "certification_ceiling": "never",
        "condensed_form": "liquid_melt",
        "material_system_class": "thermodynamic_model_liquid_oxide_endmember",
        "reaction": "NiO(l) -> Ni(g) + 0.5 O2(g)",
        "reference_state": {
            "NiO_activity": 1.0,
            "O2_fugacity_bar": 1.0,
            "standard_pressure_Pa": 100000.0,
        },
        "derivation": {
            "equation": (
                "log10(p_Ni/Pa) = 5 - delta_G_standard/(R*T*ln(10))"
            ),
            "liquid_basis": (
                "ThermoEngine MELTS pure-endmember G transformed with "
                "VapoRock MOL_LIQ_OXIDES"
            ),
            "gas_source": "NASA CEA9 cea_Ni_gibbs + cea_O2_gibbs",
        },
        "software": {
            "python": platform.python_version(),
            "numpy": _package_version("numpy"),
            "scipy": _package_version("scipy"),
            "vaporock": _package_version("vaporock"),
            "thermoengine": _package_version("thermoengine"),
            **liquid_identity,
        },
        "source_digests_sha256": {
            "generator": _sha256(Path(__file__)),
            "nasa_cea_extract": _sha256(EXTRACT),
        },
        "grid": {
            "requested_domain_K": [1400.0, 2273.15],
            "sampled_domain_K": [
                float(temperatures_K[0]),
                float(temperatures_K[-1]),
            ],
            "step_K": 10.0,
            "n_points": int(len(temperatures_K)),
        },
        "antoine_log10_pa": fit,
        "rows": rows,
    }
    OUTPUT.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    print(
        f"wrote={OUTPUT.relative_to(ROOT)} points={len(rows)} "
        f"max_abs_dex={fit['max_abs_residual_dex']:.12g} "
        f"rms_dex={fit['rms_residual_dex']:.12g}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
