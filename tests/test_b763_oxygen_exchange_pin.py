"""Pin of the b-763 C2A oxygen-exchange dn and melt log fO2.

The tests-only commit recorded the pre-fix capacitor series. These
rows are the fixed-pressure boundary, engines live: a pumped headspace
no longer caps dn at the 1e-9 mol floor inventory. Each row is
(hour, T_C, melt log10 fO2, exchange O2 mol).
"""

from __future__ import annotations

import pytest

from simulator.backend_names import ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
from simulator.core import FERRIC_DIVERGENCE_WARNING_THRESHOLD
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
    (1, 1065.0, -8.024469193979, 0.000000000000000e+00),
    (2, 1080.0, -7.793333585321, 0.000000000000000e+00),
    (3, 1095.0, -7.568173654055, 0.000000000000000e+00),
    (4, 1110.0, -7.348778819303, 0.000000000000000e+00),
    (5, 1125.0, -7.134961391344, 0.000000000000000e+00),
    (6, 1140.0, -6.926525762502, 0.000000000000000e+00),
    (7, 1155.0, -6.723303635112, 0.000000000000000e+00),
    (8, 1170.0, -6.525111741185, 0.000000000000000e+00),
    (9, 1185.0, -6.331800356797, 0.000000000000000e+00),
    (10, 1200.0, -6.143195971955, 0.000000000000000e+00),
    (11, 1215.0, -5.959167056650, 0.000000000000000e+00),
    (12, 1230.0, -5.779547190283, 0.000000000000000e+00),
    (13, 1245.0, -5.604223102630, 0.000000000000000e+00),
    (14, 1260.0, -5.433032740301, 0.000000000000000e+00),
    (15, 1275.0, -5.265881562749, 0.000000000000000e+00),
    (16, 1290.0, -5.102608928417, 0.000000000000000e+00),
    (17, 1305.0, -5.337600706630, 2.231427844771989e+01),
    (18, 1320.0, -5.593320412000, 2.151624916910132e+01),
    (19, 1327.5, -5.910462843756, 1.878089941813457e+01),
    (20, 1335.0, -6.211575566618, 1.619895134810067e+01),
    (21, 1342.5, -6.492555943873, 1.382966927067690e+01),
    (22, 1350.0, -6.757154285692, 1.173966812314325e+01),
    (23, 1357.5, -6.998308143826, 9.901881288494746e+00),
    (24, 1365.0, -7.224922577523, 8.355612525932882e+00),
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
    (12, 1230.0, -3.723044245472, 0.000000000000000e+00),
    (13, 1245.0, -3.547701410755, 0.000000000000000e+00),
    (14, 1260.0, -3.376527675640, 0.000000000000000e+00),
    (15, 1275.0, -3.209352883613, 0.000000000000000e+00),
    (16, 1290.0, -3.046102093707, 0.000000000000000e+00),
    (17, 1305.0, -2.886603317105, 0.000000000000000e+00),
    (18, 1320.0, -2.730804079437, 0.000000000000000e+00),
    (19, 1327.5, -2.654209386292, 0.000000000000000e+00),
    (20, 1335.0, -2.578528297039, 0.000000000000000e+00),
    (21, 1342.5, -2.503669032780, 0.000000000000000e+00),
    (22, 1350.0, -2.429702864491, 0.000000000000000e+00),
    (23, 1357.5, -2.356527111840, 0.000000000000000e+00),
    (24, 1365.0, -2.284225946335, 0.000000000000000e+00),
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
        dn_mol = float(reservoir.get("exchange_o2_mol", 0.0) or 0.0)
        reference_T_K = reservoir.get("reference_T_K")
        if reference_T_K is None:
            assert dn_mol == pytest.approx(0.0, abs=1e-15)
            notice = dict(reservoir.get("exchange_notice") or {})
            assert notice.get("message") == (
                "solid-state ferric decomposition not modelled"
            )
        else:
            divergence = dict(reservoir.get("ferric_divergence") or {})
            assert float(divergence.get("delta_abs", 0.0) or 0.0) <= (
                FERRIC_DIVERGENCE_WARNING_THRESHOLD
            )
        rows.append(
            (
                int(snap.hour),
                float(snap.temperature_C),
                float(reservoir["melt_intrinsic_fO2_log"]),
                dn_mol,
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
