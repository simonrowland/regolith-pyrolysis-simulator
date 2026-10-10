"""Pin of the pre-fix b-763 C2A oxygen-exchange dn and melt log fO2.

Measured on this branch at 63cfe2ea8, engines live, before the
fixed-pressure headspace boundary. Each row is
(hour, T_C, melt log10 fO2, exchange O2 mol). A later commit replaces
an assertion only when that ruling changes the exchange output.
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
    (1, 1065.0, -8.024469193979, 1.483676206173399e-10),
    (2, 1080.0, -7.793333585329, 1.814864743042401e-10),
    (3, 1095.0, -7.568173654055, 2.129901776496919e-10),
    (4, 1110.0, -7.348778819303, 2.429623336232971e-10),
    (5, 1125.0, -7.134961391344, 2.714794741277950e-10),
    (6, 1140.0, -6.926525762502, 2.986161978051093e-10),
    (7, 1155.0, -6.723303635112, 3.244398739345952e-10),
    (8, 1170.0, -6.525111741185, 3.490174186837279e-10),
    (9, 1185.0, -6.331800356797, 3.724080372050433e-10),
    (10, 1200.0, -6.143195971955, 3.946721304275145e-10),
    (11, 1215.0, -5.959167056650, 4.158615511205408e-10),
    (12, 1230.0, -5.779547190283, 4.360312330821598e-10),
    (13, 1245.0, -5.604223102630, 4.552263233620737e-10),
    (14, 1260.0, -5.433032740301, 4.734972448335038e-10),
    (15, 1275.0, -5.265881562749, 4.908829373995007e-10),
    (16, 1290.0, -5.102608928417, 5.169285946713147e-10),
    (17, 1305.0, -4.942835904005, 5.916532577636221e-10),
    (18, 1320.0, -4.787036667663, 6.739372959085209e-10),
    (19, 1327.5, -4.710109718955, 7.179949579254507e-10),
    (20, 1335.0, -4.634428631648, 7.641379665994556e-10),
    (21, 1342.5, -4.559194307188, 8.122846912028064e-10),
    (22, 1350.0, -4.485228141728, 8.626194580020331e-10),
    (23, 1357.5, -4.411630812586, 9.150272525070304e-10),
    (24, 1365.0, -4.339329651169, 9.697207670972050e-10),
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
    (11, 1215.0, -3.422615496782, 7.627572445756830e-10),
    (12, 1230.0, -3.422615496798, 7.551465542237709e-10),
    (13, 1245.0, -3.422598168765, 7.476853690212457e-10),
    (14, 1260.0, -3.422598168766, 7.403724811980736e-10),
    (15, 1275.0, -3.422571033936, 7.331990243514068e-10),
    (16, 1290.0, -3.422571033896, 7.397596146678930e-10),
    (17, 1305.0, -3.422528612293, 8.134135587646875e-10),
    (18, 1320.0, -3.422528612140, 8.922142651970627e-10),
    (19, 1327.5, -3.422469163608, 9.335765324524985e-10),
    (20, 1335.0, -3.422469163334, 9.762761184023153e-10),
    (21, 1342.5, -3.422394907148, 1.020303239901258e-09),
    (22, 1350.0, -3.422394906674, 1.065690765834799e-09),
    (23, 1357.5, -3.422302087784, 1.112417735247407e-09),
    (24, 1365.0, -3.422302086980, 1.160521789495882e-09),
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
