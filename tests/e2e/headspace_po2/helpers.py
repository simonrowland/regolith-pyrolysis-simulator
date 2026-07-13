from __future__ import annotations

import copy
from pathlib import Path
from typing import Optional

import yaml

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import CampaignPhase


ROOT = Path(__file__).resolve().parents[3]


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "data" / name).read_text())


def build_headspace_sim(
    *,
    enabled: bool,
    campaign: CampaignPhase = CampaignPhase.C0,
    start_temperature_C: Optional[float] = None,
) -> PyrolysisSimulator:
    """Build a simulator pinned to a single campaign for headspace tests.

    ``start_temperature_C`` lets callers pre-position the melt before
    ``start_campaign`` so a hot-T anchor (e.g. C2A in its peak SiO
    1400-1600 C window) is reachable without a full thermal ramp.  When
    ``None`` the simulator's default initial temperature (25 C from
    :class:`MeltState`) is kept, which matches the C0 vacuum-bakeoff
    entry condition used by the toggle-mechanics regressions.
    """
    setpoints = copy.deepcopy(_load_yaml("setpoints.yaml"))
    setpoints.setdefault("chemistry_kernel", {})["allow_fallback_vapor"] = True
    # Pending t-194 grounded Cr/Mn alphas; alpha=1.0 prototype fallback.
    setpoints["chemistry_kernel"]["allow_unmeasured_alpha_fallback"] = True
    setpoints["overhead_headspace"]["enabled"] = bool(enabled)

    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        setpoints,
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    if start_temperature_C is not None:
        sim.melt.temperature_C = float(start_temperature_C)
    sim.start_campaign(campaign)
    return sim


def run_campaign_headspace(
    *,
    enabled: bool,
    hours: int,
    campaign: CampaignPhase = CampaignPhase.C0,
    start_temperature_C: Optional[float] = None,
):
    """Step a single-campaign headspace simulator and capture per-hour state.

    Returns ``(sim, snapshots, hour_trace, sio_cumulative_kg)``.  The
    ``hour_trace`` keys are 1-based ``snapshot.hour`` values.  Pinning
    a hot-T anchor (e.g. for the finite-pO2 vs IW SiO regression)
    requires both ``campaign=CampaignPhase.C2A`` and an explicit
    ``start_temperature_C`` inside the SiO Antoine valid range
    (``valid_range_K: [1400, 2200]`` -> >=1126.85 C); below that floor
    the kernel returns no ``vapor_pressures_Pa['SiO']`` entry and the
    finite-pO2 vs IW ratio is undefined.
    """
    sim = build_headspace_sim(
        enabled=enabled,
        campaign=campaign,
        start_temperature_C=start_temperature_C,
    )
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


def run_c0_headspace(*, enabled: bool, hours: int = 24):
    """Backwards-compatible C0 vacuum-bakeoff entry point.

    Preserved for the toggle-mechanics regressions
    (``test_headspace_po2_regression.py``) that depend on the
    C0 atmosphere model.  New hot-T regressions should call
    :func:`run_campaign_headspace` directly.
    """
    return run_campaign_headspace(
        enabled=enabled, hours=hours, campaign=CampaignPhase.C0
    )
