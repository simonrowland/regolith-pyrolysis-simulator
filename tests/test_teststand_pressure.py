"""Chamber pressure from a declared test stand.

Expected pressures are the closed forms in the task (P = Q/S_eff, and
P(t) = P0 exp(-S t / V)), or the certified element law written here.
They are not taken from a previous call to the solver.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator.physical_constants import GAS_CONSTANT
from simulator.state import CampaignPhase
from simulator.teststand import (
    TeststandRefusal,
    advance_chamber_pressure,
    apply_process_chamber_pressure,
    normal_litres_per_minute_to_mol_per_s,
    parse_teststand,
)
from simulator.transport_regime import (
    MOLAR_MASSES_KG_PER_MOL,
    beskok_karniadakis_rarefaction_factor,
    mean_molecular_speed_m_s,
    single_species_mean_free_path,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PRESET_DIR = REPO_ROOT / "data" / "presets" / "vacuum_pyrolysis"
_MISSING = object()
_T_K = 273.15
_HOUR_S = 3600.0
# Q = v_dot * P_std at the normal temperature. Independent of
# normal_litres_per_minute_to_mol_per_s and of STANDARD_ATMOSPHERE_PA.
_Q_NL_0_3 = (0.3e-3 / 60.0) * 101325.0


def _schedule(
    *,
    driver: str | None = "apparatus",
    flow: float = 0.3,
    species: str = "Ar",
    include_driver: bool = True,
) -> dict:
    pressure_control: dict = {"mode": "flow_through_with_pump"}
    if include_driver:
        pressure_control["pressure_driver"] = driver
    return {
        "gas_boundary": {
            "background_gas": {"species": species},
            "imposed_flow": {"value": flow, "unit": "NL_min"},
            "pressure_control": pressure_control,
        }
    }


def _geometry(
    *,
    speed=_MISSING,
    ultimate=_MISSING,
    path=_MISSING,
    temperature_K: float | None = _T_K,
    volume_m3=_MISSING,
    curve=_MISSING,
    vacuum_limit_mbar=_MISSING,
) -> dict:
    pump: dict = {}
    if temperature_K is not None:
        pump["balance_temperature_K"] = temperature_K
    if speed is not _MISSING:
        pump["pumping_speed_m3_s"] = speed
    if ultimate is not _MISSING:
        pump["ultimate_pressure_pa"] = ultimate
    if curve is not _MISSING:
        pump["speed_curve"] = curve
    if path is not _MISSING:
        pump["conductance_path"] = path
    if vacuum_limit_mbar is not _MISSING:
        pump["vacuum_limit_mbar"] = vacuum_limit_mbar
    chamber: dict = {}
    if volume_m3 is not _MISSING:
        chamber["volume_m3"] = volume_m3
    return {"pump": pump, "chamber": chamber, "sample": {}}


def _load(name: str) -> dict:
    return yaml.safe_load((PRESET_DIR / name).read_text(encoding="utf-8"))


def _advance(geometry: dict, **kwargs):
    schedule = kwargs.pop("schedule", None)
    if schedule is None:
        schedule = _schedule()
    return advance_chamber_pressure(
        parse_teststand(schedule, geometry),
        **kwargs,
    )


def _diverges(actual: float, expected: float) -> None:
    assert abs(actual - expected) > abs(expected) * 1.0e-6


def _orifice_conductance_m3_s(diameter_m: float, temperature_K: float) -> float:
    area_m2 = math.pi * diameter_m ** 2 / 4.0
    speed_m_s = mean_molecular_speed_m_s(
        temperature_K,
        MOLAR_MASSES_KG_PER_MOL["Ar"],
    )
    return 0.25 * area_m2 * speed_m_s


def _poiseuille_k_m3_s_per_pa(
    diameter_m: float,
    length_m: float,
    viscosity_pa_s: float,
) -> float:
    radius_m = diameter_m / 2.0
    return math.pi * radius_m ** 4 / (8.0 * viscosity_pa_s * length_m)


class _Melt:
    def __init__(self, campaign, p_total_mbar: float, pO2_mbar: float) -> None:
        self.campaign = campaign
        self.p_total_mbar = p_total_mbar
        self.pO2_mbar = pO2_mbar
        self.validated = 0

    def validate_melt_pressures(self) -> None:
        self.validated += 1


class _Overhead:
    def __init__(self, melt_offgas_O2_mol_hr: float = 0.0) -> None:
        self.melt_offgas_O2_mol_hr = melt_offgas_O2_mol_hr
        self.mre_anode_O2_mol_hr = 0.0


class _Schedules:
    def __init__(self, schedule) -> None:
        self._schedule = schedule

    def _lab_schedule(self, campaign):
        return self._schedule


class _Sim:
    def __init__(self, schedule, geometry, *, campaign, p_total_mbar, pO2_mbar=0.0, offgas_mol_hr=0.0):
        self.melt = _Melt(campaign, p_total_mbar, pO2_mbar)
        self.overhead = _Overhead(offgas_mol_hr)
        self.campaign_mgr = _Schedules(schedule)
        self.setpoints = {"lab_geometry": geometry}


def test_teststand_refusal_is_not_a_pytest_class() -> None:
    assert TeststandRefusal.__name__.startswith("Test")
    assert TeststandRefusal.__test__ is False
    try:
        del TeststandRefusal.__test__
        assert getattr(TeststandRefusal, "__test__", True) is True
        assert TeststandRefusal.__name__.startswith("Test")
    finally:
        TeststandRefusal.__test__ = False


def test_steady_state_carrier_only_is_q_over_s() -> None:
    speed_m3_s = 0.004
    assert _Q_NL_0_3 == 0.506625
    expected_pa = _Q_NL_0_3 / speed_m3_s
    n_dot = normal_litres_per_minute_to_mol_per_s(0.3)
    geometry = _geometry(speed=speed_m3_s, ultimate=0.0, path=[])
    at_hour = _advance(
        geometry,
        dt_s=_HOUR_S,
        evolved_noncondensable_mol=0.0,
        form="steady_state",
    )
    at_one_second = _advance(
        geometry,
        dt_s=1.0,
        evolved_noncondensable_mol=0.0,
        form="steady_state",
    )
    assert at_hour.q_pa_m3_s == pytest.approx(_Q_NL_0_3, rel=1e-12)
    assert at_hour.pressure_pa == pytest.approx(expected_pa, rel=1e-12)
    assert at_one_second.q_pa_m3_s == pytest.approx(_Q_NL_0_3, rel=1e-12)
    assert at_one_second.pressure_pa == pytest.approx(expected_pa, rel=1e-12)
    assert at_hour.s_eff_m3_s == pytest.approx(speed_m3_s, rel=1e-12)
    assert at_hour.form == "steady_state"
    doubled = _advance(
        geometry,
        dt_s=_HOUR_S,
        evolved_noncondensable_mol=0.0,
        carrier_mol=2.0 * n_dot * _HOUR_S,
        form="steady_state",
    )
    _diverges(doubled.pressure_pa, expected_pa)
    assert doubled.pressure_pa == pytest.approx(2.0 * expected_pa, rel=1e-12)


def test_analytic_pumpdown_matches_exponential() -> None:
    speed_m3_s = 0.004
    volume_m3 = 0.01
    dt_s = 10.0
    initial_pa = 1000.0
    expected_pa = initial_pa * math.exp(-speed_m3_s * dt_s / volume_m3)
    geometry = _geometry(
        speed=speed_m3_s,
        ultimate=0.0,
        path=[],
        volume_m3=volume_m3,
    )
    step = _advance(
        geometry,
        schedule=_schedule(flow=0.0),
        dt_s=dt_s,
        evolved_noncondensable_mol=0.0,
        form="analytic",
        pressure_pa=initial_pa,
    )
    assert step.pressure_pa == pytest.approx(expected_pa, rel=1e-12)
    assert step.q_pa_m3_s == pytest.approx(0.0, abs=1e-15)
    mutated = _advance(
        _geometry(
            speed=speed_m3_s,
            ultimate=0.0,
            path=[],
            volume_m3=2.0 * volume_m3,
        ),
        schedule=_schedule(flow=0.0),
        dt_s=dt_s,
        evolved_noncondensable_mol=0.0,
        form="analytic",
        pressure_pa=initial_pa,
    )
    _diverges(mutated.pressure_pa, expected_pa)


def test_missing_pump_speed_refuses_and_a_declared_speed_solves() -> None:
    geometry = _geometry(speed=None, ultimate=None, path=[])
    with pytest.raises(TeststandRefusal) as caught:
        _advance(
            geometry,
            dt_s=1.0,
            evolved_noncondensable_mol=0.0,
            form="steady_state",
        )
    assert caught.value.code == "missing_pump_speed"
    speed_m3_s = 0.004
    expected_pa = _Q_NL_0_3 / speed_m3_s
    solved = _advance(
        _geometry(speed=speed_m3_s, ultimate=0.0, path=[]),
        dt_s=1.0,
        evolved_noncondensable_mol=0.0,
        form="steady_state",
    )
    assert solved.pressure_pa == pytest.approx(expected_pa, rel=1e-12)


def test_explicit_zero_speed_is_invalid_not_missing() -> None:
    with pytest.raises(TeststandRefusal) as caught:
        parse_teststand(
            _schedule(),
            _geometry(speed=0.0, ultimate=0.0, path=[]),
        )
    assert caught.value.code == "invalid_pump_speed"
    parsed = parse_teststand(
        _schedule(),
        _geometry(speed=0.004, ultimate=0.0, path=[]),
    )
    assert parsed.pump.nominal_speed_m3_s == pytest.approx(0.004)


def test_orifice_outside_free_molecular_refuses_with_a_notice() -> None:
    diameter_m = 1.0e-4
    speed_m3_s = 1.0e-8
    # Pump-only guess is Q/S = 100 Pa. At that pressure this orifice is
    # not free-molecular, so the certified aperture law must not run.
    out_of_regime = {
        "kind": "orifice",
        "diameter_m": diameter_m,
    }
    with pytest.raises(TeststandRefusal) as caught:
        _advance(
            _geometry(speed=speed_m3_s, ultimate=0.0, path=[out_of_regime]),
            dt_s=1.0,
            evolved_noncondensable_mol=0.0,
            carrier_mol=1.0e-6 / (GAS_CONSTANT * _T_K),
            form="steady_state",
        )
    assert caught.value.code == "orifice_out_of_certified_regime"
    assert caught.value.notices
    assert "certified regime" in caught.value.notices[0]
    conductance = _orifice_conductance_m3_s(diameter_m, _T_K)
    throughput = 1.0e-8
    inlet_pa = throughput / speed_m3_s
    expected_pa = inlet_pa + throughput / conductance
    solved = _advance(
        _geometry(speed=speed_m3_s, ultimate=0.0, path=[out_of_regime]),
        dt_s=1.0,
        evolved_noncondensable_mol=0.0,
        carrier_mol=throughput / (GAS_CONSTANT * _T_K),
        form="steady_state",
    )
    assert solved.pressure_pa == pytest.approx(expected_pa, rel=1e-12)
    assert not solved.notices


def test_molecular_orifice_adds_the_aperture_drop_in_series() -> None:
    diameter_m = 1.0e-4
    speed_m3_s = 1.0e-8
    throughput = 1.0e-8
    conductance = _orifice_conductance_m3_s(diameter_m, _T_K)
    expected_pa = throughput / speed_m3_s + throughput / conductance
    element = {"kind": "orifice", "diameter_m": diameter_m}
    solved = _advance(
        _geometry(speed=speed_m3_s, ultimate=0.0, path=[element]),
        dt_s=1.0,
        evolved_noncondensable_mol=0.0,
        carrier_mol=throughput / (GAS_CONSTANT * _T_K),
        form="steady_state",
    )
    assert solved.pressure_pa == pytest.approx(expected_pa, rel=1e-12)
    series = 1.0 / (1.0 / speed_m3_s + 1.0 / conductance)
    assert solved.s_eff_m3_s == pytest.approx(series, rel=1e-12)
    doubled = _advance(
        _geometry(speed=speed_m3_s, ultimate=0.0, path=[element]),
        dt_s=1.0,
        evolved_noncondensable_mol=0.0,
        carrier_mol=2.0 * throughput / (GAS_CONSTANT * _T_K),
        form="steady_state",
    )
    _diverges(doubled.pressure_pa, expected_pa)


def test_viscous_tube_matches_the_poiseuille_closed_form() -> None:
    diameter_m = 0.02
    length_m = 1.0
    viscosity_pa_s = 2.0e-5
    speed_m3_s = 0.001
    throughput = 0.1
    coefficient = _poiseuille_k_m3_s_per_pa(
        diameter_m,
        length_m,
        viscosity_pa_s,
    )
    inlet_pa = throughput / speed_m3_s
    expected_pa = math.sqrt(inlet_pa ** 2 + 2.0 * throughput / coefficient)
    # The two equalities are the closed form. Production must land on them.
    assert throughput == pytest.approx(speed_m3_s * inlet_pa, rel=1e-12)
    assert throughput == pytest.approx(
        0.5 * coefficient * (expected_pa ** 2 - inlet_pa ** 2),
        rel=1e-12,
    )
    element = {
        "kind": "tube",
        "diameter_m": diameter_m,
        "length_m": length_m,
        "dynamic_viscosity_pa_s": viscosity_pa_s,
    }
    solved = _advance(
        _geometry(speed=speed_m3_s, ultimate=0.0, path=[element]),
        dt_s=1.0,
        evolved_noncondensable_mol=0.0,
        carrier_mol=throughput / (GAS_CONSTANT * _T_K),
        form="steady_state",
    )
    assert solved.pump_inlet_pressure_pa == pytest.approx(inlet_pa, rel=1e-12)
    assert solved.pressure_pa == pytest.approx(expected_pa, rel=1e-12)
    second = dict(element)
    two = _advance(
        _geometry(speed=speed_m3_s, ultimate=0.0, path=[element, second]),
        dt_s=1.0,
        evolved_noncondensable_mol=0.0,
        carrier_mol=throughput / (GAS_CONSTANT * _T_K),
        form="steady_state",
    )
    two_tube_pa = math.sqrt(expected_pa ** 2 + 2.0 * throughput / coefficient)
    assert two.pressure_pa == pytest.approx(two_tube_pa, rel=1e-12)
    _diverges(two.pressure_pa, expected_pa)


def test_transitional_tube_requires_the_flag_and_then_uses_rarefaction() -> None:
    diameter_m = 0.001
    length_m = 0.05
    viscosity_pa_s = 2.0e-5
    speed_m3_s = 1.0e-8
    throughput = 1.0e-8
    element = {
        "kind": "tube",
        "diameter_m": diameter_m,
        "length_m": length_m,
        "dynamic_viscosity_pa_s": viscosity_pa_s,
    }
    with pytest.raises(TeststandRefusal) as caught:
        _advance(
            _geometry(speed=speed_m3_s, ultimate=0.0, path=[element]),
            dt_s=1.0,
            evolved_noncondensable_mol=0.0,
            carrier_mol=throughput / (GAS_CONSTANT * _T_K),
            form="steady_state",
        )
    assert caught.value.code == "transitional_conductance_not_authorized"
    assert caught.value.notices
    authorized = dict(element)
    authorized["allow_transitional"] = True
    inlet_pa = throughput / speed_m3_s
    coefficient = _poiseuille_k_m3_s_per_pa(
        diameter_m,
        length_m,
        viscosity_pa_s,
    )
    probed = single_species_mean_free_path("Ar", inlet_pa, _T_K, diameter_m)
    rarefaction = beskok_karniadakis_rarefaction_factor(probed.knudsen_number)
    expected_pa = math.sqrt(
        inlet_pa ** 2 + 2.0 * throughput / (coefficient * rarefaction)
    )
    solved = _advance(
        _geometry(speed=speed_m3_s, ultimate=0.0, path=[authorized]),
        dt_s=1.0,
        evolved_noncondensable_mol=0.0,
        carrier_mol=throughput / (GAS_CONSTANT * _T_K),
        form="steady_state",
    )
    assert solved.pressure_pa == pytest.approx(expected_pa, rel=1e-3)
    without_rarefaction = math.sqrt(
        inlet_pa ** 2 + 2.0 * throughput / coefficient
    )
    _diverges(solved.pressure_pa, without_rarefaction)


def test_short_free_molecular_tube_refuses_until_it_is_long() -> None:
    diameter_m = 0.01
    speed_m3_s = 1.0e-6
    throughput = speed_m3_s * 0.01
    short = {
        "kind": "tube",
        "diameter_m": diameter_m,
        "length_m": 0.05,
    }
    with pytest.raises(TeststandRefusal) as caught:
        _advance(
            _geometry(speed=speed_m3_s, ultimate=0.0, path=[short]),
            dt_s=1.0,
            evolved_noncondensable_mol=0.0,
            carrier_mol=throughput / (GAS_CONSTANT * _T_K),
            form="steady_state",
        )
    assert caught.value.code == "clausing_short_tube_not_certified"
    assert caught.value.notices
    length_m = 0.2
    long = dict(short)
    long["length_m"] = length_m
    area_m2 = math.pi * diameter_m ** 2 / 4.0
    transmission = 4.0 * diameter_m / (3.0 * length_m)
    conductance = 0.25 * area_m2 * mean_molecular_speed_m_s(
        _T_K,
        MOLAR_MASSES_KG_PER_MOL["Ar"],
    ) * transmission
    expected_pa = throughput / speed_m3_s + throughput / conductance
    solved = _advance(
        _geometry(speed=speed_m3_s, ultimate=0.0, path=[long]),
        dt_s=1.0,
        evolved_noncondensable_mol=0.0,
        carrier_mol=throughput / (GAS_CONSTANT * _T_K),
        form="steady_state",
    )
    assert solved.pressure_pa == pytest.approx(expected_pa, rel=1e-12)


def test_regime_change_between_guess_and_solution_returns_no_pressure() -> None:
    diameter_m = 1.0e-6
    element = {"kind": "orifice", "diameter_m": diameter_m}
    with pytest.raises(TeststandRefusal) as caught:
        _advance(
            _geometry(speed=15.1 / _HOUR_S, ultimate=0.0, path=[element]),
            dt_s=1.0,
            evolved_noncondensable_mol=0.0,
            carrier_mol=0.506625 / (GAS_CONSTANT * _T_K),
            form="steady_state",
        )
    assert caught.value.code == "regime_changed_between_guess_and_solution"
    speed_m3_s = 1.0e-8
    throughput = 1.0e-8
    conductance = _orifice_conductance_m3_s(diameter_m, _T_K)
    expected_pa = throughput / speed_m3_s + throughput / conductance
    solved = _advance(
        _geometry(speed=speed_m3_s, ultimate=0.0, path=[element]),
        dt_s=1.0,
        evolved_noncondensable_mol=0.0,
        carrier_mol=throughput / (GAS_CONSTANT * _T_K),
        form="steady_state",
    )
    assert solved.pressure_pa == pytest.approx(expected_pa, rel=1e-12)


def test_cited_ndx15_speed_does_not_produce_the_printed_robinot_pressure() -> None:
    # 15.1 m^3/h is the nXDS15i brochure peak (Edwards 3601-0086), passed
    # in as an argument. It is not read from the preset.
    speed_m3_s = 15.1 / _HOUR_S
    expected_pa = _Q_NL_0_3 / speed_m3_s
    solved = _advance(
        _geometry(speed=speed_m3_s, ultimate=0.0, path=[], temperature_K=_T_K),
        schedule=_schedule(flow=0.3),
        dt_s=_HOUR_S,
        evolved_noncondensable_mol=0.0,
        form="steady_state",
    )
    assert solved.pressure_pa == pytest.approx(expected_pa, rel=1e-12)
    assert abs(solved.pressure_pa - 1300.0) > 1000.0
    preset = _load("robinot_2026.yaml")
    assert preset["lab_geometry"]["pump"]["pumping_speed_m3_s"] is None
    assert preset["lab_schedule"]["gas_boundary"]["pressure_control"][
        "pressure_driver"
    ] == "imposed"


def test_printed_vacuum_limit_is_not_an_ultimate_pressure() -> None:
    with pytest.raises(TeststandRefusal) as caught:
        _advance(
            _geometry(
                speed=15.1 / _HOUR_S,
                ultimate=None,
                path=[],
                vacuum_limit_mbar=7.0e-3,
            ),
            dt_s=1.0,
            evolved_noncondensable_mol=0.0,
            form="steady_state",
        )
    assert caught.value.code == "missing_ultimate_pressure"
    parsed = parse_teststand(
        _schedule(),
        _geometry(
            speed=15.1 / _HOUR_S,
            ultimate=None,
            path=[],
            vacuum_limit_mbar=7.0e-3,
        ),
    )
    assert parsed.printed_vacuum_limit_mbar == pytest.approx(7.0e-3)
    assert parsed.pump.ultimate_pressure_pa is None
    speed_m3_s = 15.1 / _HOUR_S
    expected_pa = _Q_NL_0_3 / speed_m3_s
    solved = _advance(
        _geometry(
            speed=speed_m3_s,
            ultimate=0.0,
            path=[],
            vacuum_limit_mbar=7.0e-3,
        ),
        dt_s=1.0,
        evolved_noncondensable_mol=0.0,
        form="steady_state",
    )
    assert solved.pressure_pa == pytest.approx(expected_pa, rel=1e-12)


def test_every_vacuum_preset_declares_imposed_pressure() -> None:
    robinot = _load("robinot_2026.yaml")
    pomeroy = _load("pomeroy_cardiff_2006.yaml")
    sesko = _load("sesko_2024.yaml")
    for preset in (robinot, pomeroy, sesko):
        control = preset["lab_schedule"]["gas_boundary"]["pressure_control"]
        assert control["pressure_driver"] == "imposed"
        speed = preset["lab_geometry"]["pump"].get("pumping_speed_m3_s", None)
        assert speed is None
        assert speed != 0
        parsed = parse_teststand(preset["lab_schedule"], preset["lab_geometry"])
        assert parsed.pressure_driver == "imposed"
        assert parsed.pump is None or parsed.pump.nominal_speed_m3_s is None
    sample = robinot["lab_geometry"]["sample"]
    assert sample["exposed_melt_area_m2"] is None
    assert sample["pellet_diameter_mm"] == 13
    assert sample["pellet_height_mm"] == 10
    assert sample["mass_g"] == pytest.approx(3.38)
    assert robinot["lab_geometry"]["pump"]["vacuum_limit_mbar"] == pytest.approx(7.0e-3)
    for selector in robinot["measurement_selectors"]:
        assert "exposed_melt_area_not_printed" in selector["certification"]["blocked_by"]
        assert "assumed_exposed_melt_area" not in selector["certification"]["blocked_by"]


def test_sesko_placeholder_path_parses_as_imposed() -> None:
    preset = _load("sesko_2024.yaml")
    parsed = parse_teststand(preset["lab_schedule"], preset["lab_geometry"])
    assert parsed.pressure_driver == "imposed"
    assert parsed.path_placeholder is True
    assert parsed.elements is None
    assert any("placeholder" in notice for notice in parsed.notices)


def test_absent_driver_with_a_numeric_speed_is_ambiguous() -> None:
    with pytest.raises(TeststandRefusal) as caught:
        parse_teststand(
            _schedule(include_driver=False),
            _geometry(speed=0.004, ultimate=0.0, path=[]),
        )
    assert caught.value.code == "pressure_driver_ambiguous"
    parsed = parse_teststand(
        _schedule(driver="imposed"),
        _geometry(speed=0.004, ultimate=0.0, path=[]),
    )
    assert parsed.pressure_driver == "imposed"
    assert any("not applied" in notice for notice in parsed.notices)


def test_speed_curve_refuses_a_non_unique_root_and_solves_a_unique_one() -> None:
    flat = [
        {"pressure_pa": 0.0, "speed_m3_s": 0.0},
        {"pressure_pa": 10.0, "speed_m3_s": 0.0},
    ]
    with pytest.raises(TeststandRefusal) as flat_caught:
        _advance(
            _geometry(path=[], curve=flat),
            schedule=_schedule(flow=0.0),
            dt_s=1.0,
            evolved_noncondensable_mol=0.0,
            carrier_mol=0.0,
            form="steady_state",
        )
    assert flat_caught.value.code == "pump_speed_curve_root_not_unique"
    hump = [
        {"pressure_pa": 0.0, "speed_m3_s": 0.0},
        {"pressure_pa": 10.0, "speed_m3_s": 1.0},
        {"pressure_pa": 20.0, "speed_m3_s": 0.0},
    ]
    with pytest.raises(TeststandRefusal) as hump_caught:
        _advance(
            _geometry(path=[], curve=hump),
            dt_s=1.0,
            evolved_noncondensable_mol=0.0,
            carrier_mol=5.0 / (GAS_CONSTANT * _T_K),
            form="steady_state",
        )
    assert hump_caught.value.code == "pump_speed_curve_root_not_unique"
    speed_m3_s = 0.004
    throughput = 0.4
    curve = [
        {"pressure_pa": 0.0, "speed_m3_s": speed_m3_s},
        {"pressure_pa": 1000.0, "speed_m3_s": speed_m3_s},
    ]
    solved = _advance(
        _geometry(path=[], curve=curve),
        dt_s=1.0,
        evolved_noncondensable_mol=0.0,
        carrier_mol=throughput / (GAS_CONSTANT * _T_K),
        form="steady_state",
    )
    assert solved.pressure_pa == pytest.approx(throughput / speed_m3_s, rel=1e-12)


def test_nominal_speed_and_curve_together_refuse() -> None:
    curve = [
        {"pressure_pa": 0.0, "speed_m3_s": 0.004},
        {"pressure_pa": 1000.0, "speed_m3_s": 0.004},
    ]
    with pytest.raises(TeststandRefusal) as caught:
        parse_teststand(
            _schedule(),
            _geometry(speed=0.004, path=[], curve=curve),
        )
    assert caught.value.code == "ambiguous_pump_speed"
    parsed = parse_teststand(
        _schedule(),
        _geometry(path=[], curve=curve),
    )
    assert parsed.pump.nominal_speed_m3_s is None
    assert parsed.pump.speed_curve_pa_to_m3_s is not None


def test_process_hook_imposed_robinot_does_not_write_pressure() -> None:
    preset = _load("robinot_2026.yaml")
    untouched = _Sim(
        None,
        preset["lab_geometry"],
        campaign=CampaignPhase.C0,
        p_total_mbar=13.0,
    )
    apply_process_chamber_pressure(untouched, dt_s=_HOUR_S)
    assert untouched.melt.p_total_mbar == 13.0
    assert not hasattr(untouched, "last_teststand_pressure")
    sim = _Sim(
        preset["lab_schedule"],
        preset["lab_geometry"],
        campaign=CampaignPhase.C0,
        p_total_mbar=13.0,
    )
    apply_process_chamber_pressure(sim, dt_s=_HOUR_S)
    assert sim.melt.p_total_mbar == 13.0
    assert sim.melt.validated == 0
    diagnostic = sim.last_teststand_pressure
    assert diagnostic["applied"] is False
    assert diagnostic["form"] is None
    assert diagnostic["pressure_driver"] == "imposed"
    assert any("not fully specified" in notice for notice in diagnostic["notices"])
    apparatus = _Sim(
        _schedule(driver="apparatus", flow=0.3),
        _geometry(speed=0.004, ultimate=0.0, path=[], volume_m3=0.01),
        campaign=CampaignPhase.C0,
        p_total_mbar=13.0,
    )
    apply_process_chamber_pressure(apparatus, dt_s=_HOUR_S)
    assert apparatus.last_teststand_pressure["applied"] is True
    assert apparatus.melt.p_total_mbar != 13.0


def test_process_hook_apparatus_uses_steady_state_and_evolved_gas() -> None:
    speed_m3_s = 0.004
    n_dot = normal_litres_per_minute_to_mol_per_s(0.3)
    expected_mbar = (_Q_NL_0_3 / speed_m3_s) / 100.0
    sim = _Sim(
        _schedule(driver="apparatus", flow=0.3),
        _geometry(speed=speed_m3_s, ultimate=0.0, path=[]),
        campaign=CampaignPhase.C0,
        p_total_mbar=13.0,
    )
    apply_process_chamber_pressure(sim, dt_s=_HOUR_S)
    assert sim.melt.p_total_mbar == pytest.approx(expected_mbar, rel=1e-12)
    diagnostic = sim.last_teststand_pressure
    assert diagnostic["form"] == "steady_state"
    assert diagnostic["applied"] is True
    assert diagnostic["dt_s"] == pytest.approx(_HOUR_S)
    assert diagnostic["schedule_pressure_mbar"] == pytest.approx(13.0)
    assert sim.melt.validated == 1
    sim.overhead.melt_offgas_O2_mol_hr = n_dot * _HOUR_S
    apply_process_chamber_pressure(sim, dt_s=_HOUR_S)
    assert sim.melt.p_total_mbar == pytest.approx(2.0 * expected_mbar, rel=1e-12)
    _diverges(sim.melt.p_total_mbar, expected_mbar)


def test_c2a_staged_apparatus_refuses_before_writing_pressure() -> None:
    geometry = _geometry(speed=0.004, ultimate=0.0, path=[])
    schedule = _schedule(driver="apparatus", flow=0.3)
    sim = _Sim(
        schedule,
        geometry,
        campaign=CampaignPhase.C2A_STAGED,
        p_total_mbar=13.0,
    )
    with pytest.raises(TeststandRefusal) as caught:
        apply_process_chamber_pressure(sim, dt_s=_HOUR_S)
    assert caught.value.code == "campaign_pressure_control_conflicts_with_apparatus"
    assert sim.melt.p_total_mbar == 13.0
    allowed = _Sim(
        schedule,
        geometry,
        campaign=CampaignPhase.C0,
        p_total_mbar=13.0,
    )
    apply_process_chamber_pressure(allowed, dt_s=_HOUR_S)
    assert allowed.last_teststand_pressure["applied"] is True
    assert allowed.melt.p_total_mbar != 13.0


def test_solved_total_below_existing_oxygen_partial_refuses() -> None:
    speed_m3_s = 0.004
    expected_mbar = (_Q_NL_0_3 / speed_m3_s) / 100.0
    sim = _Sim(
        _schedule(driver="apparatus", flow=0.3),
        _geometry(speed=speed_m3_s, ultimate=0.0, path=[]),
        campaign=CampaignPhase.C0,
        p_total_mbar=13.0,
        pO2_mbar=expected_mbar + 1.0,
    )
    with pytest.raises(TeststandRefusal) as caught:
        apply_process_chamber_pressure(sim, dt_s=_HOUR_S)
    assert caught.value.code == "partial_pressure_exceeds_solved_total"
    assert sim.melt.p_total_mbar == 13.0
    sim.melt.pO2_mbar = 0.0
    apply_process_chamber_pressure(sim, dt_s=_HOUR_S)
    assert sim.melt.p_total_mbar == pytest.approx(expected_mbar, rel=1e-12)
