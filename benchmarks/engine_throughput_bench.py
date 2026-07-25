#!/usr/bin/env python3
"""Contention-robust throughput authority for the simulator perf ratchet.

This harness guards the call-volume/CPU-cost incident class that let composed
full-run tests expand toward multi-hour CI failures. Each stage proves a named
hot-path counter moved before reporting a rate.

FIX THE CODE, DO NOT WEAKEN OR DELETE THIS BENCHMARK.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())

from simulator.backends import SimulatorBuildConfig, build_simulator
from simulator.chemistry.ellingham_graph import (
    effective_equilibrium_pressure_Pa,
)
from simulator.condensation import CondensationModel
from simulator.config import ConfigBundle, load_config_bundle
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import (
    CampaignPhase,
    CondensationTrain,
    EvaporationFlux,
    MeltState,
)


BASELINE_PATH = REPO_ROOT / "tests" / "perf" / "perf_ratchet_baselines.json"
MACHINE_CLASS = "M5 MacBook Pro"
STAGE_NAMES = (
    "internal_analytical_equilibrium",
    "vapor_pressure_eval",
    "sim_step_c2a_hour",
    "condensation_route_hour",
    "runner_fixture_replay",
)
TEMPERATURES_K = (1273.15, 1573.15, 1873.15)
# Premise: one runner replay is about 5 s wall and its isolated
# warmup-plus-three-trial stage is under 23 s wall on this class. Algebra:
# 12*5 = 60 s per replay and 8*22.5 = 180 s per stage worker. Units:
# dimensionless headroom * s = s.
# Sanity: both walls exceed the rev-4 8x contention headroom and are hang
# protection only; neither value enters a ratchet comparison.
RUNNER_TRIAL_HANG_WALL_S = 60
STAGE_WORKER_HANG_WALL_S = 180
BUILTIN_LIQUIDUS_FIXTURE = {
    "source": "perf-ratchet-builtin-fixture",
    "solidus_T_C": 1100.0,
    "liquidus_T_C": 1400.0,
    "path": ((1100.0, 0.0), (1400.0, 1.0)),
}
PROTOCOL = {
    "warmups": 1,
    "trials": 3,
    "aggregation": "median_trial_rate",
    "isolation": "one_process_per_stage",
    "in_process_timer": "time.process_time",
    "subprocess_timer": "resource.getrusage(RUSAGE_CHILDREN)",
    "stage_work_units_per_trial": {
        "internal_analytical_equilibrium": 500,
        "vapor_pressure_eval": 36000,
        "sim_step_c2a_hour": 1,
        "condensation_route_hour": 4,
        "runner_fixture_replay": 1,
    },
    "hot_path_calls_per_trial": {
        "internal_analytical_equilibrium": 500,
        "vapor_pressure_eval": 36000,
        "sim_step_c2a_hour": 1,
        "condensation_route_hour": 164,
        "runner_fixture_replay": 24,
    },
}


@dataclass(frozen=True)
class TrialObservation:
    work_units: int
    hot_path_calls: int
    cpu_seconds: float
    details: Mapping[str, Any]


def protocol_metadata() -> dict[str, Any]:
    return json.loads(json.dumps(PROTOCOL))


def detect_machine_class() -> str:
    if platform.system() != "Darwin":
        return f"{platform.system()} {platform.machine()}"
    try:
        result = subprocess.run(
            ["system_profiler", "SPHardwareDataType"],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return f"macOS {platform.machine()} (hardware unknown)"
    hardware = result.stdout
    if "Model Name: MacBook Pro" in hardware and "Chip: Apple M5" in hardware:
        return MACHINE_CLASS
    model = _hardware_value(hardware, "Model Identifier")
    chip = _hardware_value(hardware, "Chip")
    return f"macOS {model or 'unknown-model'} {chip or platform.machine()}"


def _hardware_value(hardware: str, label: str) -> str:
    prefix = f"{label}:"
    for line in hardware.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return ""


def _children_cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    # Premise: child work consumes user CPU plus kernel CPU. Algebra:
    # CPU_total = ru_utime + ru_stime. Units: s + s = s. Sanity: cumulative
    # child CPU cannot decrease within one parent process.
    return float(usage.ru_utime + usage.ru_stime)


def _build_lunar_c2a_sim(bundle: ConfigBundle):
    backend = InternalAnalyticalBackend()
    assert backend.initialize({})
    sim = build_simulator(
        SimulatorBuildConfig(
            backend=backend,
            setpoints=bundle.setpoints,
            feedstocks=bundle.feedstocks,
            vapor_pressures=bundle.vapor_pressures,
            materials=bundle.materials,
        )
    )
    sim.load_batch("lunar_mare_low_ti", 1000.0)
    sim.start_campaign(CampaignPhase.C2A)
    return sim


def _fixed_melt_vector(sim) -> tuple[Any, ...]:
    return (
        float(sim.melt.temperature_C),
        float(sim.melt.p_total_mbar),
        float(sim.melt.pO2_mbar or 0.0),
        tuple(sorted((str(k), float(v)) for k, v in sim.melt.composition_kg.items())),
    )


def _internal_equilibrium_trial_factory(
    bundle: ConfigBundle,
) -> Callable[[], TrialObservation]:
    sim = _build_lunar_c2a_sim(bundle)
    sim.melt.temperature_C = 1500.0
    vector_before = _fixed_melt_vector(sim)
    original = sim._internal_analytical_equilibrium
    counter = {"calls": 0}

    def counted_equilibrium():
        counter["calls"] += 1
        return original()

    sim._internal_analytical_equilibrium = counted_equilibrium
    work_units = PROTOCOL["stage_work_units_per_trial"][
        "internal_analytical_equilibrium"
    ]

    def trial() -> TrialObservation:
        calls_before = counter["calls"]
        cpu_before = time.process_time()
        result = None
        for _ in range(work_units):
            result = sim._internal_analytical_equilibrium()
        cpu_seconds = time.process_time() - cpu_before
        hot_path_calls = counter["calls"] - calls_before
        assert result is not None and result.status == "ok"
        assert result.vapor_pressures_Pa
        assert hot_path_calls == work_units
        assert _fixed_melt_vector(sim) == vector_before
        return TrialObservation(
            work_units=work_units,
            hot_path_calls=hot_path_calls,
            cpu_seconds=cpu_seconds,
            details={
                "hot_path": "PyrolysisSimulator._internal_analytical_equilibrium",
                "fixed_melt_species": len(sim.melt.composition_kg),
            },
        )

    return trial


def _vapor_pressure_trial_factory(
    bundle: ConfigBundle,
) -> Callable[[], TrialObservation]:
    roster = tuple(
        sorted(
            set(bundle.vapor_pressures.get("metals", {}))
            | set(bundle.vapor_pressures.get("oxide_vapors", {}))
        )
    )
    expected_calls = len(roster) * len(TEMPERATURES_K)
    configured_work = PROTOCOL["stage_work_units_per_trial"]["vapor_pressure_eval"]
    assert configured_work % expected_calls == 0
    roster_sweeps = configured_work // expected_calls

    def trial() -> TrialObservation:
        hot_path_calls = 0
        positive_pressures = 0
        cpu_before = time.process_time()
        for _ in range(roster_sweeps):
            for temperature_K in TEMPERATURES_K:
                for species in roster:
                    pressure = effective_equilibrium_pressure_Pa(
                        species,
                        temperature_K,
                        1.0e-9,
                        vapor_pressure_data=bundle.vapor_pressures,
                        a_oxide=0.5,
                    )
                    hot_path_calls += 1
                    positive_pressures += pressure > 0.0
        cpu_seconds = time.process_time() - cpu_before
        assert positive_pressures > 0
        assert hot_path_calls == configured_work
        return TrialObservation(
            work_units=configured_work,
            hot_path_calls=hot_path_calls,
            cpu_seconds=cpu_seconds,
            details={
                "hot_path": "effective_equilibrium_pressure_Pa",
                "roster": roster,
                "temperatures_K": TEMPERATURES_K,
            },
        )

    return trial


def _install_builtin_liquidus_fixture(sim) -> None:
    sim._melt_redox_liquidus_gate_curve = lambda: dict(
        BUILTIN_LIQUIDUS_FIXTURE
    )


def _sim_step_trial_factory(
    bundle: ConfigBundle,
) -> Callable[[], TrialObservation]:
    def trial() -> TrialObservation:
        sim = _build_lunar_c2a_sim(bundle)
        _install_builtin_liquidus_fixture(sim)
        sim.melt.temperature_C = 1450.0
        for _ in range(4):
            sim.step()
        assert sim.melt.campaign == CampaignPhase.C2A
        assert sim.melt.campaign_hour == 4

        original = sim._internal_analytical_equilibrium
        counter = {"equilibrium_calls": 0}

        def counted_equilibrium():
            counter["equilibrium_calls"] += 1
            return original()

        sim._internal_analytical_equilibrium = counted_equilibrium
        hour_before = int(sim.melt.hour)
        child_cpu_before = _children_cpu_seconds()
        cpu_before = time.process_time()
        snapshot = sim.step()
        cpu_seconds = time.process_time() - cpu_before
        child_cpu_seconds = _children_cpu_seconds() - child_cpu_before
        assert child_cpu_seconds == 0.0, (
            "sim_step_c2a_hour spawned a child process; the stage must remain "
            "internal-analytical/builtin"
        )
        assert int(sim.melt.hour) == hour_before + 1
        assert int(snapshot.hour) == int(sim.melt.hour)
        return TrialObservation(
            work_units=1,
            hot_path_calls=counter["equilibrium_calls"],
            cpu_seconds=cpu_seconds,
            details={
                "hot_path": "PyrolysisSimulator.step -> "
                "_internal_analytical_equilibrium",
                "campaign": sim.melt.campaign.name,
                "measured_campaign_hour": sim.melt.campaign_hour,
            },
        )

    return trial


def _condensation_trial_factory(
    bundle: ConfigBundle,
) -> Callable[[], TrialObservation]:
    flux = EvaporationFlux(
        species_kg_hr={
            "Fe": 0.20,
            "Cr": 0.05,
            "SiO": 0.30,
            "Na": 0.10,
            "K": 0.05,
            "Mg": 0.15,
            "Ca": 0.10,
        },
        total_kg_hr=0.95,
    )
    melt = MeltState(temperature_C=1500.0)
    work_units = PROTOCOL["stage_work_units_per_trial"][
        "condensation_route_hour"
    ]

    def trial() -> TrialObservation:
        model = CondensationModel(
            CondensationTrain.create_default(),
            vapor_pressure_data=bundle.vapor_pressures,
            materials=bundle.materials,
            wall_temperature_C=900.0,
        )
        model.configure_operating_conditions(
            overhead_pressure_mbar=1.0,
            gas_temperature_C=1400.0,
            wall_temperature_C=900.0,
            stir_factor=6.0,
            radial_stir_factor=6.0,
            carrier_gas="N2",
            campaign_name="C2A",
            campaign_hour=5.0,
            stage_area_m2_by_stage={
                str(stage.stage_number): 1.0 for stage in model.train.stages
            },
        )
        original = model._condensation_efficiency
        counter = {"efficiency_calls": 0}

        def counted_efficiency(*args, **kwargs):
            counter["efficiency_calls"] += 1
            return original(*args, **kwargs)

        model._condensation_efficiency = counted_efficiency
        cpu_before = time.process_time()
        result = None
        for _ in range(work_units):
            result = model.route(flux, melt)
        cpu_seconds = time.process_time() - cpu_before
        assert result is not None
        assert counter["efficiency_calls"] > work_units
        assert set(result.remaining_by_species) == set(flux.species_kg_hr)
        return TrialObservation(
            work_units=work_units,
            hot_path_calls=counter["efficiency_calls"],
            cpu_seconds=cpu_seconds,
            details={
                "hot_path": "CondensationModel.route -> "
                "_condensation_efficiency",
                "flux_species": tuple(sorted(flux.species_kg_hr)),
            },
        )

    return trial


RUNNER_CHILD_PROBE = r"""
import json
import sys
from pathlib import Path

