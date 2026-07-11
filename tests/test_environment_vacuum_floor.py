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


def test_intrinsic_melt_fo2_clamp_lowers_to_vacuum_body_floor() -> None:
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)

    default_clamped = sim._compute_intrinsic_melt_fO2(temperature_K=1000.0)
    sim.melt.body = "moon"
    lunar_clamped = sim._compute_intrinsic_melt_fO2(temperature_K=1000.0)

    assert default_clamped == pytest.approx(math.log10(DEFAULT_VACUUM_FLOOR_BAR))
    assert lunar_clamped == pytest.approx(math.log10(MOON_VACUUM_FLOOR_BAR))
    assert lunar_clamped < default_clamped
