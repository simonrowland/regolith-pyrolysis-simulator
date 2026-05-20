import json
import subprocess
import sys
from pathlib import Path

import pytest

from simulator import condensation as condensation_module
from simulator.condensation import CondensationModel
from simulator.state import (
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

BASELINE_SIO_EVOLVED_KG = {
    "lunar_mare_low_ti": 3.73034175962,
    "mars_basalt": 3.82535373379,
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
    assert actual["sio_evolved_kg"] == BASELINE_SIO_EVOLVED_KG[feedstock]
    assert expected["sio_evolved_kg"] == BASELINE_SIO_EVOLVED_KG[feedstock]
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
