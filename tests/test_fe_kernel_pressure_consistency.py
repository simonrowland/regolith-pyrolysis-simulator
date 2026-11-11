"""Fe fouling-authority pressure is the kernel pressure that drives Fe flux.

The reported ``selected_runtime_pressure`` on the Fe carrier is the batch
channel serialized at ``simulator/evaporation.py`` (the copy that lands on
``vapour_carrier_authority_by_species``). The kernel reads that same tick's
``vapour_batch_flux_pressures_Pa`` map at
``engines/builtin/evaporation_flux.py``. Fe's number is the Ellingham
selection published onto the channel, not a second pressure.
"""

from __future__ import annotations

import pytest

from simulator import engine_local_config
from simulator.chemistry.kernel import ChemistryIntent
from simulator.core import PyrolysisSimulator
from simulator.vapour_rail.instrumentation import CONTROL_FLUX_PRESSURES_KEY
from tests.binding_admission_fixtures import isolate_engine_local_config
from tests.test_runner_smoke import SCENARIOS, _run_scenario


def _fe_selected_runtime_pa(report):
    if not isinstance(report, dict):
        return None
    channels = report.get("channels_by_species")
    if not isinstance(channels, dict):
        return None
    channel = channels.get("Fe")
    if not isinstance(channel, dict):
        return None
    pressure = channel.get("selected_runtime_pressure")
    if not isinstance(pressure, dict) or pressure.get("kind") != "value":
        return None
    pa = pressure.get("pa")
    if isinstance(pa, bool) or not isinstance(pa, (int, float)):
        return None
    return float(pa)


def test_lunar_c0_fe_authority_pressure_matches_kernel_flux_pressure(tmp_path):
    """Smallest lunar C0 run that reaches a flux-active Fe tick.

    Hour 1 of ``lunar_mare_low_ti_C0_24h`` dispatches evaporation with Fe
    in the kernel flux map. The carrier record and the kernel input are
    taken from that dispatch, before a later tick can merge another record
    onto the fouling notice.
    """

    pairs: list[tuple[float, float]] = []
    original = PyrolysisSimulator._dispatch_only

    def capture_fe_flux_pressure(self, intent, *, control_inputs, **kwargs):
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            kernel_map = control_inputs.get(CONTROL_FLUX_PRESSURES_KEY) or {}
            reported = _fe_selected_runtime_pa(
                getattr(self, "_last_vapour_batch_report", None)
            )
            if "Fe" in kernel_map and reported is not None:
                pairs.append((reported, float(kernel_map["Fe"])))
        return original(self, intent, control_inputs=control_inputs, **kwargs)

    scenario = {**SCENARIOS[0], "hours": 1}
    PyrolysisSimulator._dispatch_only = capture_fe_flux_pressure
    try:
        with isolate_engine_local_config(
            tmp_path / "engines",
            source_path=engine_local_config.config_path(),
        ):
            payload = _run_scenario(scenario)
    finally:
        PyrolysisSimulator._dispatch_only = original

    assert payload["status"] == "ok"
    assert pairs, "lunar C0 did not reach a flux-active Fe tick"
    for reported_pa, kernel_pa in pairs:
        assert reported_pa == pytest.approx(kernel_pa, rel=1e-12)
