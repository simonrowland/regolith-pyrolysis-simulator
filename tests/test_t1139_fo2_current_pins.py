"""Pins of the pre-fix t-1139 oxygen numbers.

These capture the outputs the b-747 / b-748 / b-749 commits change.
Expected values are the production functions' current results, measured
before those commits. Later commits replace an assertion only when the
ruling changes that output.
"""

from __future__ import annotations

import math
from copy import deepcopy

import pytest
import yaml
from pathlib import Path

from engines.builtin.overhead_gas_equilibrium import (
    BuiltinOverheadGasEquilibriumProvider,
)
from simulator.campaigns import CampaignManager
from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.fe_redox import intrinsic_melt_fO2
from simulator.state import Atmosphere, CampaignPhase, MeltState


DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# RH03 hour-0 holdup measured on this branch before the pressure bound:
# 823.2141246549886 mol O2 in 0.07409407407407409 m3 at 2473.15 K.
_RH03_O2_MOL = 823.2141246549886
_RH03_VOLUME_M3 = 0.07409407407407409
_RH03_TEMPERATURE_K = 2473.15
_RH03_IDEAL_O2_BAR = 2284.6193155497276


def _setpoints() -> dict:
    return yaml.safe_load((DATA_DIR / "setpoints.yaml").read_text()) or {}


def _ferrous_lunar_composition() -> dict[str, float]:
    return {
        "SiO2": 45.0,
        "FeO": 16.5,
        "Na2O": 0.4,
        "K2O": 0.1,
    }


def _n2_lab_schedule() -> dict:
    return {
        "id": "t1139-pin-schedule",
        "duration_h": 1.0,
        "interpolation": "piecewise_linear",
        "interpolation_source_class": "test_fixture",
        "furnace_ceiling_C": 1700.0,
        "melt_temperature_C": [
            {"t_h": 0.0, "value": 1200.0, "unit": "C"},
            {"t_h": 1.0, "value": 1300.0, "unit": "C"},
        ],
        "chamber_pressure_mbar": [
            {"t_h": 0.0, "value": 13.0, "unit": "mbar"},
            {"t_h": 1.0, "value": 13.0, "unit": "mbar"},
        ],
        "gas_boundary": {
            "background_gas": {
                "species": "N2",
                "mole_fraction": 1.0,
                "source_class": "test_fixture",
                "citation_id": "unit_test",
            },
            "imposed_flow": {
                "reported_status": "not_reported",
                "source_class": "test_fixture",
                "citation_id": "unit_test",
                "digest": "not_applicable",
                "reason": "unit test",
            },
            "pressure_control": {
                "mode": "flow_through_with_pump",
                "source_class": "test_fixture",
                "citation_id": "unit_test",
            },
        },
    }


def test_pin_intrinsic_melt_fo2_at_25c_is_the_vacuum_floor() -> None:
    composition = _ferrous_lunar_composition()
    seeded = intrinsic_melt_fO2(composition, 298.15)
    moon_floor = intrinsic_melt_fO2(
        composition,
        298.15,
        vacuum_floor_bar=1.3e-12,
    )

    assert seeded == pytest.approx(math.log10(DEFAULT_VACUUM_FLOOR_BAR))
    assert seeded == pytest.approx(-9.0)
    assert moon_floor == pytest.approx(math.log10(1.3e-12))


def test_pin_zero_o2_argon_schedule_is_controlled_o2() -> None:
    schedule = deepcopy(_n2_lab_schedule())
    schedule["gas_boundary"]["background_gas"]["species"] = "Ar"
    manager = CampaignManager(_setpoints())
    manager.overrides["C2A"] = {"lab_schedule": schedule}
    melt = MeltState()

    manager.configure_campaign(melt, CampaignPhase.C2A)
    manager.apply_lab_schedule_controls(
        melt,
        CampaignPhase.C2A,
        sample_time_h=0.0,
    )

    assert melt.pO2_mbar == pytest.approx(0.0)
    assert melt.atmosphere is Atmosphere.CONTROLLED_O2
    assert melt.background_gas_species == "Ar"


def test_pin_sealed_ideal_gas_partial_matches_the_rh03_hour0_holdup() -> None:
    partials = BuiltinOverheadGasEquilibriumProvider.compute_partial_pressures_bar(
        {"O2": _RH03_O2_MOL},
        _RH03_VOLUME_M3,
        _RH03_TEMPERATURE_K,
    )

    assert partials["O2"] == pytest.approx(_RH03_IDEAL_O2_BAR, rel=1e-12)
