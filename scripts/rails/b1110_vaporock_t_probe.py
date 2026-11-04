#!/usr/bin/env python3
"""Probe VapoRock temperature behavior through the project adapter."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping

import yaml

from engines.yaml_loader import YAML12SafeLoader
from engines.engine_commissioning import VAPOROCK_T_MAX_K, VAPOROCK_T_MIN_K
from simulator.melt_backend.vaporock import (
    VapoRockBackend,
    vaporock_sum_pressure_bar,
)
from simulator.state import OXIDE_SPECIES

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "docs-private/research/2026-11-03-b1110-vaporock-window/probe.json"
)

TEMPERATURE_GRID_K = tuple(range(1000, 3001, 50))
ADAPTER_CUTS_K = (int(VAPOROCK_T_MIN_K), int(VAPOROCK_T_MAX_K))
ADAPTER_EDGE_PROBES_K = tuple(
    cut + offset for cut in ADAPTER_CUTS_K for offset in (-1, 1)
)
# Probe each JANAF lower interval boundary and one kelvin on either side.
# Comparing adjacent log-pressure changes shows any local jump in slope.
JANAF_LOWER_BOUNDARIES_K = {"Al": 2791, "Fe": 3133, "Mg": 1366}
JANAF_EDGE_PROBES_K = tuple(
    temperature + offset
    for temperature in JANAF_LOWER_BOUNDARIES_K.values()
    for offset in (-1, 0, 1)
)
TEMPERATURES_K = tuple(
    sorted(
        set(TEMPERATURE_GRID_K)
        | set(ADAPTER_EDGE_PROBES_K)
        | set(JANAF_EDGE_PROBES_K)
    )
)

MARE_TEST_FIXTURE_WT_PCT = {
    "SiO2": 45.0,
    "TiO2": 2.5,
    "Al2O3": 10.0,
    "FeO": 18.0,
    "MgO": 10.0,
    "CaO": 11.0,
    "Na2O": 0.4,
    "K2O": 0.1,
    "Cr2O3": 0.4,
    "MnO": 0.3,
}


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.load(path.read_text(encoding="utf-8"), Loader=YAML12SafeLoader)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return payload


def _major_oxide_composition(source: Mapping[str, Any]) -> dict[str, float]:
    raw = source.get("composition_wt_pct")
    if not isinstance(raw, Mapping):
        raise ValueError("Feedstock fixture has no composition_wt_pct mapping")
    return {
        oxide: float(raw[oxide])
        for oxide in OXIDE_SPECIES
        if oxide in raw
    }


def _load_compositions() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    feedstocks = _load_yaml(REPO_ROOT / "data/feedstocks.yaml")
    demaria = _load_yaml(
        REPO_ROOT / "data/literature/extracts/kems-022-demaria-1971.yaml"
    )
    demaria_observation = next(
        observation
        for observation in demaria["species"]["Al2O"]["observations"]
        if observation["observation_id"] == "demaria_1971_al2o_psat_table4"
    )
    demaria_12022 = next(
        point
        for point in demaria_observation["values"]["points"]
        if point["sample"] == "12022"
    )
    demaria_composition = demaria_12022["sample_oxide_composition_wt_pct"]

    compositions = [
        {
            "id": "lunar_mare_test_fixture",
            "source": "tests/test_vaporock_backend.py::_MARE_PROBE_WT_PCT",
            "composition_wt_pct": MARE_TEST_FIXTURE_WT_PCT,
        },
        {
            "id": "lunar_pkt_kreep_average_major_oxides",
            "source": "data/feedstocks.yaml::lunar_pkt_kreep_average",
            "composition_wt_pct": _major_oxide_composition(
                feedstocks["lunar_pkt_kreep_average"]
            ),
            "composition_note": (
                "Uses the fixture's major oxide composition; its trace-element "
                "inventory is outside VapoRock's melt oxide input."
            ),
        },
        {
            "id": "lunar_highlands_lhs1_high_al",
            "source": "data/feedstocks.yaml::lunar_highlands_lhs1",
            "composition_wt_pct": _major_oxide_composition(
                feedstocks["lunar_highlands_lhs1"]
            ),
            "composition_note": (
                "High-Al repository melt fixture (26.29 wt% Al2O3), selected "
                "for the Al2O/AlO probe. The DeMaria 12022 starting bulk "
                "composition is recorded below as source context; it has "
                "8.2 wt% Al2O3 and is not itself an Al-rich melt."
            ),
        },
    ]
    demaria_reference = {
        "source": "data/literature/extracts/kems-022-demaria-1971.yaml",
        "observation_id": demaria_observation["observation_id"],
        "temperature_range_K": demaria_observation["T_range_K"],
        "sample": "12022",
        "composition_wt_pct": demaria_composition,
    }
    return compositions, demaria_reference


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return number if math.isfinite(number) else None


def _log10_pressure_bar(pressure_Pa: Any) -> float | None:
    try:
        pressure = float(pressure_Pa)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(pressure) or pressure <= 0.0:
        return None
    # VapoRock returns Pa here; 1 bar is exactly 100000 Pa.
    return math.log10(pressure / 100000.0)


def _notice_summary(notice: Any) -> dict[str, Any] | None:
    if not isinstance(notice, Mapping):
        return None
    return {
        key: _json_safe(notice[key])
        for key in (
            "kind",
            "reason",
            "authority",
            "certified_band",
            "failed_constraints",
            "warnings",
            "temperature_K",
        )
        if key in notice
    }


def _evaluate_point(
    backend: VapoRockBackend,
    composition: Mapping[str, float],
    species: tuple[str, ...],
    temperature_K: int,
) -> dict[str, Any]:
    result = backend.equilibrate(
        temperature_K - 273.15,
        composition_kg=composition,
        fO2_log=-8.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
    )
    diagnostics = dict(result.diagnostics or {})
    full_speciation = getattr(result, "vaporock_full_speciation_Pa", None)
    if full_speciation is None:
        full_speciation = result.vapor_pressures_Pa or {}
    full_speciation = dict(full_speciation or {})

    total_bar = diagnostics.get("sum_pressure_bar")
    if total_bar is None:
        total_bar = vaporock_sum_pressure_bar(full_speciation)
    try:
        total_bar = float(total_bar)
    except (TypeError, ValueError):
        total_bar = math.nan

    return {
        "temperature_K": temperature_K,
        "inverse_temperature_K_inv": 1.0 / temperature_K,
        "liquid_fraction": 1.0,
        "backend_status": str(result.status),
        "backend_status_reason": diagnostics.get("backend_status_reason"),
        "sum_pressure_bar": _json_safe(total_bar),
        "log10_P_total_bar": (
            math.log10(total_bar)
            if math.isfinite(total_bar) and total_bar > 0.0
            else None
        ),
        "log10_p_i_bar": {
            species_name: _log10_pressure_bar(full_speciation.get(species_name))
            for species_name in species
        },
        "species_missing_from_output": sorted(
            set(species) - set(full_speciation)
        ),
        "notices": {
            "vaporock_temperature": _notice_summary(
                diagnostics.get("vaporock_temperature_notice")
            ),
            "melt_commissioning": _notice_summary(
                diagnostics.get("commissioning_notice")
            ),
        },
    }


def _non_monotonic_intervals(
    points: list[dict[str, Any]], species: tuple[str, ...]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for species_name in species:
        rows = list(points)
        rows.sort(key=lambda point: point["inverse_temperature_K_inv"])
        previous_direction = 0
        previous_change: tuple[dict[str, Any], dict[str, Any], float] | None = None
        for lower, upper in zip(rows, rows[1:]):
            lower_log = lower["log10_p_i_bar"].get(species_name)
            upper_log = upper["log10_p_i_bar"].get(species_name)
            if lower_log is None or upper_log is None:
                previous_direction = 0
                previous_change = None
                continue
            delta = (
                upper_log - lower_log
            )
            # O2 is fixed by fO2, so ~1e-15 dex sign flips are only
            # floating-point noise, not a temperature trend reversal.
            if abs(delta) <= 1e-12:
                continue
            direction = (delta > 0.0) - (delta < 0.0)
            if previous_direction and direction != previous_direction:
                assert previous_change is not None
                before, middle, before_delta = previous_change
                # The adapter returns live statuses only after its inclusive
                # sum-pressure guard passes; do not recalculate the ceiling.
                sum_pressure_guard_passed = all(
                    point["backend_status"] in {"ok", "non_authoritative"}
                    for point in (before, middle, upper)
                )
                findings.append({
                    "species": species_name,
                    "previous_interval_T_K": [
                        before["temperature_K"], middle["temperature_K"]
                    ],
                    "reversing_interval_T_K": [
                        middle["temperature_K"], upper["temperature_K"]
                    ],
                    "previous_delta_log10_p_bar": before_delta,
                    "reversing_delta_log10_p_bar": delta,
                    "below_10_bar": sum_pressure_guard_passed,
                })
            previous_direction = direction
            previous_change = (lower, upper, delta)
    return findings


def _edge_triplet(
    points_by_temperature: Mapping[int, dict[str, Any]],
    cut_K: int,
    species: tuple[str, ...],
) -> dict[str, Any]:
    rows = [points_by_temperature[cut_K + offset] for offset in (-1, 0, 1)]
    return {
        "cut_temperature_K": cut_K,
        "points": [
            {
                "temperature_K": row["temperature_K"],
                "inverse_temperature_K_inv": row["inverse_temperature_K_inv"],
                "backend_status": row["backend_status"],
                "backend_status_reason": row["backend_status_reason"],
                "log10_P_total_bar": row["log10_P_total_bar"],
                "vaporock_temperature_notice": row["notices"][
                    "vaporock_temperature"
                ],
                "melt_commissioning_notice": row["notices"][
                    "melt_commissioning"
                ],
                "log10_p_i_bar": {
                    name: row["log10_p_i_bar"][name] for name in species
                },
            }
            for row in rows
        ],
        "adjacent_log_pressure_changes": [
            {
                "temperature_interval_K": [
                    lower["temperature_K"], upper["temperature_K"]
                ],
                "delta_log10_P_total_bar": (
                    upper["log10_P_total_bar"]
                    - lower["log10_P_total_bar"]
                    if lower["log10_P_total_bar"] is not None
                    and upper["log10_P_total_bar"] is not None
                    else None
                ),
                "delta_log10_p_i_bar": {
                    name: (
                        upper["log10_p_i_bar"][name]
                        - lower["log10_p_i_bar"][name]
                        if lower["log10_p_i_bar"][name] is not None
                        and upper["log10_p_i_bar"][name] is not None
                        else None
                    )
                    for name in species
                },
            }
            for lower, upper in zip(rows, rows[1:])
        ],
    }


def _janaf_edge_checks(
    points_by_temperature: Mapping[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    findings = []
    for species, boundary_K in JANAF_LOWER_BOUNDARIES_K.items():
        below_T = boundary_K - 1
        at_T = boundary_K
        above_T = boundary_K + 1
        below = points_by_temperature[below_T]
        at = points_by_temperature[at_T]
        above = points_by_temperature[above_T]
        below_log = below["log10_p_i_bar"].get(species)
        at_log = at["log10_p_i_bar"].get(species)
        above_log = above["log10_p_i_bar"].get(species)

        below_delta = (
            at_log - below_log
            if below_log is not None and at_log is not None
            else None
        )
        above_delta = (
            above_log - at_log
            if at_log is not None and above_log is not None
            else None
        )
        findings.append({
            "species": species,
            "janaf_lower_boundary_K": boundary_K,
            "below_temperature_K": below_T,
            "boundary_temperature_K": at_T,
            "above_temperature_K": above_T,
            "below_log10_p_bar": below_log,
            "boundary_log10_p_bar": at_log,
            "above_log10_p_bar": above_log,
            "delta_below_to_boundary_log10_p_bar": below_delta,
            "delta_boundary_to_above_log10_p_bar": above_delta,
            "change_in_adjacent_delta_log10_p_bar": (
                above_delta - below_delta
                if below_delta is not None and above_delta is not None
                else None
            ),
            "status_by_temperature": {
                str(temperature): {
                    "backend_status": points_by_temperature[temperature][
                        "backend_status"
                    ],
                    "backend_status_reason": points_by_temperature[temperature][
                        "backend_status_reason"
                    ],
                }
                for temperature in (below_T, at_T, above_T)
            },
        })
    return findings


def run_probe() -> dict[str, Any]:
    compositions, demaria_reference = _load_compositions()
    backend = VapoRockBackend()
    if not backend.initialize({"warm_worker": False}):
        raise RuntimeError("VapoRock adapter could not initialize")
    species = tuple(sorted(backend.get_vapor_species()))

    cases = []
    for case in compositions:
        points = [
            _evaluate_point(
                backend,
                case["composition_wt_pct"],
                species,
                temperature_K,
            )
            for temperature_K in TEMPERATURES_K
        ]
        points_by_temperature = {
            point["temperature_K"]: point for point in points
        }
        ceiling_refusals = [
            point
            for point in points
            if point["backend_status_reason"] == "sum_pressure_sanity"
        ]
        liquid_refusals = [
            point
            for point in points
            if point["backend_status_reason"] == "liquid_state"
        ]
        cases.append({
            "id": case["id"],
            "source": case["source"],
            "composition_wt_pct": case["composition_wt_pct"],
            "composition_note": case.get("composition_note"),
            "points": points,
            "analysis": {
                "adapter_cut_checks": [
                    _edge_triplet(points_by_temperature, cut, species)
                    for cut in ADAPTER_CUTS_K
                ],
                "temperature_range_refusals_at_adapter_cuts": [
                    point["temperature_K"]
                    for point in points
                    if point["temperature_K"] in ADAPTER_CUTS_K
                    and point["backend_status_reason"] == "temperature_range"
                ],
                "non_monotonic_log10_p_intervals": _non_monotonic_intervals(
                    points, species
                ),
                "janaf_segment_edge_checks": _janaf_edge_checks(
                    points_by_temperature
                ),
                "sum_pressure_ceiling_refusals": [
                    {
                        "temperature_K": point["temperature_K"],
                        "sum_pressure_bar": point["sum_pressure_bar"],
                        "log10_P_total_bar": point["log10_P_total_bar"],
                        "reason": point["backend_status_reason"],
                    }
                    for point in ceiling_refusals
                ],
                "liquid_gate_refusals": [
                    {
                        "temperature_K": point["temperature_K"],
                        "reason": point["backend_status_reason"],
                    }
                    for point in liquid_refusals
                ],
                "liquid_fraction_supplied": 1.0,
            },
        })

    return {
        "schema_version": 1,
        "adapter": "simulator.melt_backend.vaporock.VapoRockBackend",
        "temperature_schedule_K": list(TEMPERATURES_K),
        "inverse_temperature_unit": "K^-1",
        "pressure_log_unit": "bar",
        "fO2_log10_bar": -8.0,
        "requested_pressure_bar": 1e-6,
        "liquid_fraction": 1.0,
        "species": list(species),
        "demaria_1971_reference": demaria_reference,
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = run_probe()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), "case_count": len(payload["cases"])}))


if __name__ == "__main__":
    main()
