import json
import subprocess
import sys
from pathlib import Path

import pytest

from simulator import condensation as condensation_module
from simulator.condensation import CondensationModel
from simulator.overhead import OverheadGasModel
from simulator.runner import build_sio_yield_report
from simulator.state import (
    CampaignPhase,
    CondensationStage,
    CondensationTrain,
    EvaporationFlux,
    MeltState,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sio_yield"

GOLDENS = (
    ("lunar_mare_low_ti", "lunar_mare_low_ti_c2a.json"),
    ("mars_basalt", "mars_basalt_c2a.json"),
)

# Post 2026-05-20 Antoine P_sat refit: builtin SiO fallback fitted to VapoRock,
# so evolved SiO dropped ~4700x to the activity-corrected magnitude.
# Was {lunar: 3.7303230676, mars: 3.82533227031} pre-refit.
BASELINE_SIO_EVOLVED_KG = {
    "lunar_mare_low_ti": 0.000786538104529,
    "mars_basalt": 0.000848707296777,
}

BASELINE_STAGE4_SIO2_KG = {
    "lunar_mare_low_ti": 1.65257779038,
    "mars_basalt": 1.69466902181,
}


def _assert_golden_close(actual, expected, path="root"):
    if isinstance(expected, dict):
        assert set(actual) == set(expected), path
        for key in expected:
            _assert_golden_close(actual[key], expected[key], f"{path}.{key}")
        return
    if isinstance(expected, list):
        assert len(actual) == len(expected), path
        for index, expected_item in enumerate(expected):
            _assert_golden_close(
                actual[index], expected_item, f"{path}[{index}]")
        return
    if isinstance(expected, (int, float)):
        tolerance = max(abs(float(expected)) * 0.01, 1.0e-12)
        assert abs(float(actual) - float(expected)) <= tolerance, path
        return
    assert actual == expected, path


@pytest.mark.parametrize(("feedstock", "golden_name"), GOLDENS)
def test_sio_yield_cli_matches_golden(tmp_path, feedstock, golden_name):
    output_path = tmp_path / golden_name
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.runner.sio_yield",
            "--feedstock",
            feedstock,
            "--campaign",
            "C2A_continuous",
            "--hours",
            "24",
            "--output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    actual = json.loads(output_path.read_text())
    expected = json.loads((FIXTURE_DIR / golden_name).read_text())

    _assert_golden_close(actual, expected)
    # Strict-equality on a baseline float caught FP-jitter (~1e-12 absolute,
    # ~1e-9 relative) introduced by F4 rump-payload assembly + S1b shuttle
    # gate after the post-2026-05-20 refit established the baseline. The
    # numbers themselves are still physics-honest (≤5e-12 % mass closure
    # held in Review E + E2 default-on test). Loosen to relative tolerance.
    assert actual["sio_evolved_kg"] == pytest.approx(
        BASELINE_SIO_EVOLVED_KG[feedstock], rel=1e-8
    )
    assert expected["sio_evolved_kg"] == pytest.approx(
        BASELINE_SIO_EVOLVED_KG[feedstock], rel=1e-8
    )
    assert "wall_deposit_kg" in actual
    assert "fouling_rate" in actual
    placement = actual["sio_to_silica_fume_kg"]
    assert placement["stage_3_sio_zone_product"] > 0.0
    assert (
        placement["stage_4_alkali_mg_carryover"]
        < BASELINE_STAGE4_SIO2_KG[feedstock]
    )
    assert 0.0 <= actual["sio_yield_pct_of_feedstock"] <= 30.0
    assert actual["alpha_SiO"] == pytest.approx(0.04)
    assert actual["alpha_provenance"] == (
        "Phase 1 \u03b1 surface (commit fc2d40b); "
        "SF2004 Table 10 SiO2(liq) Hashimoto 1990"
    )
    assert "order-of-magnitude regime check" in actual["verdict"]
    assert "not 1-decade fidelity" in actual["verdict"]


def test_band_aware_hkl_route_captures_sio_in_stage_3():
    model = CondensationModel(CondensationTrain.create_default())

    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        MeltState(),
    )

    assert route.condensed_by_stage_species[3]["SiO"] > 0.0
    assert route.condensed_by_stage_species[4]["SiO"] < 0.35
    assert route.remaining_by_species["SiO"] == pytest.approx(
        0.11006692746967289
    )
    assert route.wall_deposit_by_species.get("SiO", 0.0) >= 0.0


def test_route_destinations_sum_to_evolved_budget():
    model = CondensationModel(CondensationTrain.create_default())
    melt = MeltState()
    melt.temperature_C = 1700.0

    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        melt,
    )

    destinations = (
        route.condensed_for_species("SiO")
        + route.wall_deposit_by_species.get("SiO", 0.0)
        + route.remaining_by_species["SiO"]
    )
    assert destinations == pytest.approx(1.0)


def test_cold_liner_routes_sio_to_wall_deposit_bucket():
    model = CondensationModel(
        CondensationTrain.create_default(),
        wall_temperature_C=900.0,
    )
    melt = MeltState()
    melt.temperature_C = 1700.0

    route = model.route(
        EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0),
        melt,
    )

    assert route.wall_deposit_by_species["SiO"] > 0.0
    destinations = (
        route.condensed_for_species("SiO")
        + route.wall_deposit_by_species["SiO"]
        + route.remaining_by_species["SiO"]
    )
    assert destinations == pytest.approx(1.0)


