from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest
import yaml

from engines.builtin._common import resolve_transport_pO2_bar
from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentRequest, ProviderAccountView
from simulator.core import PyrolysisSimulator
from simulator.environment import (
    ASTEROID_VACUUM_FLOOR_BAR,
    DEFAULT_VACUUM_FLOOR_BAR,
    MARS_DATUM_PRESSURE_BAR,
    MARS_OLYMPUS_PRESSURE_BAR,
    MOON_VACUUM_FLOOR_BAR,
    vacuum_floor_bar_for_body,
    vacuum_floor_bar_for_environment,
)
from simulator.melt_backend.base import InternalAnalyticalBackend

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _load_yaml(name: str):
    return yaml.safe_load((DATA / name).read_text(encoding="utf-8"))


def _request(control_inputs: dict[str, float | str]) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={},
            species_formula_registry={},
        ),
        temperature_C=1600.0,
        pressure_bar=0.0,
        fO2_log=None,
        control_inputs=control_inputs,
    )


def _fo2_request(fO2_log: float) -> IntentRequest:
    return IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={},
            species_formula_registry={},
        ),
        temperature_C=1600.0,
        pressure_bar=0.0,
        fO2_log=fO2_log,
        control_inputs={},
    )


def _sim(feedstocks: dict | None = None) -> PyrolysisSimulator:
    return PyrolysisSimulator(
        InternalAnalyticalBackend(),
        _load_yaml("setpoints.yaml"),
        feedstocks or _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("moon", MOON_VACUUM_FLOOR_BAR),
        ("lunar", MOON_VACUUM_FLOOR_BAR),
        ("asteroid", ASTEROID_VACUUM_FLOOR_BAR),
        ("deep_space", ASTEROID_VACUUM_FLOOR_BAR),
        ("mars", MARS_DATUM_PRESSURE_BAR),
        ("unknown", DEFAULT_VACUUM_FLOOR_BAR),
        ("", DEFAULT_VACUUM_FLOOR_BAR),
    ],
)
def test_body_vacuum_floor_mapping(body: str, expected: float) -> None:
    assert vacuum_floor_bar_for_body(body) == pytest.approx(expected)


def test_mars_environment_floor_uses_explicit_ambient_pressure() -> None:
    assert vacuum_floor_bar_for_environment(
        body="mars",
        ambient_pressure_bar=MARS_OLYMPUS_PRESSURE_BAR,
    ) == pytest.approx(MARS_OLYMPUS_PRESSURE_BAR)


def test_resolve_transport_po2_accepts_lunar_nanotorr_floor() -> None:
    request = _request(
        {
            "pO2_bar": MOON_VACUUM_FLOOR_BAR,
            "vacuum_floor_bar": MOON_VACUUM_FLOOR_BAR,
        }
    )

    assert resolve_transport_pO2_bar(request) == pytest.approx(
        MOON_VACUUM_FLOOR_BAR
    )


def test_default_unknown_body_preserves_existing_1e_9_transport_floor() -> None:
    request = _request({"pO2_bar": DEFAULT_VACUUM_FLOOR_BAR})

    assert resolve_transport_pO2_bar(request) == pytest.approx(
        DEFAULT_VACUUM_FLOOR_BAR
    )

    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)

    assert sim.melt.body == ""
    assert sim._vacuum_floor_bar() == pytest.approx(DEFAULT_VACUUM_FLOOR_BAR)
    assert sim._commanded_pO2_bar() == pytest.approx(DEFAULT_VACUUM_FLOOR_BAR)


def test_resolve_transport_po2_wraps_fo2_overflow_as_value_error() -> None:
    with pytest.raises(ValueError, match="fO2_log=309.*finite pO2_bar range"):
        resolve_transport_pO2_bar(_fo2_request(309.0))


def test_explicit_feedstock_body_sets_run_vacuum_floor() -> None:
    feedstocks = copy.deepcopy(_load_yaml("feedstocks.yaml"))
    feedstocks["lunar_mare_low_ti"]["body"] = "moon"
    sim = _sim(feedstocks)
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)

    assert sim.melt.body == "moon"
    assert sim._vacuum_floor_bar() == pytest.approx(MOON_VACUUM_FLOOR_BAR)
    assert sim._commanded_pO2_bar() == pytest.approx(MOON_VACUUM_FLOOR_BAR)


@pytest.mark.parametrize(
    ("temperature_K", "expected_inverse"),
    [
        (1000.0, -22.164650192134065),
        (1673.15, -10.740754356453898),
        (1873.15, -9.161574733594396),
    ],
)
def test_intrinsic_melt_fo2_inverse_reports_below_feo_saturation(
    temperature_K: float,
    expected_inverse: float,
) -> None:
    from simulator.fe_redox import (
        calphad_ferrous_feo_activity_diagnostic,
        feo_iw_log10_fO2_bar,
        floor_vacuum_pressure_bar,
    )

    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)

    pressure_bar = floor_vacuum_pressure_bar(
        sim.melt.p_total_mbar / 1000.0,
        floor_bar=sim._vacuum_floor_bar(),
    )
    expected_activity = calphad_ferrous_feo_activity_diagnostic(
        comp_wt=sim._melt_oxide_wt_pct(),
        fO2_log=feo_iw_log10_fO2_bar(temperature_K),
        T_K=temperature_K,
        pressure_bar=pressure_bar,
        fe3_over_sigma_fe=0.0,
    )["a_FeO_authoritative"]
    expected_iw_bound = feo_iw_log10_fO2_bar(
        temperature_K,
        a_feo=expected_activity,
    )

    inventory = sim.atom_ledger.mol_by_account()
    transition_count = len(sim.atom_ledger.transitions)
    default_inverse = sim._compute_intrinsic_melt_fO2(temperature_K)
    sim.melt.body = "moon"
    lunar_inverse = sim._compute_intrinsic_melt_fO2(temperature_K)

    assert default_inverse == pytest.approx(expected_inverse, abs=1e-12)
    assert lunar_inverse == pytest.approx(default_inverse, abs=1e-12)
    assert sim.atom_ledger.mol_by_account() == inventory
    assert len(sim.atom_ledger.transitions) == transition_count

    notice = next(
        item
        for item in sim._redox_source_breakdown_diagnostic()[
            "redox_bound_notices"
        ]
        if item["flag"] == "REDOX_BELOW_FE_FEO_SATURATION"
        and item["melt_potential"] == pytest.approx(default_inverse)
    )
    assert notice["melt_potential"] == pytest.approx(default_inverse)
    assert notice["iw_bound"] == pytest.approx(expected_iw_bound)
    assert notice["a_feo"] == pytest.approx(expected_activity)
    assert default_inverse < notice["iw_bound"]