from simulator.core import PyrolysisSimulator
from simulator import runner

fixture_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
counter_path = Path(sys.argv[3])
expected = json.loads(fixture_path.read_text())
meta = expected["run_metadata"]
step_calls = 0
original_step = PyrolysisSimulator.step

def counted_step(self):
    global step_calls
    step_calls += 1
    return original_step(self)

PyrolysisSimulator.step = counted_step
PyrolysisSimulator._melt_redox_liquidus_gate_curve = (
    lambda self: {
        "source": "perf-ratchet-builtin-fixture",
        "solidus_T_C": 1100.0,
        "liquidus_T_C": 1400.0,
        "path": ((1100.0, 0.0), (1400.0, 1.0)),
    }
)
args = [
    f"--feedstock={meta['feedstock_id']}",
    f"--campaign={meta['campaign']}",
    f"--hours={meta['hours_requested']}",
    f"--mass-kg={meta['mass_kg']}",
    "--backend=internal-analytical",
    "--allow-fallback-vapor",
    "--allow-unmeasured-alpha-fallback",
    "--force-builtin-vapor-pressure",
    f"--output={output_path}",
    "--started-at-utc=2026-05-15T00:00:00Z",
    "--kernel-commit-sha=goal-18-fixture",
]
for species, kg in sorted(meta.get("additives_kg", {}).items()):
    args.append(f"--additive={species}={kg}")
