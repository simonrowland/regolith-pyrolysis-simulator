"""Pin of the b-763 C2A oxygen-exchange dn and melt log fO2.

The tests-only commit recorded the pre-fix capacitor series. These
rows are the fixed-pressure boundary, engines live: a pumped headspace
no longer caps dn at the 1e-9 mol floor inventory. Each row is
(hour, T_C, melt log10 fO2, exchange O2 mol).
"""

from __future__ import annotations

import pytest

from simulator.backend_names import ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
from simulator.runner import (
    DATA_DIR,
    PyrolysisRun,
    _apply_sio_wall_sweep_controls,
    _prepare_sio_campaign_start,
    _required_stage0_carbon_kg,
    load_config_bundle,
)


# One live 24 h campaign each. xdist must not coschedule them.
pytestmark = [pytest.mark.serial, pytest.mark.xdist_group("serial")]

# (hour, T_C, melt log10 fO2, dn_mol). Mars, C2A 24 h, 1000 kg.
_MARS_HOURS = (
    (1, 1065.0, -8.024469185179, 1.736142312849558e+00),
    (2, 1080.0, -7.897189802578, 2.181282918861843e+00),
    (3, 1095.0, -7.568173491177, 2.866864083385660e+00),
    (4, 1110.0, -7.348778380561, 3.627391188262104e+00),
    (5, 1125.0, -7.134960916916, 4.535538105993185e+00),
    (6, 1140.0, -6.926524785763, 5.452659641537931e+00),
    (7, 1155.0, -6.723302596697, 6.437513837593952e+00),
    (8, 1170.0, -6.525109859562, 7.487567243124502e+00),
    (9, 1185.0, -6.331798373102, 8.600308342616408e+00),
    (10, 1200.0, -6.143192648223, 1.007219696367225e+01),
    (11, 1215.0, -5.959163574409, 1.134103258817384e+01),
    (12, 1230.0, -5.779541658973, 1.266758217110369e+01),
    (13, 1245.0, -5.604217324548, 1.404959296829960e+01),
    (14, 1260.0, -5.433023935668, 1.548517241303118e+01),
    (15, 1275.0, -5.265872391101, 1.735102618109563e+01),
    (16, 1290.0, -5.102595396094, 1.927630787901142e+01),
    (17, 1305.0, -5.337588504445, 2.231435284546974e+01),
    (18, 1320.0, -5.593309524946, 2.151631180561190e+01),
    (19, 1327.5, -5.910453218411, 1.878094596579367e+01),
    (20, 1335.0, -6.211567087093, 1.619898343734689e+01),
    (21, 1342.5, -6.492548580142, 1.382968964551859e+01),
    (22, 1350.0, -6.757147887895, 1.173967886697780e+01),
    (23, 1357.5, -6.998302732604, 9.901885264360878e+00),
    (24, 1365.0, -7.224917947987, 8.355610933790388e+00),
)

# CI has no melt redox capacity for the first ten hours, so dn is zero.
_CI_HOURS = (
    (1, 1065.0, -5.487895368462, 0.000000000000000e+00),
    (2, 1080.0, -5.256762430640, 0.000000000000000e+00),
    (3, 1095.0, -5.031602628804, 0.000000000000000e+00),
    (4, 1110.0, -4.812211455137, 0.000000000000000e+00),
    (5, 1125.0, -4.598394202981, 0.000000000000000e+00),
    (6, 1140.0, -4.389963635265, 0.000000000000000e+00),
    (7, 1155.0, -4.186741751590, 0.000000000000000e+00),
    (8, 1170.0, -3.988556752518, 0.000000000000000e+00),
    (9, 1185.0, -3.795245705414, 0.000000000000000e+00),
    (10, 1200.0, -3.606650592087, 0.000000000000000e+00),
    (11, 1215.0, -3.902651632540, 2.588419630617889e+01),
    (12, 1230.0, -4.341374364767, 1.424321029834321e+01),
    (13, 1245.0, -4.742295025112, 1.258198361633447e+01),
    (14, 1260.0, -5.108750645587, 1.078441476020891e+01),
    (15, 1275.0, -5.443548064190, 9.066964582317798e+00),
    (16, 1290.0, -5.755377428248, 7.659633387585870e+00),
    (17, 1305.0, -6.070862068485, 6.958401911988044e+00),
    (18, 1320.0, -6.386559558706, 6.160070144233448e+00),
    (19, 1327.5, -6.681843261661, 5.142380465894478e+00),
    (20, 1335.0, -5.791400770664, 1.455459007732217e+01),
    (21, 1342.5, -6.191372691354, 5.199395936712125e+00),
    (22, 1350.0, -6.559738214715, 4.211279974616275e+00),
    (23, 1357.5, -6.892840872231, 3.384226533661546e+00),
    (24, 1365.0, -7.196573710382, 2.713957313894622e+00),
)


def _c2a_exchange_hours(feedstock_id: str) -> list[tuple[int, float, float, float]]:
    """Same C2A setup as the SiO-yield campaign: 24 h, 1000 kg, builtin Antoine."""
    bundle = load_config_bundle(DATA_DIR)
    feedstock = bundle.feedstocks.get(feedstock_id, {})
    stage0_carbon_kg = _required_stage0_carbon_kg(feedstock, 1000.0)
    additives_kg = {}
    if stage0_carbon_kg > 1.0e-12:
        additives_kg["C"] = stage0_carbon_kg
    run = PyrolysisRun(
        feedstock_id=feedstock_id,
        campaign="C2A_continuous",
        hours=24,
        mass_kg=1000.0,
        backend_name=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
        track="pyrolysis",
        additives_kg=additives_kg,
        engines={"vapor_pressure": "builtin-antoine"},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        force_builtin_vapor_pressure=True,
    )
    session = run._start_session()
    sim = session.simulator
    _prepare_sio_campaign_start(sim, t_low_c=None, t_hold_c=None, ramp_c_per_hr=None)
    _apply_sio_wall_sweep_controls(sim, liner_temperature_c=None, pO2_mbar=None)
    result = run._run_session(session)
    assert result.get("status") == "ok", result.get("error_message")
    rows = []
    for snap in sim.record.snapshots:
        reservoir = dict(getattr(snap, "oxygen_reservoir", {}) or {})
        rows.append(
            (
                int(snap.hour),
                float(snap.temperature_C),
                float(reservoir["melt_intrinsic_fO2_log"]),
                float(reservoir.get("exchange_o2_mol", 0.0) or 0.0),
            )
        )
    return rows


def _assert_pinned(actual, expected) -> None:
    assert len(actual) == len(expected)
    for got, pin in zip(actual, expected):
        assert got[0] == pin[0]
        assert got[1] == pytest.approx(pin[1], abs=1e-9)
        assert got[2] == pytest.approx(pin[2], rel=1e-9, abs=1e-12)
        assert got[3] == pytest.approx(pin[3], rel=1e-6, abs=1e-15)


def test_mars_basalt_c2a_oxygen_exchange_dn_and_fo2_pin() -> None:
    _assert_pinned(_c2a_exchange_hours("mars_basalt"), _MARS_HOURS)


def test_ci_carbonaceous_chondrite_c2a_oxygen_exchange_dn_and_fo2_pin() -> None:
    _assert_pinned(_c2a_exchange_hours("ci_carbonaceous_chondrite"), _CI_HOURS)
