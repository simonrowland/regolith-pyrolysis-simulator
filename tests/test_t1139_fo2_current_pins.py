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
from simulator.core import PyrolysisSimulator
from simulator.environment import DEFAULT_VACUUM_FLOOR_BAR
from simulator.fe_redox import (
    feo_iw_log10_fO2_bar,
    intrinsic_melt_fO2,
    melt_fO2_seed_without_ferric_iron,
)
from simulator.melt_backend.base import InternalAnalyticalBackend
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


def _sim() -> PyrolysisSimulator:
    return PyrolysisSimulator(
        InternalAnalyticalBackend(),
        _setpoints(),
        yaml.safe_load((DATA_DIR / "feedstocks.yaml").read_text()) or {},
        yaml.safe_load((DATA_DIR / "vapor_pressures.yaml").read_text()) or {},
    )


def test_melt_seed_is_holzheid_iw_plus_alkali_without_a_vacuum_floor() -> None:
    composition = _ferrous_lunar_composition()
    # Lunar Na2O 0.4 + K2O 0.1 = 0.50 wt% alkali. The seed's alkali term is
    # 0.01 dex per wt%, capped at 0.15, so this composition is +0.005 dex.
    alkali_offset_dex = 0.005
    seeded = intrinsic_melt_fO2(composition, 1338.15)
    no_alkali = intrinsic_melt_fO2({"SiO2": 45.0, "FeO": 16.5}, 1338.15)

    assert no_alkali == pytest.approx(feo_iw_log10_fO2_bar(1338.15))
    assert seeded - no_alkali == pytest.approx(alkali_offset_dex)
    assert intrinsic_melt_fO2(composition, 298.15) == pytest.approx(
        feo_iw_log10_fO2_bar(298.15) + alkali_offset_dex
    )
    assert seeded != pytest.approx(math.log10(DEFAULT_VACUUM_FLOOR_BAR))
    assert melt_fO2_seed_without_ferric_iron(composition) is True
    assert melt_fO2_seed_without_ferric_iron(
        {"FeO": 2.0, "Fe2O3": 1.0}
    ) is False


def test_reservoir_tracks_iw_until_the_first_liquid_tick() -> None:
    sim = _sim()
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    composition = sim._melt_oxide_wt_pct()
    notice = sim.melt_fO2_seed_run_notice()
    assert notice is not None
    assert notice["code"] == "melt_fO2_seed_without_ferric_iron"
    assert notice["authority"] == "IW buffer, no Fe3+/Fe2+"

    sim.melt.temperature_C = 1100.0
    sim._re_reference_melt_fO2_to_temperature()
    subliquid = intrinsic_melt_fO2(composition, 1100.0 + 273.15)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        subliquid
    )
    assert sim.melt.oxygen_reservoir.reference_T_K is None

    sim.melt.temperature_C = 1215.0
    sim._re_reference_melt_fO2_to_temperature()
    fixed = intrinsic_melt_fO2(composition, 1215.0 + 273.15)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(fixed)
    assert sim.melt.oxygen_reservoir.reference_T_K == pytest.approx(1215.0 + 273.15)

    sim.melt.temperature_C = 1230.0
    sim._re_reference_melt_fO2_to_temperature()
    fresh = intrinsic_melt_fO2(composition, 1230.0 + 273.15)
    assert sim.melt.oxygen_reservoir.reference_T_K == pytest.approx(1230.0 + 273.15)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log != pytest.approx(fresh)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log != pytest.approx(fixed)


def test_zero_o2_argon_schedule_is_a_closed_sweep() -> None:
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
    assert melt.atmosphere is Atmosphere.PN2_SWEEP
    assert melt.background_gas_species == "Ar"


def test_pin_sealed_ideal_gas_partial_matches_the_rh03_hour0_holdup() -> None:
    partials = BuiltinOverheadGasEquilibriumProvider.compute_partial_pressures_bar(
        {"O2": _RH03_O2_MOL},
        _RH03_VOLUME_M3,
        _RH03_TEMPERATURE_K,
    )

    assert partials["O2"] == pytest.approx(_RH03_IDEAL_O2_BAR, rel=1e-12)
