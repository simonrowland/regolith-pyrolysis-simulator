"""Regression tests for the Fe evaporation coefficient and its authority."""

from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from simulator.condensation import alpha_s
from simulator.evaporation import _load_evaporation_alpha_by_species


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_fe_melt_evaporation_alpha_is_upper_bound_and_flagged_outside_its_band() -> None:
    data = yaml.safe_load((REPO_ROOT / "data" / "vapor_pressures.yaml").read_text())

    alpha_by_species = _load_evaporation_alpha_by_species(data)
    spec = alpha_by_species["Fe"]

    assert isinstance(spec, Mapping)
    assert spec["form"] == "scalar"
    assert spec["value"] == pytest.approx(1.0)
    assert spec["value"] != 0.02
    assert spec["alpha_authority_status"] == "analytical_upper_bound"
    assert tuple(spec["temperature_range_K"]) == (1200, 2500)

    in_band = {"coefficient_spec": spec}
    assert alpha_s("Fe", 1500, in_band) == pytest.approx(1.0)
    assert in_band["alpha_s_evaluation"]["alpha_s_extrapolated"] is False
    assert in_band["alpha_s_evaluation"]["alpha_s_valid_range_K"] == [1200, 2500]

    for temperature, direction in ((1199, "below"), (2501, "above")):
        outside_band = {"coefficient_spec": spec}
        assert alpha_s("Fe", temperature, outside_band) == pytest.approx(1.0)
        evaluation = outside_band["alpha_s_evaluation"]
        assert evaluation["alpha_s_extrapolated"] is True
        assert evaluation[f"alpha_s_temperature_{direction}_valid_range"] is True
        assert "extrapolated beyond valid_range_K" in evaluation[
            "alpha_s_extrapolation_warning"
        ]
