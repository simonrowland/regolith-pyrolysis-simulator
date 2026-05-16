from __future__ import annotations

import copy
from pathlib import Path

import yaml

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.state import CampaignPhase


ROOT = Path(__file__).resolve().parents[3]


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "data" / name).read_text())


def build_headspace_sim(*, enabled: bool) -> PyrolysisSimulator:
    setpoints = copy.deepcopy(_load_yaml("setpoints.yaml"))
    setpoints.setdefault("chemistry_kernel", {})["allow_fallback_vapor"] = True
    setpoints["overhead_headspace"]["enabled"] = bool(enabled)

    backend = StubBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        setpoints,
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.start_campaign(CampaignPhase.C0)
    return sim


def run_c0_headspace(*, enabled: bool, hours: int = 24):
    sim = build_headspace_sim(enabled=enabled)
    snapshots = []
    hour_trace = {}
    sio_cumulative_kg = 0.0
    for _ in range(hours):
        snapshot = sim.step()
        snapshots.append(snapshot)
        sio_cumulative_kg += snapshot.evap_flux.species_kg_hr.get("SiO", 0.0)
        diagnostic = dict(sim._last_vapor_pressure_diagnostic or {})
        hour_trace[snapshot.hour] = {
            "temperature_C": sim.melt.temperature_C,
            "p_O2_bar": (sim._last_overhead_gas_equilibrium or {}).get(
                "p_O2_bar", 0.0
            ),
            "p_SiO_Pa": dict(diagnostic.get("vapor_pressures_Pa") or {}).get(
                "SiO"
            ),
            "fO2_log": sim.melt.fO2_log,
        }
    return sim, snapshots, hour_trace, sio_cumulative_kg