def test_cached_condensation_model_uses_updated_liner_temperature():
    model = CondensationModel(
        CondensationTrain.create_default(),
        wall_temperature_C=900.0,
    )
    melt = MeltState()
    melt.temperature_C = 1700.0
    flux = EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0)

    cold_route = model.route(flux, melt)
    model.configure_operating_conditions(
        wall_temperature_C=1650.0,
        overhead_pressure_mbar=10.0,
        pipe_diameter_m=0.12,
    )
    hot_route = model.route(flux, melt)

    assert cold_route.wall_deposit_by_species["SiO"] > 0.0
    assert hot_route.wall_deposit_by_species.get("SiO", 0.0) < (
        cold_route.wall_deposit_by_species["SiO"]
    )


def test_knudsen_regime_factor_rises_toward_ballistic():
    viscous_kn = condensation_module._knudsen_number(
        pressure_pa=1000.0,
        T_K=1773.15,
        characteristic_length_m=0.12,
    )
    ballistic_kn = condensation_module._knudsen_number(
        pressure_pa=0.1,
        T_K=1773.15,
        characteristic_length_m=0.12,
    )

    assert viscous_kn < 0.01
    assert ballistic_kn > 1.0
    assert condensation_module._knudsen_regime_factor(viscous_kn) < 0.1
    assert condensation_module._knudsen_regime_factor(ballistic_kn) > 0.99


def test_low_pressure_ballistic_regime_increases_wall_deposit():
    train = CondensationTrain.create_default()
    melt = MeltState()
    melt.temperature_C = 1700.0
    flux = EvaporationFlux(species_kg_hr={"SiO": 1.0}, total_kg_hr=1.0)

    viscous = CondensationModel(train, wall_temperature_C=1100.0)
    viscous.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        pipe_diameter_m=0.12,
        gas_temperature_C=1100.0,
    )
    ballistic = CondensationModel(train, wall_temperature_C=1100.0)
    ballistic.configure_operating_conditions(
        overhead_pressure_mbar=0.001,
        pipe_diameter_m=0.12,
        gas_temperature_C=1100.0,
    )

    viscous_route = viscous.route(flux, melt)
    ballistic_route = ballistic.route(flux, melt)

    assert ballistic.regime_factor > viscous.regime_factor
    assert ballistic_route.wall_deposit_by_species["SiO"] > (
        viscous_route.wall_deposit_by_species["SiO"]
    )


def test_liner_temperature_schedule_is_recipe_controllable():
    model = OverheadGasModel(
        {
            "liner_temperature_C": {
                "default_C": 1500.0,
                "schedule": [
                    {
                        "campaign": "C2A",
                        "from_campaign_hour": 0,
                        "to_campaign_hour": 4,
                        "start_C": 1100,
                        "end_C": 1600,
                    },
                    {
                        "campaign": "C2A",
                        "from_campaign_hour": 4,
                        "start_C": 1600,
                        "end_C": 1600,
                    },
                ],
            }
        }
    )
    melt = MeltState()
    melt.campaign = CampaignPhase.C2A

    melt.campaign_hour = 0
    assert model.resolve_pipe_temperature_C(melt) == pytest.approx(1100.0)
    melt.campaign_hour = 2
    assert model.resolve_pipe_temperature_C(melt) == pytest.approx(1350.0)
    melt.campaign_hour = 8
    assert model.resolve_pipe_temperature_C(melt) == pytest.approx(1600.0)


def test_po2_wall_sweep_mode_suppresses_first_tick_sio_release():
    no_suppress = build_sio_yield_report(
        feedstock_id="lunar_mare_low_ti",
        hours=1,
        t_low_c=1500.0,
        t_hold_c=1500.0,
        liner_temperature_c=1500.0,
    )
    o2_mode = build_sio_yield_report(
        feedstock_id="lunar_mare_low_ti",
        hours=1,
        t_low_c=1500.0,
        t_hold_c=1500.0,
        liner_temperature_c=1500.0,
        pO2_mbar=1.0,
    )

    assert o2_mode["sio_evolved_kg"] < no_suppress["sio_evolved_kg"] * 1.0e-5


def test_hkl_sampling_uses_actual_stage_band_not_material_defaults():
    custom_stage = CondensationStage(
        3, "Custom hot SiO stage", (1100.0, 1200.0), ["SiO"]
    )

    assert condensation_module._stage_temp_band_C(custom_stage) == (
        1100.0,
        1200.0,
    )


def test_no_antoine_species_cannot_create_unplaced_capture_budget():
    model = CondensationModel(CondensationTrain.create_default())

    route = model.route(
        EvaporationFlux(species_kg_hr={"O2": 1.0}, total_kg_hr=1.0),
        MeltState(),
    )

    assert route.remaining_by_species["O2"] == pytest.approx(1.0)
    assert route.condensed_for_species("O2") == pytest.approx(0.0)