return_code = runner.main(args)
actual = json.loads(output_path.read_text())
counter_path.write_text(json.dumps({
    "return_code": return_code,
    "step_calls": step_calls,
    "status": actual.get("status"),
    "schema_version": actual.get("schema_version"),
    "feedstock_id": actual.get("run_metadata", {}).get("feedstock_id"),
    "campaign": actual.get("run_metadata", {}).get("campaign"),
    "hours_completed": actual.get("run_metadata", {}).get("hours_completed"),
    "backend": actual.get("run_metadata", {}).get("backend"),
}))
raise SystemExit(return_code)
"""


def _runner_trial_factory() -> Callable[[], TrialObservation]:
    fixture_path = (
        REPO_ROOT
        / "tests"
        / "fixtures"
        / "runner"
        / "lunar_mare_low_ti_C0_24h.json"
    )
    expected = json.loads(fixture_path.read_text())
    expected_meta = expected["run_metadata"]

    def trial() -> TrialObservation:
        with tempfile.TemporaryDirectory(prefix="rpp-t414-runner-") as temp_dir:
            output_path = Path(temp_dir) / "actual.json"
            counter_path = Path(temp_dir) / "counter.json"
            child_cpu_before = _children_cpu_seconds()
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    RUNNER_CHILD_PROBE,
                    str(fixture_path),
                    str(output_path),
                    str(counter_path),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                check=False,
                text=True,
                timeout=RUNNER_TRIAL_HANG_WALL_S,
            )
            cpu_seconds = _children_cpu_seconds() - child_cpu_before
            output_tail = (
                output_path.read_text()[-2000:] if output_path.exists() else ""
            )
            counter_text = (
                counter_path.read_text() if counter_path.exists() else ""
            )
            assert result.returncode == 0, (
                f"runner fixture replay failed rc={result.returncode}: "
                f"stdout={result.stdout[-1000:]!r} "
                f"stderr={result.stderr[-1000:]!r} "
                f"counter={counter_text!r} output_tail={output_tail!r}"
            )
            counter = json.loads(counter_path.read_text())
        assert counter["return_code"] == 0
        assert counter["status"] == "ok"
        assert counter["schema_version"] == expected["schema_version"]
        assert counter["feedstock_id"] == expected_meta["feedstock_id"]
        assert counter["campaign"] == expected_meta["campaign"]
        assert counter["hours_completed"] == expected_meta["hours_requested"]
        assert counter["backend"] == "internal-analytical"
        assert counter["step_calls"] == expected_meta["hours_requested"]
        return TrialObservation(
            work_units=1,
            hot_path_calls=int(counter["step_calls"]),
            cpu_seconds=cpu_seconds,
            details={
                "hot_path": "simulator.runner.main -> PyrolysisSimulator.step",
                "fixture": str(fixture_path.relative_to(REPO_ROOT)),
                "hours_replayed": int(counter["hours_completed"]),
            },
        )

    return trial


def _assert_trial(stage: str, observation: TrialObservation) -> None:
    expected_work = PROTOCOL["stage_work_units_per_trial"][stage]
    expected_hot_path_calls = PROTOCOL["hot_path_calls_per_trial"][stage]
    assert observation.work_units == expected_work, (
        f"{stage}: measured work {observation.work_units}, expected {expected_work}"
    )
    assert observation.hot_path_calls == expected_hot_path_calls, (
        f"{stage}: intended hot-path counter={observation.hot_path_calls}, "
        f"expected={expected_hot_path_calls}; refusing to report a benchmark "
        "that may measure the wrong path or changed call volume"
    )
    assert observation.cpu_seconds > 0.0, (
        f"{stage}: CPU timer did not advance"
    )


def _measure_stage(
    stage: str,
    trial: Callable[[], TrialObservation],
) -> dict[str, Any]:
    for _ in range(PROTOCOL["warmups"]):
        _assert_trial(stage, trial())
    observations = [trial() for _ in range(PROTOCOL["trials"])]
    for observation in observations:
        _assert_trial(stage, observation)
    # Premise: each trial completes W declared work units in C CPU seconds.
    # Algebra: trial_rate = W/C; aggregate = median(trial_rate). Units:
    # work / CPU-s. Sanity: fixed positive W and C produce a positive rate,
    # while a slower hot path lowers the rate.
    rates = [
        observation.work_units / observation.cpu_seconds
        for observation in observations
    ]
    return {
        "rate": statistics.median(rates),
        "rate_unit": "work_units_per_cpu_second",
        "work_units": sum(item.work_units for item in observations),
        "hot_path_calls": sum(item.hot_path_calls for item in observations),
        "cpu_seconds": sum(item.cpu_seconds for item in observations),
        "trial_rates": rates,
        "details": dict(observations[-1].details),
    }


def _measure_one(stage: str) -> dict[str, Any]:
    bundle = load_config_bundle()
    trial_factories = {
        "internal_analytical_equilibrium": _internal_equilibrium_trial_factory(
            bundle
        ),
        "vapor_pressure_eval": _vapor_pressure_trial_factory(bundle),
        "sim_step_c2a_hour": _sim_step_trial_factory(bundle),
        "condensation_route_hour": _condensation_trial_factory(bundle),
        "runner_fixture_replay": _runner_trial_factory(),
    }
    return _measure_stage(stage, trial_factories[stage])


def measure_all() -> dict[str, dict[str, Any]]:
    measurements = {}
    for stage in STAGE_NAMES:
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--stage-worker",
                stage,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=STAGE_WORKER_HANG_WALL_S,
        )
        assert result.returncode == 0, (
            f"{stage} isolated measurement failed rc={result.returncode}: "
            f"stdout={result.stdout[-2000:]!r} "
            f"stderr={result.stderr[-2000:]!r}"
        )
        measurements[stage] = json.loads(result.stdout)
    return measurements


def load_baselines(path: Path = BASELINE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text())


def reblessed_payload(
    baseline: Mapping[str, Any],
    measurements: Mapping[str, Mapping[str, Any]],
    *,
    machine_class: str,
) -> dict[str, Any]:
    payload = json.loads(json.dumps(baseline))
    if payload["machine_class"] != machine_class:
        raise RuntimeError(
            "refusing cross-machine rebless: "
            f"baseline={payload['machine_class']!r}, current={machine_class!r}"
        )
    if payload["protocol"] != protocol_metadata():
        raise RuntimeError("baseline protocol does not match benchmark protocol")
    for stage in STAGE_NAMES:
        observed = float(measurements[stage]["rate"])
        current = float(payload["stages"][stage]["ratchet_rate"])
        payload["stages"][stage]["ratchet_rate"] = max(current, observed)
        payload["stages"][stage]["rate_unit"] = measurements[stage]["rate_unit"]
        payload["stages"][stage]["hot_path"] = measurements[stage]["details"][
            "hot_path"
        ]
    return payload


def _write_baselines(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure contention-robust simulator throughput ratchets."
    )
    parser.add_argument(
        "--rebless-ratchet",
        action="store_true",
        help="raise checked-in ratchets to the current measured rates",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="ratchet baseline JSON path",
    )
    parser.add_argument(
        "--stage-worker",
        choices=STAGE_NAMES,
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.stage_worker:
        print(json.dumps(_measure_one(args.stage_worker), sort_keys=True))
        return 0
    machine_class = detect_machine_class()
    measurements = measure_all()
    if args.rebless_ratchet:
        baseline = load_baselines(args.baseline)
        payload = reblessed_payload(
            baseline,
            measurements,
            machine_class=machine_class,
        )
        _write_baselines(args.baseline, payload)
    print(
        json.dumps(
            {
                "machine_class": machine_class,
                "protocol": protocol_metadata(),
                "measurements": measurements,
                "reblessed": bool(args.rebless_ratchet),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
