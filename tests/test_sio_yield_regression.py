import json
import subprocess
import sys
from pathlib import Path

import pytest


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sio_yield"

GOLDENS = (
    ("lunar_mare_low_ti", "lunar_mare_low_ti_c2a.json"),
    ("mars_basalt", "mars_basalt_c2a.json"),
)


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
    assert 0.0 <= actual["sio_yield_pct_of_feedstock"] <= 30.0
    assert actual["alpha_SiO"] == pytest.approx(0.5)
    assert actual["alpha_provenance"] == (
        "placeholder; pending Phase 1 \u03b1 surface"
    )
    assert "order-of-magnitude regime check" in actual["verdict"]
    assert "not 1-decade fidelity" in actual["verdict"]
