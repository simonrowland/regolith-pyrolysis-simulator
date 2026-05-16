from __future__ import annotations

import math

import pytest

from simulator.chemistry.kernel import ChemistryIntent

from .helpers import run_c0_headspace


def test_vaporock_sio_iw_vs_vacuum_floor_hour_12():
    sim, _snapshots, hour_trace, _sio_cumulative_kg = run_c0_headspace(
        enabled=True,
        hours=24,
    )
    hour12 = hour_trace[12]
    p_sio_finite = hour12["p_SiO_Pa"]
    temperature_C = hour12["temperature_C"]
    fO2_log_iw = sim._compute_intrinsic_melt_fO2(temperature_C + 273.15)
    iw_result = sim._chem_kernel.dispatch(
        ChemistryIntent.VAPOR_PRESSURE,
        temperature_C=temperature_C,
        pressure_bar=max(sim.melt.p_total_mbar / 1000.0, 1.0e-9),
        fO2_log=fO2_log_iw,
        control_inputs={"pO2_bar": 10.0 ** fO2_log_iw},
    )
    p_sio_iw = dict(iw_result.diagnostic or {}).get(
        "vapor_pressures_Pa", {}
    ).get("SiO")

    if not p_sio_finite or not p_sio_iw:
        pytest.fail(
            "hour-12 pinned SiO ratio unavailable: "
            f"T={temperature_C:.1f} C, "
            f"p_SiO_finite={p_sio_finite}, p_SiO_IW={p_sio_iw}"
        )

    decade = abs(math.log10(p_sio_finite / p_sio_iw))
    assert decade <= 0.3
