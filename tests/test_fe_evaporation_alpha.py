"""Regression tests for the Fe evaporation coefficient and its authority."""

from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from simulator.condensation import alpha_s
from simulator.evaporation import _load_evaporation_alpha_by_species
from simulator.vapour_rail.catalog import vapor_pressure_legacy_view


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_fe_melt_evaporation_alpha_is_bridge_and_flagged_outside_its_band() -> None:
    data = yaml.safe_load((REPO_ROOT / "data" / "vapor_pressures.yaml").read_text())

    alpha_row = vapor_pressure_legacy_view(data)["metals"]["Fe"][
        "evaporation_alpha"
    ]
    alpha_by_species = _load_evaporation_alpha_by_species(data)
    spec = alpha_by_species["Fe"]

    assert isinstance(spec, Mapping)
    assert spec["form"] == "scalar"
    assert spec["value"] == pytest.approx(0.24)
    assert spec["alpha_authority_status"] == "bridge"
    assert tuple(spec["temperature_range_K"]) == (1973, 2273)
    assert alpha_row["status"] == "bridge"
    assert tuple(alpha_row["envelope"]) == (0.21, 0.27)

    notice = spec["notice"]
    for source in (
        "Fedkin, Grossman & Ghiorso 2006",
        "Alexander",
        "Hashimoto 1983",
        "alpha=1",
    ):
        assert source in notice

    in_band = {"coefficient_spec": spec}
    assert alpha_s("Fe", 2100, in_band) == pytest.approx(0.24)
    assert in_band["alpha_s_evaluation"]["alpha_authority_status"] == "bridge"
    assert in_band["alpha_s_evaluation"]["alpha_s_extrapolated"] is False
    assert in_band["alpha_s_evaluation"]["alpha_s_valid_range_K"] == [1973, 2273]

    for temperature, direction in ((1972, "below"), (2274, "above")):
        outside_band = {"coefficient_spec": spec}
        assert alpha_s("Fe", temperature, outside_band) == pytest.approx(0.24)
        evaluation = outside_band["alpha_s_evaluation"]
        assert evaluation["alpha_s_extrapolated"] is True
        assert evaluation[f"alpha_s_temperature_{direction}_valid_range"] is True
        assert "extrapolated beyond valid_range_K" in evaluation[
            "alpha_s_extrapolation_warning"
        ]
