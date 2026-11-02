#!/usr/bin/env python3
"""Reproduce LOOP-1 headspace backpressure on the canonical campaigns.

Prints each hour's evaporation flux and melt-headspace partials for the
campaigns in the b-773 brief, and counts hours whose nonzero species set
is disjoint from the previous hour. This script only runs the simulator
and reports; the flux law stays in the evaporation owner.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# kg/hr. Matches the depletion dust floor in the evaporation owner.
_NONZERO_KG_HR = 1.0e-12
# mbar. A partial below this is the empty-headspace write.
_NONZERO_MBAR = 1.0e-12
# Ledger ids for the two silicon vapours. SiO2(g) is stored as SiO2_gas.
_SIO_KEYS = frozenset({"SiO"})
_SIO2_KEYS = frozenset({"SiO2", "SiO2_gas"})


def _run_case(case: dict) -> dict:
    from simulator.run_executor import RunExecutor
    from simulator.runner import PyrolysisRun

    additives = dict(case.get("additives_kg") or {})
    run = PyrolysisRun(
        feedstock_id=case["feedstock_id"],
        campaign=case["campaign"],
        hours=int(case["hours"]),
        mass_kg=float(case.get("mass_kg", 1000.0)),
        additives_kg=additives,
        engines={"vapor_pressure": "builtin-antoine"},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        force_builtin_vapor_pressure=True,
        run_metadata_overrides={
            "started_at_utc": "2026-10-10T00:00:00Z",
            "kernel_commit_sha": "b773-evap-osc",
        },
    )
    session = run._start_session()
    # The melt is constructed at 25 °C. C2A then ramps at 15 °C/h, so 24 h
    # from that start ends at 385 °C, below the 1050 °C window named in
    # setpoints. The cited Mars table enters the window: hour 1 is 1065 °C,
    # one ramp step above 1050 °C. This assignment is the run's initial
    # condition, the same snap the SiO sweep uses.
    initial_temperature_C = case.get("initial_temperature_C")
    if initial_temperature_C is not None:
        session.simulator.melt.temperature_C = float(initial_temperature_C)
    execution = RunExecutor().execute_session(session, hours=int(run.hours))
    sim = session.simulator
    hours = []
    evolved_kg: dict[str, float] = {}
    stage_capture_kg: dict[str, dict[str, float]] = {}
    wall_deposit_kg: dict[str, dict[str, float]] = {}
    for snapshot in sim.record.snapshots:
        flux = {
            str(species): float(rate)
            for species, rate in snapshot.evap_flux.species_kg_hr.items()
            if float(rate) > _NONZERO_KG_HR
        }
        partials = {
            str(species): float(value)
            for species, value in (
                snapshot.melt_headspace_composition_mbar or {}
            ).items()
            if float(value) > _NONZERO_MBAR
        }
        for species, rate in flux.items():
            evolved_kg[species] = evolved_kg.get(species, 0.0) + rate * float(
                snapshot.duration_h
            )
        for (stage, species), kg in (
            snapshot.condensed_by_stage_species_delta or {}
        ).items():
            stage_key = str(stage)
            bucket = stage_capture_kg.setdefault(stage_key, {})
            bucket[str(species)] = bucket.get(str(species), 0.0) + float(kg)
        for (segment, species), kg in (
            snapshot.wall_deposit_by_segment_species_delta or {}
        ).items():
            bucket = wall_deposit_kg.setdefault(str(segment), {})
            bucket[str(species)] = bucket.get(str(species), 0.0) + float(kg)
        hours.append(
            {
                "hour": int(snapshot.hour),
                "campaign": str(getattr(snapshot.campaign, "name", snapshot.campaign)),
                "temperature_C": float(snapshot.temperature_C),
                "duration_h": float(snapshot.duration_h),
                "flux_kg_hr": flux,
                "headspace_partial_mbar": partials,
                "mass_balance_error_pct": float(snapshot.mass_balance_error_pct),
            }
        )
    return {
        "feedstock_id": case["feedstock_id"],
        "campaign": case["campaign"],
        "hours_requested": int(case["hours"]),
        "status": str(getattr(execution, "status", "")),
        "additives_kg": additives,
        "initial_temperature_C": (
            None if initial_temperature_C is None else float(initial_temperature_C)
        ),
        "hours": hours,
        "evolved_kg": evolved_kg,
        "stage_capture_kg": stage_capture_kg,
        "wall_deposit_kg": wall_deposit_kg,
        "alternation": _alternation(hours),
    }


def _nonzero_species(row: dict, key: str) -> set[str]:
    return set(row[key])


def _alternation(hours: list[dict]) -> dict:
    """Count hours whose live set shares nothing with the previous hour.

    A period-2 headspace cycle makes consecutive nonempty flux sets
    disjoint: the species that evaporated are the ones suppressed next
    hour. A species that turns on once as temperature rises is not that
    cycle. ``species_flux_flips`` counts presence changes per species.
    """

    disjoint_flux_hours = []
    disjoint_partial_hours = []
    sio_mutex_hours = []
    flips: dict[str, int] = {}
    previous_present: dict[str, bool] = {}
    for previous, row in zip(hours, hours[1:]):
        flux_now = _nonzero_species(row, "flux_kg_hr")
        flux_prev = _nonzero_species(previous, "flux_kg_hr")
        if flux_now and flux_prev and flux_now.isdisjoint(flux_prev):
            disjoint_flux_hours.append(int(row["hour"]))
        partial_now = _nonzero_species(row, "headspace_partial_mbar")
        partial_prev = _nonzero_species(previous, "headspace_partial_mbar")
        if partial_now and partial_prev and partial_now.isdisjoint(partial_prev):
            disjoint_partial_hours.append(int(row["hour"]))
        sio_now = _silicon_vapour_families(flux_now)
        if len(sio_now) == 1:
            sio_mutex_hours.append(int(row["hour"]))
        for species in flux_now | flux_prev | set(previous_present):
            present = species in flux_now
            if species in previous_present and previous_present[species] != present:
                flips[species] = flips.get(species, 0) + 1
            previous_present[species] = present
        for species in flux_now:
            previous_present.setdefault(species, True)
    if hours:
        first = _nonzero_species(hours[0], "flux_kg_hr")
        if len(_silicon_vapour_families(first)) == 1:
            sio_mutex_hours.insert(0, int(hours[0]["hour"]))
    return {
        "n_hours": len(hours),
        "disjoint_flux_hours": disjoint_flux_hours,
        "disjoint_partial_hours": disjoint_partial_hours,
        "sio_exactly_one_hours": sio_mutex_hours,
        "species_flux_flips": flips,
    }


def _silicon_vapour_families(names: set[str]) -> set[str]:
    families = set()
    if names & _SIO_KEYS:
        families.add("SiO")
    if names & _SIO2_KEYS:
        families.add("SiO2")
    return families


def _cases(which: str) -> list[dict]:
    # initial_temperature_C is the campaign window floor from setpoints
    # (C2A_continuous 1050 °C, C2B 1320 °C). C2A_staged's first stage ramps
    # at 600 °C/h, so it enters its window from the constructed 25 °C.
    lunar = {
        "feedstock_id": "lunar_mare_low_ti",
        "campaign": "C2A",
        "hours": 24,
        "initial_temperature_C": 1050.0,
    }
    mars = {
        "feedstock_id": "mars_basalt",
        "campaign": "C2A",
        "hours": 24,
        "additives_kg": {"C": 30.0},
        "initial_temperature_C": 1050.0,
    }
    staged = {
        "feedstock_id": "lunar_mare_low_ti",
        "campaign": "C2A_staged",
        "hours": 24,
    }
    c2b = {
        "feedstock_id": "ci_carbonaceous_chondrite",
        "campaign": "C2B",
        "hours": 12,
        "initial_temperature_C": 1320.0,
    }
    if which == "reproduction":
        return [lunar, mars]
    if which == "yields":
        return [lunar, mars, staged, c2b]
    raise SystemExit(f"unknown case set {which!r}")


def _engine_note() -> dict:
    toml_path = ROOT / "engines" / "engines.local.toml"
    text = toml_path.read_text() if toml_path.is_file() else ""
    binaries = {}
    for key in ("magemin_binary_path", "alphamelts_binary_path"):
        path = ""
        for line in text.splitlines():
            if line.strip().startswith(key):
                path = line.split("=", 1)[1].strip().strip('"')
        binaries[key] = {
            "path": path,
            "exists": bool(path) and Path(path).is_file(),
        }
    return {
        "hostname": socket.gethostname(),
        "engines_local_toml": str(toml_path),
        "toml_present": toml_path.is_file(),
        "binaries": binaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        choices=("reproduction", "yields"),
        default="reproduction",
    )
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    payload = {"engines": _engine_note(), "cases": []}
    for case in _cases(args.cases):
        label = f"{case['feedstock_id']} {case['campaign']} {case['hours']}h"
        print(f"start {label}", flush=True)
        result = _run_case(case)
        payload["cases"].append(result)
        alt = result["alternation"]
        print(
            f"done {label}: hours={alt['n_hours']} "
            f"disjoint_flux={len(alt['disjoint_flux_hours'])} "
            f"disjoint_partial={len(alt['disjoint_partial_hours'])} "
            f"sio_exactly_one={len(alt['sio_exactly_one_hours'])} "
            f"status={result['status']}",
            flush=True,
        )
        for row in result["hours"]:
            flux = ",".join(
                f"{name}:{rate:.3e}"
                for name, rate in sorted(row["flux_kg_hr"].items())
            )
            partial = ",".join(
                f"{name}:{value:.3e}"
                for name, value in sorted(row["headspace_partial_mbar"].items())
            )
            print(
                f"  h{row['hour']:02d} {row['temperature_C']:.1f}C "
                f"flux[{flux}] partial[{partial}]",
                flush=True,
            )
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.json}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
